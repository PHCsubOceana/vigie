/*
 * Vigie — écran principal du robot.
 *
 *   anneau extérieur  : l'humeur (couleur + respiration plus ou moins rapide)
 *   anneau intérieur  : un segment par agent, coloré selon son état
 *   centre            : l'humeur en toutes lettres, la phrase du Jetson,
 *                       les trois agents les plus importants
 *   bas               : l'état de la liaison, seulement quand elle est rompue ;
 *                       sinon, le dernier mail important non lu
 *   haut              : une enveloppe et le nombre de mails importants non lus
 *
 * Un mail important qui arrive prend le centre pendant 20 s (anneau corail).
 * Toucher l'enveloppe ou la ligne corail (ou l'écran pendant l'alerte) : fiche courrier.
 * Toucher bref ailleurs : réglage du volume. Appui long : ouvre l'aperçu de la caméra
 * (disque central) ; un toucher sur l'aperçu le ferme.
 */
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "lvgl.h"
#include "ui.h"

LV_FONT_DECLARE(vigie_phrase_40);
LV_FONT_DECLARE(vigie_titre_28);
LV_FONT_DECLARE(vigie_petit_24);
LV_FONT_DECLARE(vigie_chiffres_96);

#define TAILLE          800
#define LISTE_MAX       3
#define ECART_DEG       5       /* espace entre deux segments d'agents */

/* --- Palette --------------------------------------------------------------- */

#define C_FOND          lv_color_hex(0x05070B)
#define C_TEXTE         lv_color_hex(0xF2F4F8)
#define C_DISCRET       lv_color_hex(0x8A93A6)
#define C_AMBRE         lv_color_hex(0xFFB020)
#define C_BLEU          lv_color_hex(0x4C9BFF)
#define C_VERT          lv_color_hex(0x3DDC97)
#define C_ARDOISE       lv_color_hex(0x6B7A99)
#define C_GRIS          lv_color_hex(0x4A5366)
#define C_CORAIL        lv_color_hex(0xFF7A8A)

#define ALERTE_MS       20000   /* durée de l'alerte « nouveau mail » */

typedef struct {
    const char *titre;
    lv_color_t couleur;
    uint32_t periode_ms;   /* 0 = anneau fixe */
    lv_opa_t opa_haut;
    lv_opa_t opa_bas;
} style_humeur_t;

static style_humeur_t style_de(vigie_humeur_t h)
{
    switch (h) {
    case VIGIE_ATTENTIVE:  return (style_humeur_t){"ON T'ATTEND", C_AMBRE,   700,  LV_OPA_COVER, LV_OPA_30};
    case VIGIE_AFFAIREE:   return (style_humeur_t){"AU TRAVAIL", C_BLEU,    1800, LV_OPA_COVER, LV_OPA_50};
    case VIGIE_SATISFAITE: return (style_humeur_t){"TERMINÉ",    C_VERT,    0,    LV_OPA_COVER, LV_OPA_COVER};
    case VIGIE_CALME:
    default:               return (style_humeur_t){"EN VEILLE",  C_ARDOISE, 4000, LV_OPA_70,    LV_OPA_20};
    }
}

static lv_color_t couleur_agent(agent_etat_t e)
{
    switch (e) {
    case AGENT_ATTEND:    return C_AMBRE;
    case AGENT_TRAVAILLE: return C_BLEU;
    case AGENT_FINI:      return C_VERT;
    case AGENT_PRET:
    default:              return C_GRIS;
    }
}

static const char *libelle_agent(agent_etat_t e)
{
    switch (e) {
    case AGENT_ATTEND:    return "attend ta réponse";
    case AGENT_TRAVAILLE: return "en cours";
    case AGENT_FINI:      return "terminé";
    case AGENT_PRET:
    default:              return "prêt";
    }
}

/* --- Objets ------------------------------------------------------------------ */

static lv_obj_t *halo, *anneau;                 /* humeur */
static lv_obj_t *segments[VIGIE_MAX_AGENTS];    /* agents */
static lv_obj_t *segment_vide;
static lv_obj_t *titre, *phrase, *liste, *statut;
static lv_obj_t *lignes[LISTE_MAX], *pastilles[LISTE_MAX], *noms[LISTE_MAX];

static lv_obj_t *badge, *badge_nb, *courrier_ligne;          /* courrier */
static lv_obj_t *apercu, *apercu_img, *apercu_cadre;            /* caméra */
static lv_obj_t *apercu_legende, *apercu_message;

static vigie_etat_t dernier_etat;
static vigie_liaison_t liaison = LIAISON_WIFI_ABSENT;
static bool etat_recu = false;

static vigie_courrier_t courrier;
static bool alerte = false;
static bool apercu_visible = false;
static lv_timer_t *minuterie_alerte = NULL;
static void (*rappel_toucher)(void) = NULL;

static void redessiner(void);
static void sur_appui_long(lv_event_t *e);
static void creer_reglage_volume(lv_obj_t *ecran);
static void creer_fiche_courrier(lv_obj_t *ecran);
static void sur_toucher_courrier(lv_event_t *e);
static void sur_carte(lv_event_t *e);
static void remplir_fiche(void);
static bool fiche_ouverte(void);
static void fermer_fiche(void);
static lv_obj_t *mention_son;
static uint32_t courrier_recu_tick;   /* lv_tick_get() à la réception de `courrier` */

