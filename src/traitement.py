"""Traitement d'une langue : extraction, page affichée, doublons, écriture, points de reprise (tâche 5).

Règles (tâches 2 et 4) :
- chaque page de l'espace principal (hors redirection), page d'accueil comprise ;
- texte extrait du wikitexte ; si le wikitexte ne donne rien, texte de la page affichée
  (API action=parse sur la même révision), dont la réponse brute est gardée dans data/raw ;
- seuls les doublons exacts et les pages sans aucun texte sont écartés, avec leur raison.

Reprise : toutes les `point_de_reprise` pages, les fichiers sont vidés sur le disque et
l'état (dernière page traitée, tailles des fichiers, compteurs) est enregistré dans
etat.json. Après une interruption, les fichiers sont ramenés à ces tailles, les
empreintes déjà gardées sont relues et le traitement reprend à la page suivante.
"""
import json
import logging
import os
import statistics
import time
from collections import Counter
from multiprocessing import Pool, TimeoutError as DelaiDepasse
from pathlib import Path

from extraire_html import extraire_texte_html
from extraire_texte import extraire_texte
import valider_records as V
from wikimedia import maintenant

log = logging.getLogger("collecte")

#  extraction en parallèle (processus de travail) 
_INFOS = None


def _init_travail(infos):
    global _INFOS
    _INFOS = infos


def _extraire_lot(lot):
    return [_extraire(e) for e in lot]


def _lots(elements, taille=32):
    lot = []
    for e in elements:
        lot.append(e)
        if len(lot) == taille:
            yield lot
            lot = []
    if lot:
        yield lot


def _extraire(element):
    """(index, page) -> (index, page sans wikitexte complet, texte extrait)."""
    i, p = element
    t = (extraire_texte(p["wikitexte"], _INFOS["noms_fichier"], _INFOS["noms_categorie"],
                        _INFOS["interlangues"], titre=p["titre"])
         if p["modele"] == "wikitext" else "")
    leger = {k: v for k, v in p.items() if k != "wikitexte"}
    leger["extrait"] = (p["wikitexte"] or "")[:300]
    return i, leger, t


