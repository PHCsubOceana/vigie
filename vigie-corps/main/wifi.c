/*
 * Vigie, Wi-Fi par la puce ESP32-C6 (voir wifi.h).
 *
 * La reconnexion automatique (une tentative par seconde) est suspendue pendant une
 * recherche de réseaux ou l'essai d'un nouveau réseau : la C6 ne sait pas chercher
 * pendant qu'elle se connecte.
 */
#include <stdlib.h>
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "freertos/event_groups.h"
#include "esp_log.h"
#include "esp_heap_caps.h"
#include "esp_event.h"
#include "esp_netif.h"
#include "esp_wifi.h"
#include "nvs.h"
#include "wifi.h"

static const char *TAG = "wifi";

#define NB_ESSAIS     3
#define ESSAI_MS      15000
#define RECHERCHE_MS  15000

#define BIT_IP        BIT0
#define BIT_COUPE     BIT1
#define BIT_RECHERCHE BIT2

typedef struct {
    enum { ORDRE_CHERCHER, ORDRE_ESSAYER } type;
    char ssid[33];
    char mdp[65];
} ordre_t;

static wifi_rappels_t rappels;
static EventGroupHandle_t evenements;
static QueueHandle_t ordres;
static volatile bool connecte = false;
static volatile bool reconnexion = true;
static volatile uint8_t raison_coupure = 0;
static char ssid_actuel[33];

/* --- Réseau enregistré ---------------------------------------------------------------- */

static void lire_reseau(char *ssid, size_t t_ssid, char *mdp, size_t t_mdp)
{
    nvs_handle_t h;
    bool trouve = false;
    if (nvs_open("vigie", NVS_READONLY, &h) == ESP_OK) {
        trouve = nvs_get_str(h, "wifi_ssid", ssid, &t_ssid) == ESP_OK && ssid[0];
        if (trouve && nvs_get_str(h, "wifi_mdp", mdp, &t_mdp) != ESP_OK) {
            mdp[0] = '\0';
        }
        nvs_close(h);
    }
    if (!trouve) {
        snprintf(ssid, t_ssid, "%s", CONFIG_VIGIE_WIFI_SSID);
        snprintf(mdp, t_mdp, "%s", CONFIG_VIGIE_WIFI_PASSWORD);
    }
}

static void garder_reseau(const char *ssid, const char *mdp)
{
    nvs_handle_t h;
    if (nvs_open("vigie", NVS_READWRITE, &h) == ESP_OK) {
        nvs_set_str(h, "wifi_ssid", ssid);
        nvs_set_str(h, "wifi_mdp", mdp);
        nvs_commit(h);
        nvs_close(h);
    }
}

static void appliquer(const char *ssid, const char *mdp)
{
    wifi_config_t cfg = {0};
    memcpy(cfg.sta.ssid, ssid, strnlen(ssid, sizeof(cfg.sta.ssid)));
    memcpy(cfg.sta.password, mdp, strnlen(mdp, sizeof(cfg.sta.password)));
    /* Sans mot de passe : réseau ouvert. Sinon WPA2 au moins (WPA3 accepté). */
    cfg.sta.threshold.authmode = mdp[0] ? WIFI_AUTH_WPA2_PSK : WIFI_AUTH_OPEN;
    esp_wifi_set_config(WIFI_IF_STA, &cfg);
    snprintf(ssid_actuel, sizeof(ssid_actuel), "%s", ssid);
}

/* --- Événements ------------------------------------------------------------------------ */

static void sur_wifi(void *arg, esp_event_base_t base, int32_t id, void *donnees)
{
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) {
        if (reconnexion) {
            esp_wifi_connect();
        }
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        raison_coupure = ((wifi_event_sta_disconnected_t *)donnees)->reason;
        xEventGroupSetBits(evenements, BIT_COUPE);
        if (connecte) {
            ESP_LOGW(TAG, "Wi-Fi perdu (raison %d)", raison_coupure);
            connecte = false;
            if (rappels.connexion) rappels.connexion(false, ssid_actuel);
        }
        if (!reconnexion) {
            return;
        }
        vTaskDelay(pdMS_TO_TICKS(1000));
        if (reconnexion) {
            esp_wifi_connect();
        }
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_SCAN_DONE) {
        xEventGroupSetBits(evenements, BIT_RECHERCHE);
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t *ip = donnees;
        ESP_LOGI(TAG, "Connecté à %s, adresse IP " IPSTR, ssid_actuel, IP2STR(&ip->ip_info.ip));
        connecte = true;
        xEventGroupSetBits(evenements, BIT_IP);
        if (rappels.connexion) rappels.connexion(true, ssid_actuel);
    }
}

