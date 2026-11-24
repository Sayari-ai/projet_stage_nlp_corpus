"""Normalisation du texte (tâche 6), en préservant les spécificités de chaque langue.

Usage (depuis la racine du projet) :
    python src/normaliser.py --langues tw ee fon        # quelques langues
    python src/normaliser.py --tout                     # toutes les langues du run source
    python src/normaliser.py --reprendre                # reprend le dernier run non terminé
    python src/normaliser.py --verifier [RUN]           # contrôle complet d'un run déjà fait (lecture seule)

Le run source (corpus de la tâche 5) est lu dans config.yaml (normalisation.source) et n'est
jamais modifié. Le résultat va dans data/processed/normalise/<run>/ :
    corpus/<code>.jsonl       textes normalisés, {"text": ...}
    provenance/<code>.jsonl   provenance mise à jour (nouvelle empreinte + empreinte d'origine)
    exclusions/<code>.jsonl   textes devenus identiques ou vides après normalisation
    mesures/<code>.json       nombre d'applications de chaque règle, exemples avant/après, contrôles

Règles validées (tâche 6), appliquées dans cet ordre :
    R4  caractères de contrôle : réparés (\\x93 \\x94 -> “ ”…) ou retirés
    R3  caractères invisibles inutiles retirés (largeur nulle, \\ufeff, marques de sens, tiret conditionnel)
        ZWJ et ZWNJ sont gardés (utiles aux écritures arabes et autres)
    R2  espaces spéciales (insécable, fine…) -> espace normale
    R6  ponctuation détournée propre à une langue (fon : ‚ -> ,)
    R5  sosies : lettre d'un autre alphabet utilisée dans un mot latin -> bonne lettre (ε -> ɛ, ↄ -> ɔ…)
    R9  apostrophes et guillemets uniformisés : ’ ‘ ` ´ -> '   “ ” „ -> "   « texte » -> "texte"   ‹ › -> '
        (ʼ, lettre à part entière, et les signes ′ ″ des coordonnées sont gardés)
    R7  restes du nettoyage : crochets orphelins, « File:| », parenthèses vides, restes de balisage
        (<ref, }}, vignette|…) devenus visibles après le retrait d'un caractère invisible
    R8  espaces en trop, lignes vides en trop
    R1  Unicode NFC
On ne touche jamais : les lettres africaines, les tons et diacritiques, les écritures non latines,
les mots cités dans un autre alphabet, la casse, les chiffres.
"""
import argparse
import functools
import json
import logging
import os
import re
import sys
import time
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from multiprocessing import Pool
from pathlib import Path

import yaml

ICI = Path(__file__).resolve().parent
ROOT = ICI.parent
sys.path.insert(0, str(ICI))
import valider_records as V                      # noqa: E402
from extraire_texte import BALISAGE              # noqa: E402

VERSION = "1.0"
log = logging.getLogger("normalisation")

# R4 : caractères de contrôle 
# Codes C1 de Windows-1252 qu'on rencontre à la place de la ponctuation typographique
C1_REPARES = {"\x91": "‘", "\x92": "’", "\x93": "“", "\x94": "”", "\x96": "–", "\x97": "—", "\x85": "…"}
CONTROLE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")      # tout sauf \t (0x09) et \n (0x0a)

# R3 : invisibles retirés (ZWNJ U+200C et ZWJ U+200D sont gardés) 
INVISIBLES = re.compile("[\u200b\ufeff\u200e\u200f\u202a-\u202e\u2066-\u2069\u00ad\u2060\u2061-\u2064\u180e]")

# R2 : espaces spéciales -> espace normale
ESPACES = re.compile("[\t\u00a0\u1680\u2000-\u200a\u202f\u205f\u3000]")

#R6 : ponctuation détournée, par langue 
PONCTUATION_LANGUE = {"fon": {"‚": ","}}

