#!/usr/bin/env python3
"""
Vigie — tri du courrier (Jetson).

Relève les boîtes IMAP à intervalle régulier, trie chaque nouveau mail
(règles d'abord, LLM local ensuite) et publie en MQTT la liste des mails
importants encore non lus :

  vigie/courrier (retenu) :
    {"nb": 2,
     "importants": [{"id", "compte", "de", "sujet", "resume", "raison", "recu"}, ...],
     "maj": 1792750000}

Un mail sort de la liste dès qu'il est lu (sur le téléphone ou ailleurs),
supprimé, ou plus vieux que `garder_h` heures.

Chaque boîte apparaît aussi comme un agent de la Vigie (POST /event de vigie.py) :
« attend » quand un mail important n'est pas lu (l'humeur passe à « attentive »),
« pret » sinon. Désactivable : [ecran] agents = false.

Rien ne quitte le Jetson : le LLM est celui d'Ollama, en local.
La boîte est ouverte en lecture seule : la Vigie ne marque jamais un mail comme lu.

Contacts connus : toute adresse à qui tu as écrit (dossier « Envoyés » de chaque
boîte) et, si un jeton est donné, tous les contacts de ton CRM HubSpot. Un mail d'un
contact connu est toujours important (sauf envoi en masse, laissé au jugement du LLM).

Comptes Microsoft (Outlook.com, Hotmail, Microsoft 365) : Microsoft refuse les mots de
passe en IMAP, il faut OAuth2. Une fois par compte : vigie-courrier.py --connexion NOM
(code à saisir sur https://microsoft.com/devicelogin), le jeton est ensuite renouvelé seul.

Usage :
  vigie-courrier.py                 service (boucle)
  vigie-courrier.py --une-fois      un seul passage, puis quitte
  vigie-courrier.py --essai [N]     trie les N derniers mails de chaque compte et
                                    affiche le verdict, sans rien publier ni retenir
  vigie-courrier.py --connexion NOM autorise un compte Microsoft (une fois)
  vigie-courrier.py --contacts      apprend les contacts et affiche leur nombre
"""

import argparse
import email
import email.policy
import email.utils
import html
import imaplib
import json
import logging
import os
import queue
import re
import signal
import socket
import ssl
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

try:
    import tomllib
except ImportError:  # Python < 3.11
    try:
        import tomli as tomllib
    except ImportError:
        sys.exit("Python 3.11 ou plus est nécessaire (module tomllib), ou : pip install tomli")

try:
    import paho.mqtt.client as mqtt
except ImportError:
    mqtt = None

DIR = Path(os.path.expanduser(os.environ.get("VIGIE_DIR", "~/vigie")))
CONF = Path(os.environ.get("VIGIE_COURRIER_CONF", DIR / "courrier.toml"))
ETAT = Path(os.environ.get("VIGIE_COURRIER_ETAT", DIR / "courrier-etat.json"))
JOURNAL = Path(os.environ.get("VIGIE_COURRIER_JOURNAL", DIR / "courrier.jsonl"))
JETONS = Path(os.environ.get("VIGIE_COURRIER_JETONS", DIR / "courrier-jetons.json"))
CONTACTS = Path(os.environ.get("VIGIE_COURRIER_CONTACTS", DIR / "courrier-contacts.json"))
MS_LOGIN = os.environ.get("MS_LOGIN_URL", "https://login.microsoftonline.com")
MS_SCOPE = "https://outlook.office.com/IMAP.AccessAsUser.All offline_access"
HUBSPOT_API = os.environ.get("HUBSPOT_API_URL", "https://api.hubapi.com")
CONTACTS_TOUS_LES_S = 6 * 3600   # relecture des « Envoyés » et de HubSpot
ENVOYES_MAX = 3000               # au premier apprentissage : les 3000 derniers envois
ENVOYES_BUDGET_S = 45            # dans le service, temps maximal d'apprentissage par passage

MQTT_HOST = os.environ.get("MQTT_HOST", "localhost")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_USER = os.environ.get("MQTT_USER", "")
MQTT_PASS = os.environ.get("MQTT_PASS", "")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
VIGIE_URL = os.environ.get("VIGIE_URL", f"http://localhost:{os.environ.get('VIGIE_PORT', '8765')}")
VIGIE_TOKEN = os.environ.get("VIGIE_TOKEN", "")
RAPPEL_AGENT_S = 30 * 60      # vigie.py oublie un agent muet depuis 2 h : on se rappelle à elle
MODEL = os.environ.get("VIGIE_MODEL", "llama3.2:3b-instruct-q5_K_M")

SUJET = "vigie/courrier"
SUJET_VU = "vigie/courrier/vu"   # l'écran y publie l'id d'un mail touché « vu »
MAX_PUBLIES = 8
MAX_PAR_PASSAGE = 25          # au-delà, on ne trie que les plus récents
PREMIER_PASSAGE_H = 24        # au tout premier passage : non lus des dernières 24 h
PREMIER_PASSAGE_MAX = 10
EXTRAIT_OCTETS = 200_000      # on ne télécharge que le début de chaque mail

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("courrier")


# --- Configuration ---------------------------------------------------------------

def sans_accents(texte):
    """Minuscules, sans accents ni apostrophes typographiques : « Réservé » = « reserve »."""
    t = unicodedata.normalize("NFKD", texte.lower().replace("’", "'"))
    return "".join(c for c in t if not unicodedata.combining(c))


