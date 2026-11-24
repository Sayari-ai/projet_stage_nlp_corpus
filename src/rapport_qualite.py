"""Rapport qualité et statistiques par langue (tâche 9).

Agrège, pour chaque langue du périmètre final, les mesures déjà produites par les étapes précédentes :
  - volume collecté puis normalisé (tâche 6 : data/processed/normalise/<run>/mesures/<code>.json, etat.json)
  - doublons, textes générés en masse (boilerplate/template), inadéquation de langue (tâche 7 : bilan de
    l'écriture des drapeaux, 7.7)
  - exclusions et volume final exporté (tâche 8 : bilan.json du dernier export réussi)
  - longueur des textes finaux (lue directement dans data/processed/corpus/<code>.jsonl : médiane et part de
    textes courts, aucune mesure équivalente n'existant dans les étapes précédentes)

Ne relit JAMAIS les données brutes de la collecte (tâche 5) ni les dumps : seulement les petits fichiers de
bilan déjà écrits par chaque étape, donc aucun besoin que data/processed/collecte/ soit présent sur ce disque.

Sorties, dans data/processed/qualite/ :
  - rapport_qualite_par_langue.csv   : un tableau, une ligne par langue, pour relecture/tableur
  - rapport_qualite_par_langue.jsonl : même contenu, une ligne JSON par langue
  - couverture.json                 : périmètre cible (config.yaml, tâche 1/2) vs langues réellement dans le
                                       corpus final, avec les écarts et leur raison apparente
  - manifeste_collecte.csv / .jsonl : un fichier par langue — dump utilisé (nom, taille, sha1), pages lues,
                                       textes collectés, exclusions à la collecte et leurs raisons, volume en
                                       mots. Écrit UNIQUEMENT si data/processed/collecte/<run>/ (ou --collecte)
                                       est accessible ; sinon cette sortie est simplement absente, le reste du
                                       rapport n'en a pas besoin.

Usage (depuis la racine du projet) :
    python src/rapport_qualite.py
    python src/rapport_qualite.py --export 20261122T081323Z_export   # imposer le dossier d'export (sinon : le
                                                                      # plus récent avec un bilan.json complet)
    python src/rapport_qualite.py --collecte "D:/archive/collecte/20261105T065025Z"   # si collecte/ a été
                                                                                      # déplacé hors du projet
"""
import argparse
import csv
import json
import statistics
import sys
from pathlib import Path

import yaml

ICI = Path(__file__).resolve().parent
ROOT = ICI.parent
VERSION = "0.1"

SEUILS_COURT = (200, 500)  # en caractères ; deux seuils pour ne pas trancher arbitrairement sur un seul


