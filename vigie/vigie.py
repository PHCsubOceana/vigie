#!/usr/bin/env python3
"""
Vigie : le cerveau, côté Jetson.

Reçoit les événements des agents (hooks de Claude Code, scripts, autres agents)
en HTTP, tient l'état de chaque agent, en déduit une humeur, fait écrire une
phrase courte par le LLM local (Ollama), et publie le tout en MQTT pour
l'écran rond (ESP32-P4).

  POST /event    un événement JSON (format hook Claude Code, ou format générique)
  GET  /etat     l'état courant, en JSON (pratique pour déboguer)
  GET  /sante    renvoie « ok »

MQTT publié :
  vigie/etat        état complet (retenu) : humeur, agents, phrase
  vigie/evenement   chaque événement reçu, résumé (non retenu)

Dépendance : paho-mqtt (facultative — sans elle, le service tourne sans MQTT).
"""

import json
import logging
import os
import re
import socket
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# --- Configuration (variables d'environnement, fichier vigie.env) -------------

PORT = int(os.environ.get("VIGIE_PORT", "8765"))
TOKEN = os.environ.get("VIGIE_TOKEN", "")  # vide = pas de contrôle
MQTT_HOST = os.environ.get("MQTT_HOST", "localhost")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_USER = os.environ.get("MQTT_USER", "")
MQTT_PASS = os.environ.get("MQTT_PASS", "")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
MODEL = os.environ.get("VIGIE_MODEL", "llama3.2:3b-instruct-q5_K_M")
LLM_ACTIF = os.environ.get("VIGIE_LLM", "1") != "0"
JOURNAL = Path(os.path.expanduser(os.environ.get("VIGIE_JOURNAL", "~/vigie/journal.jsonl")))
OUBLI_S = int(os.environ.get("VIGIE_OUBLI_S", str(2 * 3600)))  # agent muet → retiré
PRENOM = os.environ.get("VIGIE_PRENOM", "").strip() or "Camille"  # à qui parle la Vigie
SATISFAITE_S = int(os.environ.get("VIGIE_SATISFAITE_S", "600"))  # air content après une fin

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("vigie")

# --- MQTT ---------------------------------------------------------------------

try:
    import paho.mqtt.client as mqtt
except ImportError:  # le service reste utilisable sans broker
    mqtt = None


class Bus:
    def __init__(self):
        self.client = None
        if mqtt is None:
            log.warning("paho-mqtt absent : pas de publication MQTT")
            return
        try:
            self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="vigie-cerveau")
        except AttributeError:  # paho-mqtt 1.x
            self.client = mqtt.Client(client_id="vigie-cerveau")
        if MQTT_USER:
            self.client.username_pw_set(MQTT_USER, MQTT_PASS)
        self.client.will_set("vigie/cerveau", "hors-ligne", retain=True)
        self.client.on_connect = self._connecte
        self.client.reconnect_delay_set(1, 30)
        self.client.connect_async(MQTT_HOST, MQTT_PORT, keepalive=30)
        self.client.loop_start()

    def _connecte(self, client, userdata, flags, rc, *args):
        log.info("MQTT connecté à %s:%s (code %s)", MQTT_HOST, MQTT_PORT, rc)
        client.publish("vigie/cerveau", "en-ligne", retain=True)
        etat.publier()  # republie l'état après une reconnexion

    def publier(self, sujet, charge, retenu=False):
        if self.client is not None:
            self.client.publish(sujet, json.dumps(charge, ensure_ascii=False), qos=1, retain=retenu)


# --- État des agents ------------------------------------------------------------

LIBELLES = {"travaille": "travaille", "attend": "t'attend", "fini": "a fini", "pret": "est prêt"}
PRIORITE = {"attend": 0, "fini": 1, "travaille": 2, "pret": 3}


