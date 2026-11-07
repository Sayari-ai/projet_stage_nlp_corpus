"""Pipeline de collecte reproductible (tâche 5).

Usage (depuis la racine du projet) :
    python src/collecter.py --langues wo yo nqo ha am     # run de test sur quelques langues
    python src/collecter.py --tout                        # les éditions retenues + l'échantillon français
    python src/collecter.py --reprendre                   # reprend le dernier run non terminé
    python src/collecter.py --reprendre 20261030T120000Z  # reprend ce run

Pour chaque langue, dans l'ordre des dumps les plus petits aux plus grands :
    1. informations du wiki (API siteinfo), réponse brute gardée dans data/raw ;
    2. téléchargement du dump pages-articles (reprenable, SHA-1 vérifié) dans data/raw ;
    3. extraction, nettoyage, doublons, écriture dans data/processed/collecte/<run>/ ;
    4. validation automatique (schéma, règles, provenance) et mesures.
Le français (langue de comparaison) est un échantillon tiré par l'API, traité en dernier.
Tout est noté dans run.log et journal_requetes.jsonl ; l'état est dans etat.json.
"""
import argparse
import json
import logging
import os
import platform
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

ICI = Path(__file__).resolve().parent
ROOT = ICI.parent
sys.path.insert(0, str(ICI))

import valider_records as V
from dumps import ordre_fichiers, pages_du_dump
from francais import pages_francaises, tirer_echantillon
from traitement import ecrire_json, traiter_langue
from wikimedia import ClientWikimedia, infos_wiki, maintenant

VERSION = "1.0"
log = logging.getLogger("collecte")


def charger_env(chemin=ROOT / ".env"):
    """Variables du fichier .env (non versionné) : NOM=valeur, une par ligne."""
    if Path(chemin).exists():
        for ligne in Path(chemin).read_text(encoding="utf-8").splitlines():
            ligne = ligne.strip()
            if ligne and not ligne.startswith("#") and "=" in ligne:
                nom, valeur = ligne.split("=", 1)
                os.environ.setdefault(nom.strip(), valeur.strip().strip('"').strip("'"))


def charger_config():
    with open(ROOT / "config" / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def configurer_journal(dossier_run):
    log.setLevel(logging.INFO)
    log.handlers.clear()
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%dT%H:%M:%S")
    for h in (logging.FileHandler(dossier_run / "run.log", encoding="utf-8"), logging.StreamHandler(sys.stdout)):
        h.setFormatter(fmt)
        log.addHandler(h)


def version_code():
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                              timeout=10).stdout.strip() or None
    except Exception:
        return None


def versions_outils():
    import bs4, jsonschema, mwparserfromhell, requests
    return {"python": platform.python_version(), "plateforme": platform.platform(),
            "requests": requests.__version__, "mwparserfromhell": mwparserfromhell.__version__,
            "beautifulsoup4": bs4.__version__, "jsonschema": jsonschema.__version__}


