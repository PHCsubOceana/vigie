/*
 * Vigie — corps (ESP32-P4-WIFI6-Touch-LCD-3.4C).
 *
 * Se connecte au Wi-Fi par la puce C6, s'abonne aux messages du Jetson
 * (vigie/etat, vigie/cerveau, vigie/courrier, vigie/commande) et les affiche
 * sur l'écran rond. Regarde aussi devant lui avec la caméra (camera.c).
 * Joue un petit son (son.c) quand un agent t'attend, termine, ou qu'un mail important
 * arrive. Volume : toucher bref sur l'écran, ou MQTT vigie/corps/volume (0..100), gardé en NVS.
 */
#include <stdlib.h>
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_event.h"
#include "esp_netif.h"
#include "esp_wifi.h"
#include "nvs_flash.h"
#include "nvs.h"
#include "esp_timer.h"
#include "mqtt_client.h"
#include "cJSON.h"
#include "bsp/esp-bsp.h"
#include "ui.h"
#include "camera.h"
#include "vigie.h"
#include "son.h"

static const char *TAG = "vigie";

static bool wifi_ok = false;
static bool mqtt_ok = false;
static bool cerveau_ok = false;
static esp_mqtt_client_handle_t mqtt = NULL;
static bool liaison_etait_ok = false;

/* --- Volume, gardé en NVS ---------------------------------------------------------------- */

static int lire_volume(void)
{
    nvs_handle_t h;
    int32_t v = CONFIG_VIGIE_VOLUME;
    if (nvs_open("vigie", NVS_READONLY, &h) == ESP_OK) {
        nvs_get_i32(h, "volume", &v);
        nvs_close(h);
    }
    return v < 0 ? 0 : v > 100 ? 100 : (int)v;
}

static void garder_volume(int v)
{
    nvs_handle_t h;
    if (nvs_open("vigie", NVS_READWRITE, &h) == ESP_OK) {
        nvs_set_i32(h, "volume", v);
        nvs_commit(h);
        nvs_close(h);
    }
}

/* Appelé par l'écran (tâche LVGL, verrou déjà pris). */
/* Mail marqué « vu » sur l'écran : le Jetson le retire de sa liste (sans le marquer lu
 * dans la boîte : la Vigie reste en lecture seule). */
static void sur_mail_vu(const char *id)
{
    ESP_LOGI(TAG, "Mail vu : %s", id);
    vigie_publier("vigie/courrier/vu", id, false);
}

static void sur_volume_ecran(int volume, bool final)
{
    son_volume(volume);
    if (final) {
        garder_volume(volume);
        son_jouer(SON_TEST);
        ESP_LOGI(TAG, "Volume réglé à %d", volume);
    }
}

void vigie_publier(const char *sujet, const char *texte, bool retenu)
{
    if (mqtt && mqtt_ok) {
        /* enqueue : ne bloque pas la tâche appelante (caméra) ; envoyé par la tâche MQTT. */
        esp_mqtt_client_enqueue(mqtt, sujet, texte, 0, retenu ? 1 : 0, retenu, true);
    }
}

/* --- Affichage, toujours sous le verrou LVGL ------------------------------------ */

static void maj_liaison(void)
{
    vigie_liaison_t l = !wifi_ok ? LIAISON_WIFI_ABSENT
                      : !mqtt_ok ? LIAISON_BROKER_ABSENT
                      : !cerveau_ok ? LIAISON_CERVEAU_ABSENT
                      : LIAISON_OK;
    if (liaison_etait_ok && l != LIAISON_OK) {
        son_jouer(SON_HORS_LIGNE);
    }
    liaison_etait_ok = (l == LIAISON_OK);
    bsp_display_lock((uint32_t)-1);
    ui_afficher_liaison(l);
    bsp_display_unlock();
}

/* Copie une chaîne UTF-8 sans couper un caractère accentué en deux. */
/* Règle d'affichage : jamais de tiret long (— ou –) sur l'écran, d'où qu'il vienne
 * (phrase du LLM, résumé de mail, nom d'agent). « a — b » devient « a, b », un tiret
 * long isolé devient « - ». Le texte ne peut que raccourcir : tout se fait sur place. */
