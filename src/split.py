"""Splits train/validation/test (tâche 10).

Ajoute un champ "split" (train / validation / test) à chaque ligne de data/processed/provenance/<code>.jsonl
(et à provenance.parquet), sans jamais toucher au corpus (texte, inchangé). Le corpus et la provenance
restent reliés par text_sha256 (jamais par position de ligne) — rien ne change à ce principe.

Méthode (décidée et validée le 23/11) :
  - Répartition PAR LANGUE : 80 % train / 10 % validation / 10 % test (paramètres dans config.yaml, clé
    "split"), pour que chaque langue soit représentée dans les trois splits.
  - Langues en dessous du seuil (config "seuil_langue_min", 20 textes) : 100 % train, val/test vides — pas
    assez de textes pour un split qui ait un sens ; justifié dans le rapport, comme les langues à 0 texte de
    la tâche 9.
  - Anti-fuite : deux catégories de textes NE DOIVENT PAS être séparées entre splits, car elles contiennent
    un contenu quasi identique ou identique :
      * quasi-doublons (tâche 7.2, near_duplicate) : groupes de textes très proches DANS UNE MÊME LANGUE ;
      * doublons exacts entre langues (tâche 7.1) : même texte mot pour mot dans deux langues différentes.
    Ces deux catégories n'ont jamais été retirées du corpus (voir tâche 7 : seuls des drapeaux qualité, pas de
    suppression) — donc sans précaution, un texte et son quasi-doublon pourraient se retrouver l'un en train,
    l'autre en test. Ce script reconstitue ces groupes (reliés aux index finaux par source_url, jamais par
    position de ligne — insensible aux décalages dus aux exclusions de la tâche 8) et assigne TOUJOURS la
    totalité d'un groupe à un seul et même split.
  - Attribution reproductible : chaque texte (ou groupe, pour les textes groupés) reçoit un split déterminé
    par un hachage de son text_sha256 (ou, pour un groupe, de la liste triée de ses membres) combiné à
    random_seed (config.yaml) — aucun état aléatoire à conserver, un 2e ingénieur obtient exactement le même
    résultat en relançant le script sur les mêmes données.
  - Écriture SÉQUENTIELLE, une langue à la fois (la plus grosse d'abord), avec remplacement atomique
    immédiat de l'ancien fichier dès qu'une langue est terminée — jamais plusieurs langues dupliquées
    (ancien + nouveau) en même temps sur le disque. Avec ar et arz à elles seules (≈ 1 Go), les
    traiter même 2 à la fois doublerait temporairement l'espace nécessaire ; en séquentiel, le
    surcroît d'espace au pire moment reste celui d'UNE SEULE langue (arz, ≈ 600 Mo) plus la
    reconstruction du Parquet (≈ 340 Mo).

Sorties :
  - data/processed/provenance/<code>.jsonl et provenance.parquet : avec le champ "split" en plus.
  - data/processed/qualite/analyses/<run>_split/clusters_regroupes.jsonl : audit des groupes anti-fuite
    (quasi-doublons + doublons inter-langues) et du split attribué à chacun.
  - data/processed/qualite/analyses/<run>_split/bilan.json : comptes par langue/split, vérification
    automatique qu'aucun groupe ni aucun texte identique ne se retrouve dans deux splits différents.

Usage (depuis la racine du projet) :
    python src/split.py
"""
import argparse
import hashlib
import json
import logging
import os
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import yaml
from jsonschema import Draft7Validator

ICI = Path(__file__).resolve().parent
ROOT = ICI.parent
VERSION = "0.1"
log = logging.getLogger("split")

SPLITS_VALIDES = ("train", "validation", "test")
CHAMPS_PROVENANCE = ("text_sha256", "language_code", "source_url", "source_dump", "revision_date",
                     "collected_at", "license", "quality_flags", "split")
FLAGS_VALIDES = {"boilerplate", "language_mismatch", "near_duplicate", "template_generated"}

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
        "split": {"enum": list(SPLITS_VALIDES)},
    },
    "required": list(CHAMPS_PROVENANCE)}
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
    return config, config["split"]


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


def lire_lignes(chemin, indices):
    """Lignes JSON d'un fichier JSONL aux positions demandées (0 = première ligne)."""
    voulus, res = set(indices), {}
    if not voulus:
        return res
    fin = max(voulus)
    with open(chemin, encoding="utf-8") as f:
        for i, ligne in enumerate(f):
            if i in voulus:
                res[i] = json.loads(ligne)
            if i >= fin:
                break
    return res


