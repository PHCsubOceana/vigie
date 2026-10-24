#!/usr/bin/env bash
# Vigie — hook Claude Code : transmet l'événement au Jetson en précisant le compte
# Claude et le poste. Claude Code le lance avec Git Bash, même sous Windows.
#
#   Usage (dans settings.json) : bash ~/.vigie/vigie-hook.sh <compte>
#
# Réglages dans ~/.vigie : jeton (obligatoire), poste (nom court du PC, facultatif),
# url (adresse du Jetson, écrite par installer-postes.sh).
# Ne bloque jamais Claude Code : 2 s au plus, et toujours « réussi ».

COMPTE="${1:-claude}"
DIR="$HOME/.vigie"
JETON="$(cat "$DIR/jeton" 2>/dev/null | tr -d '\r\n')"
POSTE="$(cat "$DIR/poste" 2>/dev/null | tr -d '\r\n')"
URL="$(cat "$DIR/url" 2>/dev/null | tr -d '\r\n')"
URL="${URL:-http://192.168.1.50:8765/event}"

ev="$(cat)"

# Dossier de travail → nom du projet (le dernier élément du chemin).
cwd="$(printf '%s' "$ev" | sed -n 's/.*"cwd"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n 1)"
cwd="${cwd//\\\\//}"          # C:\\Users\\x  →  C:/Users/x
cwd="${cwd%/}"
dossier="${cwd##*/}"
dossier="${dossier:-sans projet}"

etiquette="$dossier · $COMPTE"
[ -n "$POSTE" ] && etiquette="$etiquette · $POSTE"
# Ni caractères de contrôle (flèches tapées au clavier…), ni \ ni " : JSON valide.
etiquette="$(printf '%s' "$etiquette" | sed 's/\x1b\[[0-9;]*[A-Za-z]//g' | tr -d '[:cntrl:]')"
etiquette="${etiquette//\\/}"          # pas de \ ni de " dans une chaîne JSON
etiquette="${etiquette//\"/}"
etiquette_sed="${etiquette//&/\\&}"    # & a un sens pour sed

# vigie.py prend le champ « projet » tel quel s'il est présent : on l'ajoute en tête.
ev="$(printf '%s' "$ev" | sed '0,/{/s//{"projet":"'"$etiquette_sed"'",/')"

# Le corps passe par l'entrée standard, pas par la ligne de commande : sous Windows,
# Git Bash convertit les arguments vers la page de code locale et abîmerait « · » et
# les accents (UTF-8 invalide → événement refusé par le Jetson).
code="$(printf '%s' "$ev" | curl -s --connect-timeout 1 -m 2 -o /dev/null -w '%{http_code}' \
  -X POST "$URL" \
  -H "Content-Type: application/json; charset=utf-8" \
  -H "X-Vigie-Token: $JETON" \
  --data-binary @- 2>/dev/null)"

# Message refusé : on le garde pour le diagnostic.
if [ "$code" != "200" ]; then
  printf '%s' "$ev" > "$DIR/dernier-refus.json" 2>/dev/null
fi

# Dernier envoi, pour le diagnostic (écrasé à chaque fois).
printf '%s  %s  HTTP %s  %s\n' "$(date '+%F %T')" "$COMPTE" "${code:-000}" "$etiquette" \
  > "$DIR/dernier-envoi.txt" 2>/dev/null
exit 0
