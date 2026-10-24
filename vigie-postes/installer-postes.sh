#!/usr/bin/env bash
# Vigie — branche les comptes Claude Code de CE poste sur la Vigie.
#
# À lancer dans Git Bash (installé avec Claude Code), sur chaque PC :
#     bash installer-postes.sh
#
# - copie le hook dans ~/.vigie/ et y range le jeton et le nom du poste ;
# - pour chaque dossier de configuration (~/.claude, ~/.claude-pro, …), demande une
#   étiquette de compte et y installe les six hooks (copie de sauvegarde avant toute
#   modification, contrôle du JSON après, retour à la sauvegarde si le contrôle échoue) ;
# - envoie un événement d'essai : l'agent « essai » apparaît 10 s sur l'écran rond.

set -u
DIR="$HOME/.vigie"
ICI="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$DIR"
cp "$ICI/vigie-hook.sh" "$DIR/vigie-hook.sh"
chmod +x "$DIR/vigie-hook.sh" 2>/dev/null

# --- Jeton ------------------------------------------------------------------------------
jeton="$(cat "$DIR/jeton" 2>/dev/null | tr -d '\r\n')"
if [ -z "$jeton" ]; then
  jeton="$(grep -ho 'X-Vigie-Token: [A-Za-z0-9]*' "$HOME"/.claude*/settings.json 2>/dev/null \
           | head -n 1 | sed 's/.*: //')"
  [ -n "$jeton" ] && echo "Jeton repris des hooks déjà installés."
fi
while [ -z "$jeton" ]; do
  read -erp "Jeton de la Vigie (VIGIE_TOKEN dans ~/vigie/vigie.env sur le Jetson) : " jeton
done
printf '%s\n' "$jeton" > "$DIR/jeton"

# --- Adresse du Jetson -------------------------------------------------------------------
defaut="$(cat "$DIR/url" 2>/dev/null | tr -d '\r\n' | sed -E 's#^https?://([^:/]+).*#\1#')"
while :; do
  if [ -n "$defaut" ]; then
    read -erp "Adresse IP du Jetson [$defaut] : " ip; ip="${ip:-$defaut}"
  else
    read -erp "Adresse IP du Jetson (ex. 192.168.1.50) : " ip
  fi
  ip="$(printf '%s' "$ip" | tr -cd 'A-Za-z0-9.-')"
  [ -n "$ip" ] && break
done
printf 'http://%s:8765/event\n' "$ip" > "$DIR/url"

# --- Poste ------------------------------------------------------------------------------
defaut="$(cat "$DIR/poste" 2>/dev/null | tr -d '\r\n')"
defaut="${defaut:-$(hostname)}"
read -erp "Nom court de ce poste, affiché sur l'écran (ex. bureau, salon) [$defaut] : " poste
poste="${poste:-$defaut}"
poste="$(printf '%s' "$poste" | sed 's/\x1b\[[0-9;]*[A-Za-z]//g' | tr -d '[:cntrl:]')"
printf '%s\n' "$poste" > "$DIR/poste"

# --- Hooks ------------------------------------------------------------------------------
json_valide() {
  local f="$1"
  if command -v powershell.exe > /dev/null 2>&1; then
    local w; w="$(cygpath -w "$f" 2>/dev/null || echo "$f")"
    powershell.exe -NoProfile -Command \
      "try { Get-Content -Raw -Encoding UTF8 -LiteralPath '$w' | ConvertFrom-Json | Out-Null; exit 0 } catch { exit 1 }" \
      > /dev/null 2>&1
  elif command -v python3 > /dev/null 2>&1; then
    python3 -m json.tool "$f" > /dev/null 2>&1
  else
    echo "  (aucun outil pour contrôler le JSON : vérifie avec /hooks dans Claude Code)"
    return 0
  fi
}