def lire_conf():
    if not CONF.exists():
        sys.exit(f"Configuration absente : {CONF} (modèle : courrier.exemple.toml)")
    with open(CONF, "rb") as f:
        conf = tomllib.load(f)
    comptes = conf.get("compte", [])
    if not comptes:
        sys.exit(f"Aucun [[compte]] dans {CONF}")
    microsoft = conf.get("microsoft", {})
    for c in comptes:
        c.setdefault("auth", "mot_de_passe")
        if c["auth"] == "microsoft":
            c.setdefault("serveur", "outlook.office365.com")
            c.setdefault("client_id", microsoft.get("client_id", ""))
            c.setdefault("tenant", microsoft.get("tenant", "common"))
            obligatoires = ("nom", "utilisateur", "client_id")
        elif c["auth"] == "mot_de_passe":
            obligatoires = ("nom", "serveur", "utilisateur", "mot_de_passe")
        else:
            sys.exit(f"Compte {c.get('nom')} : auth = « mot_de_passe » ou « microsoft »")
        for cle in obligatoires:
            if not c.get(cle):
                sys.exit(f"Compte incomplet dans {CONF} : « {cle} » manque"
                         + (" (section [microsoft], voir README)" if cle == "client_id" else ""))
        c.setdefault("port", 993)
        c.setdefault("dossier", "INBOX")
        c.setdefault("apprendre_contacts", True)
    regles = conf.get("regles", {})
    hubspot = conf.get("hubspot", {})
    ecran = conf.get("ecran", {})
    llm = conf.get("llm", {})
    return {
        "comptes": comptes,
        "intervalle_s": int(conf.get("intervalle_s", 120)),
        "garder_h": float(conf.get("garder_h", 48)),
        "toujours": [s.lower() for s in regles.get("toujours", [])],
        "jamais": [s.lower() for s in regles.get("jamais", [])],
        "mots_urgents": [s.lower() for s in regles.get("mots_urgents", [])],
        "objets_importants": [sans_accents(s) for s in regles.get("objets_importants", [])],
        "llm_actif": bool(llm.get("actif", True)),
        "contexte": llm.get("contexte", "").strip(),
        "prenom": llm.get("prenom", "").strip() or "Camille",
        "modele": llm.get("modele", MODEL),
        "agents": bool(ecran.get("agents", True)),
        "hubspot_jeton": hubspot.get("jeton", "").strip(),
        "contacts": set(),          # rempli par Contacts
    }


# --- Lecture d'un mail ------------------------------------------------------------

RE_CITATION = re.compile(
    r"^(le .{5,80} a écrit ?:|on .{5,80} wrote:|-----\s*original message|de ?: .+@.+|from: .+@.+)",
    re.IGNORECASE)


def texte_brut(msg):
    """Le texte lisible du mail, sans citations ni signature, raccourci."""
    corps = ""
    try:
        partie = msg.get_body(preferencelist=("plain", "html"))
        if partie is not None:
            corps = partie.get_content()
            if partie.get_content_type() == "text/html":
                corps = re.sub(r"(?is)<(script|style).*?</\1>", " ", corps)
                corps = re.sub(r"(?s)<br\s*/?>|</p>|</div>", "\n", corps)
                corps = html.unescape(re.sub(r"(?s)<[^>]+>", " ", corps))
    except Exception:  # mail tronqué ou mal formé : on garde ce qu'on a
        pass
    lignes = []
    for ligne in corps.splitlines():
        l = ligne.strip()
        if l == "--" or RE_CITATION.match(l):
            break
        if l.startswith(">") or not l:
            continue
        lignes.append(re.sub(r"\s+", " ", l))
    return " ".join(lignes)[:900]


def lire_mail(brut):
    msg = email.message_from_bytes(brut, policy=email.policy.default)
    nom, adresse = email.utils.parseaddr(str(msg.get("From", "")))
    try:
        sujet = str(msg.get("Subject", "") or "").strip()
    except Exception:
        sujet = ""
    entetes = {k.lower(): str(v).lower() for k, v in msg.items()
               if k.lower() in ("list-unsubscribe", "list-id", "precedence", "auto-submitted")}
    en_masse = bool(
        "list-unsubscribe" in entetes or "list-id" in entetes
        or entetes.get("precedence", "") in ("bulk", "list", "junk")
        or entetes.get("auto-submitted", "no") not in ("no", "")
        or re.search(r"no-?reply|ne-?pas-?repondre|notification", adresse, re.I)
    )
    return {
        "de_nom": nom.strip() or adresse,
        "de_adresse": adresse.lower(),
        "sujet": re.sub(r"\s+", " ", sujet)[:200],
        "en_masse": en_masse,
        "texte": texte_brut(msg),
    }


# --- Tri --------------------------------------------------------------------------