def charger_config():
    with open(ROOT / "config" / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def dernier_avec_bilan(dossier_analyses, suffixe):
    """Le dossier d'analyse le plus récent se terminant par _<suffixe> et contenant un bilan.json complet
    (ignore un éventuel essai planté sans bilan.json, pour ne jamais lire un résultat incomplet)."""
    candidats = sorted(dossier_analyses.glob(f"*_{suffixe}"), reverse=True)
    for d in candidats:
        if (d / "bilan.json").exists():
            return d
    return None


def charger_normalise(normalise_dir):
    """{code: {textes_entree, textes_sortie, textes_modifies, caracteres_avant, caracteres_apres}}"""
    par_langue = {}
    for p in sorted((normalise_dir / "mesures").glob("*.json")):
        m = json.loads(p.read_text(encoding="utf-8"))
        par_langue[m["langue"]] = {
            "textes_collectes": m["textes_entree"], "textes_normalises": m["textes_sortie"],
            "textes_modifies": m["textes_modifies"],
            "caracteres_avant": m.get("caracteres_avant"), "caracteres_apres": m.get("caracteres_apres")}
    return par_langue


def charger_bilan_qualite(dossier_qualite):
    """{code: {textes, boilerplate, language_mismatch, near_duplicate, template_generated,
               au_moins_un_drapeau, plusieurs_drapeaux, erreurs_collecte}} depuis le bilan de la 7.7."""
    b = json.loads((dossier_qualite / "bilan.json").read_text(encoding="utf-8"))
    par_langue = {}
    for r in b["langues"]:
        par_langue[r["langue"]] = {k: r[k] for k in (
            "textes", "boilerplate", "language_mismatch", "near_duplicate", "template_generated",
            "au_moins_un_drapeau", "plusieurs_drapeaux") if k in r}
        par_langue[r["langue"]]["erreurs_collecte"] = len(r.get("erreurs_collecte", []))
    return par_langue, b


def charger_bilan_export(dossier_export):
    b = json.loads((dossier_export / "bilan.json").read_text(encoding="utf-8"))
    par_langue = {}
    for r in b["langues"]:
        par_langue[r["langue"]] = {"textes_avant_export": r["textes"], "textes_exportes": r["exportes"],
                                   "textes_ecartes_export": r["exclus"], "drapeaux_export": r["drapeaux"],
                                   "anomalies_export": len(r.get("erreurs_schema", [])) +
                                                       len(r.get("erreurs_empreinte", []))}
    return par_langue, b


def longueurs_corpus(corpus_dir, code):
    """(nb_textes, longueur_mediane, {seuil: nb_textes_sous_ce_seuil}) en caractères, lu directement dans le
    corpus final — aucune étape précédente ne mesure la longueur des textes définitifs."""
    chemin = corpus_dir / f"{code}.jsonl"
    if not chemin.exists():
        return 0, None, {s: 0 for s in SEUILS_COURT}
    longueurs = []
    with open(chemin, encoding="utf-8") as f:
        for ligne in f:
            longueurs.append(len(json.loads(ligne)["text"]))
    if not longueurs:
        return 0, None, {s: 0 for s in SEUILS_COURT}
    mediane = statistics.median(longueurs)
    sous_seuil = {s: sum(1 for n in longueurs if n < s) for s in SEUILS_COURT}
    return len(longueurs), mediane, sous_seuil


def charger_collecte(collecte_dir):
    """{code: {...}} depuis data/processed/collecte/<run>/mesures/<code>.json (un fichier par langue, écrit
    par collecter.py) — jamais depuis les dumps ou le texte brut, seulement ces petits bilans."""
    par_langue = {}
    dossier_mesures = collecte_dir / "mesures"
    if not dossier_mesures.is_dir():
        return par_langue
    run_metadata = {}
    chemin_rm = collecte_dir / "run_metadata.json"
    if chemin_rm.exists():
        run_metadata = json.loads(chemin_rm.read_text(encoding="utf-8")).get("dumps", {})
    for p in sorted(dossier_mesures.glob("*.json")):
        m = json.loads(p.read_text(encoding="utf-8"))
        dumps = run_metadata.get(m["langue"], [])
        par_langue[m["langue"]] = {
            "dump_fichiers": ";".join(d["fichier"] for d in dumps),
            "dump_taille_octets": sum(d["taille"] for d in dumps) if dumps else None,
            "pages_ns0_dump": m.get("pages_ns0"), "textes_collecte": m.get("textes"),
            "exclusions_collecte": m.get("exclusions"),
            "raisons_exclusion_collecte": "; ".join(f"{k}: {v}" for k, v in
                                                     (m.get("raisons_exclusion") or {}).items()),
            "mots_total_collecte": m.get("mots_total"), "mots_mediane_collecte": m.get("mots_mediane")}
    return par_langue


def construire_manifeste_collecte(collecte_dir, dossier_sortie):
    collecte = charger_collecte(collecte_dir)
    if not collecte:
        return None
    lignes = [{"langue": code, **vals} for code, vals in sorted(collecte.items())]
    entetes = list(lignes[0].keys())
    with open(dossier_sortie / "manifeste_collecte.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=entetes)
        w.writeheader()
        w.writerows(lignes)
    with open(dossier_sortie / "manifeste_collecte.jsonl", "w", encoding="utf-8", newline="\n") as f:
        for ligne in lignes:
            f.write(json.dumps(ligne, ensure_ascii=False) + "\n")
    return lignes


def construire_rapport(config, export_dir, normalise_dir, qualite_dir, corpus_dir):
    normalise = charger_normalise(normalise_dir)
    qualite, bilan_qualite = charger_bilan_qualite(qualite_dir)
    export, bilan_export = charger_bilan_export(export_dir)
    langues = sorted(set(normalise) | set(qualite) | set(export))

    lignes = []
    for code in langues:
        n, q, e = normalise.get(code, {}), qualite.get(code, {}), export.get(code, {})
        nb_final, mediane, sous_seuil = longueurs_corpus(corpus_dir, code)
        ligne = {
            "langue": code,
            "textes_collectes": n.get("textes_collectes"),
            "textes_normalises": n.get("textes_normalises"),
            "textes_modifies_normalisation": n.get("textes_modifies"),
            "near_duplicate": q.get("near_duplicate"), "template_generated": q.get("template_generated"),
            "boilerplate": q.get("boilerplate"), "language_mismatch": q.get("language_mismatch"),
            "au_moins_un_drapeau": q.get("au_moins_un_drapeau"), "plusieurs_drapeaux": q.get("plusieurs_drapeaux"),
            "erreurs_collecte_detectees": q.get("erreurs_collecte"),
            "textes_ecartes_export": e.get("textes_ecartes_export"),
            "textes_exportes_final": e.get("textes_exportes"),
            "textes_corpus_final": nb_final, "longueur_mediane_caracteres": mediane,
        }
        for s in SEUILS_COURT:
            ligne[f"textes_courts_moins_{s}_caracteres"] = sous_seuil[s]
        lignes.append(ligne)

    dossier_sortie = ROOT / config["paths"]["processed"] / "qualite"
    dossier_sortie.mkdir(parents=True, exist_ok=True)
    entetes = list(lignes[0].keys()) if lignes else []
    with open(dossier_sortie / "rapport_qualite_par_langue.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=entetes)
        w.writeheader()
        w.writerows(lignes)
    with open(dossier_sortie / "rapport_qualite_par_langue.jsonl", "w", encoding="utf-8", newline="\n") as f:
        for ligne in lignes:
            f.write(json.dumps(ligne, ensure_ascii=False) + "\n")

    couverture = construire_couverture(config, export, langues)
    with open(dossier_sortie / "couverture.json", "w", encoding="utf-8") as f:
        json.dump(couverture, f, ensure_ascii=False, indent=1)

    return lignes, couverture, dossier_sortie


def construire_couverture(config, export, langues_traitees):
    """Périmètre cible (config.yaml, figé aux tâches 1/2) vs langues réellement présentes dans le corpus
    final avec au moins un texte exporté. Toute langue cible absente ou à 0 est signalée avec sa raison."""
    cible = set(config["scope"]["editions"]["incluses"])
    ref = config["scope"]["reference_language"]
    couvertes, vides, absentes = [], [], []
    for code in sorted(cible):
        e = export.get(code)
        if e is None:
            absentes.append(code)
        elif e["textes_exportes"] == 0:
            vides.append(code)
        else:
            couvertes.append(code)
    return {
        "perimetre_cible": len(cible), "langues_couvertes": len(couvertes),
        "langues_a_zero_texte_final": vides, "langues_cibles_jamais_traitees": absentes,
        "langue_reference_fr_incluse": ref in langues_traitees,
        "couverture_pourcentage": round(100 * len(couvertes) / len(cible), 1) if cible else None,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="Rapport qualité et statistiques par langue (tâche 9)")
    ap.add_argument("--export", metavar="DOSSIER", help="dossier d'export à utiliser (sinon : le plus récent "
                                                         "avec un bilan.json complet)")
    ap.add_argument("--collecte", metavar="CHEMIN", help="dossier du run de collecte (sinon : "
                                                         "normalisation.source du config.yaml, si présent)")
    args = ap.parse_args(argv)
    config = charger_config()
    analyses = ROOT / config["paths"]["processed"] / "qualite" / "analyses"
    export_dir = (analyses / args.export) if args.export else dernier_avec_bilan(analyses, "export")
    if export_dir is None or not (export_dir / "bilan.json").exists():
        sys.exit("Aucun export complet trouvé (bilan.json manquant) : lancer d'abord src/exporter.py.")
    normalise_dir = ROOT / config["export"]["source"]
    qualite_dir = ROOT / config["export"]["qualite"]
    corpus_dir = ROOT / config["paths"]["corpus"]
    lignes, couverture, dossier_sortie = construire_rapport(config, export_dir, normalise_dir, qualite_dir,
                                                             corpus_dir)
    print(f"{len(lignes)} langue(s) ; écrit dans {dossier_sortie.relative_to(ROOT)}")
    print(f"couverture : {couverture['langues_couvertes']}/{couverture['perimetre_cible']} "
          f"({couverture['couverture_pourcentage']} %)")
    if couverture["langues_a_zero_texte_final"]:
        print("  à 0 texte final :", ", ".join(couverture["langues_a_zero_texte_final"]))
    if couverture["langues_cibles_jamais_traitees"]:
        print("  jamais traitées :", ", ".join(couverture["langues_cibles_jamais_traitees"]))

    collecte_dir = Path(args.collecte) if args.collecte else (
        ROOT / config["normalisation"]["source"] if "normalisation" in config else None)
    if collecte_dir and collecte_dir.is_dir():
        manifeste = construire_manifeste_collecte(collecte_dir, dossier_sortie)
        if manifeste:
            print(f"manifeste de collecte : {len(manifeste)} langue(s) (source : {collecte_dir})")
        else:
            print(f"manifeste de collecte : pas de dossier mesures/ dans {collecte_dir}, ignoré")
    else:
        print("manifeste de collecte : non généré (dossier de collecte introuvable — "
              "passer --collecte CHEMIN pour le fournir)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
