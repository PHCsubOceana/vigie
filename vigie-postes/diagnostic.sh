#!/usr/bin/env bash
# Vigie — diagnostic du hook sur ce poste. Usage, dans Git Bash : bash diagnostic.sh
DIR="$HOME/.vigie"
URL="$(cat "$DIR/url" 2>/dev/null | tr -d '\r\n')"; URL="${URL:-http://192.168.1.50:8765/event}"
BASE="${URL%/event}"
JETON="$(cat "$DIR/jeton" 2>/dev/null | tr -d '\r\n')"

echo "== 1. Réglages (~/.vigie)"
echo "   hook   : $( [ -f "$DIR/vigie-hook.sh" ] && echo présent || echo ABSENT )"
echo "   jeton  : $( [ -n "$JETON" ] && echo "${#JETON} caractères, commence par ${JETON:0:3}" || echo ABSENT )"
echo "   poste  : $(cat "$DIR/poste" 2>/dev/null)"
echo "   adresse: $URL"

echo "== 2. Hooks dans les réglages de Claude Code"
for f in "$HOME"/.claude/settings.json "$HOME"/.claude-*/settings.json; do
  [ -f "$f" ] || continue
  echo "   $f : $(grep -o 'vigie-hook.sh [A-Za-z0-9_.-]*' "$f" | sort | uniq -c | tr '\n' ' ')"
done

echo "== 3. Le Jetson répond ?"
echo "   /sante → $(curl -s --connect-timeout 2 -m 4 "$BASE/sante" || echo 'PAS DE RÉPONSE')"

echo "== 4. Envoi direct d'un événement (sans le hook), réponse du Jetson :"
corps='{"projet":"diagnostic","session_id":"diag-vigie","hook_event_name":"Notification","message":"diagnostic","cwd":"C:\\diag"}'
curl -s --connect-timeout 2 -m 4 -w "\n   code HTTP %{http_code}\n" -X POST "$URL" \
  -H "Content-Type: application/json; charset=utf-8" -H "X-Vigie-Token: $JETON" --data-binary "$corps" | sed 's/^/   /'

echo "== 5. Même chose par le hook (comme Claude Code) :"
printf '%s' '{"session_id":"diag-hook","hook_event_name":"Notification","message":"diagnostic hook","cwd":"C:\\Users\\essai"}' \
  | bash "$DIR/vigie-hook.sh" diag
echo "   $(cat "$DIR/dernier-envoi.txt" 2>/dev/null || echo 'pas de trace (ancien hook ?)')"

echo "== 6. Agents connus du Jetson maintenant :"
curl -s -m 4 "$BASE/etat" | sed 's/"id"/\n   "id"/g' | grep '"id"' | sed 's/, "depuis".*//'

echo
echo "Sur l'écran : deux agents « t'attend » (anneau ambre) pendant 30 s…"
sleep 30
for id in diag-vigie diag-hook; do
  curl -s -m 4 -o /dev/null -X POST "$URL" -H "Content-Type: application/json" -H "X-Vigie-Token: $JETON" \
    --data-binary "{\"session_id\":\"$id\",\"hook_event_name\":\"SessionEnd\",\"cwd\":\"C:\\\\diag\"}"
done
echo "Agents de diagnostic retirés. Copie-moi tout ce qui précède."