# R5 : sosies 
# Lettre d'un autre alphabet (ou variante) - lettre latine correcte, appliqué seulement dans un mot latin
SOSIES = {
    # ɛ (epsilon latin) et ɔ (o ouvert), lettres de nombreuses langues africaines
    "ε": "ɛ", "ԑ": "ɛ", "є": "ɛ", "Ԑ": "Ɛ",
    "ↄ": "ɔ", "ͻ": "ɔ", "ᴐ": "ɔ", "כ": "ɔ", "Ↄ": "Ɔ", "Ͻ": "Ɔ",
    # lettres cyrilliques et grecques identiques à des lettres latines
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "х": "x", "у": "y", "і": "i", "ј": "j", "ѕ": "s",
    "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H", "О": "O", "Р": "P", "С": "C", "Т": "T", "Х": "X",
    "ο": "o", "Ο": "O", "Α": "A", "Β": "B", "Ε": "E", "Ζ": "Z", "Η": "H", "Ι": "I", "Κ": "K", "Μ": "M",
    "Ν": "N", "Ρ": "P", "Τ": "T", "Χ": "X", "Υ": "Y",
}
# Remplacements propres aux langues dont l'alphabet contient la lettre : ils s'appliquent dès que le mot
# contient une lettre latine (ex. twi « yε » -> « yɛ »)
EPSILON_O = {"ε": "ɛ", "ԑ": "ɛ", "є": "ɛ", "Ԑ": "Ɛ", "ↄ": "ɔ", "ͻ": "ɔ", "ᴐ": "ɔ", "כ": "ɔ", "Ↄ": "Ɔ", "Ͻ": "Ɔ"}
LANGUES_EPSILON_O = ("ak", "tw", "ee", "fon", "gur", "dag", "kab", "shi", "bm", "ln", "kg", "mos", "gpe",
                     "guw", "din", "nup", "ann", "bol", "fat", "kus", "dga", "kcg", "kbp")
LANGUES_D_AFRICAIN = ("ee", "fon", "guw", "gur")          # Ɖ ɖ
SOSIES_LANGUE = {}
for _c in LANGUES_EPSILON_O:
    SOSIES_LANGUE.setdefault(_c, {}).update(EPSILON_O)
for _c in LANGUES_D_AFRICAIN:
    SOSIES_LANGUE.setdefault(_c, {}).update({"Ð": "Ɖ", "Đ": "Ɖ"})


def _est_latin(ch):
    try:
        return unicodedata.name(ch).startswith("LATIN")
    except ValueError:
        return False


@functools.lru_cache(maxsize=None)
def table_sosies(code):
    table = dict(SOSIES)
    table.update(SOSIES_LANGUE.get(code, {}))
    return table, re.compile("[" + re.escape("".join(table)) + "]")


def _segments(ligne):
    """Découpe une ligne en segments (mot, est_un_mot) ; un mot = lettres et signes diacritiques qui les suivent."""
    seg, courant, mot = [], [], None
    for c in ligne:
        m = c.isalpha() or (unicodedata.category(c).startswith("M") and mot)
        if m != mot and courant:
            seg.append(("".join(courant), bool(mot)))
            courant = []
        courant.append(c)
        mot = m
    if courant:
        seg.append(("".join(courant), bool(mot)))
    return seg


def remplacer_sosies(texte, code, compte):
    """Remplace les sosies dans les mots latins.

    Un mot est corrigé seulement s'il ne contient aucune lettre d'un autre alphabet (hors sosies)
    et s'il contient plus de lettres latines que de sosies (ex. « wↄwoo », « twerεfoↄ »). Les sosies
    propres à une langue (Ð -> Ɖ en éwé) sont corrigés dès que le mot contient une lettre latine.
    Une lettre isolée, un mot grec, russe ou hébreu cité, une table d'alphabets ne sont pas touchés."""
    table, classe = table_sosies(code)
    if not classe.search(texte):
        return texte
    propres = SOSIES_LANGUE.get(code, {})
    lignes = texte.split("\n")
    for n, ligne in enumerate(lignes):
        if not classe.search(ligne):
            continue
        morceaux = []
        for w, est_mot in _segments(ligne):
            if est_mot and classe.search(w):
                lettres = [c for c in w if c.isalpha()]
                conf = [c for c in lettres if c in table]
                latines = [c for c in lettres if c not in table and _est_latin(c)]
                autres = [c for c in lettres if c not in table and not _est_latin(c)]
                if not autres and latines and (len(latines) > len(conf) or all(c in propres for c in conf)):
                    nouveau = "".join(table.get(c, c) for c in w)
                    compte["R5 sosies"] += len(conf)
                    w = nouveau
            morceaux.append(w)
        lignes[n] = "".join(morceaux)
    return "\n".join(lignes)