static lv_obj_t *creer_arc(lv_obj_t *parent, int32_t diametre, int32_t epaisseur)
{
    lv_obj_t *arc = lv_arc_create(parent);
    lv_obj_set_size(arc, diametre, diametre);
    lv_obj_center(arc);
    lv_obj_remove_style(arc, NULL, LV_PART_KNOB);
    lv_obj_remove_flag(arc, LV_OBJ_FLAG_CLICKABLE);
    lv_arc_set_rotation(arc, 270);              /* 0° en haut */
    lv_arc_set_bg_angles(arc, 0, 360);
    lv_arc_set_angles(arc, 0, 360);
    lv_obj_set_style_arc_opa(arc, LV_OPA_TRANSP, LV_PART_MAIN);
    lv_obj_set_style_arc_width(arc, epaisseur, LV_PART_INDICATOR);
    lv_obj_set_style_arc_rounded(arc, true, LV_PART_INDICATOR);
    lv_obj_set_style_pad_all(arc, 0, LV_PART_MAIN);
    return arc;
}

/* Une ligne de largeur fixe : si le texte est trop long, il défile en boucle, lentement,
 * au lieu d'être coupé par « … ». Un texte qui tient reste immobile. */
#define DEFILEMENT_PX_S  35

static void defilant(lv_obj_t *l, int32_t largeur)
{
    lv_obj_set_size(l, largeur, lv_font_get_line_height(lv_obj_get_style_text_font(l, 0)));
    lv_label_set_long_mode(l, LV_LABEL_LONG_MODE_SCROLL_CIRCULAR);
    lv_obj_set_style_anim_duration(l, lv_anim_speed(DEFILEMENT_PX_S), 0);
}

static lv_obj_t *creer_label(lv_obj_t *parent, const lv_font_t *police, lv_color_t couleur)
{
    lv_obj_t *l = lv_label_create(parent);
    lv_obj_set_style_text_font(l, police, 0);
    lv_obj_set_style_text_color(l, couleur, 0);
    lv_obj_set_style_text_align(l, LV_TEXT_ALIGN_CENTER, 0);
    return l;
}

/* --- Respiration de l'anneau d'humeur ---------------------------------------- */

static void appliquer_opa(void *obj, int32_t v)
{
    lv_obj_set_style_arc_opa(anneau, (lv_opa_t)v, LV_PART_INDICATOR);
    lv_obj_set_style_arc_opa(halo, (lv_opa_t)(v / 4), LV_PART_INDICATOR);
    (void)obj;
}

static void lancer_respiration(const style_humeur_t *s)
{
    lv_anim_delete(anneau, appliquer_opa);
    if (s->periode_ms == 0) {
        appliquer_opa(anneau, s->opa_haut);
        return;
    }
    lv_anim_t a;
    lv_anim_init(&a);
    lv_anim_set_var(&a, anneau);
    lv_anim_set_exec_cb(&a, appliquer_opa);
    lv_anim_set_values(&a, s->opa_haut, s->opa_bas);
    lv_anim_set_duration(&a, s->periode_ms / 2);
    lv_anim_set_playback_duration(&a, s->periode_ms / 2);
    lv_anim_set_repeat_count(&a, LV_ANIM_REPEAT_INFINITE);
    lv_anim_set_path_cb(&a, lv_anim_path_ease_in_out);
    lv_anim_start(&a);
}

/* --- Toucher ---------------------------------------------------------------------- */

static void sur_clic(lv_event_t *e)
{
    (void)e;
    if (rappel_toucher) {
        rappel_toucher();
    }
}

void ui_sur_toucher(void (*rappel)(void))
{
    rappel_toucher = rappel;
}

/* --- Courrier : enveloppe en haut, dernier mail en bas ------------------------------- */

