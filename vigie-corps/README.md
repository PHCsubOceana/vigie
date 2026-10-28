# Vigie : l'écran rond (Waveshare ESP32-P4-WIFI6-Touch-LCD-3.4C)

Le corps de la Vigie. Il se connecte au Wi-Fi (puce ESP32-C6 de la carte), écoute le Jetson
en MQTT et affiche l'humeur, les agents, la phrase du LLM et les mails importants sur
l'écran rond 800 × 800.

- **Anneau extérieur** : l'humeur. Ambre qui pulse (quelqu'un t'attend), bleu qui respire
  (ça travaille), vert (terminé), ardoise lente (calme), gris (liaison perdue).
- **Anneau intérieur** : un segment par agent, à la couleur de son état.
- **Centre** : l'humeur en toutes lettres, la phrase, la liste des agents. Les textes trop
  longs défilent.
- **Haut** : une enveloppe corail et le nombre de mails importants non lus (`vigie/courrier`,
  publié par le tri du courrier). Un nouveau mail important prend le centre 20 s, avec un son.
- **Bas** : un message quand la liaison est rompue (Wi-Fi, broker ou Jetson).

## Gestes

- **Toucher** : ouvre les **réglages**, trois lignes à toucher :
  - **Son** : réglage du volume (arc + « Couper le son »).
  - **Wi-Fi** : les réseaux à portée (2,4 GHz), avec la force du signal. Toucher un réseau
    demande son mot de passe (clavier AZERTY à l'écran), puis l'essaie. S'il répond, il est
    gardé en mémoire et remplace celui du `sdkconfig` ; sinon la Vigie revient au réseau
    d'avant et dit pourquoi (mot de passe refusé, réseau introuvable…).
  - **Caméra** : aperçu (disque central, en miroir, avec le cadre du visage). Un toucher le
    ferme ; il se ferme seul après 2 min. À distance : `mosquitto_pub … -t vigie/commande -m camera`.

  Les réglages se ferment d'un toucher dans le vide, ou seuls après un moment sans geste.
- Quand une alerte courrier est affichée, le toucher ouvre la **fiche des mails** : toucher un
  mail le marque « vu » sur l'écran (pas dans la boîte), toucher ailleurs ferme la fiche.
  L'enveloppe en haut l'ouvre aussi.

## Sons

Haut-parleur 8 Ω sur le connecteur **SPK**. Un son par type d'alerte : un agent attend ta
réponse, une tâche est terminée, rappel après 5 min d'attente, liaison perdue, nouveau mail
important. Volume mémorisé ; aussi réglable par MQTT (`vigie/corps/volume`, 0 à 100).

## Caméra et visage

OV5647 sur le connecteur caméra (nappe 15 broches). La détection de visage (ESP-DL
d'Espressif, modèle `human_face_detect`) tourne sur la carte, dix fois par seconde au plus :

| Sujet | Contenu |
|---|---|
| `vigie/regard` | `{"visage":true,"x":-0.12,"y":0.05,"taille":0.21,"score":0.93}`, 5 fois/s au plus, puis `{"visage":false}` |
| `vigie/presence` (retenu) | `present` après 3 détections d'affilée, `absent` après 60 s sans visage |

Réglages dans menuconfig, **Vigie → Caméra**. **Test d'orientation** : dans l'aperçu (un
miroir), lève la main droite ; elle doit apparaître à droite, sinon change le miroir
horizontal. Image à l'envers : change le retournement vertical. Sans caméra, l'écran affiche
« Caméra absente » et tout le reste fonctionne.

Si ESP-DL refuse de compiler : mettre en commentaire la ligne `espressif/human_face_detect`
de `main/idf_component.yml`. Le firmware garde la caméra et l'aperçu, sans le visage.

## Compiler et flasher

Il faut **VS Code** avec l'extension **ESP-IDF**, version **6.1**.

1. Copier ce dossier (par exemple dans `C:\esp\projets\vigie-corps`) et l'ouvrir dans VS Code.
2. Dans le terminal **ESP-IDF PowerShell**, depuis le dossier du projet :
   ```powershell
   $env:IDF_TARGET = "esp32p4"
   idf.py set-target esp32p4
   idf.py menuconfig
   ```
   Menu **Vigie** : nom du Wi-Fi (2,4 GHz), mot de passe Wi-Fi, adresse du broker
   (`mqtt://<IP_DU_JETSON>:1883`), utilisateur `vigie` et mot de passe MQTT (dans
   `~/vigie/vigie.env` sur le Jetson). `S` pour enregistrer, `Q` pour quitter.
3. Brancher la carte par la prise USB-C **UART**, trouver son port
   (`[System.IO.Ports.SerialPort]::GetPortNames()`), puis :
   ```powershell
   idf.py build
   idf.py -p <PORT_COM> flash monitor
   ```
   `Ctrl+]` pour quitter le moniteur.

La première compilation télécharge les composants (écran, LVGL, Wi-Fi, MQTT, caméra, ESP-DL) :
il faut internet et quelques minutes.

Les réglages de la carte sont dans `sdkconfig.defaults` et s'appliquent au `set-target` :
puce révision v1.x, 32 Mo de flash, PSRAM, écran 3,4". Deux options sont à garder telles
quelles :
- les avertissements par défaut ne deviennent pas des erreurs (LVGL 9.6 déprécie des en-têtes
  encore utilisés par le pilote d'écran) ;
- le *clock gating* de la veille légère est désactivé (sinon erreur mémoire et redémarrage
  au lancement).

Bon à savoir : `idf.py` « non reconnu » signifie que vous n'êtes pas dans le terminal ESP-IDF.
Si le menu **Vigie** n'apparaît pas, lancez `idf.py` depuis le dossier du projet. Un
avertissement sur la version du firmware de la puce Wi-Fi au démarrage est sans conséquence.

## Fichiers

| Fichier | Rôle |
|---|---|
| `main/main.c` | MQTT, lecture du JSON, liens entre les modules |
| `main/wifi.c` | Wi-Fi par la C6 : recherche des réseaux, essai, réseau gardé en NVS |
| `main/ui.c` | Tout le dessin (LVGL 9) et les réglages, sans dépendance à ESP-IDF |
| `main/son.c` | Sons synthétisés (codec ES8311) |
| `main/camera.c` | Caméra (esp_video, V4L2), aperçu, regard et présence |
| `main/visage.cpp` | Détection de visage (ESP-DL) |
| `main/fonts/` | Police Nunito avec les accents français (licence OFL) |
| `main/Kconfig.projbuild` | Le menu **Vigie** de menuconfig |
| `sdkconfig.defaults` | Réglages de la carte |
| `partitions.csv` | Application jusqu'à 8 Mo |