CONSIGNE = """Tu tries le courrier de {prenom}. {contexte}

Classe le mail dans UNE catégorie :
- demande : on attend une réponse, un avis, un devis, une aide ou une action de {prenom}
- proposition : offre de collaboration, de projet, de partenariat, prospect qui se présente
- invitation : réunion, rendez-vous, événement, webinaire où {prenom} est invité personnellement
- probleme : panne, bug, réclamation, retard, souci à régler
- information : nouvelle utile écrite par une personne (avancement, document, compte rendu)
- remerciement : simple merci, accusé de réception, « bien reçu », sans rien de plus
- vide : pièce jointe ou lien envoyé sans texte utile
- automatique : réponse automatique d'absence, notification sans action, alerte
- commande : achat, expédition, livraison, facture ou reçu d'un site marchand
- publicite : promotion, lettre d'information, prospection commerciale de masse

Puis résume ce que l'expéditeur demande, propose ou annonce, de son point de vue, en
commençant par son nom (pour une notification, par le nom de la personne concernée).
8 à 14 mots, en français.
Réponds uniquement en JSON : {{"categorie": "...", "resume": "..."}}

Exemple :
De : Boutique Déco <news@boutique-deco.fr> (liste de diffusion)
Objet : -30 % sur toute la collection ce week-end
Texte : Profitez de nos offres exceptionnelles...
Réponse : {{"categorie": "publicite", "resume": "Boutique Déco annonce 30 % de remise ce week-end"}}

Exemple :
De : Claire Martin <claire.martin@agence-lumen.fr>
Objet : RE: Visite VR
Texte : Merci {prenom}, bien reçu, bonne journée.
Réponse : {{"categorie": "remerciement", "resume": "Claire Martin confirme avoir bien reçu l'envoi"}}

Exemple :
De : Paul Henri <p.henri@club-plongee.fr>
Objet : Site du club
Texte : Bonjour {prenom}, voici les textes pour la page d'accueil, peux-tu les mettre en ligne ?
Réponse : {{"categorie": "demande", "resume": "Paul Henri envoie les textes du site du club à mettre en ligne"}}

Exemple :
De : Marc Dupuis <m.dupuis@studio3d.fr>
Objet : Scènes VR pour un musée
Texte : Bonjour, nous cherchons un partenaire pour des visites virtuelles, seriez-vous disponible ?
Réponse : {{"categorie": "proposition", "resume": "Marc Dupuis cherche un partenaire pour des visites virtuelles de musée"}}

Mail à trier :
De : {de}{masse}
Objet : {sujet}
Texte : {texte}
Réponse :"""

# Catégories qui comptent : pour un inconnu, et en plus pour un contact connu.
CATEGORIES_IMPORTANTES = {"demande", "proposition", "invitation", "probleme"}
CATEGORIES_CONTACT = CATEGORIES_IMPORTANTES | {"information"}


def demander_llm(conf, mail):
    """Renvoie (categorie, resume)."""
    invite = CONSIGNE.format(
        contexte=conf["contexte"],
        prenom=conf["prenom"],
        de=f'{mail["de_nom"]} <{mail["de_adresse"]}>',
        masse=" (liste de diffusion)" if mail["en_masse"] else "",
        sujet=mail["sujet"] or "(sans objet)",
        texte=mail["texte"][:700] or "(vide)",
    )
    corps = json.dumps({
        "model": conf["modele"], "prompt": invite, "stream": False, "format": "json",
        "keep_alive": "15m", "options": {"temperature": 0.1, "num_predict": 100},
    }).encode()
    req = urllib.request.Request(f"{OLLAMA_URL}/api/generate", data=corps,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as r:
        reponse = json.loads(r.read())["response"]
    data = json.loads(reponse)
    categorie = sans_accents(str(data.get("categorie", ""))).strip()
    if categorie not in CATEGORIES_CONTACT | {"remerciement", "vide", "automatique", "commande", "publicite"}:
        categorie = "inconnue"
    resume = re.sub(r'\s+', " ", str(data.get("resume", "")).strip().strip('"«» '))
    resume = re.sub(r"\s*[—–]\s*", ", ", resume)[:110]   # règle d'affichage : pas de tiret long
    return categorie, resume


def contient(texte, motifs):
    return next((m for m in motifs if m and m in texte), None)


def raison_suivi(suivi):
    if suivi == "contact connu" or suivi.startswith("objet ("):
        return suivi
    return f"expéditeur suivi ({suivi})"


def trier(conf, mail):
    """Renvoie (important, resume, raison)."""
    expediteur = f'{mail["de_nom"]} {mail["de_adresse"]}'.lower()
    sujet = mail["sujet"].lower()

    motif = contient(expediteur, conf["jamais"])
    if motif:
        return False, mail["sujet"], f"règle jamais ({motif})"

    suivi = contient(expediteur, conf["toujours"])
    objet = contient(sans_accents(mail["sujet"]), conf["objets_importants"])
    if objet and not suivi:
        suivi = f"objet ({objet})"   # ex. notifications HubSpot : envois automatiques, mais importants
    # Contact connu : présomption seulement, le LLM peut écarter un mail sans contenu utile.
    connu = mail["de_adresse"] in conf["contacts"]
    urgent = contient(sujet, conf["mots_urgents"])
    if mail["en_masse"] and not suivi and not urgent:
        return False, mail["sujet"], "envoi en masse"

    if conf["llm_actif"]:
        try:
            categorie, resume = demander_llm(conf, mail)
            resume = resume or mail["sujet"]
            if suivi:
                return True, resume, raison_suivi(suivi)
            if connu:
                # Présomption : un contact compte, sauf merci, mail vide ou message automatique.
                important = categorie not in {"remerciement", "vide", "automatique", "commande", "publicite"}
                return important, resume, f"contact connu, LLM : {categorie}"
            return categorie in CATEGORIES_IMPORTANTES, resume, f"LLM : {categorie}"
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as e:
            log.warning("Ollama injoignable (%s) : tri par règles seulement", e)
        except (ValueError, KeyError) as e:
            log.warning("Réponse du LLM illisible (%s) : tri par règles seulement", e)

    if suivi:
        return True, mail["sujet"], raison_suivi(suivi)
    if connu and not mail["en_masse"]:
        return True, mail["sujet"], "contact connu (sans LLM)"
    if urgent:
        return True, mail["sujet"], f"mot-clé ({urgent})"
    return False, mail["sujet"], "aucune règle"


# --- IMAP -------------------------------------------------------------------------

MOIS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def date_imap(t):
    g = time.gmtime(t)
    return f"{g.tm_mday:02d}-{MOIS[g.tm_mon - 1]}-{g.tm_year}"


# --- OAuth2 Microsoft (Outlook.com, Hotmail, Microsoft 365) --------------------------

def lire_jetons():
    try:
        return json.loads(JETONS.read_text())
    except (OSError, ValueError):
        return {}


def sauver_jetons(j):
    tmp = JETONS.with_suffix(".tmp")
    tmp.write_text(json.dumps(j, indent=1))
    os.chmod(tmp, 0o600)
    tmp.replace(JETONS)


def ms_post(compte, chemin, champs):
    """POST vers login.microsoftonline.com ; renvoie le JSON, même en cas d'erreur HTTP."""
    url = f"{MS_LOGIN}/{compte['tenant']}/oauth2/v2.0/{chemin}"
    data = urllib.parse.urlencode(dict(champs, client_id=compte["client_id"])).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read())
        except ValueError:
            return {"error": f"http_{e.code}"}


