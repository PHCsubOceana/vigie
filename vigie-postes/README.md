# Vigie — plusieurs comptes Claude, plusieurs postes

Chaque session Claude Code s'affiche sur l'écran avec son projet, son compte et son poste :

    site-web · pro · bureau — travaille
    rapport · perso · salon — t'attend

Rien à changer sur le Jetson : le hook remplit le champ `projet`, que `vigie.py` affiche tel quel.

## Installer (sur chaque PC)

Dans **Git Bash** (installé avec Claude Code), depuis le dossier décompressé :

```bash
bash installer-postes.sh
```

Il demande :
1. **l'adresse IP du Jetson** ;
2. **le jeton** de la Vigie, seulement s'il ne le trouve pas dans les hooks déjà installés
   (sur le Jetson : `grep VIGIE_TOKEN ~/vigie/vigie.env`) ;
3. **un nom court pour le poste** (bureau, salon…) ;
4. **une étiquette pour chaque dossier de configuration** trouvé : `~/.claude` → `perso`,
   `~/.claude-pro` → `pro` par défaut. Taper `-` pour ignorer un dossier.

Puis un agent « essai » apparaît 10 s sur l'écran. **Relancer les sessions Claude Code
ouvertes** pour qu'elles prennent les nouveaux hooks ; vérifier avec `/hooks`.

Sécurité : copie `settings.json.avant-vigie-…` avant toute modification, contrôle du JSON
après, retour à la copie si le contrôle échoue. Un `settings.json` qui a déjà d'autres hooks
n'est pas modifié : l'installateur affiche alors la commande à ajouter à la main.

## Fichiers installés

| Fichier | Rôle |
|---|---|
| `~/.vigie/vigie-hook.sh` | Le hook : ajoute « projet · compte · poste » et envoie au Jetson (2 s au plus, ne bloque jamais Claude Code) |
| `~/.vigie/jeton` | Jeton de la Vigie |
| `~/.vigie/poste` | Nom court du poste (vide = pas de poste dans l'étiquette) |
| `~/.vigie/url` | Adresse du Jetson, demandée à l'installation (`http://<IP>:8765/event`) |

Dans `settings.json`, chaque événement lance `bash ~/.vigie/vigie-hook.sh <compte>`.

## Limites

- Seul **Claude Code** (terminal, VS Code) prévient la Vigie. Les conversations dans
  claude.ai ou l'application tournent chez Anthropic et ne voient pas ton réseau local.
- Les deux PC doivent être sur le réseau de la maison (le Jetson n'est pas exposé à internet).
