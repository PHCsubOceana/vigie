/*
 * Vigie : les yeux. Voir yeux.h, et le câblage dans le README.
 *
 * Les deux écrans partagent le bus SPI et la ligne DC. Chacun a son CS, son RST et son
 * rétroéclairage. L'image est calculée par bandes dans un petit tampon en RAM interne :
 * il n'y a pas d'image complète en mémoire.
 */
#include <math.h>
#include <string.h>
#include "sdkconfig.h"
#include "yeux.h"

#if CONFIG_VIGIE_YEUX

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"
#include "driver/gpio.h"
#include "driver/ledc.h"
#include "driver/spi_master.h"
#include "esp_err.h"
#include "esp_heap_caps.h"
#include "esp_lcd_io_spi.h"
#include "esp_lcd_panel_io.h"
#include "esp_log.h"
#include "esp_random.h"
#include "esp_timer.h"

static const char *TAG = "yeux";

/* Broches du connecteur 40 points : voir le tableau du README. */
#define P_DIN   50
#define P_CLK   51
#define P_CS1   32
#define P_CS2   49
#define P_DC    52
#define P_RST1  28
#define P_RST2  20
#define P_BL1   21
#define P_BL2   29

#define BUS         SPI2_HOST
#define COTE        160
#define CENTRE      80
#define LIGNES      16                               /* hauteur d'une bande */
#define OCTETS      (COTE * LIGNES * 2)
#define IMAGE_MS    33                               /* 30 images par seconde au plus */

/* Le timer 1 et le canal 1 servent déjà à l'écran rond (composant de la carte). */
#define PWM_TIMER   LEDC_TIMER_2
#define PWM_CANAL1  LEDC_CHANNEL_2
#define PWM_CANAL2  LEDC_CHANNEL_3
#define PWM_MAX     1023

#define IRIS_R      46
#define PUPILLE_R   19
#define DEBATTEMENT_X 27.0f
#define DEBATTEMENT_Y 20.0f
#define FERME_Y     86.0f                            /* hauteur où les paupières se rejoignent */
#define NB_TEINTES  64

#define VISAGE_PERDU_US  (1500 * 1000)
#define MIRE_US          (5000 * 1000)
#define FIXE_US          (30000 * 1000)
#define REPERE_US        (180LL * 1000 * 1000)

/* --- Ce que demandent les autres tâches ---------------------------------------------------- */

static portMUX_TYPE verrou = portMUX_INITIALIZER_UNLOCKED;
static yeux_humeur_t d_humeur = YEUX_CALME;
static bool d_liaison = true, d_present = true, d_visage = false;
static float d_x = 0, d_y = 0, d_taille = 0;
static int64_t d_visage_vu = 0, d_mire_fin = 0, d_fixe_fin = 0, d_repere_fin = 0;
static float d_fixe_x = 0;
static bool demarre = false;

void yeux_humeur(yeux_humeur_t humeur)
{
    portENTER_CRITICAL(&verrou);
    d_humeur = humeur;
    portEXIT_CRITICAL(&verrou);
}

void yeux_liaison(bool ok)
{
    portENTER_CRITICAL(&verrou);
    d_liaison = ok;
    portEXIT_CRITICAL(&verrou);
}

void yeux_regard(bool visage, float x, float y, float taille)
{
    int64_t maintenant = esp_timer_get_time();
    portENTER_CRITICAL(&verrou);
    d_visage = visage;
    if (visage) {
        d_x = x;
        d_y = y;
        d_taille = taille;
        d_visage_vu = maintenant;
    }
    portEXIT_CRITICAL(&verrou);
}

void yeux_presence(bool present)
{
    portENTER_CRITICAL(&verrou);
    d_present = present;
    portEXIT_CRITICAL(&verrou);
}

void yeux_fixer(bool actif, float x)
{
    int64_t fin = actif ? esp_timer_get_time() + FIXE_US : 0;
    portENTER_CRITICAL(&verrou);
    d_fixe_x = x;
    d_fixe_fin = fin;
    portEXIT_CRITICAL(&verrou);
}

void yeux_repere(bool actif)
{
    int64_t fin = actif ? esp_timer_get_time() + REPERE_US : 0;
    portENTER_CRITICAL(&verrou);
    d_repere_fin = fin;
    portEXIT_CRITICAL(&verrou);
}

