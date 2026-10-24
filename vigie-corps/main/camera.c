/*
 * Vigie — caméra OV5647 sur le connecteur MIPI-CSI de la carte.
 *
 * Une tâche lit les images (RGB565, sortie de l'ISP), et :
 *   - dix fois par seconde au plus, réduit l'image de moitié avec le PPA
 *     (accélérateur 2D du P4), la passe en niveaux de gris en étirant son
 *     contraste, y cherche un visage (ESP-DL), publie sa position (vigie/regard)
 *     et tient à jour la présence (vigie/presence) ;
 *   - si l'aperçu est ouvert, affiche dans un disque de 600 px, en miroir,
 *     l'image que voit la détection (grise et contrastée).
 *
 * Exposition : sur puce v1.3, le contrôleur ISP d'ESP-IDF 6 plante
 * (esp-video-components #99) et le pilote n'expose aucun réglage d'exposition.
 * On active donc l'exposition et le gain automatiques du capteur lui-même
 * (registre 0x3503 de l'OV5647), par le bus I2C de la carte.
 *
 * Câblage : SCCB sur le bus I2C de la carte (GPIO 7/8, partagé avec le tactile
 * et l'audio), pas de broche reset ni power-down : comme l'exemple Waveshare
 * « 09_video_lcd_display ».
 */
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <unistd.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_check.h"
#include "esp_timer.h"
#include "esp_heap_caps.h"
#include "esp_cache.h"
#include "driver/ppa.h"
#include "driver/i2c_master.h"
#include "linux/videodev2.h"
#include "esp_video_init.h"
#include "esp_video_device.h"
#include "bsp/esp-bsp.h"
#include "camera.h"
#include "visage.h"
#include "vigie.h"
#include "ui.h"

static const char *TAG = "camera";

#define NB_TAMPONS          3
#define APERCU_MAX_US       (120LL * 1000 * 1000)  /* l'aperçu se ferme seul après 2 min */
#define DETECTION_US        (100 * 1000)           /* 10 analyses par seconde au plus */
#define REGARD_US           (200 * 1000)           /* 5 messages vigie/regard par seconde au plus */
#define PERDU_US            (600 * 1000)           /* visage disparu depuis 0,6 s → « perdu » */
#define PRESENT_APRES       3                      /* détections d'affilée pour dire « present » */
#define ABSENT_US           ((int64_t)CONFIG_VIGIE_ABSENCE_S * 1000 * 1000)
#define BILAN_US            (30LL * 1000 * 1000)
#define ALIGN_DMA           128                    /* lignes de cache L1 et L2 */

#define OV5647_ADRESSE      0x36

static int fd = -1;
static uint8_t *tampons[NB_TAMPONS];
static uint32_t larg, haut;

static bool demarree = false;
static volatile bool apercu_voulu = false;

static ppa_client_handle_t ppa = NULL;

/* Image de détection : image entière réduite de moitié, grise et contrastée. */
static uint32_t dl_larg, dl_haut;
static uint16_t *dl_buf;
static size_t dl_taille;
static int dl_bas = 0, dl_haut_ = 255;     /* dernier étirement appliqué (diagnostic) */

/* Aperçu : carré central de l'image de détection, agrandi vers le disque. */
static void *apercu_buf[2];
static size_t apercu_taille;
static uint32_t cote, ox, oy, sortie, decal;   /* en pixels de l'image caméra */
static float echelle;                          /* image caméra → aperçu */

static i2c_master_dev_handle_t capteur = NULL;

static bool present = false;

/* --- Registres de l'OV5647 (accès direct, l'exposition n'est pas exposée en V4L2) -------- */

static bool capteur_ecrire(uint16_t reg, uint8_t val)
{
    if (!capteur) {
        return false;
    }
    uint8_t b[3] = {reg >> 8, reg & 0xff, val};
    return i2c_master_transmit(capteur, b, sizeof(b), 50) == ESP_OK;
}

