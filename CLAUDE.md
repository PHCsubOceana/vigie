# La Vigie : consignes pour Claude

Ce dépôt est **public** (licence MIT). Les réglages personnels de Philippe sont dans
`CLAUDE.local.md` (non versionné) : le lire en début de session.

## Le projet

Un compagnon de bureau qui surveille des agents IA et trie le courrier, en local :

| Dossier | Tourne sur | Langage |
|---|---|---|
| `vigie/` | Jetson Orin Nano (Ubuntu 22.04, **Python 3.10**) | Python, service systemd `vigie` |
| `vigie-courrier/` | Jetson | Python, service systemd `vigie-courrier` |
| `vigie-postes/` | PC Windows, lancé par Claude Code via **Git Bash** | bash |
| `vigie-corps/` | écran rond Waveshare ESP32-P4 (puce v1.3) | C, ESP-IDF **6.1**, LVGL 9.6 |

Liaisons : hooks HTTP (PC vers Jetson, port 8765, en-tête `X-Vigie-Token`), MQTT (Jetson vers
écran, broker mosquitto). Le contrat MQTT est décrit dans `vigie/README.md` et
`vigie-corps/README.md`.

## Règles

1. **Avant chaque commit**, lancer le contrôle de `SECURITE.md` et lire le résultat. Aucune
   vraie valeur dans le dépôt : adresses IP (sauf exemples `192.168.1.x`), Wi-Fi, mots de passe,
   jetons, adresses e-mail, noms de correspondants, règles de tri personnelles. Les valeurs
   personnelles vivent dans des fichiers ignorés par Git : `vigie-corps/sdkconfig`,
   `~/vigie/vigie.env` et `~/vigie/courrier.toml` (Jetson), `~/.vigie/` (PC).
2. **Jamais de tiret long (— ou –) dans un texte affiché** : chaînes du firmware, phrases et
   résumés produits par le LLM, messages des agents. Le firmware les filtre à l'entrée
   (`sans_tirets_longs` dans `main.c`) ; ne pas en introduire dans le code non plus.
3. **Firmware** : une seule conversation à la fois travaille dessus. Partir des fichiers
   actuels du dépôt, modifier fichier par fichier, compiler avant de proposer un commit.
4. **Python** : rester compatible 3.10 (`tomllib` absent : repli sur `tomli`). Pas de nouvelle
   dépendance au-delà de `paho-mqtt` sans accord de Philippe.
5. **Lecture seule** : `vigie-courrier` ouvre les boîtes en EXAMINE et lit en BODY.PEEK. Ne
   jamais marquer un mail comme lu.
6. Les textes destinés à l'utilisateur sont en **français courant**, phrases courtes ; les
   commentaires du code aussi. Messages de commit en français.
7. Rien ne doit casser une installation existante : un nouveau réglage a toujours une valeur
   par défaut.

## Compiler l'écran

Dans le terminal **ESP-IDF 6.1 PowerShell**, depuis `vigie-corps/` :

```powershell
idf.py build
idf.py -p <PORT_COM> flash monitor
```

Premier usage d'un dossier neuf : `$env:IDF_TARGET = "esp32p4"` puis `idf.py set-target esp32p4`
(inutile si `sdkconfig` est déjà là). `Ctrl+]` quitte le moniteur.

## Déployer sur le Jetson

- `vigie/vigie.py` : copier le fichier dans `~/vigie/`, puis `sudo systemctl restart vigie`.
  Ne **pas** relancer `setup-jetson.sh` sur une installation existante (il redemande le mot de
  passe MQTT).
- `vigie-courrier/` : copier le dossier, puis `bash installer-courrier.sh` (il garde
  `courrier.toml` et redémarre le service). Essai sans rien publier :
  `python3 ~/vigie/vigie-courrier.py --essai 15`.
- Suivi : `journalctl -u vigie -f`, `journalctl -u vigie-courrier -f`, `curl -s localhost:8765/etat`.