void yeux_mire(void)
{
    int64_t fin = esp_timer_get_time() + MIRE_US;
    portENTER_CRITICAL(&verrou);
    d_mire_fin = fin;
    portEXIT_CRITICAL(&verrou);
}

/* --- Écrans ---------------------------------------------------------------------------------- */

static esp_lcd_panel_io_handle_t io[2];
static SemaphoreHandle_t envoye;
static uint16_t *bande;

static bool sur_envoi_fini(esp_lcd_panel_io_handle_t h, esp_lcd_panel_io_event_data_t *d, void *ctx)
{
    (void)h;
    (void)d;
    (void)ctx;
    BaseType_t reveil = pdFALSE;
    xSemaphoreGiveFromISR(envoye, &reveil);
    return reveil == pdTRUE;
}

/* Séquence d'initialisation de la démo Waveshare pour ce module (GC9D01). */
typedef struct {
    uint8_t commande;
    uint8_t n;
    uint8_t donnees[32];
} init_t;

static const init_t INIT[] = {
    {0xFE, 0, {0}}, {0xEF, 0, {0}},
    {0x80, 1, {0xFF}}, {0x81, 1, {0xFF}}, {0x82, 1, {0xFF}}, {0x83, 1, {0xFF}},
    {0x84, 1, {0xFF}}, {0x85, 1, {0xFF}}, {0x86, 1, {0xFF}}, {0x87, 1, {0xFF}},
    {0x88, 1, {0xFF}}, {0x89, 1, {0xFF}}, {0x8A, 1, {0xFF}}, {0x8B, 1, {0xFF}},
    {0x8C, 1, {0xFF}}, {0x8D, 1, {0xFF}}, {0x8E, 1, {0xFF}}, {0x8F, 1, {0xFF}},
    {0x3A, 1, {0x05}},
    {0xEC, 1, {0x01}},
    {0x74, 7, {0x02, 0x0E, 0x00, 0x00, 0x00, 0x00, 0x00}},
    {0x98, 1, {0x3E}}, {0x99, 1, {0x3E}},
    {0xB5, 2, {0x0D, 0x0D}},
    {0x60, 4, {0x38, 0x0F, 0x79, 0x67}},
    {0x61, 4, {0x38, 0x11, 0x79, 0x67}},
    {0x64, 6, {0x38, 0x17, 0x71, 0x5F, 0x79, 0x67}},
    {0x65, 6, {0x38, 0x13, 0x71, 0x5B, 0x79, 0x67}},
    {0x6A, 2, {0x00, 0x00}},
    {0x6C, 7, {0x22, 0x02, 0x22, 0x02, 0x22, 0x22, 0x50}},
    {0x6E, 32, {0x03, 0x03, 0x01, 0x01, 0x00, 0x00, 0x0F, 0x0F, 0x0D, 0x0D, 0x0B, 0x0B, 0x09, 0x09, 0x00, 0x00,
                0x00, 0x00, 0x0A, 0x0A, 0x0C, 0x0C, 0x0E, 0x0E, 0x10, 0x10, 0x00, 0x00, 0x02, 0x02, 0x04, 0x04}},
    {0xBF, 1, {0x01}}, {0xF9, 1, {0x40}}, {0x9B, 1, {0x3B}},
    {0x93, 3, {0x33, 0x7F, 0x00}},
    {0x7E, 1, {0x30}},
    {0x70, 6, {0x0D, 0x02, 0x08, 0x0D, 0x02, 0x08}},
    {0x71, 3, {0x0D, 0x02, 0x08}},
    {0x91, 2, {0x0E, 0x09}},
    {0xC3, 1, {0x19}}, {0xC4, 1, {0x19}}, {0xC9, 1, {0x3C}},
    {0xF0, 6, {0x53, 0x15, 0x0A, 0x04, 0x00, 0x3E}},
    {0xF2, 6, {0x53, 0x15, 0x0A, 0x04, 0x00, 0x3A}},
    {0xF1, 6, {0x56, 0xA8, 0x7F, 0x33, 0x34, 0x5F}},
    {0xF3, 6, {0x52, 0xA4, 0x7F, 0x33, 0x34, 0xDF}},
};

