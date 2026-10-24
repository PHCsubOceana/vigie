# Vigie — tri du courrier (Jetson)

Relève tes boîtes mail toutes les 2 minutes, garde les mails importants encore non lus et
les publie pour l'écran rond (`vigie/courrier`). Le tri se fait sur le Jetson : règles,
contacts connus, puis le LLM local d'Ollama (le même que la Vigie).

- Boîte ouverte **en lecture seule** : la Vigie ne marque jamais un mail comme lu.
- Un mail sort de la liste dès que tu le lis (téléphone, PC…), le supprimes, ou après 48 h.
- Au premier passage, seuls les non lus des dernières 24 h sont examinés (10 au plus).
- Chaque boîte qui a du courrier important apparaît aussi comme un **agent** de la Vigie
  (« Courrier pro : 2 mails importants ») tant qu'un mail important n'est pas lu, puis
  disparaît. `[ecran] agents = false` pour ne garder que l'enveloppe.
- S'arrête avec la Vigie : `sudo systemctl stop vigie` libère aussi le GPU du tri.

## Installer

Depuis le PC (PowerShell), copier le dossier `vigie-courrier` du dépôt sur le Jetson :

```powershell
scp -r vigie-courrier <utilisateur>@<IP_DU_JETSON>:~
```

Puis sur le Jetson :

```bash
cd ~/vigie-courrier
bash installer-courrier.sh
nano ~/vigie/courrier.toml                         # boîtes, client_id Microsoft, jeton HubSpot
python3 ~/vigie/vigie-courrier.py --connexion bureau  # une fois par compte Microsoft
python3 ~/vigie/vigie-courrier.py --contacts       # apprend tes contacts, affiche leur nombre
python3 ~/vigie/vigie-courrier.py --essai          # trie tes 10 derniers mails, sans rien publier
sudo systemctl start vigie-courrier
```

## Les trois sortes de boîtes

**OVH** (e-mail de l'hébergement, MX Plan) : serveur `ssl0.ovh.net`, ton adresse et le mot
de passe **de la boîte** (pas celui de l'espace client OVH). Pour une offre Exchange chez
OVH, le serveur est indiqué dans l'espace client (`ex?.mail.ovh.net`).

**Microsoft 365 et Outlook.com / Hotmail** : Microsoft n'accepte plus les mots de passe en
IMAP, même « d'application ». Il faut OAuth2, en deux temps :

1. *Une fois pour toutes* : enregistrer une petite application « Vigie » chez Microsoft
   (gratuit, 5 minutes), sur <https://entra.microsoft.com> connecté avec ton compte
   Microsoft 365 :
   - **Applications → Inscriptions d'applications → Nouvelle inscription** ; nom « Vigie » ;
     types de comptes : **« Comptes dans un annuaire d'organisation et comptes Microsoft
     personnels »** ; pas d'URI de redirection. Enregistrer.
   - Copier l'**ID d'application (client)** dans `client_id` de la section `[microsoft]`.
   - **Authentification → Paramètres avancés → Autoriser les flux de clients publics : Oui**.
   - **Autorisations d'API → Ajouter → Microsoft Graph → Autorisations déléguées** :
     `IMAP.AccessAsUser.All` et `offline_access`. Si tu es administrateur du compte 365,
     cliquer **Accorder un consentement d'administrateur**.
   - Dans le centre d'administration Exchange, vérifier qu'**IMAP est activé** pour la boîte
     Microsoft 365 (Destinataires → Boîtes aux lettres → la boîte → Applications de messagerie).
2. *Une fois par compte* : `python3 ~/vigie/vigie-courrier.py --connexion bureau`. Il affiche un
   code : l'entrer sur <https://microsoft.com/devicelogin> (depuis le PC ou le téléphone),
   se connecter avec la bonne adresse, accepter. Le jeton est rangé dans
   `~/vigie/courrier-jetons.json` (droits 600) et se renouvelle seul ensuite.

## Comment il trie

1. **jamais** (expéditeur) → ignoré.
2. **Contact connu** → important ; le LLM écrit seulement le résumé. Sont connus : toute
   adresse à qui tu as écrit (dossier « Envoyés » de chaque boîte, relu toutes les 6 h,
   les 3000 derniers envois au premier passage) et les contacts de ton **HubSpot** si
   `[hubspot] jeton` est rempli. Ta propre adresse et les `noreply` ne comptent pas.
3. **Envoi en masse** (lettre d'information, `noreply`, liste de diffusion) → ignoré, sauf
   si l'objet contient un des **mots_urgents** (facture, impayé, relance…). Même d'un contact
   connu : une lettre d'information reste une lettre d'information.
4. **toujours** (expéditeur ou domaine) → important.
5. Sinon le **LLM** tranche et résume en une phrase, avec le **contexte** que tu lui donnes.
6. Ollama arrêté : règles seules (contacts, toujours, puis mots urgents dans l'objet).

**Jeton HubSpot** : dans HubSpot, *Paramètres → Intégrations → Applications privées →
Créer* (nom « Vigie »), onglet *Champs d'application* : `crm.objects.contacts.read`
seulement. Copier le jeton (`pat-…`) dans `[hubspot] jeton`. Seules les adresses e-mail des
contacts sont lues, et gardées sur le Jetson (`~/vigie/courrier-contacts.json`).

`--essai` sert à régler les règles : il affiche le verdict et sa raison pour chaque mail.

## Ce qui est publié

`vigie/courrier` (retenu) :

```json
{"nb": 2, "maj": 1792750000,
 "importants": [{"id": "pro:4812", "compte": "pro", "de": "Claire Martin",
                 "sujet": "Visite VR jeudi ?", "resume": "Claire Martin demande une démo VR jeudi à 14 h",
                 "raison": "contact connu", "recu": 1792749000}]}
```

## Suivi

- `journalctl -u vigie-courrier -f` : chaque mail trié, avec son verdict.
- `~/vigie/courrier.jsonl` : le journal des verdicts (pour ajuster les règles).
- `mosquitto_sub -u vigie -P "$(grep MQTT_PASS ~/vigie/vigie.env | cut -d= -f2)" -t vigie/courrier -v`

## Fichiers

| Fichier | Rôle |
|---|---|
| `vigie-courrier.py` | Le service (Python 3.11+, ou 3.10 avec `python3-tomli` ; paho-mqtt) |
| `courrier.exemple.toml` | Modèle de configuration, copié en `~/vigie/courrier.toml` (droits 600) |
| `vigie-courrier.service` | Service systemd, lié à `vigie.service` |
| `installer-courrier.sh` | Installation |

Fichiers créés à l'usage, tous en droits 600 dans `~/vigie` : `courrier-etat.json`,
`courrier-jetons.json` (Microsoft), `courrier-contacts.json`, `courrier.jsonl`.
