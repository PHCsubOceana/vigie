/* Vigie — services communs fournis par main.c. */
#pragma once

#include <stdbool.h>

/* Publie un message MQTT si le broker est joignable (sinon, il est perdu).
 * Utilisable depuis n'importe quelle tâche : ne bloque pas. */
void vigie_publier(const char *sujet, const char *texte, bool retenu);