static int capteur_lire(uint16_t reg)
{
    if (!capteur) {
        return -1;
    }
    uint8_t a[2] = {reg >> 8, reg & 0xff};
    uint8_t v = 0;
    if (i2c_master_transmit_receive(capteur, a, sizeof(a), &v, 1, 50) != ESP_OK) {
        return -1;
    }
    return v;
}

/* Exposition en lignes et gain en 1/16, lus dans le capteur. */
static void capteur_etat(int *expo, int *gain, int *mode)
{
    int e0 = capteur_lire(0x3500), e1 = capteur_lire(0x3501), e2 = capteur_lire(0x3502);
    int g0 = capteur_lire(0x350A), g1 = capteur_lire(0x350B);
    *mode = capteur_lire(0x3503);
    *expo = (e0 < 0 || e1 < 0 || e2 < 0) ? -1 : ((((e0 & 0x0f) << 16) | (e1 << 8) | e2) >> 4);
    *gain = (g0 < 0 || g1 < 0) ? -1 : (((g0 & 0x03) << 8) | g1);
}

static void capteur_automatique(void)
{
    if (!capteur) {
        i2c_device_config_t dc = {
            .dev_addr_length = I2C_ADDR_BIT_LEN_7,
            .device_address = OV5647_ADRESSE,
            .scl_speed_hz = 100000,
        };
        if (i2c_master_bus_add_device(bsp_i2c_get_handle(), &dc, &capteur) != ESP_OK) {
            ESP_LOGW(TAG, "Accès direct au capteur impossible : exposition figée");
            capteur = NULL;
            return;
        }
    }
    int avant = capteur_lire(0x3503);
    /* bit 0 = exposition manuelle, bit 1 = gain manuel : les deux à 0 → automatiques */
    bool ok = avant >= 0 && capteur_ecrire(0x3503, (uint8_t)(avant & ~0x03));
    ESP_LOGI(TAG, "Exposition et gain automatiques du capteur : %s (0x3503 : 0x%02x → 0x%02x)",
             ok ? "activés" : "ÉCHEC", avant & 0xff, capteur_lire(0x3503) & 0xff);
    ESP_LOGI(TAG, "  cible AEC 0x3A0F=0x%02x 0x3A10=0x%02x, plafond de gain 0x3A18=0x%02x 0x3A19=0x%02x",
             capteur_lire(0x3A0F) & 0xff, capteur_lire(0x3A10) & 0xff,
             capteur_lire(0x3A18) & 0xff, capteur_lire(0x3A19) & 0xff);
}

/* --- Préparation ------------------------------------------------------------------- */

static void reglage(uint32_t id, int valeur, const char *nom)
{
    struct v4l2_ext_control c = {.id = id, .value = valeur};
    struct v4l2_ext_controls cs = {.ctrl_class = id & 0x0fff0000UL, .count = 1, .controls = &c};
    if (ioctl(fd, VIDIOC_S_EXT_CTRLS, &cs) != 0) {
        ESP_LOGW(TAG, "Réglage %s refusé par le capteur", nom);
    }
}

static void *tampon_dma(size_t *taille)
{
    *taille = (*taille + ALIGN_DMA - 1) & ~(size_t)(ALIGN_DMA - 1);
    return heap_caps_aligned_calloc(ALIGN_DMA, 1, *taille, MALLOC_CAP_SPIRAM);
}