# --- Nom des postes -----------------------------------------------------------------
#
# Les hooks de Claude Code n'envoient pas le nom de la machine : on le déduit de
# l'adresse IP de l'appel. Ordre de priorité :
#   1. ~/vigie/postes.json, si tu y as donné un nom : {"192.168.1.20": "Bureau"}
#   2. le nom réseau de la machine (résolution inverse via la box), sans le domaine
#   3. l'adresse IP elle-même
# Le fichier est relu dès qu'il change : pas besoin de redémarrer le service.

FICHIER_POSTES = Path(os.path.expanduser(
    os.environ.get("VIGIE_POSTES", str(JOURNAL.parent / "postes.json"))))


class Postes:
    def __init__(self):
        self.verrou = threading.Lock()
        self.noms_fichier = {}
        self.date_fichier = None
        self.noms_reseau = {}       # ip -> nom (ou None pendant la recherche)

    def _relire(self):
        try:
            date = FICHIER_POSTES.stat().st_mtime
        except OSError:
            self.noms_fichier, self.date_fichier = {}, None
            return
        if date != self.date_fichier:
            try:
                self.noms_fichier = {str(k): str(v) for k, v in
                                     json.loads(FICHIER_POSTES.read_text(encoding="utf-8")).items()}
                log.info("Noms de postes chargés : %s", self.noms_fichier)
            except (OSError, ValueError) as e:
                log.warning("postes.json illisible (%s)", e)
            self.date_fichier = date

    def _chercher(self, ip):
        try:
            nom = socket.gethostbyaddr(ip)[0].split(".")[0]
        except OSError:
            nom = ip
        with self.verrou:
            self.noms_reseau[ip] = nom
        log.info("Nouveau poste %s → « %s » (renommable dans %s)", ip, nom, FICHIER_POSTES)

    def nom(self, ip):
        with self.verrou:
            self._relire()
            if ip in self.noms_fichier:
                return self.noms_fichier[ip]
            if ip in ("127.0.0.1", "::1"):
                return socket.gethostname()
            if ip not in self.noms_reseau:
                self.noms_reseau[ip] = None
                threading.Thread(target=self._chercher, args=(ip,), daemon=True).start()
            return self.noms_reseau[ip] or ip


postes = Postes()


def dossier_de(ev):
    cwd = ev.get("cwd") or ""
    return Path(cwd.replace("\\", "/")).name


def nom_projet(ev):
    """Nom affiché : celui du poste pour Claude Code, celui donné par le script sinon."""
    if ev.get("projet"):
        return str(ev["projet"])
    if ev.get("_poste"):
        return ev["_poste"]
    return dossier_de(ev) or "sans nom"


TRADUCTIONS = [
    (re.compile(r"Claude needs your permission to use (.+)", re.I), r"permission pour \1"),
    (re.compile(r"Claude is waiting for your input", re.I), "attend ta réponse"),
    (re.compile(r"Claude needs your attention", re.I), "a besoin de toi"),
]


def traduire(message):
    """Les notifications de Claude Code arrivent en anglais."""
    for motif, francais in TRADUCTIONS:
        if motif.search(message):
            return motif.sub(francais, message).strip()
    return message


def etat_depuis_evenement(ev):
    """Traduit un événement en état d'agent. None = retirer l'agent."""
    if ev.get("etat") in LIBELLES:  # format générique
        return ev["etat"]
    nom = ev.get("hook_event_name", "")
    if nom == "SessionEnd":
        return None
    if nom == "SessionStart":
        return "pret"
    if nom in ("UserPromptSubmit", "PreToolUse", "PostToolUse", "SubagentStop"):
        return "travaille"
    if nom == "Stop":
        return "fini"
    if nom == "Notification":
        # idle_prompt : Claude a fini et attend la suite ; le reste : il a besoin de toi
        return "fini" if ev.get("notification_type") == "idle_prompt" else "attend"
    return "travaille"