/* Coupe la liaison en cours (ou la tentative) et attend que la C6 l'ait fait. */
static void couper(void)
{
    xEventGroupClearBits(evenements, BIT_COUPE);
    if (esp_wifi_disconnect() == ESP_OK) {
        xEventGroupWaitBits(evenements, BIT_COUPE, pdTRUE, pdTRUE, pdMS_TO_TICKS(2000));
    }
}

/* --- Recherche des réseaux ----------------------------------------------------------- */

static int force_de(int rssi)
{
    return rssi >= -55 ? 4 : rssi >= -65 ? 3 : rssi >= -75 ? 2 : 1;
}

static void chercher(void)
{
    /* En PSRAM, le temps de la recherche : la RAM interne manque. */
    const uint16_t max_trouves = UI_RESEAUX_MAX * 2;
    wifi_ap_record_t *trouves = heap_caps_malloc(max_trouves * sizeof(*trouves), MALLOC_CAP_SPIRAM);
    static ui_reseau_t reseaux[UI_RESEAUX_MAX];
    static int rssi[UI_RESEAUX_MAX];

    reconnexion = false;
    if (!connecte) {
        couper();
    }
    xEventGroupClearBits(evenements, BIT_RECHERCHE);
    wifi_scan_config_t cfg = {.show_hidden = false};
    esp_err_t err = esp_wifi_scan_start(&cfg, false);
    for (int i = 0; err == ESP_ERR_WIFI_STATE && i < 5; i++) {
        vTaskDelay(pdMS_TO_TICKS(500));   /* une tentative de connexion se termine */
        err = esp_wifi_scan_start(&cfg, false);
    }

    int nb = -1;
    if (err == ESP_OK && trouves && (xEventGroupWaitBits(evenements, BIT_RECHERCHE, pdTRUE, pdTRUE,
                                              pdMS_TO_TICKS(RECHERCHE_MS)) & BIT_RECHERCHE)) {
        uint16_t n = max_trouves;
        if (esp_wifi_scan_get_ap_records(&n, trouves) == ESP_OK) {
            nb = 0;
            for (int i = 0; i < n; i++) {
                const char *nom = (const char *)trouves[i].ssid;
                if (!nom[0]) continue;
                /* Un même réseau peut avoir plusieurs bornes : on garde la meilleure. */
                int j = 0;
                while (j < nb && strcmp(reseaux[j].ssid, nom)) j++;
                if (j == nb) {
                    if (nb == UI_RESEAUX_MAX) continue;
                    nb++;
                    rssi[j] = -1000;
                }
                if (trouves[i].rssi > rssi[j]) {
                    rssi[j] = trouves[i].rssi;
                    snprintf(reseaux[j].ssid, sizeof(reseaux[j].ssid), "%s", nom);
                    reseaux[j].force = force_de(trouves[i].rssi);
                    reseaux[j].protege = trouves[i].authmode != WIFI_AUTH_OPEN;
                }
            }
            /* Du plus fort au plus faible. */
            for (int i = 1; i < nb; i++) {
                for (int k = i; k > 0 && rssi[k] > rssi[k - 1]; k--) {
                    ui_reseau_t r = reseaux[k]; reseaux[k] = reseaux[k - 1]; reseaux[k - 1] = r;
                    int v = rssi[k]; rssi[k] = rssi[k - 1]; rssi[k - 1] = v;
                }
            }
        }
    } else {
        ESP_LOGW(TAG, "Recherche impossible (%s)", esp_err_to_name(err));
        esp_wifi_scan_stop();
    }
    free(trouves);
    ESP_LOGI(TAG, "Recherche : %d réseau(x)", nb);

    reconnexion = true;
    if (!connecte) {
        esp_wifi_connect();
    }
    if (rappels.reseaux) rappels.reseaux(reseaux, nb);
}

/* --- Essai d'un nouveau réseau --------------------------------------------------------- */

static const char *texte_raison(uint8_t r)
{
    switch (r) {
    case WIFI_REASON_AUTH_FAIL:
    case WIFI_REASON_4WAY_HANDSHAKE_TIMEOUT:
    case WIFI_REASON_HANDSHAKE_TIMEOUT:
        return "Mot de passe refusé.";
    case WIFI_REASON_NO_AP_FOUND:
    case WIFI_REASON_NO_AP_FOUND_IN_RSSI_THRESHOLD:
        return "Réseau introuvable.";
    case WIFI_REASON_NO_AP_FOUND_W_COMPATIBLE_SECURITY:
    case WIFI_REASON_NO_AP_FOUND_IN_AUTHMODE_THRESHOLD:
        return "Sécurité du réseau non prise en charge.";
    default:
        return "Le réseau ne répond pas.";
    }
}