static esp_err_t ouvrir(void)
{
    ESP_RETURN_ON_ERROR(bsp_i2c_init(), TAG, "bus I2C");
    esp_video_init_csi_config_t csi = {
        .sccb_config = {
            .init_sccb = false,
            .i2c_handle = bsp_i2c_get_handle(),
            .freq = CONFIG_BSP_I2C_CLK_SPEED_HZ,
        },
        .reset_pin = -1,
        .pwdn_pin = -1,
    };
    esp_video_init_config_t cfg = {.csi = &csi};
    ESP_RETURN_ON_ERROR(esp_video_init(&cfg), TAG, "esp_video_init (caméra branchée ? nappe dans le bon sens ?)");

    fd = open(ESP_VIDEO_MIPI_CSI_DEVICE_NAME, O_RDONLY);
    ESP_RETURN_ON_FALSE(fd >= 0, ESP_FAIL, TAG, "ouverture de %s", ESP_VIDEO_MIPI_CSI_DEVICE_NAME);

    struct v4l2_capability cap = {0};
    if (ioctl(fd, VIDIOC_QUERYCAP, &cap) == 0) {
        ESP_LOGI(TAG, "Capteur : %s (%s)", cap.card, cap.driver);
    }

    struct v4l2_format fmt = {.type = V4L2_BUF_TYPE_VIDEO_CAPTURE};
    ESP_RETURN_ON_FALSE(ioctl(fd, VIDIOC_G_FMT, &fmt) == 0, ESP_FAIL, TAG, "lecture du format");
    larg = fmt.fmt.pix.width;
    haut = fmt.fmt.pix.height;
    if (fmt.fmt.pix.pixelformat != V4L2_PIX_FMT_RGB565) {
        struct v4l2_format f = {.type = V4L2_BUF_TYPE_VIDEO_CAPTURE};
        f.fmt.pix.width = larg;
        f.fmt.pix.height = haut;
        f.fmt.pix.pixelformat = V4L2_PIX_FMT_RGB565;
        ESP_RETURN_ON_FALSE(ioctl(fd, VIDIOC_S_FMT, &f) == 0, ESP_FAIL, TAG, "passage en RGB565");
    }
    ESP_LOGI(TAG, "Image %" PRIu32 " × %" PRIu32 " en RGB565", larg, haut);

    reglage(V4L2_CID_HFLIP, CONFIG_VIGIE_CAM_HFLIP, "miroir horizontal");
    reglage(V4L2_CID_VFLIP, CONFIG_VIGIE_CAM_VFLIP, "retournement vertical");

    struct v4l2_requestbuffers req = {
        .count = NB_TAMPONS, .type = V4L2_BUF_TYPE_VIDEO_CAPTURE, .memory = V4L2_MEMORY_MMAP,
    };
    ESP_RETURN_ON_FALSE(ioctl(fd, VIDIOC_REQBUFS, &req) == 0, ESP_FAIL, TAG, "réservation des tampons");
    for (int i = 0; i < NB_TAMPONS; i++) {
        struct v4l2_buffer b = {.type = V4L2_BUF_TYPE_VIDEO_CAPTURE, .memory = V4L2_MEMORY_MMAP, .index = i};
        ESP_RETURN_ON_FALSE(ioctl(fd, VIDIOC_QUERYBUF, &b) == 0, ESP_FAIL, TAG, "tampon %d", i);
        tampons[i] = mmap(NULL, b.length, PROT_READ | PROT_WRITE, MAP_SHARED, fd, b.m.offset);
        ESP_RETURN_ON_FALSE(tampons[i] != NULL && tampons[i] != MAP_FAILED, ESP_FAIL, TAG, "mmap %d", i);
        ESP_RETURN_ON_FALSE(ioctl(fd, VIDIOC_QBUF, &b) == 0, ESP_FAIL, TAG, "file du tampon %d", i);
    }

    ppa_client_config_t pc = {.oper_type = PPA_OPERATION_SRM};
    ESP_RETURN_ON_ERROR(ppa_register_client(&pc, &ppa), TAG, "PPA");

    /* Image de détection : moitié de l'image caméra. */
    dl_larg = larg / 2;
    dl_haut = haut / 2;
    dl_taille = (size_t)dl_larg * dl_haut * 2;
    dl_buf = tampon_dma(&dl_taille);
    ESP_RETURN_ON_FALSE(dl_buf, ESP_ERR_NO_MEM, TAG, "tampon de détection");

    /* Aperçu : carré central, réduit par pas de 1/16 (précision du PPA). */
    cote = larg < haut ? larg : haut;
    ox = (larg - cote) / 2;
    oy = (haut - cote) / 2;
    echelle = (float)((int)(16.0f * UI_APERCU / cote)) / 16.0f;
    if (echelle > 1.0f) {
        echelle = 1.0f;
    }
    sortie = (uint32_t)(cote * echelle);
    decal = (UI_APERCU - sortie) / 2;
    apercu_taille = (size_t)UI_APERCU * UI_APERCU * 2;
    for (int i = 0; i < 2; i++) {
        size_t t = apercu_taille;
        apercu_buf[i] = tampon_dma(&t);
        apercu_taille = t;
        ESP_RETURN_ON_FALSE(apercu_buf[i], ESP_ERR_NO_MEM, TAG, "tampon d'aperçu");
    }

    int type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    ESP_RETURN_ON_FALSE(ioctl(fd, VIDIOC_STREAMON, &type) == 0, ESP_FAIL, TAG, "démarrage du flux");

    /* Après STREAMON : le pilote a écrit sa table d'initialisation dans le capteur. */
    capteur_automatique();
    return ESP_OK;
}

