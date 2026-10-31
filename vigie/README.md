# Vigie : le cerveau sur le Jetson

Le Jetson reçoit les événements de tes agents IA, en déduit une humeur, fait écrire une
phrase courte par le LLM local et publie le tout en MQTT pour l'écran rond.

```
Claude Code (PC) --hook--> vigie.py (Jetson, port 8765) --MQTT--> écran rond (ESP32-P4)
autres scripts ---HTTP-->        |
                                 +-- Ollama (une phrase, en local)
```

## Avant de commencer

- Jetson Orin Nano avec JetPack 6.2.x et **Ollama** installé (variante JetPack, GPU actif).
- Le modèle : `ollama pull llama3.2:3b-instruct-q5_K_M`.
- Une adresse IP fixe pour le Jetson, réservée dans la box.

## Installation (une fois, sur le Jetson)

```bash
scp -r vigie <utilisateur>@<IP_DU_JETSON>:~          # depuis le PC, dossier du dépôt
ssh <utilisateur>@<IP_DU_JETSON>
cd ~/vigie
bash setup-jetson.sh
```

Le script installe Mosquitto, crée le compte MQTT `vigie` (il demande le mot de passe) et
ton prénom (la Vigie s'adresse à toi), génère un jeton pour les hooks, puis lance le service
`vigie` au démarrage. Il affiche à la fin **le jeton** et **l'adresse IP** du Jetson : note-les.

Tout est rangé dans `~/vigie/vigie.env` (droits 600) : `VIGIE_TOKEN`, `MQTT_PASS`,
`VIGIE_PRENOM`, `VIGIE_MODEL`. Après une modification : `sudo systemctl restart vigie`.

## Tester

Deux terminaux SSH sur le Jetson :

```bash
# 1 : ce que l'écran recevra
mosquitto_sub -h localhost -u vigie -P <motdepasse> -t 'vigie/#' -v

# 2 : une session Claude simulée
bash test-vigie.sh localhost:8765 "$(grep VIGIE_TOKEN vigie.env | cut -d= -f2)"
```

L'humeur passe par `calme`, `affairee`, `attentive`, `affairee` puis `satisfaite`, et chaque
changement est suivi, une à deux secondes plus tard, d'une phrase écrite par le LLM.

## Brancher Claude Code (sur chaque PC)

Le plus simple : l'archive **vigie-postes** (installateur pour Git Bash sous Windows). Elle
demande l'adresse du Jetson, le jeton et un nom de poste, puis installe les six hooks.

À la main : copier `hooks-claude-code.json` dans `~/.claude/settings.json` (fusionner s'il y a
déjà une clé `"hooks"`), remplacer `TON_JETON` par le jeton et `jetson.local` par l'IP du
Jetson. Vérifier ensuite avec `/hooks` dans Claude Code.

**Nom des postes** : si l'événement ne précise pas de projet, la Vigie affiche le nom du PC.
Elle le prend dans l'en-tête `X-Vigie-Poste`, sinon dans `~/vigie/postes.json`
(`{"192.168.1.20": "Bureau"}`, relu à chaud), sinon dans le nom réseau, sinon l'adresse IP.

## Faire parler n'importe quel autre agent

```bash
curl -X POST http://<IP_DU_JETSON>:8765/event -H "X-Vigie-Token: <jeton>" \
  -d '{"source":"script","agent":"sauvegarde","projet":"sauvegarde","etat":"travaille"}'
```

`etat` vaut `travaille`, `attend`, `fini` ou `pret`. `message` (facultatif) précise une
attente. Pour retirer un agent : `{"agent":"sauvegarde","hook_event_name":"SessionEnd"}`.

Sous PowerShell, utiliser `Invoke-RestMethod` plutôt que `curl.exe`, qui abîme les guillemets
du JSON.

## Contrat MQTT

| Sujet | Retenu | Contenu |
|---|---|---|
| `vigie/etat` | oui | état complet, à afficher |
| `vigie/evenement` | non | chaque événement, pour une petite animation |
| `vigie/cerveau` | oui | `en-ligne` / `hors-ligne` (le Jetson est-il là ?) |

```json
{
  "humeur": "attentive",
  "phrase": "Le site demande ton autorisation pour Bash.",
  "agents": [
    {"id": "abc", "source": "claude-code", "projet": "mon-site · pro · bureau",
     "etat": "attend", "depuis": 1792697531, "message": "permission pour Bash"}
  ],
  "maj": 1792697543,
  "version": 7
}
```

Humeurs : `attentive` (quelqu'un t'attend), `affairee` (ça travaille), `satisfaite` (une
tâche vient de finir, pendant 10 min), `calme`. Les agents sont triés du plus important au
moins important. Un agent muet depuis 2 h est oublié.

Le LLM ne décide rien : il reçoit des faits déjà établis et les met en phrase. Une phrase qui
contredit l'état réel est refusée, et si Ollama ne répond pas, une phrase simple prend le
relais : la Vigie ne se tait jamais.

## Dépannage

```bash
systemctl status vigie mosquitto
journalctl -u vigie -f          # phrases du LLM et erreurs
curl -s localhost:8765/etat     # état courant
tail ~/vigie/journal.jsonl      # historique des événements
```

Pour libérer le GPU du Jetson : `sudo systemctl stop vigie` (arrête aussi le tri du courrier).

## Boîtier imprimé

Le dossier `3D/jetson-boitier-impression/` contient un boîtier pour le Jetson Orin Nano
(Super) Developer Kit, en deux pièces imprimées sans support :

- **le bac** (122 × 98 × 43 mm, bleu nuit) : fentes d'aération sur l'avant et les côtés, fond
  percé sous la carte pour le SSD, grande fenêtre à l'arrière pour toutes les prises,
  colonnes de vis dans les quatre coins ;
- **le couvercle** (122 × 98 × 13 mm) : grande grille ronde au-dessus du ventilateur,
  satellite ventilé et logotype explorations360 en trois couleurs (bleu nuit, orange,
  bleu ciel), avec un pictogramme « centre du réseau » : le Jetson reçoit tout et
  redistribue aux écrans.

Visserie : 4 vis M3 × 12 à tête fraisée, et 4 patins adhésifs de 12 mm sous le bac. Le kit
mesure 103 × 91 × 35 mm (pieds et ventilateur compris) ; il est tenu d'avant en arrière avec
1 mm de jeu. Ouvrir `apercu-3d.html` pour voir le boîtier ouvert ou fermé, avec le Jetson
dedans.
