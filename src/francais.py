"""Échantillon aléatoire d'articles de la Wikipédia en français (langue de comparaison, décision S2).

Le français n'est pas collecté en entier (près de 2,8 millions d'articles) : un échantillon
d'articles tirés au hasard par l'API (list=random, espace principal, sans redirection).
- La liste tirée est enregistrée dans data/raw/api/fr/echantillon_<N>.json : un nouveau run
  réutilise la même liste, ce qui rend l'échantillon reproductible.
- Le contenu est lu par lots de 50 pages (dernière révision) ; chaque réponse brute est gardée
  dans data/raw/api/fr/revisions/ et réutilisée si le run est relancé.
"""
import json
import logging
from pathlib import Path

from traitement import ecrire_json
from wikimedia import maintenant

log = logging.getLogger("collecte")
API = "https://fr.wikipedia.org/w/api.php"
LOT = 50


def tirer_echantillon(client, dossier_api, taille):
    chemin = Path(dossier_api) / "fr" / f"echantillon_{taille}.json"
    if chemin.exists():
        with open(chemin, encoding="utf-8") as f:
            return json.load(f)["page_ids"]
    vus, ordre = set(), []
    while len(ordre) < taille:
        d = client.get_json(API, {"action": "query", "list": "random", "rnnamespace": 0,
                                  "rnfilterredir": "nonredirects", "rnlimit": 500})
        for p in d["query"]["random"]:
            if p["id"] not in vus and len(ordre) < taille:
                vus.add(p["id"])
                ordre.append(p["id"])
        if len(ordre) % 10000 < 500:
            log.info("fr : %s articles tirés sur %s", len(ordre), taille)
    chemin.parent.mkdir(parents=True, exist_ok=True)
    ecrire_json(chemin, {"tire_le_utc": maintenant(), "taille": taille, "page_ids": ordre})
    return ordre


def lot_de_revisions(client, dossier_api, n, ids):
    chemin = Path(dossier_api) / "fr" / "revisions" / f"lot_{n:05d}.json"
    if chemin.exists():
        with open(chemin, encoding="utf-8") as f:
            return json.load(f)
    d = client.get_json(API, {"action": "query", "prop": "revisions", "pageids": "|".join(map(str, ids)),
                              "rvprop": "ids|timestamp|content", "rvslots": "main"})
    d["_collecte_utc"] = maintenant()
    chemin.parent.mkdir(parents=True, exist_ok=True)
    ecrire_json(chemin, d)
    return d


def pages_francaises(client, dossier_api, page_ids):
    """(index, page) dans l'ordre de l'échantillon, au même format que les pages d'un dump.
    Une page supprimée depuis le tirage est sautée (elle ne compte pas dans l'échantillon)."""
    for n, debut in enumerate(range(0, len(page_ids), LOT)):
        ids = page_ids[debut:debut + LOT]
        d = lot_de_revisions(client, dossier_api, n, ids)
        par_id = {p["pageid"]: p for p in d["query"]["pages"] if "pageid" in p and not p.get("missing")}
        for k, pid in enumerate(ids):
            p = par_id.get(pid)
            if not p or not p.get("revisions"):
                continue
            rev = p["revisions"][0]
            slot = rev["slots"]["main"]
            yield debut + k, {
                "titre": p["title"], "ns": p["ns"], "page_id": pid, "redirection": False,
                "revision_id": rev["revid"], "revision_date": rev["timestamp"],
                "modele": slot.get("contentmodel", "wikitext"), "wikitexte": slot.get("content", ""),
                "_lot_collecte": d["_collecte_utc"],
            }
