/*
 * Vigie : les yeux (module Waveshare 0,71" DualEye, deux écrans GC9D01 de 160 x 160).
 *
 * Les yeux sont dessinés par le programme, sans image : l'iris prend la couleur de
 * l'humeur, le regard suit le visage vu par la caméra, les paupières clignent.
 * Toutes ces fonctions rendent la main tout de suite et s'appellent depuis n'importe
 * quelle tâche.
 */
#pragma once

#include <stdbool.h>

typedef enum {
    YEUX_CALME,
    YEUX_ATTENTIVE,
    YEUX_AFFAIREE,
    YEUX_SATISFAITE,
} yeux_humeur_t;

/* Démarre l'affichage dans sa propre tâche. Sans module branché, rien ne bloque. */
void yeux_demarrer(void);

void yeux_humeur(yeux_humeur_t humeur);

/* Liaison avec le Jetson : sans elle, les yeux deviennent gris et mi-clos. */
void yeux_liaison(bool ok);

/* Visage vu par la caméra : x et y de -1 à +1 dans l'image, taille de 0 à 1. */
void yeux_regard(bool visage, float x, float y, float taille);

/* Quelqu'un est là ou non. Personne et rien à signaler : les yeux s'assoupissent. */
void yeux_presence(bool present);

/* Essai : regard fixe pendant 30 s, x de -1 (à gauche vu de face) à +1 (à droite).
 * actif = false rend le regard au suivi du visage. */
void yeux_fixer(bool actif, float x);

/* Pour mesurer : un point au centre de chaque écran pendant 3 min (false : retour normal). */
void yeux_repere(bool actif);

/* Mire de contrôle pendant 5 s : œil 1 rouge, œil 2 bleu, bande blanche en haut. */
void yeux_mire(void);