static esp_err_t ouvrir_ecran(int n, int cs, int rst, bool retourne)
{
    const esp_lcd_panel_io_spi_config_t c = {
        .cs_gpio_num = cs,
        .dc_gpio_num = P_DC,
        .spi_mode = 0,
        .pclk_hz = CONFIG_VIGIE_YEUX_SPI_MHZ * 1000 * 1000,
        .trans_queue_depth = 4,
        .on_color_trans_done = sur_envoi_fini,
        .lcd_cmd_bits = 8,
        .lcd_param_bits = 8,
    };
    esp_err_t e = esp_lcd_new_panel_io_spi((esp_lcd_spi_bus_handle_t)BUS, &c, &io[n]);
    if (e != ESP_OK) {
        return e;
    }

    /* Le module garde 100 nF sur RST : on laisse à la ligne le temps de bouger. */
    const gpio_config_t g = {.pin_bit_mask = 1ULL << rst, .mode = GPIO_MODE_OUTPUT};
    e = gpio_config(&g);
    if (e != ESP_OK) {
        return e;
    }
    gpio_set_level(rst, 0);
    vTaskDelay(pdMS_TO_TICKS(20));
    gpio_set_level(rst, 1);
    vTaskDelay(pdMS_TO_TICKS(120));

    for (size_t i = 0; i < sizeof(INIT) / sizeof(INIT[0]) && e == ESP_OK; i++) {
        e = esp_lcd_panel_io_tx_param(io[n], INIT[i].commande, INIT[i].donnees, INIT[i].n);
    }
    /* Sur ce module, les deux écrans sont tournés d'un quart de tour, en sens opposés :
     * 0x30 pour l'œil 1 et 0xF0 pour l'œil 2, réglés sur le module, connecteur en bas.
     * 0xC0 retourne l'image de 180°, 0x08 échange le rouge et le bleu. */
    uint8_t orientation = n == 0 ? 0x30 : 0xF0;
    if (retourne) {
        orientation ^= 0xC0;
    }
#if CONFIG_VIGIE_YEUX_BGR
    orientation |= 0x08;
#endif
    if (e == ESP_OK) e = esp_lcd_panel_io_tx_param(io[n], 0x36, &orientation, 1);
    if (e == ESP_OK) e = esp_lcd_panel_io_tx_param(io[n], 0x11, NULL, 0);
    vTaskDelay(pdMS_TO_TICKS(200));
    return e;
}

/* L'écran reçoit l'octet de poids fort en premier. */
static inline uint16_t rvb(int r, int v, int b)
{
    uint16_t c = (uint16_t)(((r & 0xF8) << 8) | ((v & 0xFC) << 3) | (b >> 3));
    return (uint16_t)((c << 8) | (c >> 8));
}

static void envoyer_bande(int n, int y, int hauteur)
{
    xSemaphoreTake(envoye, 0);    /* au cas où un ancien signal traînerait */
    const uint8_t colonnes[4] = {0, 0, 0, COTE - 1};
    const uint8_t lignes[4] = {0, (uint8_t)y, 0, (uint8_t)(y + hauteur - 1)};
    if (esp_lcd_panel_io_tx_param(io[n], 0x2A, colonnes, 4) != ESP_OK
        || esp_lcd_panel_io_tx_param(io[n], 0x2B, lignes, 4) != ESP_OK
        || esp_lcd_panel_io_tx_color(io[n], 0x2C, bande, (size_t)(COTE * hauteur * 2)) != ESP_OK) {
        return;
    }
    if (xSemaphoreTake(envoye, pdMS_TO_TICKS(200)) != pdTRUE) {
        ESP_LOGW(TAG, "Envoi vers l'œil %d sans réponse", n + 1);
    }
}

/* --- Dessin ------------------------------------------------------------------------------------ */

typedef struct {
    int ix, iy;           /* centre de l'iris */
    int pupille;
    int haut, bas;        /* paupières, au milieu de l'œil */
    int r, v, b;          /* couleur de l'iris */
} oeil_t;

static uint16_t teintes[NB_TEINTES];
static uint8_t courbe_haut[COTE], courbe_bas[COTE];

static void preparer_courbes(void)
{
    for (int x = 0; x < COTE; x++) {
        int d = x - CENTRE;
        courbe_haut[x] = d * d / 90;
        courbe_bas[x] = d * d / 120;
    }
}

