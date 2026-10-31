#!/usr/bin/env bash
# Installe la Vigie sur le Jetson : broker Mosquitto protégé par mot de passe,
# service vigie.py lancé au démarrage.
# Usage : depuis le dossier qui contient ce script →  bash setup-jetson.sh
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
UTILISATEUR="$(whoami)"

echo ">>> Paquets (Mosquitto, client MQTT, pip)"
sudo apt-get update -qq
sudo apt-get install -y mosquitto mosquitto-clients python3-pip curl
pip3 install --user --upgrade paho-mqtt

echo ">>> Compte MQTT « vigie »"
read -rsp "Choisis un mot de passe MQTT (il servira aussi à l'écran rond) : " PASS; echo
sudo mosquitto_passwd -c -b /etc/mosquitto/passwd vigie "$PASS"
sudo chown root:mosquitto /etc/mosquitto/passwd
sudo chmod 0640 /etc/mosquitto/passwd

sudo tee /etc/mosquitto/conf.d/vigie.conf > /dev/null <<'EOF'
listener 1883 0.0.0.0
allow_anonymous false
password_file /etc/mosquitto/passwd
EOF
sudo systemctl enable mosquitto
sudo systemctl restart mosquitto

echo ">>> Configuration du service"
read -rp "Ton prénom (la Vigie s'adresse à toi) : " PRENOM
PRENOM="${PRENOM:-Camille}"
if [ ! -f "$DIR/vigie.env" ]; then
  JETON="$(head -c 18 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 20)"
  cat > "$DIR/vigie.env" <<EOF
VIGIE_PORT=8765
VIGIE_TOKEN=$JETON
MQTT_HOST=localhost
MQTT_USER=vigie
MQTT_PASS=$PASS
OLLAMA_URL=http://localhost:11434
VIGIE_MODEL=llama3.2:3b-instruct-q5_K_M
VIGIE_JOURNAL=$DIR/journal.jsonl
VIGIE_PRENOM=$PRENOM
EOF
else
  sed -i "s|^MQTT_PASS=.*|MQTT_PASS=$PASS|" "$DIR/vigie.env"
  grep -q "^VIGIE_PRENOM=" "$DIR/vigie.env" \
    && sed -i "s|^VIGIE_PRENOM=.*|VIGIE_PRENOM=$PRENOM|" "$DIR/vigie.env" \
    || echo "VIGIE_PRENOM=$PRENOM" >> "$DIR/vigie.env"
fi
chmod 600 "$DIR/vigie.env"

sed -e "s|__USER__|$UTILISATEUR|g" -e "s|__DIR__|$DIR|g" "$DIR/vigie.service" \
  | sudo tee /etc/systemd/system/vigie.service > /dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now vigie
sleep 2

echo
echo ">>> Vérifications"
systemctl is-active mosquitto vigie || true
curl -s http://localhost:8765/sante && echo
echo
echo "Jeton pour les hooks (à mettre dans settings.json de Claude Code) :"
grep '^VIGIE_TOKEN=' "$DIR/vigie.env" | cut -d= -f2
echo "Adresse IP du Jetson : $(hostname -I | awk '{print $1}')"