def ms_connexion(compte):
    """Autorisation par code (« device code ») : une seule fois par compte."""
    d = ms_post(compte, "devicecode", {"scope": MS_SCOPE})
    if "device_code" not in d:
        sys.exit(f"Microsoft refuse la demande : {d.get('error')} — {d.get('error_description', '')[:300]}")
    print(f"\nCompte « {compte['nom']} » ({compte['utilisateur']}) :")
    print(f"  1. ouvre {d.get('verification_uri', 'https://microsoft.com/devicelogin')}")
    print(f"  2. entre le code  {d['user_code']}")
    print(f"  3. connecte-toi avec {compte['utilisateur']} et accepte l'accès « IMAP ».")
    print("En attente…", flush=True)
    intervalle = int(d.get("interval", 5))
    fin = time.time() + int(d.get("expires_in", 900))
    while time.time() < fin:
        time.sleep(intervalle)
        t = ms_post(compte, "token", {"grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                                      "device_code": d["device_code"]})
        if "refresh_token" in t:
            jetons = lire_jetons()
            jetons[compte["nom"]] = {"refresh_token": t["refresh_token"], "utilisateur": compte["utilisateur"]}
            sauver_jetons(jetons)
            print(f"Autorisé. Jeton rangé dans {JETONS}.")
            return
        if t.get("error") == "slow_down":
            intervalle += 5
        elif t.get("error") != "authorization_pending":
            sys.exit(f"Échec : {t.get('error')} — {t.get('error_description', '')[:300]}")
    sys.exit("Code expiré : relance --connexion.")


_acces = {}   # nom → (jeton d'accès, expiration)


def ms_jeton_acces(compte):
    nom = compte["nom"]
    jeton, expire = _acces.get(nom, (None, 0))
    if jeton and time.time() < expire - 120:
        return jeton
    jetons = lire_jetons()
    if nom not in jetons:
        raise RuntimeError(f"compte Microsoft non autorisé : lance « vigie-courrier.py --connexion {nom} »")
    t = ms_post(compte, "token", {"grant_type": "refresh_token", "scope": MS_SCOPE,
                                  "refresh_token": jetons[nom]["refresh_token"]})
    if "access_token" not in t:
        raise RuntimeError(f"jeton Microsoft refusé ({t.get('error')}) : relance « --connexion {nom} »")
    if t.get("refresh_token") and t["refresh_token"] != jetons[nom]["refresh_token"]:
        jetons[nom]["refresh_token"] = t["refresh_token"]   # Microsoft le renouvelle : on garde le neuf
        sauver_jetons(jetons)
    _acces[nom] = (t["access_token"], time.time() + int(t.get("expires_in", 3600)))
    return t["access_token"]


# --- IMAP -----------------------------------------------------------------------------

def nom_imap(dossier):
    return '"' + dossier.replace("\\", "\\\\").replace('"', '\\"') + '"'


class Boite:
    """Une boîte IMAP ouverte en lecture seule le temps d'un passage."""

    def __init__(self, compte):
        self.c = compte
        self.m = None

    def __enter__(self):
        contexte = ssl.create_default_context()
        if not self.c.get("verifier_certificat", True):  # serveur maison à certificat auto-signé
            contexte.check_hostname = False
            contexte.verify_mode = ssl.CERT_NONE
        self.m = imaplib.IMAP4_SSL(self.c["serveur"], int(self.c["port"]),
                                   ssl_context=contexte, timeout=40)
        try:
            if self.c["auth"] == "microsoft":
                chaine = f"user={self.c['utilisateur']}\x01auth=Bearer {ms_jeton_acces(self.c)}\x01\x01"
                essais = []

                def reponse(defi):
                    # 1er tour : le jeton. Si le serveur relance (refus), il envoie le détail
                    # de l'erreur et attend une réponse vide ; renvoyer le jeton le ferait boucler.
                    essais.append(defi)
                    if len(essais) == 1:
                        return chaine.encode()
                    log.warning("[%s] refus du serveur : %s", self.c["nom"],
                                (defi or b"").decode(errors="replace")[:300])
                    return b""

                self.m.authenticate("XOAUTH2", reponse)
            else:
                self.m.login(self.c["utilisateur"], self.c["mot_de_passe"])
        except imaplib.IMAP4.error as e:
            if self.c["auth"] == "microsoft":
                conseil = ("IMAP désactivé sur la boîte, ou accès refusé par l'administrateur "
                           "Microsoft 365 ; sinon relancer --connexion " + self.c["nom"])
            else:
                conseil = ("Gmail : mot de passe d'application ; OVH : mot de passe de la boîte "
                           "(pas celui de l'espace client)")
            raise RuntimeError(f"connexion refusée ({e}). {conseil}.") from None
        return self

    def ouvrir(self, dossier):
        """Sélectionne un dossier en lecture seule ; renvoie (uidvalidity, uidnext)."""
        nom = nom_imap(dossier)
        typ, data = self.m.status(nom, "(UIDNEXT UIDVALIDITY)")
        texte = data[0].decode(errors="replace") if typ == "OK" and data and data[0] else ""
        self.uidnext = int(re.search(r"UIDNEXT (\d+)", texte).group(1)) if "UIDNEXT" in texte else None
        self.uidvalidity = int(re.search(r"UIDVALIDITY (\d+)", texte).group(1)) if "UIDVALIDITY" in texte else 0
        typ, _ = self.m.select(nom, readonly=True)
        if typ != "OK":
            raise RuntimeError(f"dossier {dossier} introuvable")
        return self.uidvalidity, self.uidnext

    def dossier_envoyes(self):
        """Le dossier des messages envoyés : attribut \\Sent, sinon un nom habituel."""
        if self.c.get("dossier_envoyes"):
            return self.c["dossier_envoyes"]
        typ, data = self.m.list()
        noms = []
        for ligne in data or []:
            if not isinstance(ligne, bytes):
                continue
            m = re.match(rb'\((?P<attr>[^)]*)\) (?:"[^"]*"|NIL) (?P<nom>.+)$', ligne)
            if not m:
                continue
            nom = m.group("nom").decode(errors="replace").strip()
            if nom.startswith('"') and nom.endswith('"'):
                nom = nom[1:-1].replace('\\"', '"').replace("\\\\", "\\")
            if b"\\sent" in m.group("attr").lower():
                return nom
            noms.append(nom)
        habituels = ["Sent", "Sent Items", "Sent Messages", "INBOX.Sent", "Envoy&AOk-s",
                     "&AMk-l&AOk-ments envoy&AOk-s", "[Gmail]/Sent Mail", "INBOX.Envoy&AOk-s"]
        for h in habituels:   # nom exact, ou dernier élément du chemin (INBOX.Éléments envoyés)
            for n in noms:
                dernier = re.split(r"[./]", n)[-1].lower()
                if n.lower() == h.lower() or dernier == h.lower():
                    return n
        return None

    def destinataires(self, uids):
        """Adresses des champs To, Cc et Bcc des messages donnés."""
        adresses = set()
        for i in range(0, len(uids), 200):
            lot = ",".join(str(u) for u in uids[i:i + 200])
            typ, data = self.m.uid("FETCH", lot, "(BODY.PEEK[HEADER.FIELDS (TO CC BCC)])")
            if typ != "OK":
                continue
            for morceau in data:
                if isinstance(morceau, tuple) and len(morceau) == 2:
                    entetes = email.message_from_bytes(morceau[1], policy=email.policy.compat32)
                    valeurs = [str(v) for k in ("To", "Cc", "Bcc") for v in entetes.get_all(k, [])]
                    for _, adr in email.utils.getaddresses(valeurs):
                        adr = adr.strip().lower()
                        if "@" in adr:
                            adresses.add(adr)
        return adresses

    def __exit__(self, *exc):
        try:
            self.m.logout()
        except Exception:
            pass

    def chercher(self, *critere):
        typ, data = self.m.uid("SEARCH", None, *critere)
        if typ != "OK" or not data or not data[0]:
            return []
        return sorted(int(x) for x in data[0].split())

    def dernier_uid(self):
        if self.uidnext:
            return self.uidnext - 1
        uids = self.chercher("ALL")
        return uids[-1] if uids else 0

    def lire(self, uid):
        """(drapeaux, date de réception, octets du mail) ou None si le mail a disparu."""
        typ, data = self.m.uid("FETCH", str(uid), f"(FLAGS INTERNALDATE BODY.PEEK[]<0.{EXTRAIT_OCTETS}>)")
        if typ != "OK":
            return None
        for morceau in data:
            if isinstance(morceau, tuple) and len(morceau) == 2:
                entete, brut = morceau
                drapeaux = re.search(rb"FLAGS \(([^)]*)\)", entete)
                drapeaux = drapeaux.group(1).decode().split() if drapeaux else []
                date = imaplib.Internaldate2tuple(entete)
                recu = time.mktime(date) if date else time.time()
                return drapeaux, recu, brut
        return None

    def drapeaux(self, uids):
        """{uid: [drapeaux]} pour les mails encore présents."""
        if not uids:
            return {}
        typ, data = self.m.uid("FETCH", ",".join(str(u) for u in uids), "(FLAGS)")
        res = {}
        if typ != "OK":
            return res
        for ligne in data:
            if isinstance(ligne, tuple):
                ligne = ligne[0]
            if not isinstance(ligne, bytes):
                continue
            u = re.search(rb"UID (\d+)", ligne)
            f = re.search(rb"FLAGS \(([^)]*)\)", ligne)
            if u:
                res[int(u.group(1))] = f.group(1).decode().split() if f else []
        return res


# --- État et publication ------------------------------------------------------------

def charger_etat():
    try:
        return json.loads(ETAT.read_text())
    except (OSError, ValueError):
        return {}


def sauver_etat(etat):
    tmp = ETAT.with_suffix(".tmp")
    tmp.write_text(json.dumps(etat, ensure_ascii=False, indent=1))
    os.chmod(tmp, 0o600)
    tmp.replace(ETAT)


def journaliser(entree):
    try:
        with open(os.open(JOURNAL, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600), "a", encoding="utf-8") as f:
            f.write(json.dumps(entree, ensure_ascii=False) + "\n")
    except OSError:
        pass


class Bus:
    def __init__(self):
        self.client = None
        if mqtt is None:
            log.warning("paho-mqtt absent : rien ne sera publié")
            return
        try:
            self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="vigie-courrier")
        except AttributeError:  # paho-mqtt 1.x
            self.client = mqtt.Client(client_id="vigie-courrier")
        if MQTT_USER:
            self.client.username_pw_set(MQTT_USER, MQTT_PASS)
        self.client.on_connect = self._connecte
        self.client.on_message = self._message
        self.vus = queue.Queue()
        self.dernier = None
        self.client.reconnect_delay_set(1, 30)
        self.client.connect_async(MQTT_HOST, MQTT_PORT, keepalive=30)
        self.client.loop_start()

    def _connecte(self, client, userdata, flags, rc, *args):
        log.info("MQTT connecté à %s:%s", MQTT_HOST, MQTT_PORT)
        client.subscribe(SUJET_VU, qos=1)
        if self.dernier is not None:
            client.publish(SUJET, self.dernier, qos=1, retain=True)

    def _message(self, client, userdata, msg, *args):
        if msg.topic == SUJET_VU and msg.payload:
            self.vus.put(msg.payload.decode(errors="replace").strip())

    def publier(self, charge):
        texte = json.dumps(charge, ensure_ascii=False)
        self.dernier = texte
        if self.client is not None:
            self.client.publish(SUJET, texte, qos=1, retain=True)

    def attendre_vu(self, delai):
        """Un id de mail marqué vu sur l'écran, ou None au bout de `delai` secondes."""
        if self.client is None:
            time.sleep(delai)
            return None
        try:
            return self.vus.get(timeout=delai)
        except queue.Empty:
            return None

    def fermer(self):
        if self.client is not None:
            self.client.loop_stop()
            self.client.disconnect()


class Agents:
    """Une boîte qui a des mails importants non lus apparaît comme un agent de la Vigie,
    « Courrier <nom> », qui « attend » avec le message « 2 mails importants ». Sans mail
    important, l'agent est retiré (l'enveloppe de l'écran suffit) : pas de ligne « prêt »
    qui ne dit rien et prendrait la place d'un vrai agent dans la liste."""

    def __init__(self, actif):
        self.actif = actif
        self.envoye = {}   # nom → (etat, message, heure)

    def _poster(self, corps):
        req = urllib.request.Request(f"{VIGIE_URL}/event", data=json.dumps(corps).encode(),
                                     headers={"Content-Type": "application/json",
                                              "X-Vigie-Token": VIGIE_TOKEN})
        try:
            with urllib.request.urlopen(req, timeout=5):
                return True
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as e:
            log.warning("Vigie injoignable (%s)", e)
            return False

    def signaler(self, nom, e):
        if not self.actif:
            return
        n = len(e.get("importants", []))
        if e.get("erreur"):
            etat, message = "pret", "relève impossible"
        elif n:
            etat = "attend"
            message = "1 mail important" if n == 1 else f"{n} mails importants"
        else:
            etat, message = "absent", ""
        avant = self.envoye.get(nom)
        if avant and avant[:2] == (etat, message) and (etat == "absent"
                                                       or time.time() - avant[2] < RAPPEL_AGENT_S):
            return
        if etat == "absent":
            ok = self._poster({"agent": f"courrier-{nom}", "hook_event_name": "SessionEnd"})
        else:
            ok = self._poster({"source": "courrier", "agent": f"courrier-{nom}",
                               "projet": f"Courrier {nom}", "etat": etat, "message": message})
        if ok:
            self.envoye[nom] = (etat, message, time.time())

    def retirer(self, nom):
        if self.actif and self._poster({"agent": f"courrier-{nom}", "hook_event_name": "SessionEnd"}):
            self.envoye.pop(nom, None)


class Contacts:
    """Adresses connues : destinataires de tes envois (chaque boîte) + contacts HubSpot.

    Fichier ~/vigie/courrier-contacts.json :
      {"adresses": [...], "envoyes": {compte: {"dossier", "uidvalidity", "dernier_uid"}},
       "hubspot": [...], "maj": {compte|"hubspot": horodatage}}
    """

    def __init__(self, conf):
        self.conf = conf
        try:
            self.d = json.loads(CONTACTS.read_text())
        except (OSError, ValueError):
            self.d = {}
        self.d.setdefault("adresses", [])
        self.d.setdefault("hubspot", [])
        self.d.setdefault("envoyes", {})
        self.d.setdefault("maj", {})
        self.miens = {c["utilisateur"].lower() for c in conf["comptes"]}
        self._publier()

    def _publier(self):
        tout = set(self.d["adresses"]) | set(self.d["hubspot"])
        self.conf["contacts"] = {a for a in tout if a not in self.miens
                                 and not re.search(r"no-?reply|ne-?pas-?repondre", a)}

    def _sauver(self):
        tmp = CONTACTS.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.d, ensure_ascii=False))
        os.chmod(tmp, 0o600)
        tmp.replace(CONTACTS)

    def a_jour(self, cle):
        return time.time() - self.d["maj"].get(cle, 0) < CONTACTS_TOUS_LES_S

    def apprendre_envoyes(self, compte, b, forcer=False):
        """À appeler avec une Boite connectée. Relit seulement les envois nouveaux.

        Par lots de 50, avec la progression enregistrée après chaque lot : un premier
        apprentissage long (serveurs Microsoft lents) peut s'interrompre et reprendre au
        passage suivant. Dans le service, 45 s au plus par passage pour ne pas retarder le
        tri ; avec --contacts (forcer), jusqu'au bout."""
        nom = compte["nom"]
        if not compte.get("apprendre_contacts", True) or (self.a_jour(nom) and not forcer):
            return
        dossier = b.dossier_envoyes()
        if not dossier:
            log.warning("[%s] dossier « Envoyés » introuvable (régler dossier_envoyes)", nom)
            self.d["maj"][nom] = time.time()
            return
        uidvalidity, _ = b.ouvrir(dossier)
        e = self.d["envoyes"].get(nom, {})
        if e.get("dossier") != dossier or e.get("uidvalidity") != uidvalidity:
            uids = b.chercher("ALL")[-ENVOYES_MAX:]
            e = {"dossier": dossier, "uidvalidity": uidvalidity, "dernier_uid": 0}
        else:
            uids = [u for u in b.chercher("UID", f"{e['dernier_uid'] + 1}:*") if u > e["dernier_uid"]]
        avant = len(self.d["adresses"])
        limite = None if forcer else time.time() + ENVOYES_BUDGET_S
        lus = 0
        for i in range(0, len(uids), 50):
            lot = uids[i:i + 50]
            self.d["adresses"] = sorted(set(self.d["adresses"]) | b.destinataires(lot))
            e["dernier_uid"] = lot[-1]
            self.d["envoyes"][nom] = e
            self._sauver()
            lus += len(lot)
            if len(uids) > 100:
                log.info("[%s] « %s » : %d / %d envois lus…", nom, dossier, lus, len(uids))
            if limite and time.time() > limite and lus < len(uids):
                self._publier()
                log.info("[%s] apprentissage des contacts interrompu, reprise au prochain passage", nom)
                return
        self.d["envoyes"][nom] = e
        self.d["maj"][nom] = time.time()
        self._sauver()
        self._publier()
        log.info("[%s] « %s » : %d envoi(s) lu(s), %d nouveau(x) contact(s)",
                 nom, dossier, len(uids), len(self.d["adresses"]) - avant)

    def apprendre_hubspot(self, forcer=False):
        jeton = self.conf["hubspot_jeton"]
        if not jeton or (self.a_jour("hubspot") and not forcer):
            return
        adresses, apres = set(), None
        try:
            for _ in range(500):   # 50 000 contacts au plus
                params = {"limit": 100, "properties": "email", "archived": "false"}
                if apres:
                    params["after"] = apres
                req = urllib.request.Request(
                    f"{HUBSPOT_API}/crm/v3/objects/contacts?{urllib.parse.urlencode(params)}",
                    headers={"Authorization": f"Bearer {jeton}"})
                with urllib.request.urlopen(req, timeout=30) as r:
                    page = json.loads(r.read())
                for c in page.get("results", []):
                    adr = (c.get("properties") or {}).get("email") or ""
                    if "@" in adr:
                        adresses.add(adr.strip().lower())
                apres = ((page.get("paging") or {}).get("next") or {}).get("after")
                if not apres:
                    break
        except urllib.error.HTTPError as err:
            log.warning("HubSpot refuse la lecture des contacts (HTTP %s) : jeton ou droits "
                        "« crm.objects.contacts.read » à vérifier", err.code)
            return
        except (urllib.error.URLError, socket.timeout, TimeoutError, ValueError) as err:
            log.warning("HubSpot injoignable (%s)", err)
            return
        self.d["hubspot"] = sorted(adresses)
        self.d["maj"]["hubspot"] = time.time()
        self._sauver()
        self._publier()
        log.info("HubSpot : %d contact(s)", len(adresses))