def charger_totaux_langues(config):
    """Nombre de textes exportés par langue (tâche 8) — depuis le dernier bilan.json d'export disponible."""
    analyses = ROOT / config["paths"]["processed"] / "qualite" / "analyses"
    dossiers = sorted((d for d in analyses.glob("*_export") if (d / "bilan.json").exists()),
                      key=lambda d: d.name, reverse=True)
    if not dossiers:
        raise SystemExit("Aucun bilan.json d'export (tâche 8) trouvé — lancer src/exporter.py d'abord.")
    bilan = json.loads((dossiers[0] / "bilan.json").read_text(encoding="utf-8"))
    return {r["langue"]: r["exportes"] for r in bilan["langues"]}, dossiers[0].name


def langues_presentes(provenance_dir):
    return sorted(p.stem for p in provenance_dir.glob("*.jsonl"))


# 10.1 : reconstitution des groupes anti-fuite 

def construire_regroupements(sp, provenance_dir, langues):
    """Reconstitue, pour chaque langue, les groupes de quasi-doublons (tâche 7.2, dans une même langue) et
    les groupes de doublons exacts entre langues (tâche 7.1) — reliés aux lignes du corpus FINAL par
    source_url (jamais par position de ligne : les index des analyses de la tâche 7 pointent dans l'ancien
    corpus normalisé, data/processed/normalise/<run>, qui a pu perdre quelques lignes à l'export, tâche 8)."""
    ancien_source = ROOT / sp["source"]
    dossier_quasi = ROOT / sp["quasi_doublons"]
    dossier_doublons = ROOT / sp["doublons_langues"]
    non_retrouves = Counter()

    chemin_inter = dossier_doublons / "doublons_langues.jsonl"
    groupes_inter = [json.loads(l) for l in open(chemin_inter, encoding="utf-8")] if chemin_inter.exists() else []
    urls_cibles_inter = defaultdict(set)
    for g in groupes_inter:
        for code, url in g["sources"].items():
            urls_cibles_inter[code].add(url)

    clusters = []
    url_final_par_langue = {}
    groupes_qd_par_langue = {}
    for code in langues:
        chemin_groupes = dossier_quasi / "quasi_doublons" / f"{code}.jsonl"
        groupes_qd = [json.loads(l) for l in open(chemin_groupes, encoding="utf-8")] if chemin_groupes.exists() else []
        groupes_qd_par_langue[code] = groupes_qd
        idx_anciens = {i for g in groupes_qd for i in (g["garde"], *g["marques"])}
        chemin_prov_ancien = ancien_source / "provenance" / f"{code}.jsonl"
        urls_qd = lire_lignes(chemin_prov_ancien, idx_anciens) if idx_anciens and chemin_prov_ancien.exists() else {}
        urls_cibles = {v["source_url"] for v in urls_qd.values()} | urls_cibles_inter.get(code, set())

        trouve = {}
        if urls_cibles:
            chemin_final = provenance_dir / f"{code}.jsonl"
            with open(chemin_final, encoding="utf-8") as f:
                for i, ligne in enumerate(f):
                    u = json.loads(ligne)["source_url"]
                    if u in urls_cibles:
                        trouve[u] = i
                        if len(trouve) == len(urls_cibles):
                            break
        url_final_par_langue[code] = trouve

        for g in groupes_qd:
            membres = set()
            for i in (g["garde"], *g["marques"]):
                rec = urls_qd.get(i)
                idx = trouve.get(rec["source_url"]) if rec else None
                if idx is None:
                    non_retrouves["quasi_doublons"] += 1
                    continue
                membres.add((code, idx))
            if len(membres) > 1:
                clusters.append(sorted(membres))

    for g in groupes_inter:
        membres = set()
        for code, url in g["sources"].items():
            idx = url_final_par_langue.get(code, {}).get(url)
            if idx is None:
                non_retrouves["doublons_langues"] += 1
                continue
            membres.add((code, idx))
        if len(membres) > 1:
            clusters.append(sorted(membres))

    return clusters, dict(non_retrouves)


# 10.2 : attribution des splits 

def decider_split(seed, cle, ratios):
    h = hashlib.sha256(f"{seed}:{cle}".encode("utf-8")).digest()
    v = int.from_bytes(h[:8], "big") / 2 ** 64
    if v < ratios["train"]:
        return "train"
    if v < ratios["train"] + ratios["validation"]:
        return "validation"
    return "test"


def cle_cluster(membres):
    return "|".join(f"{c}:{i}" for c, i in membres)