static void sans_tirets_longs(char *s)
{
    char *w = s;
    for (char *r = s; *r;) {
        bool tiret = (unsigned char)r[0] == 0xE2 && (unsigned char)r[1] == 0x80
                     && ((unsigned char)r[2] == 0x94 || (unsigned char)r[2] == 0x93);
        if (!tiret) {
            *w++ = *r++;
            continue;
        }
        r += 3;
        if (w > s && w[-1] == ' ' && *r == ' ') {
            w[-1] = ',';          /* « a — b » → « a, b » */
            *w++ = ' ';
            r++;
        } else {
            *w++ = '-';
        }
    }
    *w = '\0';
}

static void copier_utf8(char *dst, size_t taille, const char *src)
{
    if (!src) {
        dst[0] = '\0';
        return;
    }
    size_t n = strlen(src);
    if (n >= taille) {
        n = taille - 1;
        while (n > 0 && ((unsigned char)src[n] & 0xC0) == 0x80) {
            n--;    /* recule jusqu'au début du caractère */
        }
    }
    memcpy(dst, src, n);
    dst[n] = '\0';
    sans_tirets_longs(dst);
}

static vigie_humeur_t humeur_de(const char *s)
{
    if (!s) return VIGIE_CALME;
    if (!strcmp(s, "attentive"))  return VIGIE_ATTENTIVE;
    if (!strcmp(s, "affairee"))   return VIGIE_AFFAIREE;
    if (!strcmp(s, "satisfaite")) return VIGIE_SATISFAITE;
    return VIGIE_CALME;
}

static agent_etat_t etat_agent_de(const char *s)
{
    if (!s) return AGENT_PRET;
    if (!strcmp(s, "attend"))    return AGENT_ATTEND;
    if (!strcmp(s, "travaille")) return AGENT_TRAVAILLE;
    if (!strcmp(s, "fini"))      return AGENT_FINI;
    return AGENT_PRET;
}

/* --- Sons selon les changements d'état ------------------------------------------------------ */

#define RAPPEL_ATTENTE_US (5LL * 60 * 1000000)   /* un seul rappel après 5 min d'attente */

static vigie_agent_t precedents[VIGIE_MAX_AGENTS];
static int nb_precedents = -1;            /* -1 : premier message, on ne sonne pas */
static int64_t attente_depuis = 0;        /* 0 : personne n'attend */
static bool rappel_fait = false;

static agent_etat_t etat_precedent(const char *id, bool *trouve)
{
    for (int i = 0; i < nb_precedents; i++) {
        if (!strcmp(precedents[i].id, id)) {
            *trouve = true;
            return precedents[i].etat;
        }
    }
    *trouve = false;
    return AGENT_PRET;
}

/* Les boîtes mail (source « courrier ») ont leur propre son, joué dans traiter_courrier(). */
static void sonner_selon(const vigie_etat_t *e)
{
    bool attente = false, fini = false, quelquun_attend = false;
    for (int i = 0; i < e->nb_agents; i++) {
        const vigie_agent_t *a = &e->agents[i];
        if (!strcmp(a->source, "courrier")) continue;
        bool trouve;
        agent_etat_t avant = etat_precedent(a->id, &trouve);
        if (a->etat == AGENT_ATTEND) {
            quelquun_attend = true;
            if (!trouve || avant != AGENT_ATTEND) attente = true;
        } else if (a->etat == AGENT_FINI && trouve && avant != AGENT_FINI) {
            fini = true;
        }
    }

    if (nb_precedents >= 0) {               /* pas de son pour l'état retrouvé au démarrage */
        if (attente)   son_jouer(SON_ATTENTE);
        else if (fini) son_jouer(SON_FINI);
    }

    if (!quelquun_attend) {
        attente_depuis = 0;
    } else if (attente || attente_depuis == 0) {
        attente_depuis = esp_timer_get_time();
        rappel_fait = false;
    }

    nb_precedents = e->nb_agents;
    memcpy(precedents, e->agents, sizeof(vigie_agent_t) * e->nb_agents);
}

static void tache_rappel(void *arg)
{
    while (1) {
        vTaskDelay(pdMS_TO_TICKS(10000));
        if (attente_depuis && !rappel_fait &&
            esp_timer_get_time() - attente_depuis > RAPPEL_ATTENTE_US) {
            rappel_fait = true;
            son_jouer(SON_ATTENTE);
        }
    }
}