def charge_publiee(etat):
    tous = []
    for nom, e in etat.items():
        for m in e.get("importants", []):
            tous.append({"id": f"{nom}:{m['uid']}", "compte": nom, "de": m["de"],
                         "sujet": m["sujet"], "resume": m["resume"], "raison": m["raison"],
                         "recu": int(m["recu"])})
    tous.sort(key=lambda m: m["recu"], reverse=True)
    return {"nb": len(tous), "importants": tous[:MAX_PUBLIES]}


# --- Un passage ---------------------------------------------------------------------

def passer_compte(conf, compte, e, contacts):
    """Relève un compte ; met à jour son état `e` en place."""
    nom = compte["nom"]
    maintenant = time.time()
    with Boite(compte) as b:
        try:
            contacts.apprendre_envoyes(compte, b)
        except (imaplib.IMAP4.error, RuntimeError) as err:
            log.warning("[%s] contacts non relus : %s", nom, err)
        b.ouvrir(compte["dossier"])
        if e.get("uidvalidity") != b.uidvalidity or "dernier_uid" not in e:
            # Premier passage (ou boîte renumérotée) : on ne remonte pas tout l'historique.
            log.info("[%s] premier passage : non lus des dernières %d h", nom, PREMIER_PASSAGE_H)
            candidats = b.chercher("UNSEEN", "SINCE", date_imap(maintenant - PREMIER_PASSAGE_H * 3600))
            candidats = candidats[-PREMIER_PASSAGE_MAX:]
            e.clear()
            e.update({"uidvalidity": b.uidvalidity, "dernier_uid": b.dernier_uid(), "importants": []})
        else:
            candidats = [u for u in b.chercher("UID", f"{e['dernier_uid'] + 1}:*") if u > e["dernier_uid"]]
            if candidats:
                e["dernier_uid"] = max(e["dernier_uid"], candidats[-1])
            candidats = candidats[-MAX_PAR_PASSAGE:]

        for uid in candidats:
            lu = b.lire(uid)
            if lu is None:
                continue
            drapeaux, recu, brut = lu
            if "\\Seen" in drapeaux or "\\Deleted" in drapeaux:
                continue  # déjà lu ailleurs : rien à signaler
            mail = lire_mail(brut)
            important, resume, raison = trier(conf, mail)
            log.info("[%s] %s — %s : %s (%s)", nom, mail["de_nom"], mail["sujet"][:60],
                     "IMPORTANT" if important else "ignoré", raison)
            journaliser({"t": int(maintenant), "compte": nom, "uid": uid, "de": mail["de_adresse"],
                         "sujet": mail["sujet"], "important": important, "raison": raison,
                         "resume": resume})
            if important:
                e["importants"].append({"uid": uid, "de": mail["de_nom"][:60], "sujet": mail["sujet"][:120],
                                        "resume": resume, "raison": raison, "recu": recu})

        # Ce qui a été lu, supprimé ou qui a vieilli sort de la liste.
        suivis = [m["uid"] for m in e["importants"]]
        presents = b.drapeaux(suivis)
        limite = maintenant - conf["garder_h"] * 3600
        e["importants"] = [
            m for m in e["importants"]
            if m["uid"] in presents and "\\Seen" not in presents[m["uid"]]
            and "\\Deleted" not in presents[m["uid"]] and m["recu"] >= limite
        ]