#  état du run
def ecrire_json(chemin, donnees):
    """Écriture atomique (fichier temporaire puis remplacement)."""
    tmp = Path(str(chemin) + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(donnees, f, ensure_ascii=False, indent=1)
    os.replace(tmp, chemin)


class Sorties:
    """Les trois fichiers d'une langue : corpus, provenance, exclusions."""
    NOMS = ("corpus", "provenance", "exclusions")

    def __init__(self, dossier_run, code, tailles=None):
        self.chemins = {n: Path(dossier_run) / n / f"{code}.jsonl" for n in self.NOMS}
        self.f = {}
        for n, ch in self.chemins.items():
            ch.parent.mkdir(parents=True, exist_ok=True)
            if tailles:                                    # reprise : retour au dernier point enregistré
                with open(ch, "ab") as g:
                    g.truncate(tailles[n])
                self.f[n] = open(ch, "a", encoding="utf-8", newline="\n")
            else:
                self.f[n] = open(ch, "w", encoding="utf-8", newline="\n")

    def ecrire(self, nom, objet):
        self.f[nom].write(json.dumps(objet, ensure_ascii=False) + "\n")

    def vider(self):
        tailles = {}
        for n, g in self.f.items():
            g.flush()
            os.fsync(g.fileno())
            tailles[n] = g.tell()
        return tailles

    def fermer(self):
        for g in self.f.values():
            g.close()


def empreintes_gardees(chemin_corpus):
    vus = set()
    if Path(chemin_corpus).exists():
        with open(chemin_corpus, encoding="utf-8") as f:
            for l in f:
                vus.add(V.sha256(json.loads(l)["text"]))
    return vus


#page affichée (API), avec sauvegarde brute 
def page_affichee(client, dossier_api, code, revision_id):
    """Texte de la page affichée ; (None, raison) si l'API ne la rend pas.
    La réponse brute est gardée dans data/raw/api/parse/<code>/<révision>.json et réutilisée."""
    chemin = Path(dossier_api) / "parse" / code / f"{revision_id}.json"
    if chemin.exists():
        with open(chemin, encoding="utf-8") as f:
            brut = json.load(f)
    else:
        brut = client.page_affichee(code, revision_id)
        brut["_collecte_utc"] = maintenant()
        chemin.parent.mkdir(parents=True, exist_ok=True)
        ecrire_json(chemin, brut)
    if "error" in brut:
        return None, f"page affichée non disponible ({brut['error'].get('code')})", brut["_collecte_utc"]
    return extraire_texte_html(brut["parse"]["text"]), None, brut["_collecte_utc"]


#  traitement d'une langue 
def traiter_langue(code, pages, infos, source_de, client, dossier_run, dossier_api, etat, sauver_etat,
                   processus=1, point_de_reprise=5000):
    """pages : itérable de (index, page) dans un ordre fixe ; source_de(page) -> valeur de source_dump.

    etat : dictionnaire d'état de la langue (modifié sur place) ; sauver_etat() l'enregistre.
    """
    reprise = etat.get("point")
    compte = Counter(reprise["compte"]) if reprise else Counter()
    dernier = reprise["index"] if reprise else -1
    sorties = Sorties(dossier_run, code, reprise["tailles"] if reprise else None)
    vus = empreintes_gardees(sorties.chemins["corpus"]) if reprise else set()
    if reprise:
        log.info("%s : reprise après la page n° %s (%s textes déjà gardés)", code, dernier, len(vus))
    t0, depuis_point = time.time(), 0

    a_traiter = ((i, p) for i, p in pages if i > dernier)
    if processus > 1:
        pool = Pool(processus, initializer=_init_travail, initargs=(infos,))
        resultats = pool.imap(_extraire_lot, _lots(a_traiter))
    else:
        pool = None
        _init_travail(infos)
        resultats = map(_extraire_lot, _lots(a_traiter))

    def enregistrer_point(i):
        etat["point"] = {"index": i, "tailles": sorties.vider(), "compte": dict(compte)}
        etat["duree_s"] = round(etat.get("duree_s", 0) + time.time() - t0_point[0])
        t0_point[0] = time.time()
        sauver_etat()

    t0_point = [t0]
    dernier_vu = [dernier]

    def suivants():
        """Pages extraites, dans l'ordre ; signale une page longue au lieu de rester muet."""
        attente = 0
        while True:
            try:
                lot = resultats.next(timeout=120) if pool else next(resultats)
            except StopIteration:
                return
            except DelaiDepasse:
                attente += 2
                log.warning("%s : extraction longue en cours (après la page n° %s), %s min d'attente",
                            code, dernier_vu[0], attente)
                continue
            attente = 0
            yield from lot
    try:
        for i, p, t in suivants():
            dernier_vu[0] = i
            compte["pages_ns0"] += 1
            url = f"https://{code}.wikipedia.org/w/index.php?oldid={p['revision_id']}"
            source, raison, collecte, par_api = source_de(p), None, maintenant(), False
            if not t:                                      # page faite de modèles : page affichée
                compte["pages_lues_api"] += 1
                par_api = True
                t, raison, collecte = page_affichee(client, dossier_api, code, p["revision_id"])
                source = f"api-{collecte[:10].replace('-', '')}"
                if raison is None and not t:
                    raison = "aucun texte, même dans la page affichée"
            if raison is None:
                erreurs = V.regles_texte(t)
                if erreurs:                                # ne doit pas arriver : le nettoyage retire ces restes
                    raison = "règle enfreinte : " + erreurs[0]
                else:
                    h = V.sha256(t)
                    if h in vus:
                        raison = "doublon exact d'un texte déjà gardé"
                    else:
                        vus.add(h)
                        compte["textes"] += 1
                        if par_api:                        # texte gardé, issu de la page affichée
                            compte["textes_api"] += 1
                        sorties.ecrire("corpus", {"text": t})
                        sorties.ecrire("provenance", {
                            "text_sha256": h, "language_code": code, "source_url": url,
                            "source_dump": source, "revision_date": p["revision_date"],
                            "collected_at": collecte, "license": "CC-BY-SA-4.0", "quality_flags": []})
            if raison:
                compte["exclusions"] += 1
                compte["raison : " + raison] += 1
                sorties.ecrire("exclusions", {"langue": code, "titre": p["titre"], "source_url": url,
                                              "raison": raison, "extrait": p["extrait"]})
            depuis_point += 1
            if depuis_point >= point_de_reprise:
                enregistrer_point(i)
                depuis_point = 0
                log.info("%s : %s pages traitées, %s textes", code, compte["pages_ns0"], compte["textes"])
            dernier_i = i
        if depuis_point:
            enregistrer_point(dernier_i)
    finally:
        if pool:
            pool.terminate()
        sorties.fermer()
    return mesures_langue(code, sorties.chemins["corpus"], compte, etat.get("duree_s", 0))


def mesures_langue(code, chemin_corpus, compte, duree_s):
    mots = []
    with open(chemin_corpus, encoding="utf-8") as f:
        for l in f:
            mots.append(len(json.loads(l)["text"].split()))
    m = {
        "langue": code,
        "pages_ns0": compte.get("pages_ns0", 0),
        "textes": len(mots),
        "exclusions": compte.get("exclusions", 0),
        "pages_lues_api": compte.get("pages_lues_api", 0),
        "textes_api": compte.get("textes_api", 0),
        "mots_total": sum(mots),
        "mots_mediane": statistics.median(mots) if mots else 0,
        "mots_moyenne": round(sum(mots) / len(mots), 1) if mots else 0,
        "duree_s": duree_s,
        "raisons_exclusion": {k[len("raison : "):]: v for k, v in sorted(compte.items()) if k.startswith("raison : ")},
    }
    m["c2_chaque_page_comptee"] = m["pages_ns0"] == m["textes"] + m["exclusions"]
    return m
