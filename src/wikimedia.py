"""Accès aux serveurs Wikimedia (tâche 5) : API MediaWiki et dumps.

Règles d'accès de la tâche 3 :
- User-Agent descriptif avec un contact (format : nom/version (contact) bibliothèque/version) ;
- requêtes en série, pause entre deux appels, paramètre maxlag pour l'API ;
- un seul téléchargement de dump à la fois.

Robustesse (connexion instable) :
- nouvelles tentatives avec attente croissante sur les erreurs 429/5xx, les coupures
  et les délais dépassés ;
- téléchargement des dumps reprenable : un fichier .part est complété avec une
  requête HTTP Range au lieu d'être recommencé ; l'empreinte SHA-1 est vérifiée.
Chaque requête est inscrite dans le journal du run (journal_requetes.jsonl).
"""
import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

log = logging.getLogger("collecte")

ERREURS_RESEAU = (requests.ConnectionError, requests.Timeout, requests.exceptions.ChunkedEncodingError)


def maintenant():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ClientWikimedia:
    def __init__(self, contact, journal, pause_s=0.2, tentatives_max=8, version="1.0"):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = (
            f"projet_stage_nlp-bot/{version} ({contact}) python-requests/{requests.__version__}")
        self.pause_s = pause_s
        self.tentatives_max = tentatives_max
        self.journal = Path(journal)

    # journal 
    def _noter(self, url, statut, erreur=None):
        ligne = {"url": url, "statut_http": statut, "horodatage_utc": maintenant()}
        if erreur:
            ligne["erreur"] = erreur
        with open(self.journal, "a", encoding="utf-8") as f:
            f.write(json.dumps(ligne, ensure_ascii=False) + "\n")

    # API 
    def get_json(self, url, params=None):
        """GET JSON avec nouvelles tentatives ; lève RuntimeError après tentatives_max échecs."""
        params = dict(params or {})
        if url.endswith("api.php"):
            params.update({"format": "json", "formatversion": "2", "maxlag": "5"})
        for essai in range(self.tentatives_max):
            attente = min(2 ** essai, 60)
            try:
                r = self.session.get(url, params=params, timeout=(15, 90))
                self._noter(r.url, r.status_code)
                if r.status_code in (429, 500, 502, 503, 504):
                    time.sleep(int(r.headers.get("Retry-After", "0") or 0) or attente)
                    continue
                r.raise_for_status()
                data = r.json()
            except (*ERREURS_RESEAU, ValueError) as e:
                self._noter(url, None, type(e).__name__)
                log.warning("réseau : %s sur %s, nouvelle tentative dans %s s", type(e).__name__, url, attente)
                time.sleep(attente)
                continue
            if isinstance(data, dict) and isinstance(data.get("error"), dict) and data["error"].get("code") == "maxlag":
                time.sleep(attente)
                continue
            time.sleep(self.pause_s)
            return data
        raise RuntimeError(f"échec après {self.tentatives_max} tentatives : {url}")

    def siteinfo(self, code):
        return self.get_json(f"https://{code}.wikipedia.org/w/api.php",
                             {"action": "query", "meta": "siteinfo",
                              "siprop": "general|namespaces|namespacealiases|interwikimap"})["query"]

    def page_affichee(self, code, revision_id):
        """Réponse brute de l'API action=parse pour une révision."""
        return self.get_json(f"https://{code}.wikipedia.org/w/api.php",
                             {"action": "parse", "oldid": revision_id, "prop": "text",
                              "disablelimitreport": "1", "disableeditsection": "1"})

    def statut_dump(self, dbname, date):
        return self.get_json(f"https://dumps.wikimedia.org/{dbname}/{date}/dumpstatus.json")

    # dumps 
    def telecharger(self, url, chemin, sha1, taille=None):
        """Télécharge url dans chemin (reprise avec Range si un .part existe), vérifie le SHA-1."""
        chemin = Path(chemin)
        if chemin.exists() and sha1_fichier(chemin) == sha1:
            return False                                   # déjà présent et correct
        part = chemin.with_name(chemin.name + ".part")
        for essai in range(self.tentatives_max):
            deja = part.stat().st_size if part.exists() else 0
            entetes = {"Range": f"bytes={deja}-"} if deja else {}
            try:
                with self.session.get(url, headers=entetes, stream=True, timeout=(15, 120)) as r:
                    self._noter(url, r.status_code)
                    if r.status_code == 416:               # déjà complet
                        break
                    r.raise_for_status()
                    mode = "ab" if (deja and r.status_code == 206) else "wb"
                    with open(part, mode) as f:
                        for bloc in r.iter_content(1 << 20):
                            f.write(bloc)
                break
            except ERREURS_RESEAU as e:
                attente = min(30 * (essai + 1), 300)
                self._noter(url, None, type(e).__name__)
                log.warning("téléchargement coupé (%s) : %s, reprise dans %s s", type(e).__name__, chemin.name, attente)
                time.sleep(attente)
        else:
            raise RuntimeError(f"téléchargement impossible : {chemin.name}")
        if sha1_fichier(part) != sha1:
            part.unlink()
            raise RuntimeError(f"SHA-1 incorrect pour {chemin.name} : fichier supprimé, relancer pour le retélécharger")
        part.replace(chemin)
        return True


def sha1_fichier(chemin):
    h = hashlib.sha1()
    with open(chemin, "rb") as f:
        for bloc in iter(lambda: f.read(1 << 20), b""):
            h.update(bloc)
    return h.hexdigest()


def infos_wiki(siteinfo):
    """Noms utiles à l'extraction, tirés de la réponse siteinfo."""
    ns, alias = siteinfo["namespaces"], siteinfo.get("namespacealiases", [])
    noms = lambda i: sorted({n for n in [ns[str(i)].get("name"), ns[str(i)].get("canonical")]
                             + [a["alias"] for a in alias if a["id"] == i] if n})
    return {
        "dbname": siteinfo["general"]["wikiid"],
        "page_accueil": siteinfo["general"]["mainpage"],
        "noms_fichier": noms(6),
        "noms_categorie": noms(14),
        "interlangues": sorted(i["prefix"] for i in siteinfo.get("interwikimap", []) if i.get("language")),
    }