class Etat:
    def __init__(self):
        self.verrou = threading.Lock()
        self.agents = {}
        self.phrase = "Rien à signaler."
        self.version = 0
        self.reveil = threading.Event()

    def evenement(self, ev):
        sid = str(ev.get("session_id") or ev.get("agent") or "inconnu")
        # Claude Code a son propre champ « source » (startup, resume…) : on l'ignore
        source = "claude-code" if "hook_event_name" in ev else str(ev.get("source") or "script")
        nouvel_etat = etat_depuis_evenement(ev)
        maintenant = time.time()
        with self.verrou:
            ancien = self.agents.get(sid, {}).get("etat")
            if nouvel_etat is None:
                self.agents.pop(sid, None)
            else:
                agent = self.agents.setdefault(sid, {"id": sid, "source": source, "depuis": maintenant})
                agent["projet"] = nom_projet(ev)
                agent["dossier"] = dossier_de(ev)
                agent["poste"] = ev.get("_poste", "")
                agent["vu"] = maintenant
                if nouvel_etat != ancien:
                    agent["etat"] = nouvel_etat
                    agent["depuis"] = maintenant
                agent["message"] = traduire(str(ev.get("message") or ""))[:200] if nouvel_etat == "attend" else ""
            change = nouvel_etat != ancien
            if change:
                self.version += 1
                self.phrase = self._phrase_simple()
        resume = {"agent": sid[:8], "source": source, "projet": nom_projet(ev),
                  "evenement": ev.get("hook_event_name") or ev.get("etat"), "etat": nouvel_etat}
        journaliser(dict(resume, t=maintenant))
        bus.publier("vigie/evenement", resume)
        if change:
            self.publier()           # réaction immédiate, phrase simple
            self.reveil.set()        # puis le LLM affine la phrase
        return change

    def oublier_les_muets(self):
        limite = time.time() - OUBLI_S
        with self.verrou:
            muets = [k for k, a in self.agents.items() if a["vu"] < limite]
            for k in muets:
                del self.agents[k]
            if muets:
                self.version += 1
                self.phrase = self._phrase_simple()
        if muets:
            self.publier()

    def humeur(self):
        etats = {a["etat"] for a in self.agents.values()}
        if "attend" in etats:
            return "attentive"
        if "travaille" in etats:
            return "affairee"
        recent = time.time() - SATISFAITE_S
        if any(a["etat"] == "fini" and a["depuis"] > recent for a in self.agents.values()):
            return "satisfaite"
        return "calme"

    def _phrase_simple(self):
        attente = [a for a in self.agents.values() if a["etat"] == "attend"]
        if attente:
            if len(attente) > 1:
                return f"{len(attente)} agents attendent ta réponse."
            a = attente[0]
            if a["message"].startswith("permission"):
                return f"{a['projet']} demande ton autorisation."
            return f"{a['projet']} attend ta réponse."
        finis = [a for a in self.agents.values() if a["etat"] == "fini"]
        if finis:
            finis.sort(key=lambda a: -a["depuis"])
            return f"{finis[0]['projet']} a terminé."
        actifs = [a for a in self.agents.values() if a["etat"] == "travaille"]
        if actifs:
            return f"{actifs[0]['projet']} est en cours, rien à signaler." if len(actifs) == 1 \
                else f"{len(actifs)} tâches en cours, rien à signaler."
        return "Rien à signaler."

    def instantane(self):
        with self.verrou:
            # le plus important d'abord : qui t'attend, puis ce qui vient de finir, etc.
            agents = sorted(self.agents.values(),
                            key=lambda a: (PRIORITE[a["etat"]], -a["depuis"]))
            return {
                "humeur": self.humeur(),
                "phrase": self.phrase,
                "agents": [{"id": a["id"][:8], "source": a["source"], "projet": a["projet"],
                            "poste": a.get("poste", ""), "dossier": a.get("dossier", ""),
                            "etat": a["etat"], "depuis": int(a["depuis"]), "message": a["message"]}
                           for a in agents],
                "maj": int(time.time()),
                "version": self.version,
            }

    def publier(self):
        bus.publier("vigie/etat", self.instantane(), retenu=True)


# --- La phrase écrite par le LLM -------------------------------------------------

