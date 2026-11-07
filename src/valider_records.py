"""Validation du corpus (tâche 2).

Le corpus ne contient que du texte : un fichier par langue, corpus/<code>.jsonl,
une ligne = {"text": "..."}.
Le registre de provenance est un fichier séparé, hors corpus, relié au texte par
l'empreinte SHA-256 du texte.

Usage :
    python src/valider_records.py <dossier_corpus> <fichier_provenance.jsonl>
"""
import hashlib
import json
import os
import re
import shutil
import sys
import unicodedata
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]

sys.path.insert(0, str(Path(__file__).resolve().parent))
from extraire_texte import BALISAGE   # restes de balisage interdits (même définition que l'extraction)


def sha256(texte):
    return hashlib.sha256(texte.encode("utf-8")).hexdigest()


def charger_validateur(nom):
    with open(ROOT / "config" / nom, encoding="utf-8") as f:
        schema = json.load(f)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def codes_autorises(chemin=ROOT / "config" / "config.yaml"):
    """Codes de langue admis : éditions incluses, à décider et référence (tâche 1)."""
    with open(chemin, encoding="utf-8") as f:
        scope = yaml.safe_load(f)["scope"]
    ed = scope.get("editions") or {}
    return set(ed.get("incluses", [])) | set(ed.get("a_decider", [])) | {scope["reference_language"]}


def regles_texte(t):
    """Erreurs d'une ligne du corpus déjà conforme au schéma."""
    err = []
    if unicodedata.normalize("NFC", t) != t:
        err.append("texte non normalisé en Unicode NFC")
    if "�" in t:
        err.append("caractère de remplacement U+FFFD (problème d'encodage)")
    m = BALISAGE.search(t)
    if m:
        err.append(f"reste de balisage wiki/HTML : « {m.group(0)} »")
    if t != t.strip():
        err.append("espaces en début ou fin de texte")
    return err


def valider_corpus(dossier, provenance, codes=None):
    """Renvoie un dictionnaire de résultats et la liste des erreurs."""
    codes = codes if codes is not None else codes_autorises()
    v_texte = charger_validateur("schema_document.json")
    v_prov = charger_validateur("schema_provenance.json")
    erreurs, res = [], {"fichiers": 0, "lignes": 0, "lignes_valides": 0, "avec_provenance": 0}

    # Registre de provenance
    registre = {}
    with open(provenance, encoding="utf-8") as f:
        for n, l in enumerate(f, 1):
            p = json.loads(l)
            e = [x.message for x in v_prov.iter_errors(p)]
            if e:
                erreurs.append(f"provenance ligne {n} : {e[0]}")
                continue
            registre.setdefault(p["text_sha256"], []).append(p)

    # Corpus
    vus_corpus = set()
    for fichier in sorted(Path(dossier).glob("*.jsonl")):
        code = fichier.stem
        res["fichiers"] += 1
        if code not in codes:
            erreurs.append(f"{fichier.name} : langue hors périmètre ({code})")
        textes_fichier = set()
        with open(fichier, encoding="utf-8") as f:
            for n, l in enumerate(f, 1):
                res["lignes"] += 1
                ligne = json.loads(l)
                e = [x.message for x in v_texte.iter_errors(ligne)]
                if not e:
                    t = ligne["text"]
                    e = regles_texte(t)
                    h = sha256(t)
                    if h in textes_fichier:
                        e.append("texte en double dans le fichier")
                    textes_fichier.add(h)
                    vus_corpus.add(h)
                    if any(p["language_code"] == code for p in registre.get(h, [])):
                        res["avec_provenance"] += 1
                    else:
                        e.append("aucune provenance pour ce texte dans le registre")
                if e:
                    erreurs.append(f"{fichier.name} ligne {n} : " + " ; ".join(e))
                else:
                    res["lignes_valides"] += 1

    orphelins = set(registre) - vus_corpus
    if orphelins:
        erreurs.append(f"{len(orphelins)} entrée(s) du registre sans texte dans le corpus")
    return res, erreurs


def valider_langue(fichier_corpus, fichier_provenance, code):
    """Validation d'une langue (pipeline de la tâche 5) : corpus/<code>.jsonl et provenance/<code>.jsonl.
    Même contrôle que valider_corpus, langue par langue pour limiter la mémoire utilisée."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        lien = Path(d) / f"{code}.jsonl"
        try:
            os.link(fichier_corpus, lien)
        except OSError:
            shutil.copy(fichier_corpus, lien)
        return valider_corpus(d, fichier_provenance)


if __name__ == "__main__":
    res, err = valider_corpus(sys.argv[1], sys.argv[2])
    print(f"{res['fichiers']} fichier(s), {res['lignes_valides']}/{res['lignes']} lignes valides, "
          f"{res['avec_provenance']}/{res['lignes']} avec provenance")
    for e in err:
        print("-", e)
    sys.exit(0 if not err else 1)