def passage(conf, etat, bus, agents, contacts):
    contacts.apprendre_hubspot()
    for compte in conf["comptes"]:
        e = etat.setdefault(compte["nom"], {})
        try:
            passer_compte(conf, compte, e, contacts)
            e.pop("erreur", None)
        except (OSError, imaplib.IMAP4.error, RuntimeError) as err:
            log.error("[%s] relève impossible : %s", compte["nom"], err)
            e["erreur"] = str(err)
        agents.signaler(compte["nom"], e)
    # Les comptes retirés de la configuration disparaissent aussi de l'état et de l'écran.
    for nom in [n for n in etat if n not in {c["nom"] for c in conf["comptes"]}]:
        del etat[nom]
        agents.retirer(nom)
    sauver_etat(etat)
    charge = charge_publiee(etat)
    charge["maj"] = int(time.time())
    bus.publier(charge)
    return charge


def marquer_vu(etat, ident, bus, agents):
    """Retire un mail de la liste des importants (sans le marquer lu dans la boîte)."""
    nom, _, uid = ident.rpartition(":")
    e = etat.get(nom)
    if not e or not uid.isdigit():
        log.warning("Mail vu inconnu : %s", ident)
        return
    avant = len(e.get("importants", []))
    e["importants"] = [m for m in e.get("importants", []) if m["uid"] != int(uid)]
    if len(e["importants"]) == avant:
        return
    log.info("[%s] mail %s marqué vu sur l'écran", nom, uid)
    journaliser({"t": int(time.time()), "compte": nom, "uid": int(uid), "vu_sur_ecran": True})
    sauver_etat(etat)
    charge = charge_publiee(etat)
    charge["maj"] = int(time.time())
    bus.publier(charge)
    agents.signaler(nom, e)