/* --- Image de détection -------------------------------------------------------------- */

static inline uint32_t luminance(uint16_t p)
{
    uint32_t r = ((p >> 11) & 0x1f) << 3;
    uint32_t g = ((p >> 5) & 0x3f) << 2;
    uint32_t b = (p & 0x1f) << 3;
    return (77 * r + 150 * g + 29 * b) >> 8;
}

/* Réduit l'image caméra de moitié (PPA), puis la passe en gris en étirant son contraste :
 * les 2 % de pixels les plus sombres deviennent noirs, les 2 % les plus clairs blancs. */
static bool preparer_detection(const uint8_t *image)
{
    ppa_srm_oper_config_t op = {
        .in = {
            .buffer = image,
            .pic_w = larg, .pic_h = haut,
            .block_w = larg, .block_h = haut,
            .block_offset_x = 0, .block_offset_y = 0,
            .srm_cm = PPA_SRM_COLOR_MODE_RGB565,
        },
        .out = {
            .buffer = dl_buf,
            .buffer_size = dl_taille,
            .pic_w = dl_larg, .pic_h = dl_haut,
            .block_offset_x = 0, .block_offset_y = 0,
            .srm_cm = PPA_SRM_COLOR_MODE_RGB565,
        },
        .rotation_angle = PPA_SRM_ROTATION_ANGLE_0,
        .scale_x = 0.5f,
        .scale_y = 0.5f,
        .mirror_x = false,
        .mirror_y = false,
        .mode = PPA_TRANS_MODE_BLOCKING,
    };
    if (ppa_do_scale_rotate_mirror(ppa, &op) != ESP_OK) {
        return false;
    }

    const uint32_t nb = dl_larg * dl_haut;
    static uint32_t histo[256];
    memset(histo, 0, sizeof(histo));
    uint32_t total = 0;
    for (uint32_t i = 0; i < nb; i += 4) {
        histo[luminance(dl_buf[i])]++;
        total++;
    }
    int bas = -1, haut_ = 255;
    uint32_t cumul = 0;
    for (int i = 0; i < 256; i++) {
        cumul += histo[i];
        if (bas < 0 && cumul >= total / 50) {
            bas = i;
        }
        if (cumul >= total - total / 50) {
            haut_ = i;
            break;
        }
    }
    if (bas < 0) {
        bas = 0;
    }
    if (haut_ - bas < 16) {          /* image presque uniforme : on n'amplifie pas le bruit */
        haut_ = bas + 16;
    }
    dl_bas = bas;
    dl_haut_ = haut_;

    static uint16_t table[256];
    for (int v = 0; v < 256; v++) {
        int g = (v - bas) * 255 / (haut_ - bas);
        g = g < 0 ? 0 : (g > 255 ? 255 : g);
        table[v] = (uint16_t)(((g >> 3) << 11) | ((g >> 2) << 5) | (g >> 3));
    }
    for (uint32_t i = 0; i < nb; i++) {
        dl_buf[i] = table[luminance(dl_buf[i])];
    }
    /* Le modèle ou le PPA peuvent relire ce tampon par DMA : on vide le cache. */
    esp_cache_msync(dl_buf, dl_taille, ESP_CACHE_MSYNC_FLAG_DIR_C2M);
    return true;
}