# R9 : apostrophes et guillemets 
APOSTROPHES = re.compile("[’‘‛`´]")
GUILLEMETS_DOUBLES = re.compile("[“”„‟]")
CHEVRON_OUVRANT = re.compile(r"«[ \u00a0\u202f]*")
CHEVRON_FERMANT = re.compile(r"[ \u00a0\u202f]*»")
CHEVRONS_SIMPLES = re.compile("[‹›]")

# R7 : restes du nettoyage 
RESTE_FICHIER = re.compile(r"\b(?:File|Fichier|Image)\s*:\s*(?:\|\s*alt\s*)?\|", re.I)
PARENTHESES_VIDES = re.compile(r"\s?\(\s*[;,:.]?\s*\)")


def crochets_orphelins(ligne):
    """Retire les [ et ] sans partenaire dans la ligne ; les paires [ ... ] sont gardées."""
    if "[" not in ligne and "]" not in ligne:
        return ligne, 0
    ouverts, retirer = [], set()
    for i, c in enumerate(ligne):
        if c == "[":
            ouverts.append(i)
        elif c == "]":
            if ouverts:
                ouverts.pop()
            else:
                retirer.add(i)
    retirer.update(ouverts)
    if not retirer:
        return ligne, 0
    return "".join(c for i, c in enumerate(ligne) if i not in retirer), len(retirer)


# R8 : espaces en trop
ESPACES_MULTIPLES = re.compile(r" {2,}")
LIGNES_VIDES = re.compile(r"\n{3,}")


def normaliser(texte, code, compte=None, trace=None):
    """Applique les règles validées de la tâche 6 à un texte.
    compte (Counter) reçoit le nombre de changements ; trace (liste) reçoit (règle, avant, après) par règle appliquée."""
    compte = compte if compte is not None else Counter()
    t = texte
    etape = [texte]

    def noter(regle):
        if trace is not None and t != etape[0]:
            trace.append((regle, etape[0], t))
        etape[0] = t
    # R4
    n = 0
    for a, b in C1_REPARES.items():
        if a in t:
            n += t.count(a)
            t = t.replace(a, b)
    t, k = CONTROLE.subn("", t)
    compte["R4 caractères de contrôle"] += n + k
    noter("R4 caractères de contrôle")
    # R3
    t, k = INVISIBLES.subn("", t)
    compte["R3 invisibles retirés"] += k
    noter("R3 invisibles retirés")
    # R2
    t, k = ESPACES.subn(" ", t)
    compte["R2 espaces spéciales"] += k
    noter("R2 espaces spéciales")
    # R6
    for a, b in PONCTUATION_LANGUE.get(code, {}).items():
        if a in t:
            compte["R6 ponctuation détournée"] += t.count(a)
            t = t.replace(a, b)
    noter("R6 ponctuation détournée")
    # R5
    t = remplacer_sosies(t, code, compte)
    noter("R5 sosies")
    # R9
    t, k1 = APOSTROPHES.subn("'", t)
    t, k2 = GUILLEMETS_DOUBLES.subn('"', t)
    t, k3 = CHEVRON_OUVRANT.subn('"', t)
    t, k4 = CHEVRON_FERMANT.subn('"', t)
    t, k5 = CHEVRONS_SIMPLES.subn("'", t)
    compte["R9 apostrophes et guillemets"] += k1 + k2 + k3 + k4 + k5
    noter("R9 apostrophes et guillemets")
    # R7 et R8, répétées jusqu'à ce que le texte ne change plus : retirer un caractère invisible,
    # un crochet ou une espace peut faire apparaître un autre reste (« ([) » -> « () », « <\u200bref » -> « <ref »)
    for _ in range(5):
        debut = t
        # R7
        t, k = RESTE_FICHIER.subn("", t)
        lignes, k2 = [], 0
        for ligne in t.split("\n"):
            ligne, r = crochets_orphelins(ligne)
            k2 += r
            lignes.append(ligne)
        t = "\n".join(lignes)
        t, k3 = BALISAGE.subn("", t)               # restes de balisage (même définition que l'extraction)
        t, k4 = PARENTHESES_VIDES.subn("", t)
        compte["R7 restes du nettoyage"] += k + k2 + k3 + k4
        noter("R7 restes du nettoyage")
        # R8
        avant = t
        t = ESPACES_MULTIPLES.sub(" ", t)
        t = "\n".join(l.strip() for l in t.split("\n"))
        t = LIGNES_VIDES.sub("\n\n", t).strip()
        if t != avant:
            compte["R8 espaces en trop"] += 1
        noter("R8 espaces en trop")
        if t == debut:
            break
    # R1
    t2 = unicodedata.normalize("NFC", t)
    if t2 != t:
        compte["R1 NFC"] += 1
        t = t2
        noter("R1 NFC")
    return t