/* De l'intérieur de l'iris, soutenu, vers un bord plus sombre. */
static void preparer_teintes(const oeil_t *o)
{
    for (int i = 0; i < NB_TEINTES; i++) {
        float t = (float)i / (NB_TEINTES - 1);
        float f = t < 0.78f ? 0.45f + 0.70f * t : 1.0f - (t - 0.78f) * 2.6f;
        int r = (int)(o->r * f), v = (int)(o->v * f), b = (int)(o->b * f);
        teintes[i] = rvb(r > 255 ? 255 : r, v > 255 ? 255 : v, b > 255 ? 255 : b);
    }
}

static void dessiner_oeil(int n, const oeil_t *o)
{
    const uint16_t noir = rvb(0, 0, 0);
    const uint16_t blanc = rvb(245, 248, 255);
    const uint16_t fond = rvb(0x12, 0x18, 0x26);        /* le blanc de l'œil, très sombre */
    const uint16_t paupiere = rvb(0x05, 0x07, 0x0B);
    const uint32_t iris2 = IRIS_R * IRIS_R;
    const uint32_t pupille2 = (uint32_t)(o->pupille * o->pupille);
    const uint32_t echelle = ((NB_TEINTES - 1) << 16) / iris2;
    const int ex = o->ix - 15, ey = o->iy - 17;          /* grand reflet */
    const int fx = o->ix + 13, fy = o->iy + 14;          /* petit reflet */

    for (int y0 = 0; y0 < COTE; y0 += LIGNES) {
        uint16_t *p = bande;
        for (int y = y0; y < y0 + LIGNES; y++) {
            const int dy = y - CENTRE;
            const int iy = y - o->iy;
            for (int x = 0; x < COTE; x++) {
                const int dx = x - CENTRE;
                uint16_t c;
                if (dx * dx + dy * dy > CENTRE * CENTRE) {
                    c = noir;                            /* hors du disque visible */
                } else if (y < o->haut + courbe_haut[x] || y > o->bas - courbe_bas[x]) {
                    c = paupiere;
                } else {
                    const int ix = x - o->ix;
                    const uint32_t d2 = (uint32_t)(ix * ix + iy * iy);
                    const int ax = x - ex, ay = y - ey, bx = x - fx, by = y - fy;
                    if (ax * ax + ay * ay < 90 || bx * bx + by * by < 18) {
                        c = blanc;
                    } else if (d2 < pupille2) {
                        c = noir;
                    } else if (d2 < iris2) {
                        c = teintes[(d2 * echelle) >> 16];
                    } else {
                        c = fond;
                    }
                }
                *p++ = c;
            }
        }
        envoyer_bande(n, y0, LIGNES);
    }
}

static void dessiner_uni(int n, uint16_t couleur, int bande_blanche)
{
    const uint16_t blanc = rvb(255, 255, 255);
    for (int y0 = 0; y0 < COTE; y0 += LIGNES) {
        uint16_t c = y0 < bande_blanche ? blanc : couleur;
        for (int i = 0; i < COTE * LIGNES; i++) {
            bande[i] = c;
        }
        envoyer_bande(n, y0, LIGNES);
    }
}

/* Un point de 5 pixels de large au centre exact de l'écran, sur fond noir. */
static void dessiner_repere(int n)
{
    const uint16_t noir = rvb(0, 0, 0), blanc = rvb(255, 255, 255);
    for (int y0 = 0; y0 < COTE; y0 += LIGNES) {
        uint16_t *p = bande;
        for (int y = y0; y < y0 + LIGNES; y++) {
            for (int x = 0; x < COTE; x++) {
                /* Le centre tombe entre les pixels 79 et 80. */
                const int dx = 2 * x + 1 - COTE, dy = 2 * y + 1 - COTE;
                *p++ = dx * dx + dy * dy <= 36 ? blanc : noir;
            }
        }
        envoyer_bande(n, y0, LIGNES);
    }
}

/* --- Rétroéclairage ------------------------------------------------------------------------------ */