static void traiter_etat(const char *json, int len)
{
    cJSON *racine = cJSON_ParseWithLength(json, len);
    if (!racine) {
        ESP_LOGW(TAG, "JSON illisible sur vigie/etat");
        return;
    }
    static vigie_etat_t e;  /* statique : évite de charger la pile de la tâche MQTT */
    memset(&e, 0, sizeof(e));
    e.humeur = humeur_de(cJSON_GetStringValue(cJSON_GetObjectItem(racine, "humeur")));
    copier_utf8(e.phrase, sizeof(e.phrase), cJSON_GetStringValue(cJSON_GetObjectItem(racine, "phrase")));

    const cJSON *agent;
    cJSON_ArrayForEach(agent, cJSON_GetObjectItem(racine, "agents")) {
        if (e.nb_agents >= VIGIE_MAX_AGENTS) break;
        vigie_agent_t *a = &e.agents[e.nb_agents++];
        copier_utf8(a->id, sizeof(a->id), cJSON_GetStringValue(cJSON_GetObjectItem(agent, "id")));
        copier_utf8(a->source, sizeof(a->source), cJSON_GetStringValue(cJSON_GetObjectItem(agent, "source")));
        copier_utf8(a->projet, sizeof(a->projet), cJSON_GetStringValue(cJSON_GetObjectItem(agent, "projet")));
        copier_utf8(a->message, sizeof(a->message), cJSON_GetStringValue(cJSON_GetObjectItem(agent, "message")));
        a->etat = etat_agent_de(cJSON_GetStringValue(cJSON_GetObjectItem(agent, "etat")));
    }
    cJSON_Delete(racine);

    ESP_LOGI(TAG, "Humeur %d, %d agent(s) : %s", e.humeur, e.nb_agents, e.phrase);
    sonner_selon(&e);
    bsp_display_lock((uint32_t)-1);
    ui_afficher_etat(&e);
    bsp_display_unlock();
}

/* --- Courrier ------------------------------------------------------------------------ */

/* `retenu` : message retenu par le broker, rejoué à l'abonnement (démarrage de l'écran,
 * reconnexion). Il ne fait que poser le point de départ : pas de son ni d'alerte.
 * Un message en direct alerte dès qu'il contient un mail plus récent que tout ce qu'on a vu. */
static void traiter_courrier(const char *json, int len, bool retenu)
{
    cJSON *racine = cJSON_ParseWithLength(json, len);
    if (!racine) {
        ESP_LOGW(TAG, "JSON illisible sur vigie/courrier");
        return;
    }
    static vigie_courrier_t c;
    static double plus_recent_vu = 0;   /* date du mail le plus récent déjà signalé */
    memset(&c, 0, sizeof(c));
    const cJSON *nb = cJSON_GetObjectItem(racine, "nb");
    c.nb = cJSON_IsNumber(nb) ? nb->valueint : 0;

    const cJSON *maj_j = cJSON_GetObjectItem(racine, "maj");
    double maj = cJSON_IsNumber(maj_j) ? maj_j->valuedouble : 0;
    double plus_recent = 0;
    const cJSON *m;
    cJSON_ArrayForEach(m, cJSON_GetObjectItem(racine, "importants")) {
        const cJSON *recu = cJSON_GetObjectItem(m, "recu");
        if (cJSON_IsNumber(recu) && recu->valuedouble > plus_recent) {
            plus_recent = recu->valuedouble;
        }
        if (c.nb_detail >= VIGIE_MAX_MAILS) continue;
        vigie_mail_t *d = &c.mails[c.nb_detail++];
        copier_utf8(d->de, sizeof(d->de), cJSON_GetStringValue(cJSON_GetObjectItem(m, "de")));
        copier_utf8(d->resume, sizeof(d->resume), cJSON_GetStringValue(cJSON_GetObjectItem(m, "resume")));
        copier_utf8(d->compte, sizeof(d->compte), cJSON_GetStringValue(cJSON_GetObjectItem(m, "compte")));
        copier_utf8(d->id, sizeof(d->id), cJSON_GetStringValue(cJSON_GetObjectItem(m, "id")));
        copier_utf8(d->sujet, sizeof(d->sujet), cJSON_GetStringValue(cJSON_GetObjectItem(m, "sujet")));
        /* Pas d'horloge sur la carte : l'âge vient des deux dates du Jetson. */
        d->age_s = (maj > 0 && cJSON_IsNumber(recu) && maj >= recu->valuedouble)
                   ? (int)(maj - recu->valuedouble) : -1;
    }
    cJSON_Delete(racine);

    bool nouveau = !retenu && plus_recent > plus_recent_vu;
    if (plus_recent > plus_recent_vu) {
        plus_recent_vu = plus_recent;
    }
    ESP_LOGI(TAG, "Courrier : %d important(s)%s", c.nb, nouveau ? ", dont un nouveau" : "");
    if (nouveau) {
        son_jouer(SON_MAIL);
    }
    bsp_display_lock((uint32_t)-1);
    ui_afficher_courrier(&c, nouveau);
    bsp_display_unlock();
}

