"""Export final du corpus (tâche 8).

Combine le corpus normalisé (texte, tâche 6) et la provenance finale de la tâche 7 (quality_flags) en :
  - data/processed/corpus/<code>.jsonl      : {"text": ...} seul, un fichier par langue (schéma validé
    tâche 2 — aucun autre champ dans le corpus ; la provenance est toujours gardée à part, reliée par empreinte).
  - data/processed/provenance/<code>.jsonl  : un fichier par langue (relié au corpus par text_sha256, jamais
    par position de ligne). Les fragments calculés pendant l'export sont DÉPLACÉS ici, jamais recopiés : une
    copie doublerait l'espace disque nécessaire (déjà serré sur cette machine).
  - data/processed/provenance.parquet       : même contenu que provenance/*.jsonl réunis, en Parquet.
  - data/processed/exclusions.jsonl         : textes écartés à l'export, avec leur raison (même principe que
    le journal d'exclusions de normaliser.py).
  - data/processed/export_exemples.jsonl    : quelques exemples par langue (texte + provenance), pour relecture.

Validation : schéma JSON (bibliothèque jsonschema) sur 100 % des lignes écrites, correspondance exacte
corpus <-> provenance par empreinte, quality_flags restreint aux 4 valeurs valides.

Usage (depuis la racine du projet) :
    python src/exporter.py
    # en cas de plantage après que toutes les langues ont déjà été traitées (ex. disque plein pendant le
    # seul regroupement final) : reprend sans refaire le travail par langue, à partir d'un dossier d'export
    # déjà présent dans data/processed/qualite/analyses/ :
    python src/exporter.py --terminer 20261122T081323Z_export
"""
import argparse
import hashlib
import json
import logging
import os
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from itertools import zip_longest
from multiprocessing import Pool
from pathlib import Path

import yaml
from jsonschema import Draft7Validator

ICI = Path(__file__).resolve().parent
ROOT = ICI.parent
VERSION = "0.1"
log = logging.getLogger("export")

FLAGS_VALIDES = {"boilerplate", "language_mismatch", "near_duplicate", "template_generated"}
CHAMPS_PROVENANCE = ("text_sha256", "language_code", "source_url", "source_dump", "revision_date",
                     "collected_at", "license", "quality_flags")

SCHEMA_CORPUS = {"type": "object", "additionalProperties": False,
                 "properties": {"text": {"type": "string", "minLength": 1}}, "required": ["text"]}
SCHEMA_PROVENANCE = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "text_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "language_code": {"type": "string", "minLength": 1},
        "source_url": {"type": "string", "minLength": 1},
        "source_dump": {"type": "string", "minLength": 1},
        "revision_date": {"type": "string", "minLength": 1},
        "collected_at": {"type": "string", "minLength": 1},
        "license": {"type": "string", "minLength": 1},
        "quality_flags": {"type": "array", "items": {"enum": sorted(FLAGS_VALIDES)}},
    },
    "required": list(CHAMPS_PROVENANCE)}
_VALIDATEUR_CORPUS = Draft7Validator(SCHEMA_CORPUS)
_VALIDATEUR_PROVENANCE = Draft7Validator(SCHEMA_PROVENANCE)