# --- Essai ------------------------------------------------------------------------------

def essai(conf, n, contacts):
    contacts.apprendre_hubspot()
    for compte in conf["comptes"]:
        print(f"\n=== {compte['nom']} ({compte['utilisateur']}) — {n} derniers mails")
        with Boite(compte) as b:
            contacts.apprendre_envoyes(compte, b)
            b.ouvrir(compte["dossier"])
            uids = b.chercher("ALL")[-n:]
            for uid in reversed(uids):
                lu = b.lire(uid)
                if lu is None:
                    continue
                mail = lire_mail(lu[2])
                important, resume, raison = trier(conf, mail)
                marque = "★" if important else "·"
                print(f"{marque} {mail['de_nom'][:28]:28} {mail['sujet'][:50]:50} [{raison}]")
                if important:
                    print(f"    → {resume}")


# --- Programme principal ------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(description="Vigie — tri du courrier")
    p.add_argument("--une-fois", action="store_true", help="un seul passage puis quitte")
    p.add_argument("--essai", nargs="?", const=10, type=int, metavar="N",
                   help="trie les N derniers mails sans rien publier")
    p.add_argument("--connexion", metavar="NOM", help="autorise un compte Microsoft (une fois)")
    p.add_argument("--contacts", action="store_true", help="apprend les contacts et affiche leur nombre")
    args = p.parse_args()
    conf = lire_conf()

    if args.connexion:
        compte = next((c for c in conf["comptes"] if c["nom"] == args.connexion), None)
        if not compte or compte["auth"] != "microsoft":
            sys.exit(f"Pas de compte « {args.connexion} » avec auth = \"microsoft\" dans {CONF}")
        ms_connexion(compte)
        return

    contacts = Contacts(conf)
    if args.contacts:
        contacts.apprendre_hubspot(forcer=True)
        for compte in conf["comptes"]:
            try:
                with Boite(compte) as b:
                    contacts.apprendre_envoyes(compte, b, forcer=True)
            except (OSError, imaplib.IMAP4.error, RuntimeError) as err:
                print(f"[{compte['nom']}] {err}")
        print(f"{len(contacts.d['adresses'])} adresse(s) tirée(s) des envois, "
              f"{len(contacts.d['hubspot'])} de HubSpot, {len(conf['contacts'])} contact(s) en tout.")
        return

    if args.essai:
        essai(conf, args.essai, contacts)
        return

    bus = Bus()
    agents = Agents(conf["agents"])
    etat = charger_etat()
    if etat:  # republie tout de suite la dernière liste connue
        charge = charge_publiee(etat)
        charge["maj"] = int(time.time())
        bus.publier(charge)

    arret = {"demande": False}
    signal.signal(signal.SIGTERM, lambda *a: arret.update(demande=True))
    log.info("Tri du courrier : %d compte(s), relève toutes les %d s",
             len(conf["comptes"]), conf["intervalle_s"])
    try:
        while not arret["demande"]:
            charge = passage(conf, etat, bus, agents, contacts)
            log.info("%d mail(s) important(s) non lu(s)", charge["nb"])
            if args.une_fois:
                time.sleep(1)  # laisse partir le message MQTT
                break
            fin = time.time() + conf["intervalle_s"]
            while time.time() < fin and not arret["demande"]:
                ident = bus.attendre_vu(1)
                if ident:
                    marquer_vu(etat, ident, bus, agents)
    except KeyboardInterrupt:
        pass
    finally:
        bus.fermer()


if __name__ == "__main__":
    main()