def traiter_langue(code, provenance_dir, cle_par_index, seed, ratios, force_train):
    # Écrit le nouveau fichier à côté de l'ancien (suffixe .tmp), puis REMPLACE l'ancien par déplacement
    # atomique dès que cette langue est terminée — jamais d'attente que les 68 langues soient toutes faites :
    # sur ce disque déjà serré, garder en même temps l'ancien ET le nouveau provenance de TOUTES les langues
    # (près de 1,5 Go au total) serait exactement le genre de duplication qui a fait planter la tâche 8.
    # Traitement séquentiel (une langue à la fois, pas de Pool) pour la même raison : avec ar et arz
    # (≈ 1 Go à elles deux), même 2 langues en parallèle doubleraient temporairement l'espace nécessaire.
    t0 = time.time()
    chemin_final = provenance_dir / f"{code}.jsonl"
    chemin_tmp = provenance_dir / f"{code}.jsonl.tmp"
    compte = Counter()
    erreurs = []
    n = 0
    with open(chemin_final, encoding="utf-8") as f_in, \
         open(chemin_tmp, "w", encoding="utf-8", newline="\n") as f_out:
        for i, ligne in enumerate(f_in):
            d = json.loads(ligne)
            if force_train:
                split = "train"
            else:
                cle = cle_par_index.get(i, d["text_sha256"])
                split = decider_split(seed, cle, ratios)
            d["split"] = split
            if not _VALIDATEUR_PROVENANCE.is_valid(d):
                erreurs.append(f"{code} ligne {i} : provenance hors schéma après ajout du split")
            compte[split] += 1
            f_out.write(json.dumps(d, ensure_ascii=False) + "\n")
            n += 1
    os.replace(chemin_tmp, chemin_final)
    return {"langue": code, "textes": n, "force_train": force_train, "erreurs": erreurs,
            **{k: compte.get(k, 0) for k in SPLITS_VALIDES}, "duree_s": round(time.time() - t0)}


def repartir(config, sp):
    d = nouveau_dossier(config, "split")
    provenance_dir = ROOT / config["paths"]["provenance"]
    langues = langues_presentes(provenance_dir)
    totaux, dossier_export = charger_totaux_langues(config)
    ratios = sp["ratios"]
    seuil = sp["seuil_langue_min"]
    seed = config["random_seed"]
    t0 = time.time()

    log.info("10.1 Reconstitution des groupes anti-fuite (quasi-doublons tâche 7.2, doublons inter-langues "
             "tâche 7.1) pour %s langue(s)", len(langues))
    clusters, non_retrouves = construire_regroupements(sp, provenance_dir, langues)
    if non_retrouves:
        log.warning("Membres de groupe non retrouvés dans le corpus final (déjà écartés à l'export, "
                    "tâche 8) : %s", non_retrouves)

    log.info("10.2 Attribution des splits : ratios %s, seuil langue min %s textes, seed %s", ratios, seuil, seed)
    cluster_split, cluster_par_index = {}, defaultdict(dict)
    with open(d / "clusters_regroupes.jsonl", "w", encoding="utf-8", newline="\n") as f:
        for membres in clusters:
            cle = cle_cluster(membres)
            split = decider_split(seed, cle, ratios)
            for code, i in membres:
                cluster_par_index[code][i] = cle
            f.write(json.dumps({"membres": [{"langue": c, "index": i} for c, i in membres], "split": split},
                               ensure_ascii=False) + "\n")
            cluster_split[cle] = split

    log.info("10.3 Écriture du champ split, une langue à la fois (la plus grosse d'abord), remplacement "
             "immédiat de l'ancien fichier — jamais plus d'une langue dupliquée sur le disque à la fois")
    langues_tri = sorted(langues, key=lambda c: -(provenance_dir / f"{c}.jsonl").stat().st_size)
    resultats = []
    for code in langues_tri:
        r = traiter_langue(code, provenance_dir, cluster_par_index.get(code, {}), seed, ratios,
                           totaux.get(code, 0) < seuil)
        resultats.append(r)
        log.info("%-4s %8s textes : %7s train, %6s validation, %6s test %s (%s s)", r["langue"], r["textes"],
                 r["train"], r["validation"], r["test"],
                 "[tout en train, < seuil]" if r["force_train"] else
                 ("[ANOMALIE]" if r["erreurs"] else ""), r["duree_s"])
        for e in r["erreurs"]:
            log.error("  %s", e)
    resultats.sort(key=lambda r: r["langue"])
    return _finaliser(config, d, provenance_dir, resultats, clusters, cluster_split, totaux, seuil,
                      dossier_export, t0)