static void creer_courrier(lv_obj_t *ecran)
{
    static lv_point_precise_t rabat[] = {{2, 2}, {17, 13}, {32, 2}};

    badge = lv_obj_create(ecran);
    lv_obj_remove_style_all(badge);
    lv_obj_add_flag(badge, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_set_ext_click_area(badge, 40);           /* cible confortable pour le doigt */
    lv_obj_add_event_cb(badge, sur_toucher_courrier, LV_EVENT_CLICKED, NULL);
    lv_obj_set_size(badge, LV_SIZE_CONTENT, LV_SIZE_CONTENT);
    lv_obj_set_flex_flow(badge, LV_FLEX_FLOW_ROW);
    lv_obj_set_flex_align(badge, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    lv_obj_set_style_pad_column(badge, 10, 0);
    lv_obj_align(badge, LV_ALIGN_TOP_MID, 0, 112);

    lv_obj_t *enveloppe = lv_obj_create(badge);
    lv_obj_remove_style_all(enveloppe);
    lv_obj_remove_flag(enveloppe, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_set_size(enveloppe, 34, 24);
    lv_obj_set_style_border_width(enveloppe, 3, 0);
    lv_obj_set_style_border_color(enveloppe, C_CORAIL, 0);
    lv_obj_set_style_radius(enveloppe, 4, 0);
    lv_obj_t *trait = lv_line_create(enveloppe);
    lv_line_set_points(trait, rabat, 3);
    lv_obj_set_style_line_width(trait, 3, 0);
    lv_obj_set_style_line_color(trait, C_CORAIL, 0);
    lv_obj_set_style_line_rounded(trait, true, 0);

    badge_nb = creer_label(badge, &vigie_titre_28, C_CORAIL);
    lv_obj_add_flag(badge, LV_OBJ_FLAG_HIDDEN);

    courrier_ligne = creer_label(ecran, &vigie_petit_24, C_CORAIL);
    defilant(courrier_ligne, 370);
    lv_obj_align(courrier_ligne, LV_ALIGN_BOTTOM_MID, 0, -118);
    lv_obj_add_flag(courrier_ligne, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_set_ext_click_area(courrier_ligne, 24);
    lv_obj_add_event_cb(courrier_ligne, sur_toucher_courrier, LV_EVENT_CLICKED, NULL);
    lv_obj_add_flag(courrier_ligne, LV_OBJ_FLAG_HIDDEN);
}

static void texte_mail(char *dst, size_t taille, const vigie_mail_t *m)
{
    if (m->resume[0] && m->de[0] && !strstr(m->resume, m->de)) {
        snprintf(dst, taille, "%s : %s", m->de, m->resume);
    } else {
        snprintf(dst, taille, "%s", m->resume[0] ? m->resume : m->de);
    }
}

static void dessiner_courrier(void)
{
    bool montrer = liaison == LIAISON_OK && courrier.nb > 0;
    if (!montrer) {
        lv_obj_add_flag(badge, LV_OBJ_FLAG_HIDDEN);
        lv_obj_add_flag(courrier_ligne, LV_OBJ_FLAG_HIDDEN);
        return;
    }
    lv_label_set_text_fmt(badge_nb, "%d", courrier.nb);
    lv_obj_remove_flag(badge, LV_OBJ_FLAG_HIDDEN);
    if (courrier.nb_detail > 0 && !alerte && !apercu_visible) {
        char texte[200];
        texte_mail(texte, sizeof(texte), &courrier.mails[0]);
        lv_label_set_text(courrier_ligne, texte);
        lv_obj_remove_flag(courrier_ligne, LV_OBJ_FLAG_HIDDEN);
    } else {
        lv_obj_add_flag(courrier_ligne, LV_OBJ_FLAG_HIDDEN);
    }
}

static void fin_alerte(lv_timer_t *t)
{
    (void)t;
    minuterie_alerte = NULL;
    alerte = false;
    redessiner();
}

/* --- Aperçu de la caméra --------------------------------------------------------- */

static void creer_apercu(lv_obj_t *ecran)
{
    apercu = lv_obj_create(ecran);
    lv_obj_remove_style_all(apercu);
    lv_obj_set_size(apercu, UI_APERCU, UI_APERCU);
    lv_obj_center(apercu);
    lv_obj_set_style_radius(apercu, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_clip_corner(apercu, true, 0);
    lv_obj_set_style_bg_color(apercu, lv_color_hex(0x000000), 0);
    lv_obj_set_style_bg_opa(apercu, LV_OPA_COVER, 0);
    lv_obj_remove_flag(apercu, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_flag(apercu, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_event_cb(apercu, sur_clic, LV_EVENT_CLICKED, NULL);

    /* Un canevas plutôt qu'une image : changer de tampon vide aussi le cache de LVGL. */
    apercu_img = lv_canvas_create(apercu);
    lv_obj_remove_flag(apercu_img, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_center(apercu_img);
    lv_obj_add_flag(apercu_img, LV_OBJ_FLAG_HIDDEN);

    apercu_cadre = lv_obj_create(apercu);
    lv_obj_remove_style_all(apercu_cadre);
    lv_obj_remove_flag(apercu_cadre, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_set_style_border_width(apercu_cadre, 4, 0);
    lv_obj_set_style_border_color(apercu_cadre, C_VERT, 0);
    lv_obj_set_style_radius(apercu_cadre, 16, 0);
    lv_obj_add_flag(apercu_cadre, LV_OBJ_FLAG_HIDDEN);

    apercu_legende = creer_label(apercu, &vigie_petit_24, C_TEXTE);
    lv_obj_set_style_bg_color(apercu_legende, C_FOND, 0);
    lv_obj_set_style_bg_opa(apercu_legende, LV_OPA_60, 0);
    lv_obj_set_style_pad_hor(apercu_legende, 14, 0);
    lv_obj_set_style_pad_ver(apercu_legende, 4, 0);
    lv_obj_set_style_radius(apercu_legende, 14, 0);
    lv_obj_align(apercu_legende, LV_ALIGN_BOTTOM_MID, 0, -50);

    apercu_message = creer_label(apercu, &vigie_phrase_40, C_DISCRET);
    lv_obj_center(apercu_message);
    lv_obj_add_flag(apercu_message, LV_OBJ_FLAG_HIDDEN);

    lv_obj_add_flag(apercu, LV_OBJ_FLAG_HIDDEN);
}

void ui_camera_afficher(bool visible, const char *message)
{
    apercu_visible = visible;
    dessiner_courrier();    /* la ligne du bas dépasse du disque : cachée pendant l'aperçu */
    if (!visible) {
        lv_obj_add_flag(apercu, LV_OBJ_FLAG_HIDDEN);
        return;
    }
    if (message) {
        lv_label_set_text(apercu_message, message);
        lv_obj_remove_flag(apercu_message, LV_OBJ_FLAG_HIDDEN);
        lv_obj_add_flag(apercu_img, LV_OBJ_FLAG_HIDDEN);
        lv_obj_add_flag(apercu_cadre, LV_OBJ_FLAG_HIDDEN);
        lv_label_set_text(apercu_legende, "Touche pour fermer");
    } else {
        lv_obj_add_flag(apercu_message, LV_OBJ_FLAG_HIDDEN);
        lv_label_set_text(apercu_legende, "Je regarde…");
    }
    lv_obj_remove_flag(apercu, LV_OBJ_FLAG_HIDDEN);
    lv_obj_move_foreground(apercu);
}

void ui_camera_image(const void *pixels)
{
    lv_canvas_set_buffer(apercu_img, (void *)pixels, UI_APERCU, UI_APERCU, LV_COLOR_FORMAT_RGB565);
    lv_obj_remove_flag(apercu_img, LV_OBJ_FLAG_HIDDEN);
    lv_obj_invalidate(apercu_img);
}

void ui_camera_visage(bool vu, int x1, int y1, int x2, int y2, const char *legende)
{
    if (vu) {
        lv_obj_set_pos(apercu_cadre, x1, y1);
        lv_obj_set_size(apercu_cadre, x2 - x1, y2 - y1);
        lv_obj_remove_flag(apercu_cadre, LV_OBJ_FLAG_HIDDEN);
    } else {
        lv_obj_add_flag(apercu_cadre, LV_OBJ_FLAG_HIDDEN);
    }
    if (legende) {
        lv_label_set_text(apercu_legende, legende);
    }
}

/* --- Initialisation ------------------------------------------------------------- */

void ui_init(void)
{
    lv_obj_t *ecran = lv_screen_active();
    lv_obj_set_style_bg_color(ecran, C_FOND, 0);
    lv_obj_set_style_bg_opa(ecran, LV_OPA_COVER, 0);
    lv_obj_remove_flag(ecran, LV_OBJ_FLAG_SCROLLABLE);

    halo = creer_arc(ecran, TAILLE - 8, 40);
    anneau = creer_arc(ecran, TAILLE - 24, 14);

    segment_vide = creer_arc(ecran, TAILLE - 110, 20);
    lv_obj_set_style_arc_color(segment_vide, C_GRIS, LV_PART_INDICATOR);
    lv_obj_set_style_arc_opa(segment_vide, LV_OPA_40, LV_PART_INDICATOR);
    for (int i = 0; i < VIGIE_MAX_AGENTS; i++) {
        segments[i] = creer_arc(ecran, TAILLE - 110, 20);
        lv_obj_add_flag(segments[i], LV_OBJ_FLAG_HIDDEN);
    }

    titre = creer_label(ecran, &vigie_titre_28, C_ARDOISE);
    lv_obj_set_style_text_letter_space(titre, 6, 0);
    lv_obj_align(titre, LV_ALIGN_TOP_MID, 0, 175);

    phrase = creer_label(ecran, &vigie_phrase_40, C_TEXTE);
    lv_label_set_long_mode(phrase, LV_LABEL_LONG_WRAP);
    lv_obj_set_width(phrase, 540);
    lv_obj_set_style_text_line_space(phrase, 4, 0);
    lv_obj_align(phrase, LV_ALIGN_CENTER, 0, -40);

    liste = lv_obj_create(ecran);
    lv_obj_remove_style_all(liste);
    lv_obj_remove_flag(liste, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_set_size(liste, 540, LV_SIZE_CONTENT);
    lv_obj_set_flex_flow(liste, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_flex_align(liste, LV_FLEX_ALIGN_START, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    lv_obj_set_style_pad_row(liste, 8, 0);
    lv_obj_align(liste, LV_ALIGN_TOP_MID, 0, 515);
    for (int i = 0; i < LISTE_MAX; i++) {
        lignes[i] = lv_obj_create(liste);
        lv_obj_remove_style_all(lignes[i]);
        lv_obj_remove_flag(lignes[i], LV_OBJ_FLAG_CLICKABLE);
        lv_obj_set_size(lignes[i], LV_SIZE_CONTENT, LV_SIZE_CONTENT);
        lv_obj_set_flex_flow(lignes[i], LV_FLEX_FLOW_ROW);
        lv_obj_set_flex_align(lignes[i], LV_FLEX_ALIGN_START, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
        lv_obj_set_style_pad_column(lignes[i], 12, 0);

        pastilles[i] = lv_obj_create(lignes[i]);
        lv_obj_remove_style_all(pastilles[i]);
        lv_obj_remove_flag(pastilles[i], LV_OBJ_FLAG_CLICKABLE);
        lv_obj_set_size(pastilles[i], 14, 14);
        lv_obj_set_style_radius(pastilles[i], LV_RADIUS_CIRCLE, 0);
        lv_obj_set_style_bg_opa(pastilles[i], LV_OPA_COVER, 0);

        noms[i] = creer_label(lignes[i], &vigie_petit_24, C_DISCRET);
        defilant(noms[i], 420);
        lv_obj_set_style_text_align(noms[i], LV_TEXT_ALIGN_LEFT, 0);
        lv_obj_add_flag(lignes[i], LV_OBJ_FLAG_HIDDEN);
    }

    statut = creer_label(ecran, &vigie_petit_24, C_AMBRE);
    lv_obj_align(statut, LV_ALIGN_BOTTOM_MID, 0, -110);

    creer_courrier(ecran);
    creer_apercu(ecran);
    creer_fiche_courrier(ecran);

    lv_obj_add_flag(ecran, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_event_cb(ecran, sur_appui_long, LV_EVENT_SHORT_CLICKED, NULL); /* bref : volume */
    lv_obj_add_event_cb(ecran, sur_clic, LV_EVENT_LONG_PRESSED, NULL);        /* long : caméra */

    mention_son = creer_label(ecran, &vigie_petit_24, C_DISCRET);
    lv_label_set_text(mention_son, "son coupé");
    lv_obj_align(mention_son, LV_ALIGN_BOTTOM_MID, 0, -75);
    lv_obj_add_flag(mention_son, LV_OBJ_FLAG_HIDDEN);

    creer_reglage_volume(ecran);

    memset(&dernier_etat, 0, sizeof(dernier_etat));
    dernier_etat.humeur = VIGIE_CALME;
    ui_afficher_liaison(LIAISON_WIFI_ABSENT);
}

/* --- Mise à jour -------------------------------------------------------------- */

static void dessiner_segments(const vigie_etat_t *e)
{
    int n = e->nb_agents > VIGIE_MAX_AGENTS ? VIGIE_MAX_AGENTS : e->nb_agents;
    if (n <= 0) {
        lv_obj_remove_flag(segment_vide, LV_OBJ_FLAG_HIDDEN);
    } else {
        lv_obj_add_flag(segment_vide, LV_OBJ_FLAG_HIDDEN);
    }
    for (int i = 0; i < VIGIE_MAX_AGENTS; i++) {
        if (i >= n) {
            lv_obj_add_flag(segments[i], LV_OBJ_FLAG_HIDDEN);
            continue;
        }
        int part = 360 / n;
        int debut = i * part + (n > 1 ? ECART_DEG : 0);
        int fin = (i + 1) * part - (n > 1 ? ECART_DEG : 0);
        lv_arc_set_angles(segments[i], debut, fin);
        lv_obj_set_style_arc_color(segments[i], couleur_agent(e->agents[i].etat), LV_PART_INDICATOR);
        lv_obj_set_style_arc_opa(segments[i],
                                 e->agents[i].etat == AGENT_PRET ? LV_OPA_50 : LV_OPA_COVER,
                                 LV_PART_INDICATOR);
        lv_obj_remove_flag(segments[i], LV_OBJ_FLAG_HIDDEN);
    }
}

static void dessiner_liste(const vigie_etat_t *e)
{
    char texte[128];
    for (int i = 0; i < LISTE_MAX; i++) {
        if (i >= e->nb_agents) {
            lv_obj_add_flag(lignes[i], LV_OBJ_FLAG_HIDDEN);
            continue;
        }
        const vigie_agent_t *a = &e->agents[i];
        /* Boîte mail : son message (« 2 mails importants ») dit plus que l'état. */
        const char *detail = (!strcmp(a->source, "courrier") && a->message[0]) ? a->message
                                                                              : libelle_agent(a->etat);
        snprintf(texte, sizeof(texte), "%s : %s", a->projet, detail);
        lv_label_set_text(noms[i], texte);
        lv_obj_set_style_bg_color(pastilles[i], couleur_agent(e->agents[i].etat), 0);
        lv_obj_remove_flag(lignes[i], LV_OBJ_FLAG_HIDDEN);
    }
}

static void redessiner(void)
{
    if (liaison != LIAISON_OK || !etat_recu) {
        /* Sans nouvelles fraîches, la Vigie ne prétend rien : anneau gris, phrase d'attente. */
        const style_humeur_t hors_ligne = {"HORS LIGNE", C_GRIS, 4000, LV_OPA_60, LV_OPA_10};
        lv_obj_set_style_arc_color(anneau, hors_ligne.couleur, LV_PART_INDICATOR);
        lv_obj_set_style_arc_color(halo, hors_ligne.couleur, LV_PART_INDICATOR);
        lv_obj_set_style_text_color(titre, C_DISCRET, 0);
        lv_label_set_text(titre, liaison == LIAISON_OK ? "CONNEXION" : hors_ligne.titre);
        lv_label_set_text(phrase, liaison == LIAISON_OK ? "Connexion au Jetson…" : "Le Jetson ne répond plus.");
        lancer_respiration(&hors_ligne);
        vigie_etat_t vide = {0};
        dessiner_segments(&vide);
        dessiner_liste(&vide);
        dessiner_courrier();
        return;
    }
    dessiner_courrier();
    if (alerte && courrier.nb_detail > 0) {
        /* Un mail important vient d'arriver : il prend le centre un moment. */
        const style_humeur_t a = {"COURRIER", C_CORAIL, 700, LV_OPA_COVER, LV_OPA_30};
        char texte[200];
        texte_mail(texte, sizeof(texte), &courrier.mails[0]);
        lv_obj_set_style_arc_color(anneau, a.couleur, LV_PART_INDICATOR);
        lv_obj_set_style_arc_color(halo, a.couleur, LV_PART_INDICATOR);
        lv_obj_set_style_text_color(titre, a.couleur, 0);
        lv_label_set_text(titre, a.titre);
        lv_label_set_text(phrase, texte);
        lancer_respiration(&a);
        dessiner_segments(&dernier_etat);
        dessiner_liste(&dernier_etat);
        return;
    }
    style_humeur_t s = style_de(dernier_etat.humeur);
    lv_obj_set_style_arc_color(anneau, s.couleur, LV_PART_INDICATOR);
    lv_obj_set_style_arc_color(halo, s.couleur, LV_PART_INDICATOR);
    lv_obj_set_style_text_color(titre, s.couleur, 0);
    lv_label_set_text(titre, s.titre);
    lancer_respiration(&s);
    dessiner_segments(&dernier_etat);
    dessiner_liste(&dernier_etat);
    lv_label_set_text(phrase, dernier_etat.phrase[0] ? dernier_etat.phrase : "Rien à signaler.");
}

void ui_afficher_etat(const vigie_etat_t *etat)
{
    bool phrase_changee = strcmp(etat->phrase, dernier_etat.phrase) != 0;
    dernier_etat = *etat;
    etat_recu = true;
    redessiner();
    if (liaison == LIAISON_OK && !alerte) {
        if (phrase_changee) {
            lv_obj_fade_in(phrase, 400, 0);
        }
    }
}

void ui_afficher_liaison(vigie_liaison_t l)
{
    liaison = l;
    switch (l) {
    case LIAISON_WIFI_ABSENT:    lv_label_set_text(statut, "Connexion au Wi-Fi…"); break;
    case LIAISON_BROKER_ABSENT:  lv_label_set_text(statut, "Serveur MQTT injoignable"); break;
    case LIAISON_CERVEAU_ABSENT: lv_label_set_text(statut, "Service Vigie arrêté sur le Jetson"); break;
    case LIAISON_OK:             lv_label_set_text(statut, ""); break;
    }
    redessiner();
}

void ui_afficher_courrier(const vigie_courrier_t *c, bool nouveau)
{
    courrier = *c;
    courrier_recu_tick = lv_tick_get();
    if (fiche_ouverte()) {
        if (courrier.nb_detail > 0) {
            remplir_fiche();
        } else {
            fermer_fiche();
        }
    }
    if (nouveau && c->nb_detail > 0) {
        alerte = true;
        if (minuterie_alerte) {
            lv_timer_reset(minuterie_alerte);
        } else {
            minuterie_alerte = lv_timer_create(fin_alerte, ALERTE_MS, NULL);
            lv_timer_set_repeat_count(minuterie_alerte, 1);
        }
    } else if (c->nb == 0 && alerte) {
        /* Lu entre-temps : l'alerte n'a plus lieu d'être. */
        if (minuterie_alerte) {
            lv_timer_delete(minuterie_alerte);
            minuterie_alerte = NULL;
        }
        alerte = false;
    }
    redessiner();
    if (nouveau && alerte && liaison == LIAISON_OK) {
        lv_obj_fade_in(phrase, 400, 0);
    }
}

/* --- Réglage du volume ----------------------------------------------------------
 *
 * Un toucher bref ouvre un voile sombre avec un grand arc à faire glisser du doigt,
 * la valeur au centre et un bouton pour couper le son. Il se referme tout seul
 * après quelques secondes sans geste, ou d'un toucher hors de l'arc.
 */

#define FERMETURE_MS 6000

static ui_volume_cb_t rappel_volume = NULL;

static lv_obj_t *voile, *arc_vol, *valeur_vol, *bouton_muet, *texte_muet;
static lv_timer_t *minuterie;
static int volume_actuel = 60;
static int volume_avant_muet = 60;

static void maj_reglage(void)
{
    lv_label_set_text_fmt(valeur_vol, "%d %%", volume_actuel);
    lv_label_set_text(texte_muet, volume_actuel == 0 ? "Remettre le son" : "Couper le son");
    lv_obj_set_style_arc_color(arc_vol, volume_actuel == 0 ? C_GRIS : C_BLEU, LV_PART_INDICATOR);
    if (volume_actuel == 0) {
        lv_obj_remove_flag(mention_son, LV_OBJ_FLAG_HIDDEN);
    } else {
        lv_obj_add_flag(mention_son, LV_OBJ_FLAG_HIDDEN);
    }
}

static void fermer_reglage(void)
{
    lv_obj_add_flag(voile, LV_OBJ_FLAG_HIDDEN);
    lv_timer_pause(minuterie);
}

static void relancer_minuterie(void)
{
    lv_timer_reset(minuterie);
    lv_timer_resume(minuterie);
}

static void sur_minuterie(lv_timer_t *t)
{
    (void)t;
    fermer_reglage();
}

static void sur_arc(lv_event_t *e)
{
    lv_event_code_t code = lv_event_get_code(e);
    volume_actuel = lv_arc_get_value(arc_vol);
    if (volume_actuel > 0) {
        volume_avant_muet = volume_actuel;
    }
    maj_reglage();
    relancer_minuterie();
    if (rappel_volume) {
        rappel_volume(volume_actuel, code == LV_EVENT_RELEASED);
    }
}

static void sur_muet(lv_event_t *e)
{
    (void)e;
    volume_actuel = volume_actuel == 0 ? (volume_avant_muet > 0 ? volume_avant_muet : 50) : 0;
    lv_arc_set_value(arc_vol, volume_actuel);
    maj_reglage();
    relancer_minuterie();
    if (rappel_volume) {
        rappel_volume(volume_actuel, true);
    }
}

static void sur_voile(lv_event_t *e)
{
    /* seul un toucher sur le voile lui-même ferme (pas sur l'arc ni le bouton) */
    if (lv_event_get_target(e) == voile) {
        fermer_reglage();
    }
}

static void sur_appui_long(lv_event_t *e)
{
    (void)e;
    if (alerte && courrier.nb_detail > 0) {
        ui_ouvrir_courrier();    /* pendant l'alerte, on veut savoir ce qui l'a déclenchée */
    } else {
        ui_ouvrir_volume();
    }
}

void ui_sur_volume(ui_volume_cb_t rappel)
{
    rappel_volume = rappel;
}

static void creer_reglage_volume(lv_obj_t *ecran)
{
    voile = lv_obj_create(ecran);
    lv_obj_remove_style_all(voile);
    lv_obj_set_size(voile, TAILLE, TAILLE);
    lv_obj_center(voile);
    lv_obj_set_style_radius(voile, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_bg_color(voile, C_FOND, 0);
    lv_obj_set_style_bg_opa(voile, LV_OPA_COVER, 0);
    lv_obj_add_flag(voile, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_remove_flag(voile, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_event_cb(voile, sur_voile, LV_EVENT_CLICKED, NULL);

    arc_vol = lv_arc_create(voile);
    lv_obj_set_size(arc_vol, 620, 620);
    lv_obj_center(arc_vol);
    lv_arc_set_rotation(arc_vol, 0);
    lv_arc_set_bg_angles(arc_vol, 135, 45);          /* 270°, ouvert en bas */
    lv_arc_set_range(arc_vol, 0, 100);
    lv_obj_set_style_arc_width(arc_vol, 30, LV_PART_MAIN);
    lv_obj_set_style_arc_width(arc_vol, 30, LV_PART_INDICATOR);
    lv_obj_set_style_arc_color(arc_vol, lv_color_hex(0x1B2230), LV_PART_MAIN);
    lv_obj_set_style_arc_rounded(arc_vol, true, LV_PART_MAIN);
    lv_obj_set_style_arc_rounded(arc_vol, true, LV_PART_INDICATOR);
    lv_obj_set_style_bg_color(arc_vol, C_TEXTE, LV_PART_KNOB);
    lv_obj_set_style_pad_all(arc_vol, 12, LV_PART_KNOB);
    lv_obj_add_event_cb(arc_vol, sur_arc, LV_EVENT_VALUE_CHANGED, NULL);
    lv_obj_add_event_cb(arc_vol, sur_arc, LV_EVENT_RELEASED, NULL);

    lv_obj_t *etiquette = creer_label(voile, &vigie_titre_28, C_DISCRET);
    lv_obj_set_style_text_letter_space(etiquette, 6, 0);
    lv_label_set_text(etiquette, "VOLUME");
    lv_obj_align(etiquette, LV_ALIGN_CENTER, 0, -100);

    valeur_vol = creer_label(voile, &vigie_chiffres_96, C_TEXTE);
    lv_obj_align(valeur_vol, LV_ALIGN_CENTER, 0, 0);

    bouton_muet = lv_obj_create(voile);
    lv_obj_remove_style_all(bouton_muet);
    lv_obj_set_size(bouton_muet, LV_SIZE_CONTENT, LV_SIZE_CONTENT);
    lv_obj_set_style_pad_hor(bouton_muet, 28, 0);
    lv_obj_set_style_pad_ver(bouton_muet, 14, 0);
    lv_obj_set_style_radius(bouton_muet, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_border_width(bouton_muet, 2, 0);
    lv_obj_set_style_border_color(bouton_muet, C_DISCRET, 0);
    lv_obj_set_style_bg_color(bouton_muet, lv_color_hex(0x1B2230), LV_STATE_PRESSED);
    lv_obj_set_style_bg_opa(bouton_muet, LV_OPA_COVER, LV_STATE_PRESSED);
    lv_obj_add_flag(bouton_muet, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_align(bouton_muet, LV_ALIGN_CENTER, 0, 120);
    lv_obj_add_event_cb(bouton_muet, sur_muet, LV_EVENT_CLICKED, NULL);
    texte_muet = creer_label(bouton_muet, &vigie_petit_24, C_TEXTE);

    minuterie = lv_timer_create(sur_minuterie, FERMETURE_MS, NULL);
    lv_timer_pause(minuterie);

    lv_arc_set_value(arc_vol, volume_actuel);
    maj_reglage();
    lv_obj_add_flag(voile, LV_OBJ_FLAG_HIDDEN);
}

void ui_afficher_volume(int volume)
{
    volume_actuel = volume < 0 ? 0 : volume > 100 ? 100 : volume;
    if (volume_actuel > 0) {
        volume_avant_muet = volume_actuel;
    }
    lv_arc_set_value(arc_vol, volume_actuel);
    maj_reglage();
}

void ui_ouvrir_volume(void)
{
    lv_obj_remove_flag(voile, LV_OBJ_FLAG_HIDDEN);
    lv_obj_move_to_index(voile, -1);
    relancer_minuterie();
}

/* --- Fiche courrier ---------------------------------------------------------------
 *
 * Le détail des mails importants non lus : pour chacun, l'expéditeur, la boîte et l'âge
 * (en corail), le résumé du Jetson (en blanc, deux lignes), l'objet (en gris).
 */

#define FICHE_MS 30000

static lv_obj_t *fiche, *fiche_titre, *fiche_plus;
static lv_obj_t *fiche_cartes[VIGIE_MAX_MAILS], *fiche_tete[VIGIE_MAX_MAILS];
static lv_obj_t *fiche_resume[VIGIE_MAX_MAILS], *fiche_objet[VIGIE_MAX_MAILS];
static lv_timer_t *minuterie_fiche;

static void age_texte(char *dst, size_t taille, int age_s)
{
    if (age_s < 0) {
        dst[0] = '\0';
    } else if (age_s < 90) {
        snprintf(dst, taille, " · à l'instant");
    } else if (age_s < 3600) {
        snprintf(dst, taille, " · il y a %d min", age_s / 60);
    } else if (age_s < 48 * 3600) {
        snprintf(dst, taille, " · il y a %d h", age_s / 3600);
    } else {
        snprintf(dst, taille, " · il y a %d j", age_s / 86400);
    }
}

static bool fiche_ouverte(void)
{
    return fiche && !lv_obj_has_flag(fiche, LV_OBJ_FLAG_HIDDEN);
}

static void fermer_fiche(void)
{
    lv_obj_add_flag(fiche, LV_OBJ_FLAG_HIDDEN);
    lv_timer_pause(minuterie_fiche);
}

static void sur_fiche(lv_event_t *e)
{
    (void)e;
    fermer_fiche();
}

static void sur_minuterie_fiche(lv_timer_t *t)
{
    (void)t;
    fermer_fiche();
}

static void sur_toucher_courrier(lv_event_t *e)
{
    (void)e;
    if (courrier.nb_detail > 0) {
        ui_ouvrir_courrier();
    }
}

static void creer_fiche_courrier(lv_obj_t *ecran)
{
    fiche = lv_obj_create(ecran);
    lv_obj_remove_style_all(fiche);
    lv_obj_set_size(fiche, TAILLE, TAILLE);
    lv_obj_center(fiche);
    lv_obj_set_style_radius(fiche, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_bg_color(fiche, C_FOND, 0);
    lv_obj_set_style_bg_opa(fiche, LV_OPA_COVER, 0);
    lv_obj_set_style_border_width(fiche, 6, 0);
    lv_obj_set_style_border_color(fiche, C_CORAIL, 0);
    lv_obj_set_style_border_opa(fiche, LV_OPA_50, 0);
    lv_obj_add_flag(fiche, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_remove_flag(fiche, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_event_cb(fiche, sur_fiche, LV_EVENT_CLICKED, NULL);

    fiche_titre = creer_label(fiche, &vigie_titre_28, C_CORAIL);
    lv_obj_set_style_text_letter_space(fiche_titre, 4, 0);
    lv_obj_align(fiche_titre, LV_ALIGN_TOP_MID, 0, 120);

    lv_obj_t *colonne = lv_obj_create(fiche);
    lv_obj_remove_style_all(colonne);
    lv_obj_remove_flag(colonne, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_remove_flag(colonne, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_size(colonne, 600, LV_SIZE_CONTENT);
    lv_obj_set_flex_flow(colonne, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_flex_align(colonne, LV_FLEX_ALIGN_START, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    lv_obj_set_style_pad_row(colonne, 22, 0);
    lv_obj_align(colonne, LV_ALIGN_TOP_MID, 0, 180);

    for (int i = 0; i < VIGIE_MAX_MAILS; i++) {
        lv_obj_t *c = lv_obj_create(colonne);
        lv_obj_remove_style_all(c);
        lv_obj_add_flag(c, LV_OBJ_FLAG_CLICKABLE);
        lv_obj_remove_flag(c, LV_OBJ_FLAG_SCROLLABLE);
        lv_obj_set_style_radius(c, 18, 0);
        lv_obj_set_style_pad_ver(c, 6, 0);
        lv_obj_set_style_bg_color(c, lv_color_hex(0x1B2230), LV_STATE_PRESSED);
        lv_obj_set_style_bg_opa(c, LV_OPA_COVER, LV_STATE_PRESSED);
        lv_obj_add_event_cb(c, sur_carte, LV_EVENT_CLICKED, (void *)(intptr_t)i);
        lv_obj_set_size(c, i == 0 ? 600 : 560, LV_SIZE_CONTENT);
        lv_obj_set_flex_flow(c, LV_FLEX_FLOW_COLUMN);
        lv_obj_set_flex_align(c, LV_FLEX_ALIGN_START, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
        lv_obj_set_style_pad_row(c, 4, 0);
        fiche_cartes[i] = c;

        fiche_tete[i] = creer_label(c, &vigie_petit_24, C_CORAIL);
        defilant(fiche_tete[i], i == 0 ? 600 : 560);

        fiche_resume[i] = creer_label(c, &vigie_titre_28, C_TEXTE);
        lv_obj_set_width(fiche_resume[i], lv_pct(100));
        lv_label_set_long_mode(fiche_resume[i], LV_LABEL_LONG_DOT);
        lv_obj_set_height(fiche_resume[i], 2 * lv_font_get_line_height(&vigie_titre_28));

        fiche_objet[i] = creer_label(c, &vigie_petit_24, C_DISCRET);
        defilant(fiche_objet[i], i == 0 ? 600 : 560);
    }

    fiche_plus = creer_label(fiche, &vigie_petit_24, C_DISCRET);
    lv_obj_align(fiche_plus, LV_ALIGN_BOTTOM_MID, 0, -78);

    minuterie_fiche = lv_timer_create(sur_minuterie_fiche, FICHE_MS, NULL);
    lv_timer_pause(minuterie_fiche);
    lv_obj_add_flag(fiche, LV_OBJ_FLAG_HIDDEN);
}

static void (*rappel_vu)(const char *id) = NULL;

void ui_sur_mail_vu(void (*rappel)(const char *id))
{
    rappel_vu = rappel;
}

/* Remplit la fiche avec le contenu actuel de `courrier`. */
static void remplir_fiche(void)
{
    int ecoule_s = (int)(lv_tick_elaps(courrier_recu_tick) / 1000);
    lv_label_set_text_fmt(fiche_titre, courrier.nb > 1 ? "%d MAILS IMPORTANTS" : "%d MAIL IMPORTANT",
                          courrier.nb);
    for (int i = 0; i < VIGIE_MAX_MAILS; i++) {
        if (i >= courrier.nb_detail) {
            lv_obj_add_flag(fiche_cartes[i], LV_OBJ_FLAG_HIDDEN);
            continue;
        }
        const vigie_mail_t *m = &courrier.mails[i];
        char age[32], tete[160], objet[128];
        age_texte(age, sizeof(age), m->age_s < 0 ? -1 : m->age_s + ecoule_s);
        snprintf(tete, sizeof(tete), "%s%s%s%s", m->de[0] ? m->de : "Expéditeur inconnu",
                 m->compte[0] ? " · " : "", m->compte, age);
        lv_label_set_text(fiche_tete[i], tete);
        lv_label_set_text(fiche_resume[i], m->resume[0] ? m->resume : m->sujet);
        snprintf(objet, sizeof(objet), "Objet : %s", m->sujet[0] ? m->sujet : "(sans objet)");
        lv_label_set_text(fiche_objet[i], objet);
        lv_obj_remove_flag(fiche_cartes[i], LV_OBJ_FLAG_HIDDEN);
    }
    int autres = courrier.nb - courrier.nb_detail;
    if (autres > 0) {
        lv_label_set_text_fmt(fiche_plus, autres > 1 ? "et %d autres · toucher un mail : vu"
                                                     : "et %d autre · toucher un mail : vu", autres);
    } else {
        lv_label_set_text(fiche_plus, "Toucher un mail : vu · ailleurs : fermer");
    }
}

/* Toucher une carte : le mail est marqué vu, retiré tout de suite de l'écran. */
static void sur_carte(lv_event_t *e)
{
    int i = (int)(intptr_t)lv_event_get_user_data(e);
    if (i < 0 || i >= courrier.nb_detail) {
        return;
    }
    char id[sizeof(courrier.mails[0].id)];
    snprintf(id, sizeof(id), "%s", courrier.mails[i].id);
    for (int j = i; j < courrier.nb_detail - 1; j++) {
        courrier.mails[j] = courrier.mails[j + 1];
    }
    courrier.nb_detail--;
    courrier.nb = courrier.nb > 0 ? courrier.nb - 1 : 0;
    if (courrier.nb == 0) {
        alerte = false;
        if (minuterie_alerte) {
            lv_timer_delete(minuterie_alerte);
            minuterie_alerte = NULL;
        }
    }
    if (rappel_vu && id[0]) {
        rappel_vu(id);
    }
    redessiner();
    if (courrier.nb_detail == 0) {
        fermer_fiche();
    } else {
        remplir_fiche();
        lv_timer_reset(minuterie_fiche);
    }
}

void ui_ouvrir_courrier(void)
{
    if (courrier.nb_detail <= 0) {
        return;
    }
    remplir_fiche();
    lv_obj_remove_flag(fiche, LV_OBJ_FLAG_HIDDEN);
    lv_obj_move_to_index(fiche, -1);
    lv_timer_reset(minuterie_fiche);
    lv_timer_resume(minuterie_fiche);
}