CONSIGNE = """Tu es la Vigie, un petit robot qui veille sur les agents IA de {prenom}.
Tu tutoies {prenom}. Tu écris UNE phrase courte en français courant, comme un collègue
qui passe une tête dans le bureau, 15 mots maximum, sans emoji ni guillemets.
Tu reformules uniquement les faits donnés : n'ajoute aucun agent, aucun état, aucune durée.
Priorité : ce qu'on demande à {prenom}, puis ce qui vient de se terminer, puis ce qui est en cours.
Quand rien n'est demandé à {prenom}, n'en parle pas, ou dis simplement « rien à signaler ».
Expressions interdites : « rien ne t'attend », « personne ne t'attend ».

Faits :
- Rien n'est demandé à {prenom}.
- Terminé : site (il y a 1 min).
- Rien n'est en cours.
Phrase : Le site a terminé, tu peux jeter un œil au résultat.

Faits :
- site attend la réponse de {prenom} (permission pour Bash).
- Rien n'est terminé.
- En cours : sauvegarde.
Phrase : Le site demande ton autorisation pour Bash ; la sauvegarde continue.

Faits :
- Rien n'est demandé à {prenom}.
- Rien n'est terminé.
- En cours : site, rapport.
Phrase : Le site et le rapport sont en cours, rien à signaler.

Faits :
- rapport attend la réponse de {prenom} (attend ta réponse).
- Terminé : sauvegarde (il y a 4 min).
- Rien n'est en cours.
Phrase : Le rapport attend ta réponse ; la sauvegarde est terminée.

Faits :
- Rien n'est demandé à {prenom}.
- Terminé : rapport (il y a 2 min).
- En cours : site.
Phrase : Le rapport est terminé, le site est toujours en cours.

Faits :
{etat}
Phrase :"""


def decrire(instant):
    """Les faits, déjà tranchés : le petit modèle n'a qu'à les reformuler."""
    maintenant = time.time()
    par_etat = {"attend": [], "fini": [], "travaille": []}
    for a in instant["agents"]:
        if a["etat"] in par_etat:
            par_etat[a["etat"]].append(a)
    lignes = []
    if par_etat["attend"]:
        for a in par_etat["attend"]:
            motif = f" ({a['message']})" if a["message"] else ""
            lignes.append(f"- {a['projet']} attend la réponse de {PRENOM}{motif}.")
    else:
        lignes.append(f"- Rien n'est demandé à {PRENOM}.")
    if par_etat["fini"]:
        noms = ", ".join(f"{a['projet']} (il y a {max(1, int((maintenant - a['depuis']) / 60))} min)"
                         for a in par_etat["fini"])
        lignes.append(f"- Terminé : {noms}.")
    else:
        lignes.append("- Rien n'est terminé.")
    if par_etat["travaille"]:
        lignes.append("- En cours : " + ", ".join(a["projet"] for a in par_etat["travaille"]) + ".")
    else:
        lignes.append("- Rien n'est en cours.")
    return "\n".join(lignes)


# Mots qui trahissent une invention quand l'état correspondant est vide
INDICES = {
    "attend": re.compile(r"attend|patient|permission|autoris|valid|répon|feu vert|accord|besoin de (toi|ton)|demande", re.I),
    "travaille": re.compile(r"en cours|travail|démarr|occup|continue|tourne|avanc", re.I),
    "fini": re.compile(r"termin|fini|achev|bouclé|résultat", re.I),
}


# Tournures refusées, même si le LLM insiste : calques de l'anglais, peu clairs
INTERDITES = re.compile(r"rien\s+ne\s+t\s*['’]?\s*attend|personne\s+ne\s+t\s*['’]?\s*attend|rien\s+ne\s+(te\s+)?presse", re.I)


def phrase_fidele(phrase, instant):
    if INTERDITES.search(phrase):
        return False
    etats = {a["etat"] for a in instant["agents"]}
    for nom, motif in INDICES.items():
        if nom not in etats and motif.search(phrase):
            # « tu n'as rien à faire », « aucune action » restent permis
            if nom == "attend" and re.search(r"rien à faire|aucune action", phrase, re.I):
                continue
            return False
    return True


