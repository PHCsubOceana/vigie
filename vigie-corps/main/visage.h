/*
 * Vigie — détection de visage (ESP-DL, modèle human_face_detect d'Espressif).
 * Si le composant n'est pas installé, ces fonctions répondent « indisponible »
 * et le reste du firmware fonctionne normalement.
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    int x1, y1, x2, y2;    /* cadre, en pixels de l'image analysée */
    float score;           /* confiance, 0..1 */
} visage_t;

bool visage_disponible(void);

/* Image RGB565 (petit-boutiste, comme la caméra). Renvoie le nombre de visages
 * écrits dans `res` (au plus `max`), du plus sûr au moins sûr ; -1 si indisponible. */
int visage_detecter(const uint8_t *rgb565, int largeur, int hauteur, visage_t *res, int max);

#ifdef __cplusplus
}
#endif