def dernier_run_non_termine(dossier_collecte):
    for d in sorted(dossier_collecte.glob("*T*Z"), reverse=True):
        e = d / "etat.json"
        if e.exists() and json.loads(e.read_text(encoding="utf-8")).get("statut") != "terminé":
            return d.name
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(description="Collecte reproductible du corpus (tâche 5)")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--langues", nargs="+", help="codes des éditions à collecter")
    g.add_argument("--tout", action="store_true", help="toutes les éditions retenues + échantillon français")
    g.add_argument("--reprendre", nargs="?", const="", metavar="RUN", help="reprendre un run interrompu")
    args = ap.parse_args(argv)
    charger_env()
    contact = os.environ.get("WIKIMEDIA_CONTACT")
    if not contact:
        sys.exit("Contact manquant : créer le fichier .env à la racine du projet avec la ligne "
                 "WIKIMEDIA_CONTACT=votre.adresse@exemple.com (voir .env.example).")

    config = charger_config()
    pp = config["pipeline"]
    dossier_collecte = ROOT / config["paths"]["processed"] / "collecte"
    dossier_api = ROOT / config["paths"]["raw"] / "api"
    dossier_dumps = ROOT / config["paths"]["raw"] / "dumps" / config["dump_date"]
    for d in (dossier_collecte, dossier_api, dossier_dumps):
        d.mkdir(parents=True, exist_ok=True)

    # nouveau run ou reprise 
    if args.reprendre is not None:
        run_id = args.reprendre or dernier_run_non_termine(dossier_collecte)
        if not run_id or not (dossier_collecte / run_id / "etat.json").exists():
            sys.exit("Aucun run à reprendre.")
        dossier_run = dossier_collecte / run_id
        etat = json.loads((dossier_run / "etat.json").read_text(encoding="utf-8"))
        configurer_journal(dossier_run)
        log.info("Reprise du run %s", run_id)
        config = yaml.safe_load((dossier_run / "config.yaml").read_text(encoding="utf-8"))  # même configuration
        pp = config["pipeline"]
        for c, e in etat["langues"].items():               # les langues en échec sont retentées
            if e["statut"] == "échec":
                e["statut"] = "à faire"
                e.pop("erreur", None)
        etat["statut"] = "en cours"
    else:
        autorises = V.codes_autorises()
        if args.tout:
            langues = list(config["scope"]["editions"]["incluses"])
            francais = True
        else:
            hors = [c for c in args.langues if c not in autorises]
            if hors:
                sys.exit(f"Langue(s) hors périmètre : {', '.join(hors)}")
            francais = config["scope"]["reference_language"] in args.langues
            langues = [c for c in args.langues if c != config["scope"]["reference_language"]]
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        dossier_run = dossier_collecte / run_id
        dossier_run.mkdir(parents=True)
        shutil.copy(ROOT / "config" / "config.yaml", dossier_run / "config.yaml")
        etat = {"run_id": run_id, "statut": "en cours", "debut_utc": maintenant(), "version_pipeline": VERSION,
                "version_code_git": version_code(), "dump_date": config["dump_date"],
                "langues_demandees": langues, "francais": francais, "langues": {}}
        configurer_journal(dossier_run)
        log.info("Nouveau run %s : %s édition(s)%s", run_id, len(langues), " + échantillon français" if francais else "")

    def sauver_etat():
        ecrire_json(dossier_run / "etat.json", etat)

    sauver_etat()
    client = ClientWikimedia(contact, dossier_run / "journal_requetes.jsonl",
                             pp["pause_api_s"], pp["tentatives_max"], VERSION)
    processus = pp.get("processus") or max(1, (os.cpu_count() or 2) - 1)
    date = etat["dump_date"]

    #  1. informations des wikis et des dumps (ordre : du plus petit au plus grand) 
    for code in etat["langues_demandees"]:
        e = etat["langues"].setdefault(code, {"statut": "à faire"})
        if "dump" in e:
            continue
        try:
            si = client.siteinfo(code)
            (dossier_api / "siteinfo").mkdir(parents=True, exist_ok=True)
            ecrire_json(dossier_api / "siteinfo" / f"{code}_{run_id}.json", si)
            e["infos"] = infos_wiki(si)
            st = client.statut_dump(e["infos"]["dbname"], date)
            job = st["jobs"]["articlesdump"]
            if job["status"] != "done":
                raise RuntimeError(f"dump {date} non terminé ({job['status']})")
            e["dump"] = [{"fichier": n, "url": m["url"], "sha1": m["sha1"], "taille": m["size"]}
                         for n, m in job["files"].items()]
            e["dump"] = [next(x for x in e["dump"] if x["fichier"] == n) for n in ordre_fichiers([x["fichier"] for x in e["dump"]])]
        except Exception as ex:                            # langue en échec : notée, le run continue
            e["statut"], e["erreur"] = "échec", f"{type(ex).__name__} : {ex}"
            log.error("%s : %s", code, e["erreur"])
        sauver_etat()
    ordre = sorted((c for c in etat["langues_demandees"] if "dump" in etat["langues"][c]),
                   key=lambda c: sum(x["taille"] for x in etat["langues"][c]["dump"]))
    taille_totale = sum(x["taille"] for c in ordre for x in etat["langues"][c]["dump"])
    log.info("%s dump(s) à traiter, %.1f Mo au total ; ordre : %s", len(ordre), taille_totale / 1e6, " ".join(ordre))

    #  2-4. traitement langue par langue
    def finir_langue(code, mesures):
        e = etat["langues"][code]
        res, erreurs = V.valider_langue(dossier_run / "corpus" / f"{code}.jsonl",
                                        dossier_run / "provenance" / f"{code}.jsonl", code)
        mesures["validation"] = res
        mesures["erreurs_validation"] = erreurs[:50]
        (dossier_run / "mesures").mkdir(exist_ok=True)
        ecrire_json(dossier_run / "mesures" / f"{code}.json", mesures)
        e["statut"] = "terminée"
        e.pop("point", None)
        sauver_etat()
        log.info("%s terminée : %s textes, %s écartées, %s mots, validation %s/%s (%s s)", code, mesures["textes"],
                 mesures["exclusions"], mesures["mots_total"], res["lignes_valides"], res["lignes"], mesures["duree_s"])

    for code in ordre:
        e = etat["langues"][code]
        if e["statut"] == "terminée":
            continue
        try:
            infos = e["infos"]
            for x in e["dump"]:
                if client.telecharger("https://dumps.wikimedia.org" + x["url"], dossier_dumps / x["fichier"],
                                      x["sha1"], x["taille"]):
                    log.info("%s : %s téléchargé (%.1f Mo)", code, x["fichier"], x["taille"] / 1e6)
            e["statut"] = "en cours"
            sauver_etat()
            source = f"{infos['dbname']}-{date}-pages-articles"

            def pages():
                i = 0
                for x in e["dump"]:
                    for p in pages_du_dump(dossier_dumps / x["fichier"]):
                        if p["ns"] == 0 and not p["redirection"]:
                            yield i, p
                            i += 1

            mesures = traiter_langue(code, pages(), infos, lambda p: source, client, dossier_run, dossier_api,
                                     e, sauver_etat, processus, pp["point_de_reprise_pages"])
            finir_langue(code, mesures)
        except KeyboardInterrupt:
            log.warning("Interruption : relancer avec --reprendre %s", run_id)
            raise
        except Exception as ex:
            e["statut"], e["erreur"] = "échec", f"{type(ex).__name__} : {ex}"
            sauver_etat()
            log.exception("%s : échec, le run continue avec la langue suivante", code)

    #  échantillon français 
    fr = config["scope"]["reference_language"]
    if etat["francais"] and etat["langues"].get(fr, {}).get("statut") != "terminée":
        e = etat["langues"].setdefault(fr, {})
        e["statut"] = "en cours"
        try:
            si = client.siteinfo(fr)
            e["infos"] = infos_wiki(si)
            ids = tirer_echantillon(client, dossier_api, pp["echantillon_fr"])
            e["echantillon"] = len(ids)
            sauver_etat()
            mesures = traiter_langue(fr, pages_francaises(client, dossier_api, ids), e["infos"],
                                     lambda p: f"api-{p['_lot_collecte'][:10].replace('-', '')}",
                                     client, dossier_run, dossier_api, e, sauver_etat, processus,
                                     pp["point_de_reprise_pages"])
            finir_langue(fr, mesures)
        except KeyboardInterrupt:
            log.warning("Interruption : relancer avec --reprendre %s", run_id)
            raise
        except Exception as ex:
            e["statut"], e["erreur"] = "échec", f"{type(ex).__name__} : {ex}"
            sauver_etat()
            log.exception("fr : échec")

    # bilan 
    toutes = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((dossier_run / "mesures").glob("*.json"))] \
        if (dossier_run / "mesures").exists() else []
    ecrire_json(dossier_run / "mesures.json", toutes)
    echecs = {c: v.get("erreur") for c, v in etat["langues"].items() if v["statut"] != "terminée"}
    controles = {
        "corpus valide et 100 % des textes avec provenance": all(
            m["validation"]["lignes_valides"] == m["validation"]["lignes"] == m["validation"]["avec_provenance"]
            and not m["erreurs_validation"] for m in toutes),
        "chaque page comptée (C2)": all(m["c2_chaque_page_comptee"] for m in toutes),
        "chaque langue demandée a des textes (C1)": not echecs and all(m["textes"] > 0 for m in toutes),
    }
    etat.update({"statut": "terminé" if not echecs else "terminé avec échecs", "fin_utc": maintenant(),
                 "controles": controles, "echecs": echecs})
    sauver_etat()
    ecrire_json(dossier_run / "run_metadata.json", {
        "run_id": run_id, "debut_utc": etat["debut_utc"], "fin_utc": etat["fin_utc"],
        "version_pipeline": VERSION, "version_code_git": etat.get("version_code_git"), "outils": versions_outils(),
        "dump_date": date, "dumps": {c: etat["langues"][c].get("dump") for c in ordre},
        "echantillon_fr": etat["langues"].get(fr, {}).get("echantillon"), "processus": processus,
        "controles": controles, "echecs": echecs,
        "totaux": {"textes": sum(m["textes"] for m in toutes), "mots": sum(m["mots_total"] for m in toutes)}})
    log.info("Run %s %s : %s langue(s), %s textes, %s mots", run_id, etat["statut"], len(toutes),
             sum(m["textes"] for m in toutes), sum(m["mots_total"] for m in toutes))
    for nom, ok in controles.items():
        log.info("  %s : %s", nom, "OK" if ok else "NON")
    if echecs:
        log.warning("Langues en échec : %s — relancer avec --reprendre %s", ", ".join(echecs), run_id)
    return 0 if not echecs and all(controles.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