# contrôles après normalisation 
INTERDITS = re.compile("[\x00-\x08\x0b-\x1f\x7f-\x9f\u200b\ufeff\u200e\u200f\u202a-\u202e\u2066-\u2069\u00ad"
                       "\u00a0\u2000-\u200a\u202f\u3000’‘“”„«»‹›]")


def controle_texte(t, code):
    """Erreurs résiduelles d'un texte normalisé (vide si tout va bien)."""
    err = []
    if unicodedata.normalize("NFC", t) != t:
        err.append("non NFC")
    m = INTERDITS.search(t)
    if m:
        err.append(f"caractère interdit U+{ord(m.group(0)):04X}")
    if normaliser(t, code) != t:
        err.append("normalisation non stable (un 2e passage change le texte)")
    return err


#traitement d'une langue -
def maintenant():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ecrire_json(chemin, donnees):
    tmp = Path(str(chemin) + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(donnees, f, ensure_ascii=False, indent=1)
    os.replace(tmp, chemin)


def extrait(avant, apres, largeur=50):
    """Petit extrait autour du premier changement, pour le rapport."""
    i = next((k for k, (a, b) in enumerate(zip(avant, apres)) if a != b), min(len(avant), len(apres)))
    d = max(0, i - largeur // 2)
    return {"avant": avant[d:d + largeur], "apres": apres[d:d + largeur]}


def traiter_langue(args):
    code, source, sortie, controle_tous = args
    t0 = time.time()
    for d in ("corpus", "provenance", "exclusions", "mesures"):
        (sortie / d).mkdir(parents=True, exist_ok=True)
    compte, exemples = Counter(), {}
    n_entree = n_sortie = n_modifies = car_avant = car_apres = 0
    vus, erreurs, raisons = set(), [], Counter()
    horodatage = maintenant()
    tmp = {d: sortie / d / f"{code}.jsonl.part" for d in ("corpus", "provenance", "exclusions")}
    with open(source / "corpus" / f"{code}.jsonl", encoding="utf-8") as fc, \
         open(source / "provenance" / f"{code}.jsonl", encoding="utf-8") as fp, \
         open(tmp["corpus"], "w", encoding="utf-8", newline="\n") as oc, \
         open(tmp["provenance"], "w", encoding="utf-8", newline="\n") as op, \
         open(tmp["exclusions"], "w", encoding="utf-8", newline="\n") as oe:
        for lc, lp in zip(fc, fp):
            n_entree += 1
            avant = json.loads(lc)["text"]
            prov = json.loads(lp)
            h_avant = V.sha256(avant)
            if prov["text_sha256"] != h_avant:
                raise RuntimeError(f"{code} ligne {n_entree} : la provenance ne correspond pas au texte")
            c, trace = Counter(), []
            apres = normaliser(avant, code, c, trace)
            compte.update(c)
            for regle, a, b in trace:
                if len(exemples.setdefault(regle, [])) < 5:
                    exemples[regle].append(extrait(a, b))
            car_avant += len(avant)
            if apres != avant:
                n_modifies += 1
            raison = None
            if not apres:
                raison = "texte vide après normalisation"
            elif V.regles_texte(apres):                 # ne doit pas arriver : filet de sécurité
                raison = "règle enfreinte après normalisation : " + V.regles_texte(apres)[0]
            else:
                h = V.sha256(apres)
                if h in vus:
                    raison = "doublon exact après normalisation"
            if raison:
                raisons[raison] += 1
                oe.write(json.dumps({"langue": code, "source_url": prov["source_url"], "raison": raison,
                                     "text_sha256_collecte": h_avant}, ensure_ascii=False) + "\n")
                continue
            vus.add(h)
            n_sortie += 1
            car_apres += len(apres)
            if controle_tous or n_sortie <= 2000:
                e = controle_texte(apres, code)
                if e and len(erreurs) < 50:
                    erreurs.append(f"{code} texte {n_sortie} : " + " ; ".join(e))
            oc.write(json.dumps({"text": apres}, ensure_ascii=False) + "\n")
            prov.update({"text_sha256": h, "text_sha256_collecte": h_avant, "normalized_at": horodatage})
            op.write(json.dumps(prov, ensure_ascii=False) + "\n")
        if next(fp, None) is not None:
            raise RuntimeError(f"{code} : la provenance a plus de lignes que le corpus")
    for d, p in tmp.items():
        os.replace(p, sortie / d / f"{code}.jsonl")
    res, err_valid = V.valider_langue(sortie / "corpus" / f"{code}.jsonl", sortie / "provenance" / f"{code}.jsonl", code)
    mesures = {
        "langue": code, "textes_entree": n_entree, "textes_sortie": n_sortie, "textes_modifies": n_modifies,
        "ecartes": dict(raisons), "caracteres_avant": car_avant, "caracteres_apres": car_apres,
        "regles": {k: compte[k] for k in sorted(compte)}, "exemples": exemples,
        "controles_normalisation": erreurs, "validation": res, "erreurs_validation": err_valid[:50],
        "duree_s": round(time.time() - t0),
    }
    ecrire_json(sortie / "mesures" / f"{code}.json", mesures)
    return mesures


# contrôle complet d'un run déjà fait (lecture seule)
def verifier_langue(args):
    """Contrôle chaque texte normalisé d'une langue : NFC, aucun caractère interdit, normalisation stable."""
    code, run = args
    t0 = time.time()
    n = n_err = 0
    erreurs = []
    with open(run / "corpus" / f"{code}.jsonl", encoding="utf-8") as f:
        for ligne in f:
            n += 1
            e = controle_texte(json.loads(ligne)["text"], code)
            if e:
                n_err += 1
                if len(erreurs) < 50:
                    erreurs.append(f"{code} texte {n} : " + " ; ".join(e))
    return {"langue": code, "textes": n, "textes_en_erreur": n_err, "erreurs": erreurs,
            "duree_s": round(time.time() - t0)}


def verifier(run, processus):
    """Contrôle complet d'un run : écrit controle_complet.json et note le résultat dans etat.json,
    run_metadata.json et run.log. Le corpus n'est pas modifié."""
    etat = json.loads((run / "etat.json").read_text(encoding="utf-8"))
    langues = sorted(etat["langues"], key=lambda c: -(run / "corpus" / f"{c}.jsonl").stat().st_size)
    log.info("Contrôle complet du run %s : %s langue(s), %s processus", run.name, len(langues), processus)
    debut = maintenant()
    resultats = []
    with Pool(min(processus, max(1, len(langues)))) as pool:
        for r in pool.imap_unordered(verifier_langue, [(c, run) for c in langues]):
            resultats.append(r)
            log.info("%s : %s textes contrôlés, %s en erreur (%s s)", r["langue"], r["textes"],
                     r["textes_en_erreur"], r["duree_s"])
    resultats.sort(key=lambda r: r["langue"])
    total = sum(r["textes"] for r in resultats)
    n_err = sum(r["textes_en_erreur"] for r in resultats)
    ok = n_err == 0 and len(resultats) == len(etat["langues"])
    bilan = {"debut_utc": debut, "fin_utc": maintenant(), "textes_controles": total, "textes_en_erreur": n_err,
             "ok": ok}
    ecrire_json(run / "controle_complet.json", {**bilan, "langues": resultats})
    for nom in ("etat.json", "run_metadata.json"):
        d = json.loads((run / nom).read_text(encoding="utf-8"))
        d["controle_complet"] = bilan
        ecrire_json(run / nom, d)
    log.info("Contrôle complet du run %s : %s textes contrôlés, %s en erreur : %s",
             run.name, total, n_err, "OK" if ok else "NON")
    return 0 if ok else 1


#  programme principal 
def main(argv=None):
    ap = argparse.ArgumentParser(description="Normalisation du corpus (tâche 6)")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--langues", nargs="+")
    g.add_argument("--tout", action="store_true")
    g.add_argument("--reprendre", nargs="?", const="", metavar="RUN")
    g.add_argument("--verifier", nargs="?", const="", metavar="RUN",
                   help="contrôle chaque texte d'un run déjà fait (par défaut : le dernier run terminé), sans le modifier")
    ap.add_argument("--controle-complet", action="store_true",
                    help="contrôle chaque texte (par défaut : les 2 000 premiers de chaque langue)")
    args = ap.parse_args(argv)

    with open(ROOT / "config" / "config.yaml", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    pn = config.get("normalisation", {})
    source = ROOT / pn.get("source", "data/processed/collecte/20261105T065025Z")
    base = ROOT / config["paths"]["processed"] / "normalise"
    base.mkdir(parents=True, exist_ok=True)

    if args.verifier is not None:
        if args.verifier:
            run = base / args.verifier
        else:
            termines = [r for r in sorted(base.glob("*T*Z"), reverse=True) if (r / "etat.json").exists()
                        and json.loads((r / "etat.json").read_text(encoding="utf-8"))["statut"] == "terminé"]
            run = termines[0] if termines else None
        if not run or not (run / "etat.json").exists():
            sys.exit("Aucun run à vérifier.")
        log.setLevel(logging.INFO)
        log.handlers.clear()
        fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%dT%H:%M:%S")
        for h in (logging.FileHandler(run / "run.log", encoding="utf-8"), logging.StreamHandler(sys.stdout)):
            h.setFormatter(fmt)
            log.addHandler(h)
        return verifier(run, pn.get("processus") or max(1, (os.cpu_count() or 2) - 1))

    if args.reprendre is not None:
        runs = [d for d in sorted(base.glob("*T*Z"), reverse=True)
                if (d / "etat.json").exists() and json.loads((d / "etat.json").read_text(encoding="utf-8"))["statut"] != "terminé"]
        run = base / args.reprendre if args.reprendre else (runs[0] if runs else None)
        if not run or not (run / "etat.json").exists():
            sys.exit("Aucun run à reprendre.")
        etat = json.loads((run / "etat.json").read_text(encoding="utf-8"))
        source = Path(etat["source_abs"])
    else:
        langues = sorted(p.stem for p in (source / "corpus").glob("*.jsonl")) if args.tout else args.langues
        manquantes = [c for c in langues if not (source / "corpus" / f"{c}.jsonl").exists()]
        if manquantes:
            sys.exit(f"Corpus introuvable dans {source} pour : {', '.join(manquantes)}")
        run = base / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        run.mkdir()
        etat = {"run_id": run.name, "statut": "en cours", "debut_utc": maintenant(), "version": VERSION,
                "source": str(source.relative_to(ROOT)) if source.is_relative_to(ROOT) else str(source),
                "source_abs": str(source), "langues": langues,
                "controle_complet_pendant_le_run": args.controle_complet}
        ecrire_json(run / "etat.json", etat)

    log.setLevel(logging.INFO)
    log.handlers.clear()
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%dT%H:%M:%S")
    for h in (logging.FileHandler(run / "run.log", encoding="utf-8"), logging.StreamHandler(sys.stdout)):
        h.setFormatter(fmt)
        log.addHandler(h)

    faites = {p.stem for p in (run / "mesures").glob("*.json")} if (run / "mesures").exists() else set()
    a_faire = [c for c in etat["langues"] if c not in faites]
    # les plus gros fichiers d'abord, pour bien répartir le travail entre les processus
    a_faire.sort(key=lambda c: -(source / "corpus" / f"{c}.jsonl").stat().st_size)
    processus = pn.get("processus") or max(1, (os.cpu_count() or 2) - 1)
    log.info("Run %s : %s langue(s) à normaliser (%s déjà faites), source %s, %s processus",
             run.name, len(a_faire), len(faites), etat["source"], processus)
    taches = [(c, source, run, args.controle_complet) for c in a_faire]
    with Pool(min(processus, max(1, len(taches)))) as pool:
        for m in pool.imap_unordered(traiter_langue, taches):
            ok = m["validation"]["lignes_valides"] == m["validation"]["lignes"] and not m["controles_normalisation"]
            log.info("%s : %s textes -> %s, %s modifiés, %s écartés, contrôles %s (%s s)", m["langue"],
                     m["textes_entree"], m["textes_sortie"], m["textes_modifies"], sum(m["ecartes"].values()),
                     "OK" if ok else "NON", m["duree_s"])

    toutes = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((run / "mesures").glob("*.json"))]
    ecrire_json(run / "mesures.json", [{k: v for k, v in m.items() if k != "exemples"} for m in toutes])
    regles = Counter()
    for m in toutes:
        regles.update(m["regles"])
    controles = {
        "corpus valide et 100 % des textes avec provenance": all(
            m["validation"]["lignes_valides"] == m["validation"]["lignes"] == m["validation"]["avec_provenance"]
            and not m["erreurs_validation"] for m in toutes),
        "plus aucun caractère interdit, normalisation stable": all(not m["controles_normalisation"] for m in toutes),
        "toutes les langues traitées": len(toutes) == len(etat["langues"]),
    }
    etat.update({"statut": "terminé" if controles["toutes les langues traitées"] else "en cours",
                 "fin_utc": maintenant(), "controles": controles})
    ecrire_json(run / "etat.json", etat)
    ecrire_json(run / "run_metadata.json", {
        "run_id": run.name, "version": VERSION, "source": etat["source"], "debut_utc": etat["debut_utc"],
        "fin_utc": etat["fin_utc"], "controles": controles, "regles": dict(regles),
        "totaux": {"textes_entree": sum(m["textes_entree"] for m in toutes),
                   "textes_sortie": sum(m["textes_sortie"] for m in toutes),
                   "textes_modifies": sum(m["textes_modifies"] for m in toutes)}})
    log.info("Run %s %s : %s textes -> %s", run.name, etat["statut"],
             sum(m["textes_entree"] for m in toutes), sum(m["textes_sortie"] for m in toutes))
    for nom, ok in controles.items():
        log.info("  %s : %s", nom, "OK" if ok else "NON")
    return 0 if all(controles.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