/* --- Aperçu ---------------------------------------------------------------------------- */

/* Agrandit l'image de détection (moitié de la caméra) vers le disque, en miroir. */
static bool reduire(void *dest)
{
    float e = echelle * 2.0f;   /* 0,75 × 2 = 1,5 : multiple de 1/16, accepté par le PPA */
    ppa_srm_oper_config_t op = {
        .in = {
            .buffer = dl_buf,
            .pic_w = dl_larg, .pic_h = dl_haut,
            .block_w = cote / 2, .block_h = cote / 2,
            .block_offset_x = ox / 2, .block_offset_y = oy / 2,
            .srm_cm = PPA_SRM_COLOR_MODE_RGB565,
        },
        .out = {
            .buffer = dest,
            .buffer_size = apercu_taille,
            .pic_w = UI_APERCU, .pic_h = UI_APERCU,
            .block_offset_x = decal, .block_offset_y = decal,
            .srm_cm = PPA_SRM_COLOR_MODE_RGB565,
        },
        .rotation_angle = PPA_SRM_ROTATION_ANGLE_0,
        .scale_x = e,
        .scale_y = e,
        .mirror_x = true,       /* comme un miroir : on s'y retrouve plus facilement */
        .mirror_y = false,
        .mode = PPA_TRANS_MODE_BLOCKING,
    };
    return ppa_do_scale_rotate_mirror(ppa, &op) == ESP_OK;
}

/* Coordonnées image caméra → aperçu (recadré, réduit, en miroir). */
static int vers_apercu_x(int x)
{
    return (int)decal + (int)sortie - (int)((x - (int)ox) * echelle);
}

static int vers_apercu_y(int y)
{
    return (int)decal + (int)((y - (int)oy) * echelle);
}

static void virgule(char *s)
{
    for (; *s; s++) {
        if (*s == '.') {
            *s = ',';
        }
    }
}

/* --- Tâche --------------------------------------------------------------------------------- */