def maintenant():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ecrire_json(chemin, donnees):
    tmp = Path(str(chemin) + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(donnees, f, ensure_ascii=False, indent=1)
    os.replace(tmp, chemin)


def charger_config():
    with open(ROOT / "config" / "config.yaml", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    ex = config["export"]
    source = ROOT / ex["source"]
    qualite = ROOT / ex["qualite"]
    processus = config.get("qualite", {}).get("processus") or max(1, (os.cpu_count() or 2) - 1)
    return config, source, qualite, processus


def langues_source(source):
    return json.loads((source / "etat.json").read_text(encoding="utf-8"))["langues"]


def nouveau_dossier(config, nom):
    d = ROOT / config["paths"]["processed"] / "qualite" / "analyses" / (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + nom)
    d.mkdir(parents=True)
    log.setLevel(logging.INFO)
    log.handlers.clear()
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%dT%H:%M:%S")
    for h in (logging.FileHandler(d / "run.log", encoding="utf-8"), logging.StreamHandler(sys.stdout)):
        h.setFormatter(fmt)
        log.addHandler(h)
    return d


def charger_exclusions(qualite):
    """{code: {text_sha256_collecte: raison}} à partir de exclusions_export.jsonl (tâche 7)."""
    chemin = qualite / "exclusions_export.jsonl"
    par_langue = {}
    if chemin.exists():
        with open(chemin, encoding="utf-8") as f:
            for ligne in f:
                d = json.loads(ligne)
                par_langue.setdefault(d["langue"], {})[d["text_sha256_collecte"]] = d["raison"]
    return par_langue


def exporter_langue(args):
    code, source, qualite, exclues, dossier_corpus, dossier_fragments = args
    t0 = time.time()
    exclues_code = exclues.get(code, {})
    n, exportes = 0, 0
    compte_flags = Counter()
    exclusions_lignes, exemples, erreurs_schema, erreurs_empreinte = [], [], [], []
    with open(source / "corpus" / f"{code}.jsonl", encoding="utf-8") as f_corpus, \
         open(qualite / "provenance" / f"{code}.jsonl", encoding="utf-8") as f_prov, \
         open(dossier_corpus / f"{code}.jsonl", "w", encoding="utf-8", newline="\n") as f_out_corpus, \
         open(dossier_fragments / f"{code}.jsonl", "w", encoding="utf-8", newline="\n") as f_out_prov:
        for i, (ligne_c, ligne_p) in enumerate(zip_longest(f_corpus, f_prov)):
            if ligne_c is None or ligne_p is None:
                erreurs_empreinte.append(f"{code} : corpus et provenance n'ont pas le même nombre de lignes "
                                         f"(arrêt ligne {i})")
                break
            n += 1
            t = json.loads(ligne_c)["text"]
            d = json.loads(ligne_p)
            cle = d.get("text_sha256_collecte", d.get("text_sha256"))
            if cle in exclues_code:
                exclusions_lignes.append({"langue": code, "source_url": d.get("source_url"),
                                          "text_sha256_collecte": cle, "raison": exclues_code[cle]})
                continue
            if hashlib.sha256(t.encode("utf-8")).hexdigest() != d.get("text_sha256"):
                erreurs_empreinte.append(f"{code} ligne {i} : empreinte ne correspond pas au texte")
                continue
            enr_corpus = {"text": t}
            enr_prov = {k: d.get(k) for k in CHAMPS_PROVENANCE}
            if not _VALIDATEUR_CORPUS.is_valid(enr_corpus):
                erreurs_schema.append(f"{code} ligne {i} : corpus hors schéma")
                continue
            if not _VALIDATEUR_PROVENANCE.is_valid(enr_prov):
                erreurs_schema.append(f"{code} ligne {i} : provenance hors schéma "
                                      f"({sorted(_VALIDATEUR_PROVENANCE.iter_errors(enr_prov), key=str)[:1]})")
                continue
            for fl in enr_prov["quality_flags"]:
                compte_flags[fl] += 1
            f_out_corpus.write(json.dumps(enr_corpus, ensure_ascii=False) + "\n")
            f_out_prov.write(json.dumps(enr_prov, ensure_ascii=False) + "\n")
            if len(exemples) < 2:
                exemples.append({**enr_prov, "text_extrait": t[:200].replace("\n", " / ")})
            exportes += 1
    return {"langue": code, "textes": n, "exportes": exportes, "exclus": len(exclusions_lignes),
            "drapeaux": dict(compte_flags), "exclusions": exclusions_lignes, "exemples": exemples,
            "erreurs_schema": erreurs_schema, "erreurs_empreinte": erreurs_empreinte,
            "duree_s": round(time.time() - t0)}


def exporter(config, source, qualite, processus, langues=None):
    d = nouveau_dossier(config, "export")
    corpus_dir = ROOT / config["paths"]["corpus"]
    corpus_dir.mkdir(parents=True, exist_ok=True)
    fragments_dir = d / "provenance_fragments"
    fragments_dir.mkdir()
    t0 = time.time()
    langues = langues or langues_source(source)
    langues = sorted(langues, key=lambda c: -(source / "corpus" / f"{c}.jsonl").stat().st_size)
    exclues = charger_exclusions(qualite)
    log.info("8 Export final : %s langue(s), %s processus ; source=%s, provenance=%s", len(langues),
             min(processus, len(langues)), source.relative_to(ROOT), qualite.name)
    resultats = []
    args = [(c, source, qualite, exclues, corpus_dir, fragments_dir) for c in langues]
    with Pool(min(processus, len(langues))) as pool:
        for r in pool.imap_unordered(exporter_langue, args):
            resultats.append(r)
            log.info("%-4s %8s textes : %8s exportés, %2s écarté(s), drapeaux %s %s (%s s)", r["langue"],
                     r["textes"], r["exportes"], r["exclus"], r["drapeaux"],
                     "[ANOMALIE]" if r["erreurs_schema"] or r["erreurs_empreinte"] else "", r["duree_s"])
            for e in r["erreurs_schema"] + r["erreurs_empreinte"]:
                log.error("  %s", e)
    resultats.sort(key=lambda r: r["langue"])
    return _finaliser(config, d, corpus_dir, fragments_dir, resultats, t0)


def terminer_export(config, nom_dossier):
    """Reprend un export déjà calculé (corpus/ final et provenance déjà écrits sur disque, en fragments ou déjà
    déplacés dans provenance/), sans refaire le travail par langue — utile après un plantage soit pendant le
    seul regroupement final (fragments pas encore déplacés), soit pendant la finalisation elle-même (déjà
    déplacés mais pas de bilan.json, ex. pendant la construction du Parquet)."""
    d = ROOT / config["paths"]["processed"] / "qualite" / "analyses" / nom_dossier
    fragments_dir = d / "provenance_fragments"
    provenance_dir = ROOT / config["paths"]["provenance"]
    if fragments_dir.is_dir() and any(fragments_dir.glob("*.jsonl")):
        source_prov, deja_deplace = fragments_dir, False
    elif provenance_dir.is_dir() and any(provenance_dir.glob("*.jsonl")):
        source_prov, deja_deplace = provenance_dir, True
    else:
        raise SystemExit(f"Ni {fragments_dir} ni {provenance_dir} ne contiennent de provenance à reprendre.")
    log.setLevel(logging.INFO)
    log.handlers.clear()
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%dT%H:%M:%S")
    for h in (logging.FileHandler(d / "run.log", encoding="utf-8", mode="a"), logging.StreamHandler(sys.stdout)):
        h.setFormatter(fmt)
        log.addHandler(h)
    t0 = time.time()
    corpus_dir = ROOT / config["paths"]["corpus"]
    qualite = ROOT / config["export"]["qualite"]
    exclues = charger_exclusions(qualite)
    log.info("8 (reprise) Finalisation de l'export depuis %s (%s) — pas de nouveau travail par langue", d.name,
             "déjà déplacé vers provenance/" if deja_deplace else "fragments pas encore déplacés")
    resultats = []
    for frag in sorted(source_prov.glob("*.jsonl")):
        code = frag.stem
        compte_flags, exemples, n_frag, n_corpus = Counter(), [], 0, 0
        # corpus/<code>.jsonl et le fragment de provenance ont été écrits ligne à ligne ensemble par
        # exporter_langue : ils sont déjà alignés, donc on peut les relire en parallèle pour les exemples.
        with open(corpus_dir / f"{code}.jsonl", encoding="utf-8") as f_corpus, open(frag, encoding="utf-8") as f:
            for ligne_c, ligne in zip_longest(f_corpus, f):
                n_corpus += ligne_c is not None
                if ligne is None:
                    continue
                enr = json.loads(ligne)
                for fl in enr.get("quality_flags", []):
                    compte_flags[fl] += 1
                if len(exemples) < 2:
                    texte = json.loads(ligne_c)["text"] if ligne_c else ""
                    exemples.append({**enr, "text_extrait": texte[:200].replace("\n", " / ")})
                n_frag += 1
        exclus = [{"langue": code, "text_sha256_collecte": h, "raison": r}
                  for h, r in exclues.get(code, {}).items()]
        erreurs = [] if n_frag == n_corpus else \
            [f"{code} : {n_frag} lignes de provenance déjà calculées, {n_corpus} lignes dans corpus/ final"]
        resultats.append({"langue": code, "textes": n_corpus + len(exclus), "exportes": n_frag,
                          "exclus": len(exclus), "drapeaux": dict(compte_flags), "exclusions": exclus,
                          "exemples": exemples, "erreurs_schema": [], "erreurs_empreinte": erreurs,
                          "duree_s": 0})
        log.info("%-4s %8s exportés (déjà calculés), %2s écarté(s), drapeaux %s %s", code, n_frag, len(exclus),
                 dict(compte_flags), "[ANOMALIE]" if erreurs else "")
    return _finaliser(config, d, corpus_dir, fragments_dir, resultats, t0)


def _finaliser(config, d, corpus_dir, fragments_dir, resultats, t0):
    # registre de provenance final : un fichier par langue, DÉPLACÉ depuis les fragments (jamais recopié :
    # une copie doublerait temporairement l'espace disque nécessaire pour le registre entier).
    provenance_dir = ROOT / config["paths"]["provenance"]
    provenance_dir.mkdir(parents=True, exist_ok=True)
    for r in resultats:
        chemin_frag, chemin_final = fragments_dir / f"{r['langue']}.jsonl", provenance_dir / f"{r['langue']}.jsonl"
        if chemin_frag.exists():
            os.replace(chemin_frag, chemin_final)
    try:
        fragments_dir.rmdir()
    except OSError:
        pass

    # journal d'exclusions combiné
    chemin_exclusions = ROOT / config["paths"]["exclusions"]
    with open(chemin_exclusions, "w", encoding="utf-8", newline="\n") as f:
        for r in resultats:
            for ex in r["exclusions"]:
                f.write(json.dumps(ex, ensure_ascii=False) + "\n")

    # exemples
    chemin_exemples = ROOT / config["paths"]["export_exemples"]
    with open(chemin_exemples, "w", encoding="utf-8", newline="\n") as f:
        for r in resultats:
            for ex in r["exemples"]:
                f.write(json.dumps(ex, ensure_ascii=False) + "\n")

    # Parquet du registre de provenance (pas du corpus : le texte seul n'apporte rien de plus que le JSONL) —
    # écrit EN FLUX, langue par langue, avec pyarrow : jamais de DataFrame unique chargeant tout en mémoire
    # (69 langues dont certaines > 500 Mo de JSON auraient fait gonfler la mémoire, et donc le fichier
    # d'échange Windows, sur un disque déjà serré — c'est ce qui a bloqué la machine la première fois).
    import pyarrow as pa
    import pyarrow.parquet as pq
    schema_arrow = pa.schema([
        ("text_sha256", pa.string()), ("language_code", pa.string()), ("source_url", pa.string()),
        ("source_dump", pa.string()), ("revision_date", pa.string()), ("collected_at", pa.string()),
        ("license", pa.string()), ("quality_flags", pa.list_(pa.string()))])
    chemin_parquet = ROOT / config["paths"]["provenance_parquet"]
    tmp_parquet = Path(str(chemin_parquet) + ".tmp")
    TAILLE_LOT = 50_000  # borne la mémoire même pour les plus grosses langues (ar, arz : > 500 Mo de JSON)

    def lots(f):
        colonnes = {c: [] for c in CHAMPS_PROVENANCE}
        n = 0
        for ligne in f:
            enr = json.loads(ligne)
            for c in CHAMPS_PROVENANCE:
                colonnes[c].append(enr.get(c))
            n += 1
            if n >= TAILLE_LOT:
                yield colonnes
                colonnes, n = {c: [] for c in CHAMPS_PROVENANCE}, 0
        if n:
            yield colonnes

    ecrivain = pq.ParquetWriter(tmp_parquet, schema_arrow)
    try:
        for r in resultats:
            if not r["exportes"]:
                continue
            with open(provenance_dir / f"{r['langue']}.jsonl", encoding="utf-8") as f:
                for colonnes in lots(f):
                    ecrivain.write_table(pa.table(colonnes, schema=schema_arrow))
    finally:
        ecrivain.close()
    os.replace(tmp_parquet, chemin_parquet)

    textes = sum(r["textes"] for r in resultats)
    exportes = sum(r["exportes"] for r in resultats)
    exclus = sum(r["exclus"] for r in resultats)
    drapeaux = Counter()
    for r in resultats:
        drapeaux.update(r["drapeaux"])
    toutes_erreurs = [e for r in resultats for e in r["erreurs_schema"] + r["erreurs_empreinte"]]

    # contrôle final : le nombre de lignes du corpus final correspond au nombre de lignes de la provenance,
    # langue par langue, et globalement
    anomalies = list(toutes_erreurs)
    for r in resultats:
        n_corpus = sum(1 for _ in open(corpus_dir / f"{r['langue']}.jsonl", encoding="utf-8"))
        if n_corpus != r["exportes"]:
            anomalies.append(f"{r['langue']} : {n_corpus} lignes dans corpus/ final, {r['exportes']} attendues")
    n_prov = sum(sum(1 for _ in open(provenance_dir / f"{r['langue']}.jsonl", encoding="utf-8"))
                 for r in resultats)
    if n_prov != exportes:
        anomalies.append(f"provenance/*.jsonl : {n_prov} lignes au total, {exportes} attendues")

    ecrire_json(d / "bilan.json", {
        "etape": "8 export final", "version": VERSION, "fin_utc": maintenant(),
        "duree_s": round(time.time() - t0),
        "textes": textes, "exportes": exportes, "exclus": exclus, "drapeaux": dict(drapeaux),
        "anomalies": anomalies, "langues": [{k: v for k, v in r.items() if k not in ("exclusions", "exemples")}
                                            for r in resultats]})
    log.info("Total : %s textes, %s exportés, %s écarté(s) ; drapeaux %s ; %s anomalie(s) ; %s s",
             textes, exportes, exclus, dict(drapeaux), len(anomalies), round(time.time() - t0))
    if anomalies:
        log.error("ANOMALIES — à corriger avant de valider la tâche 8 :")
        for a in anomalies:
            log.error("  %s", a)
    else:
        log.info("Aucune anomalie : corpus/, provenance/, provenance.parquet et exclusions.jsonl prêts.")
    return d


def main(argv=None):
    ap = argparse.ArgumentParser(description="Export final du corpus (tâche 8)")
    ap.add_argument("--langues", nargs="+", metavar="CODE", help="limiter l'export à ces langues")
    ap.add_argument("--terminer", metavar="DOSSIER",
                    help="reprend un export déjà calculé (ex. 20261122T081323Z_export) sans refaire le "
                         "travail par langue")
    args = ap.parse_args(argv)
    config, source, qualite, processus = charger_config()
    if args.terminer:
        terminer_export(config, args.terminer)
    else:
        exporter(config, source, qualite, processus, args.langues)
    return 0


if __name__ == "__main__":
    sys.exit(main())