def demander_au_llm(texte):
    corps = json.dumps({
        "model": MODEL, "prompt": CONSIGNE.format(etat=texte, prenom=PRENOM), "stream": False,
        "keep_alive": "15m", "options": {"num_predict": 40, "temperature": 0.1},
    }).encode()
    req = urllib.request.Request(f"{OLLAMA_URL}/api/generate", data=corps,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as rep:
        phrase = json.load(rep).get("response", "").strip()
    phrase = phrase.split("\n")[0].strip(" \"«»")
    return phrase[:140] or None


def boucle_phrases():
    while True:
        etat.reveil.wait()
        time.sleep(1.5)  # regroupe les rafales d'événements
        etat.reveil.clear()
        instant = etat.instantane()
        if not instant["agents"]:
            continue  # « Rien à signaler. » suffit
        try:
            phrase = demander_au_llm(decrire(instant))
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
            log.warning("LLM indisponible (%s) : phrase simple conservée", e)
            continue
        if phrase and not phrase_fidele(phrase, instant):
            log.info("Phrase rejetée (contredit l'état) : %s", phrase)
            continue
        with etat.verrou:
            if etat.version != instant["version"] or not phrase:
                continue  # l'état a changé entre-temps : une nouvelle phrase viendra
            etat.phrase = phrase
        log.info("Phrase : %s", phrase)
        etat.publier()


def boucle_menage():
    humeur = None
    while True:
        time.sleep(30)
        etat.oublier_les_muets()
        with etat.verrou:
            nouvelle = etat.humeur()
        if humeur is not None and nouvelle != humeur:
            etat.publier()  # ex. « satisfaite » qui redevient « calme » après 10 min
        humeur = nouvelle


# --- Journal --------------------------------------------------------------------

verrou_journal = threading.Lock()


def journaliser(ligne):
    try:
        JOURNAL.parent.mkdir(parents=True, exist_ok=True)
        with verrou_journal, JOURNAL.open("a", encoding="utf-8") as f:
            f.write(json.dumps(ligne, ensure_ascii=False) + "\n")
    except OSError as e:
        log.warning("Journal inaccessible : %s", e)


# --- HTTP -----------------------------------------------------------------------

class Guichet(BaseHTTPRequestHandler):
    def _repondre(self, code, charge):
        corps = json.dumps(charge, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(corps)))
        self.end_headers()
        self.wfile.write(corps)

    def do_GET(self):
        if self.path == "/etat":
            self._repondre(200, etat.instantane())
        elif self.path == "/sante":
            self._repondre(200, {"ok": True})
        else:
            self._repondre(404, {"erreur": "inconnu"})

    def do_POST(self):
        if self.path != "/event":
            return self._repondre(404, {"erreur": "inconnu"})
        if TOKEN and self.headers.get("X-Vigie-Token") != TOKEN:
            return self._repondre(403, {"erreur": "jeton"})
        try:
            taille = min(int(self.headers.get("Content-Length", 0)), 1_000_000)
            ev = json.loads(self.rfile.read(taille) or b"{}")
            if not isinstance(ev, dict):
                raise ValueError
        except ValueError:
            return self._repondre(400, {"erreur": "json"})
        ev["_poste"] = self.headers.get("X-Vigie-Poste") or postes.nom(self.client_address[0])
        change = etat.evenement(ev)
        self._repondre(200, {"ok": True, "change": change})

    def log_message(self, *args):
        pass  # silence : un hook par appel d'outil ferait beaucoup de lignes


# --- Démarrage ------------------------------------------------------------------

etat = Etat()
bus = Bus()

if __name__ == "__main__":
    if LLM_ACTIF:
        threading.Thread(target=boucle_phrases, daemon=True).start()
    threading.Thread(target=boucle_menage, daemon=True).start()
    serveur = ThreadingHTTPServer(("0.0.0.0", PORT), Guichet)
    log.info("Vigie à l'écoute sur le port %s (modèle %s)", PORT, MODEL)
    try:
        serveur.serve_forever()
    except KeyboardInterrupt:
        pass
