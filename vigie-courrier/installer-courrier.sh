#!/usr/bin/env bash
# Installe le tri du courrier à côté du cerveau de la Vigie (~/vigie).
# Usage, depuis le dossier décompressé : bash installer-courrier.sh
set -euo pipefail

DIR="$HOME/vigie"
ICI="$(cd "$(dirname "$0")" && pwd)"
UTILISATEUR="$(id -un)"

if [ ! -f "$DIR/vigie.env" ]; then
  echo "~/vigie/vigie.env introuvable : installe d'abord le cerveau (setup-jetson.sh)." >&2
  exit 1
fi

python3 - <<'EOF'
import sys
try:
    import tomllib
except ImportError:
    try:
        import tomli  # Python 3.10 (Ubuntu 22.04) : paquet python3-tomli
    except ImportError:
        sys.exit("Il manque tomli : sudo apt install python3-tomli")
import paho.mqtt.client  # déjà installé pour vigie.py
EOF

install -m 755 "$ICI/vigie-courrier.py" "$DIR/vigie-courrier.py"
if [ ! -f "$DIR/courrier.toml" ]; then
  install -m 600 "$ICI/courrier.exemple.toml" "$DIR/courrier.toml"
  NOUVELLE_CONF=1
else
  NOUVELLE_CONF=0
fi

sed -e "s|__USER__|$UTILISATEUR|g" -e "s|__DIR__|$DIR|g" "$ICI/vigie-courrier.service" \
  | sudo tee /etc/systemd/system/vigie-courrier.service > /dev/null
sudo systemctl daemon-reload
sudo systemctl enable vigie-courrier > /dev/null

echo
if [ "$NOUVELLE_CONF" = 1 ]; then
  echo ">>> Reste à remplir ~/vigie/courrier.toml (boîtes, client_id Microsoft, jeton HubSpot) :"
  echo "      nano ~/vigie/courrier.toml"
  echo "    autoriser chaque compte Microsoft (une fois) :"
  echo "      python3 ~/vigie/vigie-courrier.py --connexion pro"
  echo "    apprendre tes contacts, puis essayer le tri sans rien publier :"
  echo "      python3 ~/vigie/vigie-courrier.py --contacts"
  echo "      python3 ~/vigie/vigie-courrier.py --essai"
  echo "    et enfin démarrer le service :"
  echo "      sudo systemctl start vigie-courrier"
else
  sudo systemctl restart vigie-courrier
  echo ">>> Service redémarré avec la configuration existante."
fi
echo "Suivi : journalctl -u vigie-courrier -f"
