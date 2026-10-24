# Avant tout commit

Ce dépôt est public. L'installation réelle de la Vigie ne l'est pas : elle contient des mots de
passe de boîtes mail, des jetons, l'adresse du Jetson et le nom du Wi-Fi.

**Contrôle à passer avant chaque commit :**

```bash
git diff --cached | grep -nE "192\.168\.[0-9]+\.[0-9]+|mot_de_passe *= *\"[^.\"]|VIGIE_TOKEN=|MQTT_PASS=|pat-[a-z0-9-]{10,}|client_id *= *\"[0-9a-f]{8}-"
```

Seules les adresses d'exemple (`192.168.1.x`) sont admises.

Trois règles :

- **Ne jamais copier `~/vigie` du Jetson dans le dépôt.** Ce dossier contient `vigie.env`,
  `courrier.toml`, `courrier-jetons.json` et les journaux. Ne reprendre que les fichiers de
  programme, un par un.
- **Pas de vraie donnée dans les exemples** : ni adresse e-mail, ni nom de correspondant, ni
  règle de tri personnelle. Les captures d'écran publiées sont floutées.
- **Après une fuite**, retirer le fichier ne suffit pas : l'historique Git le garde. Changer
  immédiatement le mot de passe ou le jeton concerné.