bloc_hooks() {
  local h="{\"type\": \"command\", \"command\": \"bash ~/.vigie/vigie-hook.sh $1\", \"timeout\": 5}"
  printf '"hooks": {'
  printf '"SessionStart": [{"hooks": [%s]}], ' "$h"
  printf '"UserPromptSubmit": [{"hooks": [%s]}], ' "$h"
  printf '"PostToolUse": [{"matcher": "*", "hooks": [%s]}], ' "$h"
  printf '"Notification": [{"hooks": [%s]}], ' "$h"
  printf '"Stop": [{"hooks": [%s]}], ' "$h"
  printf '"SessionEnd": [{"hooks": [%s]}]}' "$h"
}

installer_hooks() {
  local conf="$1" compte="$2" f="$1/settings.json"
  local cmd="bash ~/.vigie/vigie-hook.sh $compte"
  if [ ! -s "$f" ] || [ -z "$(tr -d '[:space:]{}' < "$f")" ]; then
    printf '{\n  %s\n}\n' "$(bloc_hooks "$compte")" > "$f"
    echo "  settings.json créé avec les hooks."
  else
    local sauvegarde="$f.avant-vigie-$(date +%Y%m%d-%H%M%S)"
    cp "$f" "$sauvegarde"
    if grep -q '8765/event\|vigie-hook\.sh' "$f"; then
      # Hooks Vigie déjà présents : on remplace seulement leur commande.
      sed -E -i 's#"command"[[:space:]]*:[[:space:]]*"([^"\\]|\\.)*(8765/event|vigie-hook\.sh)([^"\\]|\\.)*"#"command": "'"$cmd"'"#g' "$f"
      echo "  commandes des hooks Vigie remplacées."
    elif grep -q '"hooks"' "$f"; then
      echo "  ⚠ Ce settings.json a déjà d'autres hooks : je n'y touche pas."
      echo "    Ajoute à la main, dans « hooks », la commande : $cmd"
      echo "    (pour les six événements, voir README.md)."
      rm -f "$sauvegarde"
      return
    else
      local bloc; bloc="$(bloc_hooks "$compte")"
      awk -v bloc="$bloc" '!fait && /\{/ { sub(/\{/, "{\n  " bloc ","); fait = 1 } { print }' \
        "$sauvegarde" > "$f"
      echo "  hooks ajoutés."
    fi
    if json_valide "$f"; then
      echo "  JSON vérifié. Sauvegarde : $(basename "$sauvegarde")"
    else
      cp "$sauvegarde" "$f"
      echo "  ⚠ JSON invalide après modification : fichier d'origine remis en place."
      return
    fi
  fi
}

trouve=0
for conf in "$HOME/.claude" "$HOME"/.claude-*; do
  [ -d "$conf" ] || continue
  nom="$(basename "$conf")"
  if [ "$nom" = ".claude" ]; then def="perso"; else def="${nom#.claude-}"; fi
  read -erp "Dossier $nom : étiquette du compte [$def] (« - » pour l'ignorer) : " compte
  compte="${compte:-$def}"
  [ "$compte" = "-" ] && continue
  compte="$(printf '%s' "$compte" | tr -cd 'A-Za-z0-9_.-')"
  echo "→ $nom (« $compte »)"
  installer_hooks "$conf" "$compte"
  trouve=1
done
[ "$trouve" = 1 ] || echo "Aucun dossier ~/.claude* trouvé."

# --- Essai ------------------------------------------------------------------------------
echo
echo "Essai : un agent « essai » doit apparaître sur l'écran rond pendant 10 s…"
printf '{"session_id":"vigie-essai-%s","hook_event_name":"SessionStart","cwd":"C:\\\\essai"}' "$poste" \
  | bash "$DIR/vigie-hook.sh" "installation"
sleep 10
printf '{"session_id":"vigie-essai-%s","hook_event_name":"SessionEnd","cwd":"C:\\\\essai"}' "$poste" \
  | bash "$DIR/vigie-hook.sh" "installation"
echo "Terminé. Les sessions Claude Code déjà ouvertes doivent être relancées."