static esp_err_t lumiere_init(void)
{
    const ledc_timer_config_t t = {
        .speed_mode = LEDC_LOW_SPEED_MODE,
        .duty_resolution = LEDC_TIMER_10_BIT,
        .timer_num = PWM_TIMER,
        .freq_hz = 5000,
        .clk_cfg = LEDC_AUTO_CLK,
    };
    esp_err_t e = ledc_timer_config(&t);
    const int broches[2] = {P_BL1, P_BL2};
    const ledc_channel_t canaux[2] = {PWM_CANAL1, PWM_CANAL2};
    for (int i = 0; i < 2 && e == ESP_OK; i++) {
        const ledc_channel_config_t c = {
            .gpio_num = broches[i],
            .speed_mode = LEDC_LOW_SPEED_MODE,
            .channel = canaux[i],
            .timer_sel = PWM_TIMER,
            .duty = 0,
        };
        e = ledc_channel_config(&c);
    }
    return e;
}

static void lumiere(int pourcent)
{
    static int actuel = -1;
    if (pourcent == actuel) {
        return;
    }
    actuel = pourcent;
    const uint32_t duty = (uint32_t)(PWM_MAX * pourcent / 100);
    ledc_set_duty(LEDC_LOW_SPEED_MODE, PWM_CANAL1, duty);
    ledc_update_duty(LEDC_LOW_SPEED_MODE, PWM_CANAL1);
    ledc_set_duty(LEDC_LOW_SPEED_MODE, PWM_CANAL2, duty);
    ledc_update_duty(LEDC_LOW_SPEED_MODE, PWM_CANAL2);
}

/* --- Comportement ---------------------------------------------------------------------------- */

typedef struct {
    float haut, bas;      /* paupières grandes ouvertes ou non */
    float r, v, b;        /* mêmes couleurs que l'anneau de l'écran rond */
} allure_t;

static const allure_t ALLURES[] = {
    [YEUX_CALME]      = {24, 146, 0x6B, 0x7A, 0x99},
    [YEUX_ATTENTIVE]  = { 6, 156, 0xFF, 0xB0, 0x20},
    [YEUX_AFFAIREE]   = {34, 140, 0x4C, 0x9B, 0xFF},
    [YEUX_SATISFAITE] = {30, 116, 0x3D, 0xDC, 0x97},
};
static const allure_t ALLURE_SANS_LIAISON = {58, 132, 0x4A, 0x53, 0x66};
static const allure_t ALLURE_SOMMEIL = {76, 94, 0x6B, 0x7A, 0x99};

static float hasard(float de, float a)
{
    return de + (a - de) * ((float)(esp_random() & 0xFFFF) / 65535.0f);
}

static inline float vers(float actuel, float but, float vitesse, float dt)
{
    float k = vitesse * dt;
    return actuel + (but - actuel) * (k > 1.0f ? 1.0f : k);
}