/* --- Caméra : toucher l'écran ouvre ou ferme l'aperçu ------------------------------------ */

static bool apercu_sans_camera = false;

/* Appelée par LVGL (verrou déjà pris) ou par la commande MQTT (verrou pris par l'appelant). */
static void basculer_apercu(void)
{
    if (camera_ok()) {
        camera_apercu(!camera_apercu_ouvert());
    } else {
        apercu_sans_camera = !apercu_sans_camera;
        ui_camera_afficher(apercu_sans_camera, "Caméra absente");
    }
}

static void traiter_commande(const char *cmd)
{
    ESP_LOGI(TAG, "Commande : %s", cmd);
    if (!strcmp(cmd, "camera")) {
        bsp_display_lock((uint32_t)-1);
        basculer_apercu();
        bsp_display_unlock();
    } else if (!strcmp(cmd, "camera on") || !strcmp(cmd, "camera off")) {
        camera_apercu(!strcmp(cmd, "camera on"));
    } else if (!strcmp(cmd, "courrier")) {
        bsp_display_lock((uint32_t)-1);
        ui_ouvrir_courrier();
        bsp_display_unlock();
    }
}

/* --- MQTT ------------------------------------------------------------------------------ */

#define TAILLE_MSG 8192
static char message[TAILLE_MSG + 1];
static char sujet[64];
static bool message_retenu;

static void sur_mqtt(void *arg, esp_event_base_t base, int32_t id, void *donnees)
{
    esp_mqtt_event_handle_t ev = donnees;
    switch ((esp_mqtt_event_id_t)id) {
    case MQTT_EVENT_CONNECTED:
        ESP_LOGI(TAG, "MQTT connecté");
        mqtt_ok = true;
        esp_mqtt_client_subscribe(mqtt, "vigie/cerveau", 1);
        esp_mqtt_client_subscribe(mqtt, "vigie/etat", 1);
        esp_mqtt_client_subscribe(mqtt, "vigie/courrier", 1);
        esp_mqtt_client_subscribe(mqtt, "vigie/commande", 0);
        esp_mqtt_client_subscribe(mqtt, "vigie/corps/volume", 1);
        maj_liaison();
        camera_sur_connexion();
        break;

    case MQTT_EVENT_DISCONNECTED:
        ESP_LOGW(TAG, "MQTT déconnecté");
        mqtt_ok = false;
        maj_liaison();
        break;

    case MQTT_EVENT_DATA:
        /* Un message peut arriver en plusieurs morceaux : on le reconstitue. */
        if (ev->current_data_offset == 0) {
            int n = ev->topic_len < (int)sizeof(sujet) - 1 ? ev->topic_len : (int)sizeof(sujet) - 1;
            memcpy(sujet, ev->topic, n);
            sujet[n] = '\0';
            message_retenu = ev->retain;
        }
        if (ev->total_data_len > TAILLE_MSG) {
            ESP_LOGW(TAG, "Message trop long (%d octets), ignoré", ev->total_data_len);
            break;
        }
        memcpy(message + ev->current_data_offset, ev->data, ev->data_len);
        if (ev->current_data_offset + ev->data_len < ev->total_data_len) {
            break;  /* la suite arrive */
        }
        message[ev->total_data_len] = '\0';

        if (!strcmp(sujet, "vigie/cerveau")) {
            cerveau_ok = !strcmp(message, "en-ligne");
            ESP_LOGI(TAG, "Cerveau : %s", message);
            maj_liaison();
        } else if (!strcmp(sujet, "vigie/etat")) {
            traiter_etat(message, ev->total_data_len);
        } else if (!strcmp(sujet, "vigie/courrier")) {
            traiter_courrier(message, ev->total_data_len, message_retenu);
        } else if (!strcmp(sujet, "vigie/commande")) {
            traiter_commande(message);
        } else if (!strcmp(sujet, "vigie/corps/volume") && ev->total_data_len > 0) {
            int v = atoi(message);
            v = v < 0 ? 0 : v > 100 ? 100 : v;
            son_volume(v);
            garder_volume(v);
            bsp_display_lock((uint32_t)-1);
            ui_afficher_volume(v);
            bsp_display_unlock();
            son_jouer(SON_TEST);
            ESP_LOGI(TAG, "Volume reçu en MQTT : %d", v);
        }
        break;

    case MQTT_EVENT_ERROR:
        ESP_LOGW(TAG, "Erreur MQTT");
        break;

    default:
        break;
    }
}

