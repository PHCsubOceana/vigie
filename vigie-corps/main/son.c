#include <math.h>
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_codec_dev.h"
#include "bsp/esp-bsp.h"
#include "son.h"

static const char *TAG = "son";

#define FREQ_ECH    22050      /* fréquence par défaut du BSP */
#define BLOC        256        /* échantillons écrits à la fois */

typedef struct {
    float freq;     /* Hz ; 0 = silence */
    int duree_ms;
} note_t;

/* Chaque son est une courte suite de notes en clochette (sinus + harmonique, attaque
 * brève, décroissance douce) : présent sans être agressif. */
static const note_t M_ATTENTE[]    = {{659.3f, 170}, {880.0f, 380}};                       /* mi5 → la5 */
static const note_t M_MAIL[]       = {{1046.5f, 90}, {0, 40}, {1046.5f, 90}, {1318.5f, 300}}; /* do6 do6 mi6 */
static const note_t M_FINI[]       = {{784.0f, 110}, {987.8f, 110}, {1318.5f, 420}};       /* sol5 si5 mi6 */
static const note_t M_HORS_LIGNE[] = {{523.3f, 220}, {392.0f, 480}};                       /* do5 → sol4 */
static const note_t M_TEST[]       = {{880.0f, 220}};

typedef struct {
    const note_t *notes;
    int nb;
} melodie_t;

static const melodie_t MELODIES[] = {
    [SON_ATTENTE]    = {M_ATTENTE, 2},
    [SON_MAIL]       = {M_MAIL, 4},
    [SON_FINI]       = {M_FINI, 3},
    [SON_HORS_LIGNE] = {M_HORS_LIGNE, 2},
    [SON_TEST]       = {M_TEST, 1},
};

static esp_codec_dev_handle_t hp = NULL;
static QueueHandle_t file = NULL;
static volatile int volume_courant = 60;

static void jouer_note(const note_t *n, int16_t *tampon)
{
    int total = FREQ_ECH * n->duree_ms / 1000;
    int attaque = FREQ_ECH * 6 / 1000;               /* 6 ms */
    float tau = total / 3.5f;                         /* décroissance */
    float w = 2.0f * (float)M_PI * n->freq / FREQ_ECH;

    for (int debut = 0; debut < total; debut += BLOC) {
        int nb = total - debut < BLOC ? total - debut : BLOC;
        for (int i = 0; i < nb; i++) {
            int k = debut + i;
            float s = 0.0f;
            if (n->freq > 0) {
                float env = k < attaque ? (float)k / attaque : expf(-(k - attaque) / tau);
                s = env * (0.80f * sinf(w * k) + 0.18f * sinf(2.0f * w * k) + 0.06f * sinf(3.0f * w * k));
            }
            tampon[i] = (int16_t)(s * 0.55f * 32767.0f);
        }
        esp_codec_dev_write(hp, tampon, nb * sizeof(int16_t));
    }
}

static void tache_son(void *arg)
{
    static int16_t tampon[BLOC];
    son_t son;
    esp_codec_dev_sample_info_t fmt = {
        .bits_per_sample = 16,
        .channel = 1,
        .sample_rate = FREQ_ECH,
    };
    while (1) {
        if (xQueueReceive(file, &son, portMAX_DELAY) != pdTRUE) continue;
        if (volume_courant <= 0 || son >= sizeof(MELODIES) / sizeof(MELODIES[0])) continue;

        if (esp_codec_dev_open(hp, &fmt) != ESP_CODEC_DEV_OK) {
            ESP_LOGW(TAG, "Codec indisponible");
            continue;
        }
        esp_codec_dev_set_out_vol(hp, volume_courant);
        const melodie_t *m = &MELODIES[son];
        for (int i = 0; i < m->nb; i++) {
            jouer_note(&m->notes[i], tampon);
        }
        /* un peu de silence pour vider le tampon avant de couper l'ampli */
        memset(tampon, 0, sizeof(tampon));
        for (int i = 0; i < 8; i++) {
            esp_codec_dev_write(hp, tampon, sizeof(tampon));
        }
        esp_codec_dev_close(hp);
    }
}

void son_init(int volume)
{
    volume_courant = volume;
    hp = bsp_audio_codec_speaker_init();
    if (!hp) {
        ESP_LOGE(TAG, "Haut-parleur introuvable : la Vigie restera muette");
        return;
    }
    file = xQueueCreate(4, sizeof(son_t));
    xTaskCreate(tache_son, "son", 4096, NULL, 4, NULL);
    ESP_LOGI(TAG, "Son prêt, volume %d", volume);
}

void son_volume(int volume)
{
    volume_courant = volume < 0 ? 0 : volume > 100 ? 100 : volume;
}

void son_jouer(son_t son)
{
    if (file && volume_courant > 0) {
        xQueueSend(file, &son, 0);   /* file pleine : on n'insiste pas */
    }
}
