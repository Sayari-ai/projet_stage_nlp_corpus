"""Lecture des dumps Wikipédia (pages-articles, XML compressé en bz2)."""
import bz2
import re
import xml.etree.ElementTree as ET


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def ordre_fichiers(noms):
    """Ordre de lecture des fichiers d'un dump découpé (pages-articles1, 2, …, 10…)."""
    def cle(nom):
        m = re.search(r"pages-articles(\d+)\.xml(?:-p(\d+))?", nom)
        return (int(m.group(1)), int(m.group(2) or 0)) if m else (0, 0)
    return sorted(noms, key=cle)


def pages_du_dump(chemin):
    """Parcourt un dump page par page, sans le charger en mémoire.

    Renvoie pour chaque page : titre, ns, page_id, redirection, revision_id,
    revision_date, modele (modèle de contenu) et wikitexte.
    """
    with bz2.open(chemin, "rb") as f:
        racine = None
        for evenement, el in ET.iterparse(f, events=("start", "end")):
            if racine is None and evenement == "start":
                racine = el
                continue
            if evenement != "end" or _local(el.tag) != "page":
                continue
            champs = {}
            for c in el.iter():
                nom = _local(c.tag)
                if nom not in champs:                      # premier élément de ce nom (id de page avant id de révision)
                    champs[nom] = c
            rev = champs["revision"]
            rev_id = next(c for c in rev if _local(c.tag) == "id")
            texte = champs.get("text")
            yield {
                "titre": champs["title"].text,
                "ns": int(champs["ns"].text),
                "page_id": int(champs["id"].text),
                "redirection": "redirect" in champs,
                "revision_id": int(rev_id.text),
                "revision_date": champs["timestamp"].text,
                "modele": champs["model"].text if "model" in champs else "wikitext",
                "wikitexte": (texte.text or "") if texte is not None else "",
            }
            racine.clear()                                 # libère la mémoire des pages déjà lues
