/*
 * Vigie — caméra OV5647 (MIPI-CSI) : aperçu à l'écran, visage, présence.
 *
 * MQTT publié (par main.c, via vigie_publier) :
 *   vigie/regard   (non retenu, 5 fois par seconde au plus tant qu'un visage est vu)
 *                  {"visage":true,"x":-0.12,"y":0.05,"taille":0.21,"score":0.93}
 *                  x, y : centre du visage dans l'image de la caméra, de -1 à +1
 *                  (x > 0 : à droite de l'image ; y > 0 : en bas) ;
 *                  taille : largeur du visage / largeur de l'image (≈ distance).
 *                  {"visage":false} une fois quand le visage disparaît.
 *   vigie/presence (retenu) "present" / "absent" : quelqu'un devant le robot ou non.
 */
#pragma once

#include <stdbool.h>
#include "esp_err.h"

/* Démarre la caméra et sa tâche. À appeler après bsp_display_start(). */
esp_err_t camera_demarrer(void);

bool camera_ok(void);

/* Demande l'ouverture ou la fermeture de l'aperçu ; appliqué par la tâche caméra
 * à l'image suivante (ne prend pas le verrou d'affichage). */
void camera_apercu(bool ouvert);
bool camera_apercu_ouvert(void);

/* À appeler quand MQTT se (re)connecte : republie la présence. */
void camera_sur_connexion(void);
