/*
 * Vigie, Wi-Fi par la puce ESP32-C6 de la carte (esp_wifi_remote).
 *
 * Le réseau choisi sur l'écran est gardé en NVS (espace « vigie », clés wifi_ssid et
 * wifi_mdp). Tant qu'aucun réseau n'y est enregistré, on prend celui du sdkconfig.
 * Les rappels sont appelés depuis les tâches du Wi-Fi, sans le verrou d'affichage.
 */
#pragma once

#include <stdbool.h>
#include "ui.h"

typedef struct {
    void (*connexion)(bool connecte, const char *ssid);         /* réseau trouvé ou perdu */
    void (*reseaux)(const ui_reseau_t *reseaux, int nb);        /* nb < 0 : recherche ratée */
    void (*essai)(ui_wifi_t etat, const char *ssid, const char *detail);
} wifi_rappels_t;

void wifi_demarrer(const wifi_rappels_t *rappels);

/* Ne bloquent pas : la recherche et l'essai se font dans la tâche du Wi-Fi. */
void wifi_chercher(void);
/* Essaie ce réseau ; s'il répond, il est gardé, sinon on revient à l'ancien. */
void wifi_choisir(const char *ssid, const char *mdp);