static void essayer(const char *ssid, const char *mdp)
{
    char ancien_ssid[33], ancien_mdp[65];
    lire_reseau(ancien_ssid, sizeof(ancien_ssid), ancien_mdp, sizeof(ancien_mdp));
    ESP_LOGI(TAG, "Essai du réseau %s", ssid);
    if (rappels.essai) rappels.essai(UI_WIFI_ESSAI, ssid, NULL);

    reconnexion = false;
    couper();
    appliquer(ssid, mdp);

    bool reussi = false;
    const char *pourquoi = "Le réseau ne répond pas.";
    for (int i = 0; i < NB_ESSAIS && !reussi; i++) {
        xEventGroupClearBits(evenements, BIT_IP | BIT_COUPE);
        esp_wifi_connect();
        EventBits_t b = xEventGroupWaitBits(evenements, BIT_IP | BIT_COUPE, pdFALSE, pdFALSE,
                                            pdMS_TO_TICKS(ESSAI_MS));
        if (b & BIT_IP) {
            reussi = true;
        } else {
            if (b & BIT_COUPE) {
                pourquoi = texte_raison(raison_coupure);
                ESP_LOGW(TAG, "Essai %d raté (raison %d)", i + 1, raison_coupure);
            }
            couper();
            vTaskDelay(pdMS_TO_TICKS(500));
        }
    }

    if (reussi) {
        garder_reseau(ssid, mdp);
        ESP_LOGI(TAG, "Réseau %s enregistré", ssid);
        reconnexion = true;
        if (rappels.essai) rappels.essai(UI_WIFI_CONNECTE, ssid, NULL);
        return;
    }
    /* Raté : on revient au réseau d'avant, rien n'est enregistré. */
    ESP_LOGW(TAG, "Réseau %s refusé, retour à %s", ssid, ancien_ssid);
    appliquer(ancien_ssid, ancien_mdp);
    reconnexion = true;
    esp_wifi_connect();
    if (rappels.essai) rappels.essai(UI_WIFI_ECHEC, ssid, pourquoi);
}

static void tache_wifi(void *arg)
{
    static ordre_t o;
    while (1) {
        if (xQueueReceive(ordres, &o, portMAX_DELAY) != pdTRUE) continue;
        if (o.type == ORDRE_CHERCHER) {
            chercher();
        } else {
            essayer(o.ssid, o.mdp);
        }
        memset(o.mdp, 0, sizeof(o.mdp));
    }
}

/* --- Interface ----------------------------------------------------------------------- */

void wifi_chercher(void)
{
    ordre_t o = {.type = ORDRE_CHERCHER};
    xQueueSend(ordres, &o, 0);
}

void wifi_choisir(const char *ssid, const char *mdp)
{
    ordre_t o = {.type = ORDRE_ESSAYER};
    snprintf(o.ssid, sizeof(o.ssid), "%s", ssid);
    snprintf(o.mdp, sizeof(o.mdp), "%s", mdp ? mdp : "");
    xQueueSend(ordres, &o, 0);
    memset(o.mdp, 0, sizeof(o.mdp));
}

void wifi_demarrer(const wifi_rappels_t *r)
{
    rappels = *r;
    evenements = xEventGroupCreate();
    ordres = xQueueCreate(2, sizeof(ordre_t));

    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();

    wifi_init_config_t init = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&init));
    ESP_ERROR_CHECK(esp_event_handler_instance_register(WIFI_EVENT, ESP_EVENT_ANY_ID, sur_wifi, NULL, NULL));
    ESP_ERROR_CHECK(esp_event_handler_instance_register(IP_EVENT, IP_EVENT_STA_GOT_IP, sur_wifi, NULL, NULL));

    char ssid[33], mdp[65];
    lire_reseau(ssid, sizeof(ssid), mdp, sizeof(mdp));
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    appliquer(ssid, mdp);
    memset(mdp, 0, sizeof(mdp));
    if (rappels.connexion) rappels.connexion(false, ssid_actuel);
    ESP_ERROR_CHECK(esp_wifi_start());

    xTaskCreate(tache_wifi, "wifi", 4096, NULL, 3, NULL);
}
