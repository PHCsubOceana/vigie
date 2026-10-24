#!/usr/bin/env bash
# Simule une session Claude Code et un agent générique.
# Usage : bash test-vigie.sh [adresse:port] [jeton]
#   ex. sur le Jetson : bash test-vigie.sh localhost:8765 "$(grep VIGIE_TOKEN vigie.env | cut -d= -f2)"
# Dans un autre terminal, regarde ce que reçoit l'écran :
#   mosquitto_sub -h localhost -u vigie -P <motdepasse> -t 'vigie/#' -v
set -u
CIBLE="${1:-localhost:8765}"
JETON="${2:-}"

envoyer() {
  echo "→ $1"
  curl -s -X POST "http://$CIBLE/event" -H "Content-Type: application/json" \
       -H "X-Vigie-Token: $JETON" -d "$2"; echo
  sleep "${3:-3}"
}

S='"session_id":"test-site-123","cwd":"/home/camille/mon-site"'
envoyer "Session ouverte"            "{$S,\"hook_event_name\":\"SessionStart\"}"
envoyer "Claude se met au travail"   "{$S,\"hook_event_name\":\"UserPromptSubmit\"}"
envoyer "Un autre agent travaille"   '{"source":"script","agent":"sauvegarde","projet":"sauvegarde VPS","etat":"travaille"}'
envoyer "Claude demande une permission" \
        "{$S,\"hook_event_name\":\"Notification\",\"notification_type\":\"permission_prompt\",\"message\":\"Claude needs your permission to use Bash\"}" 6
envoyer "Permission accordée, il reprend" "{$S,\"hook_event_name\":\"PostToolUse\"}"
envoyer "Claude a fini"              "{$S,\"hook_event_name\":\"Stop\"}" 6
envoyer "La sauvegarde est finie"    '{"source":"script","agent":"sauvegarde","projet":"sauvegarde VPS","etat":"fini"}' 6

echo; echo "État final :"
curl -s "http://$CIBLE/etat"; echo
envoyer "Fermeture de la session"    "{$S,\"hook_event_name\":\"SessionEnd\"}" 0
