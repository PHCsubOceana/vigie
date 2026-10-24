/*
 * Vigie — affichage du plastron (écran rond 800×800).
 * Indépendant d'ESP-IDF : ne dépend que de LVGL 9, pour pouvoir être testé sur PC.
 * Toutes les fonctions doivent être appelées avec le verrou LVGL pris.
 */
#pragma once

#include <stdbool.h>

#define VIGIE_MAX_AGENTS 8
#define VIGIE_MAX_MAILS  3

/* Aperçu de la caméra : un disque de cette taille au centre de l'écran. */
#define UI_APERCU        600

typedef enum {
    VIGIE_CALME,
    VIGIE_AFFAIREE,
    VIGIE_ATTENTIVE,
    VIGIE_SATISFAITE,
} vigie_humeur_t;

typedef enum {
    AGENT_PRET,
    AGENT_TRAVAILLE,
    AGENT_ATTEND,
    AGENT_FINI,
} agent_etat_t;

typedef struct {
    char id[24];
    char source[16];
    char projet[48];
    char message[48];     /* précision de l'agent (« 2 mails importants »…) */
    agent_etat_t etat;
} vigie_agent_t;

typedef struct {
    vigie_humeur_t humeur;
    char phrase[200];
    int nb_agents;
    vigie_agent_t agents[VIGIE_MAX_AGENTS];
} vigie_etat_t;

typedef enum {
    LIAISON_WIFI_ABSENT,     /* pas de réseau */
    LIAISON_BROKER_ABSENT,   /* réseau OK, broker MQTT injoignable */
    LIAISON_CERVEAU_ABSENT,  /* broker OK, mais vigie.py ne publie plus */
    LIAISON_OK,
} vigie_liaison_t;

/* Courrier important non lu, trié par le Jetson (vigie/courrier). */
typedef struct {
    char id[48];          /* « compte:uid », pour le marquer vu */
    char de[64];
    char compte[24];      /* boîte : « pro »… */
    char sujet[96];
    char resume[128];
    int age_s;            /* âge du mail à la réception du message (-1 : inconnu) */
} vigie_mail_t;

typedef struct {
    int nb;                              /* nombre total de mails importants non lus */
    int nb_detail;                       /* nombre d'entrées remplies dans mails[] */
    vigie_mail_t mails[VIGIE_MAX_MAILS]; /* le plus récent en premier */
} vigie_courrier_t;

void ui_init(void);
void ui_afficher_etat(const vigie_etat_t *etat);
void ui_afficher_liaison(vigie_liaison_t liaison);

/* `nouveau` : un mail vient d'arriver → alerte de 20 s au centre de l'écran. */
void ui_afficher_courrier(const vigie_courrier_t *courrier, bool nouveau);

/* Fiche « courrier » : le détail des mails importants (expéditeur, boîte, âge, résumé,
 * objet). S'ouvre d'un toucher sur l'enveloppe, sur la ligne corail du bas, ou n'importe
 * où pendant l'alerte de 20 s ; se ferme d'un toucher ou seule au bout de 30 s. */
void ui_ouvrir_courrier(void);

/* Toucher un mail dans la fiche le marque « vu » : il disparaît tout de suite de l'écran
 * et le rappel reçoit son id (à transmettre au Jetson). Verrou LVGL déjà pris. */
void ui_sur_mail_vu(void (*rappel)(const char *id));

/* Appui long sur l'écran (n'importe où) appelle ce rappel, verrou LVGL déjà pris. */
void ui_sur_toucher(void (*rappel)(void));

/* Volume : un toucher bref sur l'écran ouvre le réglage (grand arc + « Couper le son »).
 * Le rappel reçoit le volume 0..100 ; final = vrai au relâchement. */
typedef void (*ui_volume_cb_t)(int volume, bool final);
void ui_sur_volume(ui_volume_cb_t rappel);
void ui_afficher_volume(int volume);   /* 0 = son coupé */
void ui_ouvrir_volume(void);

/* Aperçu caméra. `message` non NULL : texte à la place de l'image (caméra absente…). */
void ui_camera_afficher(bool visible, const char *message);
/* Image RGB565 de UI_APERCU × UI_APERCU pixels. Le tampon doit rester valable
 * jusqu'à l'appel suivant (deux tampons en alternance). */
void ui_camera_image(const void *pixels);
/* Cadre du visage, en pixels de l'aperçu ; `vu` faux pour l'effacer. */
void ui_camera_visage(bool vu, int x1, int y1, int x2, int y2, const char *legende);
