# La Vigie

*[English version](README.md)*

Un petit compagnon de bureau qui surveille vos agents IA et trie votre courrier, **en local**.

Un Jetson Orin Nano fait tourner une IA locale (Llama 3.2 3B via Ollama). Il suit les sessions
Claude Code des PC de la maison et relève les boîtes mail, puis il affiche sur un écran rond
tactile qui travaille, qui a fini, qui attend une réponse et quel mail compte vraiment. Rien ne
part dans le cloud.

```
PC (Claude Code) --hooks HTTP--> Jetson : cerveau "vigie" + IA locale (Ollama)
Boîtes mail (IMAP) <--relève--   Jetson : tri du courrier "vigie-courrier"
                                 Jetson : broker MQTT (mosquitto)
                                        |
                                        v  Wi-Fi, réseau local
                                 Écran rond ESP32-P4 (affichage, sons, caméra)
```

Le guide de fabrication pas à pas est publié sur [explorations360.com](https://explorations360.com).

## Contenu du dépôt

| Dossier | Où | Rôle |
|---|---|---|
| [`vigie/`](vigie/) | Jetson | Le cerveau : reçoit les événements, déduit l'humeur, fait écrire une phrase par le LLM, publie en MQTT |
| [`vigie-postes/`](vigie-postes/) | chaque PC (Git Bash) | Installe les hooks Claude Code, avec le nom du poste et du compte |
| [`vigie-courrier/`](vigie-courrier/) | Jetson | Tri du courrier : IMAP en lecture seule, contacts connus, classement par le LLM |
| [`vigie-corps/`](vigie-corps/) | écran rond | Firmware ESP-IDF 6.1 / LVGL 9 : affichage, gestes, sons, caméra et détection de visage |

Chaque dossier a son propre README (en français) avec l'installation détaillée.

Pièces à imprimer en 3D : [`vigie/3D/`](vigie/3D/) (boîtier du Jetson) et
[`vigie-corps/3D/`](vigie-corps/3D/) (corps du robot pour l'écran).

## Matériel

- NVIDIA Jetson Orin Nano Super Developer Kit (8 Go), JetPack 6.2.x
- Waveshare ESP32-P4-WIFI6-Touch-LCD-3.4C (écran rond tactile 800 × 800), Wi-Fi 2,4 GHz
- Haut-parleur 8 Ω (livré avec l'écran), caméra OV5647 en option
- Un ou plusieurs PC avec Claude Code

## Ordre d'installation

1. **Jetson** : JetPack, Ollama (variante JetPack) et le modèle `llama3.2:3b-instruct-q5_K_M`.
2. **`vigie/`** : `bash setup-jetson.sh`. Il affiche le jeton et l'adresse IP du Jetson.
3. **`vigie-postes/`** sur chaque PC : `bash installer-postes.sh` dans Git Bash.
4. **`vigie-corps/`** : renseigner le Wi-Fi et le broker dans menuconfig, compiler et flasher.
5. **`vigie-courrier/`** (facultatif) : `bash installer-courrier.sh`, puis remplir `courrier.toml`.

## Vie privée

- Tout tourne sur votre réseau local. Le Jetson n'a pas besoin d'être exposé à internet.
- Les boîtes mail sont ouvertes **en lecture seule** : la Vigie ne marque jamais un mail comme lu.
- Mots de passe et jetons restent sur le Jetson (`vigie.env`, `courrier.toml`, droits 600). Ils
  sont exclus du dépôt par le `.gitignore`.

## Avertissement

Projet personnel, fourni **en l'état**, sans garantie ni support. Sans lien avec Anthropic,
NVIDIA, Waveshare, Espressif ou Microsoft. Les noms de produits cités appartiennent à leurs
détenteurs respectifs.

## Licence

[MIT](LICENSE) © 2026 Philippe Carrez, explorations360.
La police Nunito incluse dans `vigie-corps/main/fonts/` est sous licence
[SIL Open Font License 1.1](vigie-corps/main/fonts/OFL.txt). La police Poppins de
`vigie/3D/jetson-boitier-impression/source/` est sous la même licence.