static void demarrer_mqtt(void)
{
    if (mqtt) return;  /* le client se reconnecte de lui-même ensuite */
    const esp_mqtt_client_config_t cfg = {
        .broker.address.uri = CONFIG_VIGIE_MQTT_URI,
        .credentials.username = CONFIG_VIGIE_MQTT_USER,
        .credentials.authentication.password = CONFIG_VIGIE_MQTT_PASSWORD,
        .credentials.client_id = "vigie-corps",
        .session.keepalive = 30,
        .network.reconnect_timeout_ms = 3000,
        .buffer.size = 2048,
    };
    mqtt = esp_mqtt_client_init(&cfg);
    esp_mqtt_client_register_event(mqtt, ESP_EVENT_ANY_ID, sur_mqtt, NULL);
    esp_mqtt_client_start(mqtt);
}

/* --- Wi-Fi (via la puce ESP32-C6) ------------------------------------------------------------- */

static void sur_wifi(void *arg, esp_event_base_t base, int32_t id, void *donnees)
{
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        if (wifi_ok) {
            ESP_LOGW(TAG, "Wi-Fi perdu, nouvelle tentative");
            wifi_ok = false;
            maj_liaison();
        }
        vTaskDelay(pdMS_TO_TICKS(1000));
        esp_wifi_connect();
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t *ip = donnees;
        ESP_LOGI(TAG, "Adresse IP : " IPSTR, IP2STR(&ip->ip_info.ip));
        wifi_ok = true;
        maj_liaison();
        demarrer_mqtt();
    }
}

static void demarrer_wifi(void)
{
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();

    wifi_init_config_t init = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&init));
    ESP_ERROR_CHECK(esp_event_handler_instance_register(WIFI_EVENT, ESP_EVENT_ANY_ID, sur_wifi, NULL, NULL));
    ESP_ERROR_CHECK(esp_event_handler_instance_register(IP_EVENT, IP_EVENT_STA_GOT_IP, sur_wifi, NULL, NULL));

    wifi_config_t cfg = {0};
    copier_utf8((char *)cfg.sta.ssid, sizeof(cfg.sta.ssid), CONFIG_VIGIE_WIFI_SSID);
    copier_utf8((char *)cfg.sta.password, sizeof(cfg.sta.password), CONFIG_VIGIE_WIFI_PASSWORD);
    cfg.sta.threshold.authmode = WIFI_AUTH_WPA2_PSK;

    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &cfg));
    ESP_ERROR_CHECK(esp_wifi_start());
}

/* --- Démarrage ------------------------------------------------------------------------- */

void app_main(void)
{
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        err = nvs_flash_init();
    }
    ESP_ERROR_CHECK(err);

    /* L'écran d'abord : la Vigie montre qu'elle est vivante avant même le réseau. */
    int volume = lire_volume();
    bsp_display_start();
    bsp_display_lock((uint32_t)-1);
    ui_init();
    ui_sur_toucher(basculer_apercu);
    ui_sur_volume(sur_volume_ecran);
    ui_sur_mail_vu(sur_mail_vu);
    ui_afficher_volume(volume);
    bsp_display_unlock();
    bsp_display_backlight_on();

    son_init(volume);
    xTaskCreate(tache_rappel, "rappel", 2048, NULL, 2, NULL);

    demarrer_wifi();

    /* Après le Wi-Fi : une caméra absente ou mal branchée ne doit rien bloquer. */
    camera_demarrer();
}