static void tache(void *arg)
{
    (void)arg;
    /* Lumière éteinte d'abord : on ne montre pas la mémoire de l'écran au démarrage. */
    if (lumiere_init() != ESP_OK) {
        ESP_LOGW(TAG, "Rétroéclairage indisponible");
    }
    lumiere(0);

    const spi_bus_config_t bus = {
        .sclk_io_num = P_CLK,
        .mosi_io_num = P_DIN,
        .miso_io_num = -1,
        .quadwp_io_num = -1,
        .quadhd_io_num = -1,
        .max_transfer_sz = OCTETS,
    };
    envoye = xSemaphoreCreateBinary();
    bande = heap_caps_malloc(OCTETS, MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL);
    esp_err_t e = (envoye && bande) ? ESP_OK : ESP_ERR_NO_MEM;
    if (e == ESP_OK) e = spi_bus_initialize(BUS, &bus, SPI_DMA_CH_AUTO);
#ifdef CONFIG_VIGIE_YEUX_OEIL1_RETOURNE
    const bool retourne1 = true;
#else
    const bool retourne1 = false;
#endif
#ifdef CONFIG_VIGIE_YEUX_OEIL2_RETOURNE
    const bool retourne2 = true;
#else
    const bool retourne2 = false;
#endif
    if (e == ESP_OK) e = ouvrir_ecran(0, P_CS1, P_RST1, retourne1);
    if (e == ESP_OK) e = ouvrir_ecran(1, P_CS2, P_RST2, retourne2);
    if (e != ESP_OK) {
        /* Les yeux ne doivent jamais empêcher le reste de fonctionner. */
        ESP_LOGE(TAG, "Yeux désactivés : %s", esp_err_to_name(e));
        vTaskDelete(NULL);
        return;
    }
    for (int n = 0; n < 2; n++) {
        dessiner_uni(n, rvb(0, 0, 0), 0);
        esp_lcd_panel_io_tx_param(io[n], 0x29, NULL, 0);
    }
    preparer_courbes();
    ESP_LOGI(TAG, "Yeux prêts (SPI %d MHz)", CONFIG_VIGIE_YEUX_SPI_MHZ);

    float gx = 0, gy = 0, but_x = 0, but_y = 0;   /* regard, de -1 à +1, vu de face */
    float haut = FERME_Y, bas = FERME_Y;          /* les yeux s'ouvrent au démarrage */
    float r = 0, v = 0, b = 0;
    float pupille = PUPILLE_R, proche = 0;
    float clin = 0;                               /* 0 ouvert, 1 fermé */
    int clin_sens = 0;
    int64_t prochain_clin = esp_timer_get_time() + 2000000;
    int64_t prochain_regard = 0;
    int64_t avant = esp_timer_get_time();
    oeil_t dernier[2];
    memset(dernier, 0, sizeof(dernier));
    bool forcer = true, mire_affichee = false, repere_affiche = false;

    for (;;) {
        const int64_t maintenant = esp_timer_get_time();
        const float dt = (float)(maintenant - avant) / 1e6f;
        avant = maintenant;

        portENTER_CRITICAL(&verrou);
        const yeux_humeur_t humeur = d_humeur;
        const bool liaison = d_liaison, present = d_present;
        const float fx = d_x, fy = d_y, ft = d_taille;
        const bool visage = d_visage && maintenant - d_visage_vu < VISAGE_PERDU_US;
        const bool mire = maintenant < d_mire_fin;
        const bool fixe = maintenant < d_fixe_fin;
        const float fixe_x = d_fixe_x;
        const bool repere = maintenant < d_repere_fin;
        portEXIT_CRITICAL(&verrou);

        if (repere) {
            if (!repere_affiche) {
                lumiere(100);
                dessiner_repere(0);
                dessiner_repere(1);
                repere_affiche = true;
            }
            vTaskDelay(pdMS_TO_TICKS(100));
            continue;
        }
        if (repere_affiche) {
            repere_affiche = false;
            forcer = true;
        }

        if (mire) {
            if (!mire_affichee) {
                lumiere(100);
                dessiner_uni(0, rvb(255, 0, 0), 2 * LIGNES);
                dessiner_uni(1, rvb(0, 0, 255), 2 * LIGNES);
                mire_affichee = true;
            }
            vTaskDelay(pdMS_TO_TICKS(100));
            continue;
        }
        if (mire_affichee) {
            mire_affichee = false;
            forcer = true;
        }

        /* Personne devant, et rien à dire : les yeux s'assoupissent. */
        const bool endormi = liaison && !present && !visage && humeur == YEUX_CALME;
        const allure_t *a = !liaison ? &ALLURE_SANS_LIAISON
                          : endormi ? &ALLURE_SOMMEIL
                          : &ALLURES[humeur];

        /* Où regarder */
        if (fixe) {
            but_x = fixe_x;
            but_y = 0;
        } else if (visage) {
            /* L'aperçu réglé en miroir retourne l'image : une personne à droite de l'image
             * est à gauche du robot vu de face. D'où le signe moins. */
#if CONFIG_VIGIE_YEUX_INVERSE_X
            but_x = fx;
#else
            but_x = -fx;
#endif
            but_y = fy;
        } else if (!liaison || endormi) {
            but_x = 0;
            but_y = 0.2f;
        } else if (maintenant >= prochain_regard) {
            if (humeur == YEUX_ATTENTIVE) {
                /* Quelqu'un attend : la Vigie regarde devant elle. */
                but_x = hasard(-0.12f, 0.12f);
                but_y = hasard(-0.10f, 0.10f);
                prochain_regard = maintenant + (int64_t)hasard(1.5e6f, 3.5e6f);
            } else if (humeur == YEUX_AFFAIREE) {
                /* Elle lit : de courts allers-retours. */
                but_x = but_x > 0 ? hasard(-0.7f, -0.3f) : hasard(0.3f, 0.7f);
                but_y = hasard(0.0f, 0.3f);
                prochain_regard = maintenant + (int64_t)hasard(0.4e6f, 1.1e6f);
            } else {
                but_x = hasard(-0.6f, 0.6f);
                but_y = hasard(-0.4f, 0.4f);
                prochain_regard = maintenant + (int64_t)hasard(1.2e6f, 4.0e6f);
            }
        }
        gx = vers(gx, but_x, visage ? 9.0f : 16.0f, dt);
        gy = vers(gy, but_y, visage ? 9.0f : 16.0f, dt);
        proche = vers(proche, visage ? ft : 0.0f, 4.0f, dt);

        /* Clignement : environ 0,2 s, jamais pendant le sommeil. */
        if (clin_sens == 0 && !endormi && maintenant >= prochain_clin) {
            clin_sens = 1;
            prochain_clin = maintenant + (int64_t)hasard(2.2e6f, 6.5e6f);
        }
        clin += (float)clin_sens * dt / 0.09f;
        if (clin >= 1.0f) {
            clin = 1.0f;
            clin_sens = -1;
        } else if (clin <= 0.0f) {
            clin = 0.0f;
            clin_sens = 0;
        }

        haut = vers(haut, a->haut, 6.0f, dt);
        bas = vers(bas, a->bas, 6.0f, dt);
        r = vers(r, a->r, 4.0f, dt);
        v = vers(v, a->v, 4.0f, dt);
        b = vers(b, a->b, 4.0f, dt);
        /* La pupille s'ouvre un peu quand le visage s'approche. */
        pupille = vers(pupille, PUPILLE_R + (proche > 0.5f ? 0.5f : proche) * 14.0f, 5.0f, dt);

        /* Convergence légère. Vu de face, l'œil 1 est à droite et l'œil 2 à gauche. */
        const float conv = (proche > 0.5f ? 0.5f : proche) * 10.0f;
        oeil_t o[2];
        for (int n = 0; n < 2; n++) {
            o[n].ix = CENTRE + (int)lroundf(gx * DEBATTEMENT_X + (n == 0 ? -conv : conv));
            o[n].iy = CENTRE + (int)lroundf(gy * DEBATTEMENT_Y);
            o[n].pupille = (int)lroundf(pupille);
            o[n].haut = (int)lroundf(haut + (FERME_Y - haut) * clin);
            o[n].bas = (int)lroundf(bas + (FERME_Y - bas) * clin);
            o[n].r = (int)r;
            o[n].v = (int)v;
            o[n].b = (int)b;
        }

        /* On n'envoie que ce qui a changé. */
        preparer_teintes(&o[0]);
        for (int n = 0; n < 2; n++) {
            if (forcer || memcmp(&o[n], &dernier[n], sizeof(oeil_t)) != 0) {
                dessiner_oeil(n, &o[n]);
                dernier[n] = o[n];
            }
        }
        forcer = false;
        lumiere(endormi ? CONFIG_VIGIE_YEUX_LUMIERE / 5 : CONFIG_VIGIE_YEUX_LUMIERE);

        /* Toujours rendre la main, même si l'envoi a pris plus d'une image. */
        const int ecoule = (int)((esp_timer_get_time() - maintenant) / 1000);
        vTaskDelay(pdMS_TO_TICKS(ecoule < IMAGE_MS - 2 ? IMAGE_MS - ecoule : 2));
    }
}

void yeux_demarrer(void)
{
    if (demarre) {
        return;
    }
    demarre = true;
    /* Cœur 0 : la détection de visage occupe le cœur 1. */
    if (xTaskCreatePinnedToCore(tache, "yeux", 4096, NULL, 2, NULL, 0) != pdPASS) {
        ESP_LOGE(TAG, "Pas de mémoire pour la tâche des yeux");
    }
}

#else   /* yeux désactivés dans menuconfig */

void yeux_demarrer(void) {}
void yeux_humeur(yeux_humeur_t humeur) { (void)humeur; }
void yeux_liaison(bool ok) { (void)ok; }
void yeux_regard(bool visage, float x, float y, float taille) { (void)visage; (void)x; (void)y; (void)taille; }
void yeux_presence(bool present) { (void)present; }
void yeux_fixer(bool actif, float x) { (void)actif; (void)x; }
void yeux_repere(bool actif) { (void)actif; }
void yeux_mire(void) {}

#endif