static void tache(void *arg)
{
    (void)arg;
    bool affiche = false;
    int64_t ouvert_depuis = 0, derniere_detection = 0, dernier_regard = 0, vu_le = 0;
    int idx = 0, d_affilee = 0, nb_analyses = 0, nb_images = 0, nb_trouves = 0;
    int64_t duree_analyses = 0, bilan = esp_timer_get_time();
    bool regard_annonce = false;
    bool dl_pret = false;    /* l'image de détection contient déjà une image */
    float meilleur_score = 0;
    visage_t v[1];
    int n = 0;   /* visages trouvés à la dernière analyse */
    const bool detection = visage_disponible();
    if (!detection) {
        ESP_LOGW(TAG, "Détection de visage indisponible (composant human_face_detect absent ou désactivé)");
    }

    for (;;) {
        struct v4l2_buffer b = {.type = V4L2_BUF_TYPE_VIDEO_CAPTURE, .memory = V4L2_MEMORY_MMAP};
        if (ioctl(fd, VIDIOC_DQBUF, &b) != 0) {
            ESP_LOGE(TAG, "Lecture d'image impossible (%d)", errno);
            vTaskDelay(pdMS_TO_TICKS(100));
            continue;
        }
        const uint8_t *image = tampons[b.index];
        int64_t maintenant = esp_timer_get_time();
        nb_images++;

        /* 1. Visage, sur l'image réduite et contrastée */
        bool analyse = false;
        bool a_traiter = maintenant - derniere_detection >= DETECTION_US;
        if (a_traiter && preparer_detection(image)) {
            derniere_detection = maintenant;
            dl_pret = true;
            if (detection) {
                n = visage_detecter((const uint8_t *)dl_buf, dl_larg, dl_haut, v, 1);
                int64_t fin = esp_timer_get_time();
                duree_analyses += fin - maintenant;
                nb_analyses++;
                analyse = true;
                if (n > 0) {
                    /* Coordonnées ramenées à l'image caméra entière. */
                    v[0].x1 = v[0].x1 * (int)larg / (int)dl_larg;
                    v[0].x2 = v[0].x2 * (int)larg / (int)dl_larg;
                    v[0].y1 = v[0].y1 * (int)haut / (int)dl_haut;
                    v[0].y2 = v[0].y2 * (int)haut / (int)dl_haut;
                    nb_trouves++;
                    if (v[0].score > meilleur_score) {
                        meilleur_score = v[0].score;
                    }
                    vu_le = fin;
                    d_affilee++;
                } else {
                    d_affilee = 0;
                }
            }
        }

        if (analyse && n > 0 && maintenant - dernier_regard >= REGARD_US) {
            dernier_regard = maintenant;
            float x = ((v[0].x1 + v[0].x2) / 2.0f) / larg * 2.0f - 1.0f;
            float y = ((v[0].y1 + v[0].y2) / 2.0f) / haut * 2.0f - 1.0f;
            float taille = (float)(v[0].x2 - v[0].x1) / larg;
            char msg[128];
            snprintf(msg, sizeof(msg), "{\"visage\":true,\"x\":%.3f,\"y\":%.3f,\"taille\":%.3f,\"score\":%.2f}",
                     x, y, taille, v[0].score);
            vigie_publier("vigie/regard", msg, false);
            regard_annonce = true;
        } else if (regard_annonce && maintenant - vu_le > PERDU_US) {
            vigie_publier("vigie/regard", "{\"visage\":false}", false);
            regard_annonce = false;
        }

        if (!present && d_affilee >= PRESENT_APRES) {
            present = true;
            ESP_LOGI(TAG, "Quelqu'un est là");
            vigie_publier("vigie/presence", "present", true);
        } else if (present && maintenant - vu_le > ABSENT_US) {
            present = false;
            ESP_LOGI(TAG, "Plus personne depuis %d s", CONFIG_VIGIE_ABSENCE_S);
            vigie_publier("vigie/presence", "absent", true);
        }

        /* Bilan : luminosité brute mesurée sur l'image caméra, avant de la rendre. */
        bool faire_bilan = maintenant - bilan > BILAN_US;
        int lum = 0;
        if (faire_bilan) {
            uint64_t somme = 0;
            uint32_t total = 0;
            for (uint32_t y = 0; y < haut; y += 8) {
                const uint16_t *ligne = (const uint16_t *)(image + (size_t)y * larg * 2);
                for (uint32_t x = 0; x < larg; x += 8) {
                    somme += luminance(ligne[x]);
                    total++;
                }
            }
            lum = total ? (int)(somme / total) : 0;
        }

        if (ioctl(fd, VIDIOC_QBUF, &b) != 0) {
            ESP_LOGE(TAG, "Remise en file du tampon %" PRIu32 " impossible", b.index);
        }

        /* 2. Aperçu : l'image que voit la détection, à 10 images/s */
        bool voulu = apercu_voulu;
        if (voulu && affiche && maintenant - ouvert_depuis > APERCU_MAX_US) {
            apercu_voulu = voulu = false;
        }
        if (voulu != affiche) {
            bsp_display_lock((uint32_t)-1);
            ui_camera_afficher(voulu, NULL);
            bsp_display_unlock();
            affiche = voulu;
            ouvert_depuis = maintenant;
        }
        if (affiche && a_traiter && dl_pret && reduire(apercu_buf[idx])) {
            char legende[64];
            bool vu = n > 0 && maintenant - vu_le < PERDU_US;
            if (!detection) {
                snprintf(legende, sizeof(legende), "Détection de visage désactivée");
            } else if (vu) {
                snprintf(legende, sizeof(legende), "visage  x %+.2f  y %+.2f",
                         ((v[0].x1 + v[0].x2) / 2.0f) / larg * 2.0f - 1.0f,
                         ((v[0].y1 + v[0].y2) / 2.0f) / haut * 2.0f - 1.0f);
                virgule(legende);
            } else {
                snprintf(legende, sizeof(legende), "Aucun visage");
            }
            bsp_display_lock((uint32_t)-1);
            ui_camera_image(apercu_buf[idx]);
            if (vu) {
                /* En miroir, le bord droit du visage devient le bord gauche du cadre. */
                ui_camera_visage(true, vers_apercu_x(v[0].x2), vers_apercu_y(v[0].y1),
                                 vers_apercu_x(v[0].x1), vers_apercu_y(v[0].y2), legende);
            } else {
                ui_camera_visage(false, 0, 0, 0, 0, legende);
            }
            bsp_display_unlock();
            idx ^= 1;
        }

        if (faire_bilan) {
            int secondes = (int)((maintenant - bilan) / 1000000);
            if (secondes < 1) {
                secondes = 1;
            }
            int expo = -1, gain = -1, mode = -1;
            capteur_etat(&expo, &gain, &mode);
            if (mode >= 0 && (mode & 0x03)) {
                /* Le pilote a repassé le capteur en manuel : on réactive l'automatique. */
                capteur_ecrire(0x3503, (uint8_t)(mode & ~0x03));
                ESP_LOGW(TAG, "Capteur repassé en manuel (0x3503=0x%02x) : automatique réactivé", mode);
            }
            ESP_LOGI(TAG, "Images : %d en %d s ; luminosité brute %d/255 ; étirement %d..%d ; "
                     "capteur : exposition %d lignes, gain %d,%02d",
                     nb_images, secondes, lum, dl_bas, dl_haut_, expo,
                     gain >= 0 ? gain / 16 : -1, gain >= 0 ? (gain % 16) * 100 / 16 : 0);
            if (nb_analyses) {
                ESP_LOGI(TAG, "Visage : %d analyses, %lld ms chacune (préparation comprise) ; "
                         "trouvé %d fois, meilleur score %.2f",
                         nb_analyses, (long long)(duree_analyses / nb_analyses / 1000),
                         nb_trouves, meilleur_score);
            }
            bilan = maintenant;
            nb_images = 0;
            nb_analyses = 0;
            nb_trouves = 0;
            duree_analyses = 0;
            meilleur_score = 0;
        }

        if (!affiche && !detection) {
            vTaskDelay(pdMS_TO_TICKS(200));   /* rien à faire des images : on ralentit */
        }
    }
}

/* --- Interface ------------------------------------------------------------------------------ */

esp_err_t camera_demarrer(void)
{
#if CONFIG_VIGIE_CAMERA
    esp_err_t err = ouvrir();
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Caméra indisponible : %s", esp_err_to_name(err));
        return err;
    }
    /* Cœur 1 : le Wi-Fi et MQTT tournent surtout sur le cœur 0. */
    if (xTaskCreatePinnedToCore(tache, "camera", 16 * 1024, NULL, 4, NULL, 1) != pdPASS) {
        return ESP_ERR_NO_MEM;
    }
    demarree = true;
    return ESP_OK;
#else
    ESP_LOGI(TAG, "Caméra désactivée dans menuconfig");
    return ESP_ERR_NOT_SUPPORTED;
#endif
}

bool camera_ok(void)
{
    return demarree;
}

void camera_apercu(bool ouvert)
{
    apercu_voulu = ouvert;
}

bool camera_apercu_ouvert(void)
{
    return apercu_voulu;
}

void camera_sur_connexion(void)
{
    if (demarree) {
        vigie_publier("vigie/presence", present ? "present" : "absent", true);
    }
}
