/*
 * Vigie — petits sons synthétisés (aucun fichier audio), joués par le codec ES8311
 * et le haut-parleur de la carte. La lecture se fait dans une tâche à part :
 * son_jouer() rend la main tout de suite.
 */
#pragma once

typedef enum {
    SON_ATTENTE,     /* un agent attend ta réponse : deux notes montantes */
    SON_MAIL,        /* un mail important (source « mail ») : trois notes vives */
    SON_FINI,        /* une tâche terminée : petit arpège montant */
    SON_HORS_LIGNE,  /* le Jetson ou le réseau ne répond plus : deux notes descendantes */
    SON_TEST,        /* une note, pour régler le volume */
} son_t;

void son_init(int volume);        /* volume 0..100 ; 0 = muet */
void son_volume(int volume);
void son_jouer(son_t son);