def _finaliser(config, d, provenance_dir, resultats, clusters, cluster_split, totaux, seuil, dossier_export, t0):
    # Chaque provenance/<code>.jsonl a déjà été remplacé, une langue à la fois, dans traiter_langue() —
    # rien à déplacer ici. Il ne reste que le Parquet (reconstruit en flux, jamais de concaténation en
    # mémoire, comme à l'export tâche 8) et les vérifications.
    import pyarrow as pa
    import pyarrow.parquet as pq
    schema_arrow = pa.schema([
        ("text_sha256", pa.string()), ("language_code", pa.string()), ("source_url", pa.string()),
        ("source_dump", pa.string()), ("revision_date", pa.string()), ("collected_at", pa.string()),
        ("license", pa.string()), ("quality_flags", pa.list_(pa.string())), ("split", pa.string())])
    chemin_parquet = ROOT / config["paths"]["provenance_parquet"]
    tmp_parquet = Path(str(chemin_parquet) + ".tmp")
    TAILLE_LOT = 50_000

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
            with open(provenance_dir / f"{r['langue']}.jsonl", encoding="utf-8") as f:
                for colonnes in lots(f):
                    ecrivain.write_table(pa.table(colonnes, schema=schema_arrow))
    finally:
        ecrivain.close()
    os.replace(tmp_parquet, chemin_parquet)

    # 10.4 vérification automatique : aucun groupe anti-fuite ne se retrouve à cheval entre deux splits.
    anomalies = [e for r in resultats for e in r["erreurs"]]
    splits_par_langue = {}
    for r in resultats:
        chemin = provenance_dir / f"{r['langue']}.jsonl"
        splits_lus = [json.loads(l)["split"] for l in open(chemin, encoding="utf-8")]
        splits_par_langue[r["langue"]] = splits_lus
        if len(splits_lus) != r["textes"]:
            anomalies.append(f"{r['langue']} : {len(splits_lus)} lignes relues, {r['textes']} attendues")
    for membres in clusters:
        cle = cle_cluster(membres)
        attendu = cluster_split.get(cle)
        vus = {splits_par_langue[code][i] for code, i in membres if code in splits_par_langue and
               i < len(splits_par_langue[code])}
        if vus - {attendu}:
            anomalies.append(f"groupe anti-fuite à cheval entre splits : {membres} -> {sorted(vus)}")
    for code, total in totaux.items():
        if total < seuil:
            vus = set(splits_par_langue.get(code, []))
            if vus - {"train"}:
                anomalies.append(f"{code} (< seuil, {total} textes) : split(s) autre(s) que train trouvé(s) : {vus}")

    total = {k: sum(r[k] for r in resultats) for k in SPLITS_VALIDES}
    textes = sum(r["textes"] for r in resultats)
    langues_sous_seuil = sorted(c for c, t in totaux.items() if t < seuil and t > 0)
    ecrire_json(d / "bilan.json", {
        "etape": "10 splits train/validation/test", "version": VERSION, "fin_utc": maintenant(),
        "duree_s": round(time.time() - t0), "source_export": dossier_export,
        "textes": textes, **total, "groupes_anti_fuite": len(clusters),
        "textes_regroupes": sum(len(m) for m in clusters),
        "langues_sous_seuil_tout_en_train": langues_sous_seuil,
        "anomalies": anomalies,
        "langues": [{k: v for k, v in r.items() if k != "erreurs"} for r in resultats]})
    log.info("Total : %s textes -> %s train, %s validation, %s test ; %s groupe(s) anti-fuite (%s textes) ; "
             "%s langue(s) sous le seuil (tout en train) ; %s anomalie(s) ; %s s",
             textes, total["train"], total["validation"], total["test"], len(clusters),
             sum(len(m) for m in clusters), len(langues_sous_seuil), len(anomalies), round(time.time() - t0))
    if anomalies:
        log.error("ANOMALIES — à corriger avant de valider la tâche 10 :")
        for a in anomalies:
            log.error("  %s", a)
    else:
        log.info("Aucune anomalie : provenance/*.jsonl et provenance.parquet contiennent le champ split, "
                 "aucun groupe anti-fuite à cheval entre splits.")
    return d


def main(argv=None):
    ap = argparse.ArgumentParser(description="Splits train/validation/test (tâche 10)")
    args = ap.parse_args(argv)
    config, sp = charger_config()
    repartir(config, sp)
    return 0


if __name__ == "__main__":
    sys.exit(main())
