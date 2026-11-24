"""Contrôle qualité du corpus (tâche 7).

Les drapeaux qualité ne retirent aucun texte : ils sont écrits dans la provenance (champ quality_flags).
Le corpus normalisé (config.yaml, section qualite.source) n'est jamais modifié ni recopié.

Usage (depuis la racine du projet) :
    python src/controle_qualite.py --doublons-langues    # 7.1 : textes identiques dans deux éditions
    python src/controle_qualite.py --sonder-langues      # prépare GlotLID et sonde 100 paragraphes par langue
    python src/controle_qualite.py --quasi-doublons [--langues wo tw]   # 7.2 : analyse des quasi-doublons
    python src/controle_qualite.py --analyse-qualite [--langues wo tw]  # 7.3 + 7.4 + 7.6 : une seule analyse
    python src/controle_qualite.py --langue [--langues wo tw]           # 7.5 : mauvaise langue (GlotLID)
    python src/controle_qualite.py --ecrire-drapeaux [--langues wo tw]  # 7.7 : écrit les quality_flags (nouvelle provenance)

Les analyses vont dans data/processed/qualite/analyses/<AAAAMMJJTHHMMSSZ>_<nom>/ .
"""
import argparse
import hashlib
import json
import logging
import os
import random
import re
import zlib
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from multiprocessing import Pool
from pathlib import Path

# Un seul fil de calcul par processus pour numpy (OpenBLAS) : sinon chaque processus réserve de la mémoire
# pour autant de fils que de cœurs, et Windows refuse les allocations quand plusieurs processus tournent.
for _v in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "1")

import numpy as np
import yaml

ICI = Path(__file__).resolve().parent
ROOT = ICI.parent
VERSION = "0.7"
log = logging.getLogger("qualite")

# GlotLID v3 
GLOTLID_REPO = "cis-lmu/glotlid"
GLOTLID_FICHIER = "model_v3.bin"          # version figée (model.bin suit toujours la dernière version)

# Étiquettes GlotLID v3 de chaque édition (vérifiées dans la liste officielle languages-v3.md).
# Liste vide : langue absente du modèle, non vérifiable.
ETIQUETTES_GLOTLID = {
    "aa": [], "af": ["afr_Latn"], "ak": ["twi_Latn", "fat_Latn"], "am": ["amh_Ethi"], "ann": ["ann_Latn"],
    "ar": ["arb_Arab"], "ary": ["ary_Arab"], "arz": ["arz_Arab"], "bm": ["bam_Latn"], "bol": [],
    "dag": ["dag_Latn"], "dga": ["dga_Latn"], "din": ["dik_Latn", "dip_Latn", "dks_Latn"], "ee": ["ewe_Latn"],
    "fat": ["fat_Latn"], "ff": ["fuv_Latn", "fuf_Latn", "ffm_Latn", "fub_Latn", "fue_Latn", "fuh_Latn", "fuq_Latn", "fuv_Arab"],
    "fon": ["fon_Latn"], "fr": ["fra_Latn"], "gpe": [], "gur": ["gur_Latn"], "guw": ["guw_Latn"],
    "ha": ["hau_Latn"], "hz": ["her_Latn"], "ig": ["ibo_Latn"], "igl": [], "kab": ["kab_Latn"], "kai": [],
    "kaj": [], "kbp": ["kbp_Latn"], "kcg": ["kcg_Latn"], "kg": ["kng_Latn", "kwy_Latn", "ldi_Latn"],
    "ki": ["kik_Latn"], "kj": ["kua_Latn"], "knc": ["knc_Latn", "knc_Arab"], "kr": ["knc_Latn", "knc_Arab"],
    "kus": ["kus_Latn"], "lg": ["lug_Latn"], "ln": ["lin_Latn"], "mg": ["plt_Latn"], "mos": ["mos_Latn"],
    "ng": ["ndo_Latn"], "nqo": ["nqo_Nkoo"], "nr": ["nbl_Latn"], "nso": ["nso_Latn"], "nup": [],
    "ny": ["nya_Latn"], "om": ["gaz_Latn", "hae_Latn"], "pcm": ["pcm_Latn"], "rn": ["run_Latn"],
    "rw": ["kin_Latn"], "sg": ["sag_Latn"], "shi": ["shi_Latn"], "sn": ["sna_Latn"], "so": ["som_Latn"],
    "ss": ["ssw_Latn"], "st": ["sot_Latn"], "sw": ["swh_Latn", "swc_Latn"], "ti": ["tir_Ethi"],
    "tig": ["tig_Ethi"], "tn": ["tsn_Latn"], "ts": ["tso_Latn"], "tum": ["tum_Latn"], "tw": ["twi_Latn"],
    "ve": ["ven_Latn"], "wo": ["wol_Latn"], "xh": ["xho_Latn"], "yo": ["yor_Latn"],
    "zgh": ["zgh_Tfng", "tzm_Tfng"], "zu": ["zul_Latn"],
}


# outils communs 
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
    pq = config.get("qualite", {})
    source = ROOT / pq.get("source", "data/processed/normalise/20261113T141439Z")
    processus = pq.get("processus") or max(1, (os.cpu_count() or 2) - 1)
    return config, source, processus


def langues_source(source):
    etat = json.loads((source / "etat.json").read_text(encoding="utf-8"))
    return etat["langues"]


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


# 7.1 : doublons exacts entre langues 
def empreintes_langue(args):
    """Empreintes SHA-256 des textes d'une langue, lues dans la provenance (même ordre que le corpus)."""
    code, source = args
    h = []
    with open(source / "provenance" / f"{code}.jsonl", encoding="utf-8") as f:
        for ligne in f:
            h.append(bytes.fromhex(json.loads(ligne)["text_sha256"]))
    return code, h


def doublons_langues(config, source, processus):
    d = nouveau_dossier(config, "doublons_langues")
    t0 = time.time()
    langues = langues_source(source)
    log.info("7.1 Doublons exacts entre langues : %s langues, source %s", len(langues), source.relative_to(ROOT))
    code_de = {c: i for i, c in enumerate(langues)}
    morceaux = []
    with Pool(min(processus, len(langues))) as pool:
        for code, h in pool.imap_unordered(empreintes_langue, [(c, source) for c in langues]):
            if h:
                a = np.empty(len(h), dtype=[("h", "S32"), ("l", "u1"), ("i", "u4")])
                a["h"] = h
                a["l"] = code_de[code]
                a["i"] = np.arange(len(h), dtype="u4")
                morceaux.append(a)
    tout = np.concatenate(morceaux)
    tout.sort(order="h")
    meme = tout["h"][1:] == tout["h"][:-1]
    debuts = np.flatnonzero(np.r_[True, ~meme])
    fins = np.r_[debuts[1:], len(tout)]
    groupes = [(s, e) for s, e in zip(debuts, fins) if e - s > 1]
    log.info("%s textes lus, %s empreintes présentes dans plus d'une ligne", len(tout), len(groupes))

    # groupes et lignes à relire (texte et adresse de la source)
    a_lire = defaultdict(set)
    liste = []
    for s, e in groupes:
        membres = [(langues[int(tout["l"][k])], int(tout["i"][k])) for k in range(s, e)]
        liste.append(membres)
        for c, i in membres:
            a_lire[c].add(i)
    textes, urls = {}, {}
    for c, idx in a_lire.items():
        for i, v in lire_lignes(source / "corpus" / f"{c}.jsonl", idx).items():
            textes[(c, i)] = v["text"]
        for i, v in lire_lignes(source / "provenance" / f"{c}.jsonl", idx).items():
            urls[(c, i)] = v["source_url"]

    paires, par_langue, meme_langue = Counter(), Counter(), 0
    with open(d / "doublons_langues.jsonl", "w", encoding="utf-8", newline="\n") as f:
        for membres in liste:
            codes = sorted({c for c, _ in membres})
            if len(codes) < len(membres):
                meme_langue += 1          # ne doit pas arriver : les doublons sont retirés dans chaque langue
            for a in range(len(codes)):
                for b in range(a + 1, len(codes)):
                    paires[f"{codes[a]}-{codes[b]}"] += 1
            for c, _ in membres:
                par_langue[c] += 1
            t = textes[membres[0]]
            f.write(json.dumps({"langues": codes, "mots": len(t.split()), "caracteres": len(t),
                                "extrait": t[:200], "sources": {c: urls[(c, i)] for c, i in membres}},
                               ensure_ascii=False) + "\n")
    mots = sorted(len(textes[m[0]].split()) for m in liste)
    bilan = {
        "etape": "7.1 doublons exacts entre langues", "version": VERSION, "source": str(source.relative_to(ROOT)),
        "debut_utc": datetime.fromtimestamp(t0, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "fin_utc": maintenant(),
        "textes_lus": int(len(tout)), "groupes": len(liste), "textes_concernes": sum(len(m) for m in liste),
        "groupes_dans_une_meme_langue": meme_langue,
        "mots_par_texte": {"mediane": mots[len(mots) // 2] if mots else 0,
                           "moins_de_5": sum(1 for x in mots if x < 5), "moins_de_20": sum(1 for x in mots if x < 20)},
        "par_paire": dict(paires.most_common()), "par_langue": dict(par_langue.most_common()),
    }
    ecrire_json(d / "bilan.json", bilan)
    log.info("%s groupes, %s textes concernés (%s groupes dans une même langue) ; %s s",
             bilan["groupes"], bilan["textes_concernes"], meme_langue, round(time.time() - t0))
    for p, n in paires.most_common(15):
        log.info("  %s : %s", p, n)
    return d


# 7.2 : quasi-doublons 
# Méthode : MinHash-LSH dans chaque langue, sur des groupes de 5 mots qui se suivent (« bardeaux »).
# 128 fonctions de hachage, 16 bandes de 8 lignes : deux textes à 0,8 de similarité ont environ 95 % de
# chances d'être comparés. Chaque paire candidate est ensuite confirmée par le calcul exact de la similarité
# de Jaccard sur les bardeaux.
# Une paire est un quasi-doublon (« même article ») si sa similarité est d'au moins 0,8 ET si les deux textes
# ont le même sujet : ils commencent par les mêmes 3 mots, ou l'un des deux a une ligne de titre courte
# (6 mots au plus) ajoutée devant et commence, après ce titre, comme l'autre. Les paires à 0,8 ou plus dont le sujet diffère sont des ébauches faites sur un
# même modèle (« même modèle ») : elles sont comptées ici et traitées en 7.3 (template_generated).
# Les quasi-doublons forment des groupes ; dans chaque groupe, le texte le plus long reste sans drapeau, les
# autres sont marqués near_duplicate.
QD_SEUIL = 0.8
QD_N = 5                    # mots par bardeau
QD_DEBUT = 3                # mots du début comparés pour décider si le sujet est le même
QD_TITRE = 6                # une 1re ligne de 6 mots au plus est traitée comme un titre ajouté devant
QD_PERM, QD_BANDES = 128, 16
QD_LIGNES = QD_PERM // QD_BANDES
QD_P = np.uint64(4294967311)                                  # nombre premier > 2^32
_rng = np.random.RandomState(42)
QD_A = _rng.randint(1, 2 ** 31 - 1, size=QD_PERM).astype(np.uint64)
QD_B = _rng.randint(0, 2 ** 31 - 1, size=QD_PERM).astype(np.uint64)
QD_MULT = _rng.randint(1, 2 ** 62, size=QD_LIGNES, dtype=np.int64).astype(np.uint64) | np.uint64(1)
# mot = lettres et chiffres de toute écriture, avec les signes diacritiques combinants (tons, voyelles arabes…)
MOT = re.compile(r"[\w\u0300-\u036f\u1ab0-\u1aff\u1dc0-\u1dff\u20d0-\u20ff\ufe20-\ufe2f"
                 r"\u0610-\u061a\u064b-\u065f\u0670\u06d6-\u06ed\u135d-\u135f\u07eb-\u07f3\u2d7f]+")
BANDES_JACCARD = [0.6, 0.7, 0.8, 0.9, 0.95, 1.0001]


def mots_texte(texte):
    return MOT.findall(texte.lower())


def bardeaux(texte, mots=None):
    """Empreintes (crc32) des groupes de 5 mots qui se suivent, en minuscules."""
    mots = mots_texte(texte) if mots is None else mots
    if not mots:
        return np.array([zlib.crc32(texte.encode("utf-8"))], dtype=np.uint64)
    if len(mots) <= QD_N:
        return np.array([zlib.crc32(" ".join(mots).encode("utf-8"))], dtype=np.uint64)
    return np.unique(np.fromiter((zlib.crc32(" ".join(mots[i:i + QD_N]).encode("utf-8"))
                                  for i in range(len(mots) - QD_N + 1)), dtype=np.uint64))


def bandes_lsh(b):
    """16 empreintes de bande à partir de la signature MinHash (128 valeurs) des bardeaux."""
    sig = ((QD_A[:, None] * b[None, :] + QD_B[:, None]) % QD_P).min(axis=1)
    return (sig.reshape(QD_BANDES, QD_LIGNES) * QD_MULT).sum(axis=1)      # le débordement est voulu (mod 2^64)


def jaccard(a, b):
    inter = np.intersect1d(a, b, assume_unique=True).size
    return inter / (a.size + b.size - inter)


def debuts(texte, mots):
    """(3 premiers mots du texte, 3 premiers mots après une 1re ligne courte de 6 mots au plus, ou None)."""
    lignes = [l for l in texte.split("\n") if MOT.search(l)]
    apres_titre = None
    if len(lignes) > 1 and len(mots_texte(lignes[0])) <= QD_TITRE:
        apres_titre = tuple(mots_texte("\n".join(lignes[1:]))[:QD_DEBUT])
    return tuple(mots[:QD_DEBUT]), apres_titre


def meme_sujet(da, db):
    """Mêmes 3 premiers mots, ou un seul des deux textes a un titre devant et commence ensuite comme l'autre.
    Deux textes qui ont chacun leur propre titre court suivi du même corps sont des ébauches d'un même modèle."""
    return da[0] == db[0] or (da[1] is not None and da[1] == db[0]) or (db[1] is not None and db[1] == da[0])


def quasi_doublons_langue(args):
    code, source, dossier = args
    t0 = time.time()
    corpus = source / "corpus" / f"{code}.jsonl"
    with open(corpus, encoding="utf-8") as f:
        n = sum(1 for _ in f)
    # tableaux réservés une fois pour toutes (16 empreintes de bande par texte), pour limiter la mémoire
    bandes = np.empty((n, QD_BANDES), dtype=np.uint64)
    longueurs = np.empty(n, dtype=np.int64)
    with open(corpus, encoding="utf-8") as f:
        for i, ligne in enumerate(f):
            t = json.loads(ligne)["text"]
            longueurs[i] = len(t)
            bandes[i] = bandes_lsh(bardeaux(t))
    res = {"langue": code, "textes": n, "paires_candidates": 0, "paires_verifiees": 0,
           "paires_par_similarite": {}, "paires_meme_article": 0, "paires_meme_modele": 0,
           "groupes": 0, "textes_marques": 0, "exemples": [], "exemples_meme_modele": []}
    if n < 2:
        res["duree_s"] = round(time.time() - t0)
        return res
    # paires candidates : textes qui partagent une bande ; dans un même seau, on relie les textes voisins
    # (chaîne), ce qui suffit pour former les groupes et évite de comparer toutes les paires d'un gros seau
    paires = set()
    for j in range(QD_BANDES):
        ordre = np.argsort(bandes[:, j], kind="stable")
        v = bandes[ordre, j]
        meme = np.flatnonzero(v[1:] == v[:-1])
        for k in meme:
            a, b = int(ordre[k]), int(ordre[k + 1])
            paires.add((a, b) if a < b else (b, a))
    del bandes
    res["paires_candidates"] = len(paires)
    # calcul exact : on relit les textes concernés
    voulus = {i for p in paires for i in p}
    bard, debut = {}, {}
    if voulus:
        with open(corpus, encoding="utf-8") as f:
            for i, ligne in enumerate(f):
                if i in voulus:
                    t = json.loads(ligne)["text"]
                    m = mots_texte(t)
                    bard[i] = bardeaux(t, m)
                    debut[i] = debuts(t, m)
    parent = list(range(n))

    def racine(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    hist = Counter()
    par_bande = defaultdict(list)          # exemples de paires « même article », par tranche
    modele = []                            # exemples de paires « même modèle »
    for a, b in sorted(paires):
        j = jaccard(bard[a], bard[b])
        if j < QD_SEUIL:
            for lo, hi in zip(BANDES_JACCARD[:-1], BANDES_JACCARD[1:]):
                if lo <= j < hi:
                    hist[f"{lo:.2f}-{min(hi, 1):.2f}"] += 1
            continue
        hist[next(f"{lo:.2f}-{min(hi, 1):.2f}" for lo, hi in zip(BANDES_JACCARD[:-1], BANDES_JACCARD[1:])
                  if lo <= j < hi)] += 1
        if meme_sujet(debut[a], debut[b]):
            res["paires_meme_article"] += 1
            cle = next(f"{lo:.2f}-{min(hi, 1):.2f}" for lo, hi in zip(BANDES_JACCARD[:-1], BANDES_JACCARD[1:])
                       if lo <= j < hi)
            if len(par_bande[cle]) < 3:
                par_bande[cle].append((round(j, 3), a, b))
            ra, rb = racine(a), racine(b)
            if ra != rb:
                parent[ra] = rb
        else:
            res["paires_meme_modele"] += 1
            if len(modele) < 3 and j >= 0.9:
                modele.append((round(j, 3), a, b))
    res["paires_verifiees"] = sum(hist.values())
    res["paires_par_similarite"] = dict(sorted(hist.items()))
    groupes = defaultdict(list)
    for i in voulus:
        groupes[racine(i)].append(i)
    groupes = [g for g in groupes.values() if len(g) > 1]
    marques = []
    with open(dossier / "quasi_doublons" / f"{code}.jsonl", "w", encoding="utf-8", newline="\n") as f:
        for num, g in enumerate(sorted(groupes, key=min)):
            garde = max(g, key=lambda i: (longueurs[i], -i))
            for i in sorted(g):
                if i != garde:
                    marques.append(i)
            f.write(json.dumps({"groupe": num, "garde": garde, "marques": sorted(i for i in g if i != garde)}) + "\n")
    res["groupes"] = len(groupes)
    res["textes_marques"] = len(marques)
    res["taille_max_groupe"] = max((len(g) for g in groupes), default=0)
    # exemples à relire : 3 paires par tranche de similarité
    ex = [(cle, j, a, b) for cle, l in sorted(par_bande.items()) for j, a, b in l]
    idx = {i for _, _, a, b in ex for i in (a, b)} | {i for _, a, b in modele for i in (a, b)}
    textes = {i: v["text"] for i, v in lire_lignes(corpus, idx).items()}
    urls = {i: v["source_url"] for i, v in lire_lignes(source / "provenance" / f"{code}.jsonl", idx).items()}
    res["exemples"] = [{"tranche": cle, "jaccard": j, "a": textes[a][:300], "b": textes[b][:300],
                        "source_a": urls[a], "source_b": urls[b]} for cle, j, a, b in ex]
    res["exemples_meme_modele"] = [{"jaccard": j, "a": textes[a][:300], "b": textes[b][:300],
                                    "source_a": urls[a], "source_b": urls[b]} for j, a, b in modele]
    res["duree_s"] = round(time.time() - t0)
    return res


def quasi_doublons(config, source, processus, langues=None):
    d = nouveau_dossier(config, "quasi_doublons")
    (d / "quasi_doublons").mkdir()
    t0 = time.time()
    langues = langues or langues_source(source)
    langues = sorted(langues, key=lambda c: -(source / "corpus" / f"{c}.jsonl").stat().st_size)
    log.info("7.2 Quasi-doublons : %s langue(s), seuil %s, bardeaux de %s mots, %s processus",
             len(langues), QD_SEUIL, QD_N, min(processus, len(langues)))
    resultats = []
    with Pool(min(processus, len(langues))) as pool:
        for r in pool.imap_unordered(quasi_doublons_langue, [(c, source, d) for c in langues]):
            resultats.append(r)
            log.info("%-4s %8s textes, paires >= %s : %6s même article, %7s même modèle ; %6s groupes, %6s marqués (%s s)",
                     r["langue"], r["textes"], QD_SEUIL, r["paires_meme_article"], r["paires_meme_modele"],
                     r["groupes"], r["textes_marques"], r["duree_s"])
    resultats.sort(key=lambda r: r["langue"])
    total = sum(r["textes"] for r in resultats)
    marques = sum(r["textes_marques"] for r in resultats)
    meme_modele = sum(r["paires_meme_modele"] for r in resultats)
    ecrire_json(d / "bilan.json", {
        "etape": "7.2 quasi-doublons", "version": VERSION, "source": str(source.relative_to(ROOT)),
        "parametres": {"seuil": QD_SEUIL, "mots_par_bardeau": QD_N, "permutations": QD_PERM, "bandes": QD_BANDES,
                       "mots_du_debut_compares": QD_DEBUT, "mots_max_ligne_titre": QD_TITRE},
        "fin_utc": maintenant(), "duree_s": round(time.time() - t0), "textes": total, "textes_marques": marques,
        "paires_meme_modele": meme_modele,
        "langues": resultats})
    log.info("%s textes marqués near_duplicate sur %s (%.2f %%) ; %s s", marques, total,
             100 * marques / max(1, total), round(time.time() - t0))
    return d


#  7.3 + 7.4 + 7.6 : une seule analyse 
# 7.3 Ébauches en série (template_generated) : MinHash sur le « squelette » du texte (minuscules, chaque nombre
#     remplacé par 0), bardeaux de 5 mots, 128 fonctions, LSH 32 bandes × 4 lignes ; dans un seau, paires de
#     voisins ; une paire est gardée si sa similarité estimée (part des 128 valeurs égales) est d'au moins 0,7.
#     Les textes ainsi reliés forment des groupes ; un texte est marqué s'il est dans un groupe d'au moins 50 textes.
# 7.4 Texte répété (boilerplate) : une ligne d'au moins 4 mots présente dans au moins 20 textes de la langue est
#     « répétée » ; un texte est marqué si au moins 50 % de ses caractères viennent de lignes répétées.
# 7.6 Contrôles sans drapeau : textes de moins de 5 et de moins de 20 mots, caractère de remplacement U+FFFD,
#     texte mal décodé (Ã©, â€™…), messages d'erreur enregistrés à la place d'un article, textes qui commencent
#     par une minuscule (nom du sujet perdu à la collecte).
T3_SEUIL, T3_GROUPE, T3_BANDES = 0.7, 50, 32
T3_LIGNES = QD_PERM // T3_BANDES
T4_MOTS, T4_TEXTES, T4_PART = 4, 20, 0.5
NOMBRE = re.compile(r"\d+")
MOJIBAKE = re.compile("Ã[ -¿ŒœŠšŸŽžƒˆ˜–-™]"
                      "|â€|Â[ -¿]|Ø[§-¿]|Ù[\u0080-\u008a€‚ƒ„…†‡]|ï»¿")
ERREUR = re.compile(r"upstream connect error|disconnect/reset before headers|internal server error|bad gateway|"
                    r"service unavailable|gateway time-?out|too many requests|wikimedia error|"
                    r"our servers are currently", re.I)
# Resserré le 22/11 : « <!doctype » et « <html » retirés (capturaient des exemples de code HTML dans des
# articles qui en parlent, et non de vraies erreurs de collecte). Les 2 vrais cas restent détectés
# (ha, ig, « upstream connect error »).


def signature16(b):
    """Signature MinHash (128 valeurs) gardée sur 16 bits : assez pour estimer la similarité, deux fois moins lourde."""
    return (((QD_A[:, None] * b[None, :] + QD_B[:, None]) % QD_P).min(axis=1) & np.uint64(0xFFFF)).astype(np.uint16)


def cle_ligne(ligne):
    return int.from_bytes(hashlib.blake2b(" ".join(ligne.lower().split()).encode("utf-8"), digest_size=8).digest(), "little")


def extrait(texte, m, avant=40, apres=80):
    return texte[max(0, m.start() - avant):m.end() + apres].replace("\n", " / ")


def analyse_langue(args):
    code, source, dossier = args
    t0 = time.time()
    corpus = source / "corpus" / f"{code}.jsonl"
    with open(corpus, encoding="utf-8") as f:
        n = sum(1 for _ in f)
    sig = np.empty((n, QD_PERM), dtype=np.uint16)
    longueurs = np.empty(n, dtype=np.int64)
    from array import array
    cles, ids, lgs = array("Q"), array("I"), array("I")
    c6 = Counter()
    ex6 = defaultdict(list)
    with open(corpus, encoding="utf-8") as f:
        for i, ligne in enumerate(f):
            t = json.loads(ligne)["text"]
            longueurs[i] = len(t)
            mots = mots_texte(t)
            # 7.6
            if len(mots) < 5:
                c6["moins_de_5_mots"] += 1
            if len(mots) < 20:
                c6["moins_de_20_mots"] += 1
            if "�" in t:
                c6["caractere_remplacement"] += 1
            for nom, motif in (("texte_mal_decode", MOJIBAKE), ("message_erreur", ERREUR)):
                m = motif.search(t)
                if m:
                    c6[nom] += 1
                    if len(ex6[nom]) < 5:
                        ex6[nom].append({"texte": i, "extrait": extrait(t, m)})
            premiere = next((ch for ch in t if ch.isalpha()), "")
            if premiere.islower():
                c6["debut_minuscule"] += 1
                if len(ex6["debut_minuscule"]) < 5:
                    ex6["debut_minuscule"].append({"texte": i, "extrait": t[:120].replace("\n", " / ")})
            # 7.3
            sig[i] = signature16(bardeaux(NOMBRE.sub("0", t.lower())))
            # 7.4
            vues = set()
            for l in t.split("\n"):
                if len(MOT.findall(l)) >= T4_MOTS:
                    k = cle_ligne(l)
                    if k not in vues:
                        vues.add(k)
                        cles.append(k)
                        ids.append(i)
                        lgs.append(len(l))
    res = {"langue": code, "textes": n}

    # 7.3 : groupes d'ébauches
    parent = list(range(n))

    def racine(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    paires_gardees = 0
    for j in range(T3_BANDES if n > 1 else 0):
        bloc = sig[:, j * T3_LIGNES:(j + 1) * T3_LIGNES].astype(np.uint64)
        cle = (bloc[:, 0] << np.uint64(48)) | (bloc[:, 1] << np.uint64(32)) | (bloc[:, 2] << np.uint64(16)) | bloc[:, 3]
        ordre = np.argsort(cle, kind="stable")
        meme = cle[ordre][1:] == cle[ordre][:-1]
        a_s, b_s = ordre[:-1][meme], ordre[1:][meme]
        for d in range(0, len(a_s), 200000):
            a, b = a_s[d:d + 200000], b_s[d:d + 200000]
            sim = (sig[a] == sig[b]).mean(axis=1)
            for x, y in zip(a[sim >= T3_SEUIL].tolist(), b[sim >= T3_SEUIL].tolist()):
                rx, ry = racine(x), racine(y)
                if rx != ry:
                    parent[rx] = ry
                    paires_gardees += 1
    racines = np.fromiter((racine(i) for i in range(n)), dtype=np.int64, count=n)
    valeurs, inverse, tailles = np.unique(racines, return_inverse=True, return_counts=True)
    taille_texte = tailles[inverse]
    t3 = np.flatnonzero(taille_texte >= T3_GROUPE)
    grands = np.flatnonzero(tailles >= T3_GROUPE)
    grands = grands[np.argsort(-tailles[grands], kind="stable")]
    res["t3"] = {"groupes": int(len(grands)), "textes_marques": int(len(t3)),
                 "plus_grand_groupe": int(tailles[grands[0]]) if len(grands) else 0, "exemples": []}
    a_lire = set()
    choix = list(grands[:5]) + list(grands[5::max(1, len(grands) // 5)][:5]) if len(grands) else []
    for g in choix:
        membres = np.flatnonzero(inverse == g)
        res["t3"]["exemples"].append({"taille": int(tailles[g]), "textes": [int(membres[0]), int(membres[len(membres) // 2])]})
        a_lire.update(res["t3"]["exemples"][-1]["textes"])

    # 7.4 : lignes répétées
    cles = np.frombuffer(cles, dtype=np.uint64)
    ids = np.frombuffer(ids, dtype=np.uint32)
    lgs = np.frombuffer(lgs, dtype=np.uint32)
    t4 = np.array([], dtype=np.int64)
    res["t4"] = {"lignes_repetees": 0, "textes_marques": 0, "lignes_les_plus_frequentes": [], "exemples": []}
    if len(cles):
        uniques, inv, nb = np.unique(cles, return_inverse=True, return_counts=True)
        repetee = nb[inv] >= T4_TEXTES
        part = np.bincount(ids, weights=lgs * repetee, minlength=n) / np.maximum(longueurs, 1)
        t4 = np.flatnonzero(part >= T4_PART)
        res["t4"]["lignes_repetees"] = int((nb >= T4_TEXTES).sum())
        res["t4"]["textes_marques"] = int(len(t4))
        haut = np.argsort(-nb, kind="stable")[:15]
        premiere_occ = {}
        for u in haut:
            k = int(uniques[u])
            premiere_occ[k] = int(ids[np.flatnonzero(cles == uniques[u])[0]])
            res["t4"]["lignes_les_plus_frequentes"].append({"cle": k, "textes": int(nb[u]), "texte_source": premiere_occ[k]})
            a_lire.add(premiere_occ[k])
        for i in t4[:: max(1, len(t4) // 8)][:8].tolist():
            res["t4"]["exemples"].append({"texte": i, "part": round(float(part[i]), 2)})
            a_lire.add(i)
    res["t6"] = {"compteurs": dict(c6), "exemples": dict(ex6)}

    # textes à relire pour les exemples
    textes = {i: v["text"] for i, v in lire_lignes(corpus, a_lire).items()}
    for e in res["t3"]["exemples"]:
        e["extraits"] = [textes[i][:200].replace("\n", " / ") for i in e["textes"]]
    for e in res["t4"]["lignes_les_plus_frequentes"]:
        t = textes[e.pop("texte_source")]
        k = e.pop("cle")
        e["ligne"] = next((l for l in t.split("\n") if len(MOT.findall(l)) >= T4_MOTS and cle_ligne(l) == k), "")[:200]
    for e in res["t4"]["exemples"]:
        e["extrait"] = textes[e["texte"]][:300].replace("\n", " / ")
    with open(dossier / "drapeaux" / f"{code}.json", "w", encoding="utf-8", newline="\n") as f:
        json.dump({"template_generated": t3.tolist(), "boilerplate": t4.tolist()}, f)
    res["duree_s"] = round(time.time() - t0)
    return res


def analyse_qualite(config, source, processus, langues=None):
    d = nouveau_dossier(config, "analyse_qualite")
    (d / "drapeaux").mkdir()
    t0 = time.time()
    langues = langues or langues_source(source)
    langues = sorted(langues, key=lambda c: -(source / "corpus" / f"{c}.jsonl").stat().st_size)
    log.info("7.3 + 7.4 + 7.6 : %s langue(s), %s processus", len(langues), min(processus, len(langues)))
    resultats = []
    with Pool(min(processus, len(langues))) as pool:
        for r in pool.imap_unordered(analyse_langue, [(c, source, d) for c in langues]):
            resultats.append(r)
            c6 = r["t6"]["compteurs"]
            log.info("%-4s %8s textes : %7s ébauches en série (%s groupes), %6s texte répété ; < 20 mots %s, "
                     "erreurs %s, mal décodé %s, début minuscule %s (%s s)", r["langue"], r["textes"],
                     r["t3"]["textes_marques"], r["t3"]["groupes"], r["t4"]["textes_marques"],
                     c6.get("moins_de_20_mots", 0), c6.get("message_erreur", 0), c6.get("texte_mal_decode", 0),
                     c6.get("debut_minuscule", 0), r["duree_s"])
    resultats.sort(key=lambda r: r["langue"])
    tot = lambda f: sum(f(r) for r in resultats)
    total6 = Counter()
    for r in resultats:
        total6.update(r["t6"]["compteurs"])
    ecrire_json(d / "bilan.json", {
        "etape": "7.3 ébauches en série + 7.4 texte répété + 7.6 contrôles sans drapeau", "version": VERSION,
        "source": str(source.relative_to(ROOT)),
        "parametres": {"t3_seuil": T3_SEUIL, "t3_taille_min_groupe": T3_GROUPE, "t3_bandes": T3_BANDES,
                       "mots_par_bardeau": QD_N, "t4_mots_min_ligne": T4_MOTS, "t4_textes_min": T4_TEXTES,
                       "t4_part_min": T4_PART},
        "fin_utc": maintenant(), "duree_s": round(time.time() - t0), "textes": tot(lambda r: r["textes"]),
        "template_generated": tot(lambda r: r["t3"]["textes_marques"]),
        "boilerplate": tot(lambda r: r["t4"]["textes_marques"]), "controles_7_6": dict(total6),
        "langues": resultats})
    log.info("Total : %s ébauches en série, %s texte répété ; 7.6 : %s ; %s s",
             tot(lambda r: r["t3"]["textes_marques"]), tot(lambda r: r["t4"]["textes_marques"]),
             dict(total6), round(time.time() - t0))
    return d


# 7.5 : mauvaise langue (language_mismatch) 
# Chaque paragraphe d'au moins 5 mots est identifié par GlotLID v3 (les plus courts ne reçoivent pas de verdict).
# Langue vérifiable : le texte est marqué si moins de 50 % des caractères de ses paragraphes identifiés sont dans
# l'une des étiquettes de la langue (ETIQUETTES_GLOTLID ; aucune langue voisine n'est acceptée).
# Langue non vérifiable (absente du modèle) et tigré (modèle non fiable) : le texte est marqué si au moins 50 % de
# ses caractères identifiés sont reconnus, avec une probabilité d'au moins 0,9, comme une grande langue.
# Le modèle (1,7 Go en mémoire) est chargé une fois par processus : peu de processus (qualite.processus_langue).
T5_MOTS, T5_PART, T5_PROBA = 5, 0.5, 0.9
NON_FIABLES = {"tig"}
GRANDES_LANGUES = {"eng_Latn", "fra_Latn", "por_Latn", "spa_Latn", "deu_Latn", "arb_Arab"}
_MODELE = None


def _init_glotlid():
    global _MODELE
    _MODELE, _ = charger_glotlid()


def langue_langue(args):
    code, source, dossier = args
    t0 = time.time()
    attendues = set(ETIQUETTES_GLOTLID.get(code, []))
    verifiable = bool(attendues) and code not in NON_FIABLES
    res = {"langue": code, "verifiable": verifiable, "etiquettes_attendues": sorted(attendues), "textes": 0,
           "textes_avec_verdict": 0, "textes_marques": 0, "paragraphes_identifies": 0,
           "etiquettes_des_textes_marques": {}, "exemples": []}
    marques, etiquettes_marques = [], Counter()
    lot_i, lot_p = [], []          # paragraphes en attente (prédits par lots de 20 000)
    par_texte = defaultdict(list)   # texte -> [(longueur, étiquette, proba)]
    debut_texte = {}

    def traiter_lot():
        # Prédiction paragraphe par paragraphe : fasttext-predict-v2 ne gère pas correctement
        # la prédiction par liste (multilinePredict renvoie une structure aplatie incompatible).
        if not lot_p:
            return
        res["paragraphes_identifies"] += len(lot_p)
        for i, p in zip(lot_i, lot_p):
            lab, pr = _MODELE.predict(p, k=1)
            par_texte[i].append((len(p), lab[0].replace("__label__", "") if len(lab) else "", float(pr[0]) if len(pr) else 0.0))
        lot_i.clear()
        lot_p.clear()

    def conclure(i):
        paras = par_texte.pop(i, [])
        if not paras:
            return
        res["textes_avec_verdict"] += 1
        total = sum(l for l, _, _ in paras)
        if verifiable:
            bon = sum(l for l, lab, _ in paras if lab in attendues)
            marque = bon / total < T5_PART
        else:
            grand = sum(l for l, lab, pr in paras if lab in GRANDES_LANGUES and pr >= T5_PROBA)
            marque = grand / total >= T5_PART
        if marque:
            marques.append(i)
            principale = Counter()
            for l, lab, _ in paras:
                principale[lab] += l
            lab = principale.most_common(1)[0][0]
            etiquettes_marques[lab] += 1
            if len(res["exemples"]) < 12 and (len(res["exemples"]) < 4 or len(marques) % 50 == 0):
                res["exemples"].append({"texte": i, "etiquette_principale": lab,
                                        "part_attendue": round(sum(l for l, la, _ in paras if la in attendues) / total, 2),
                                        "extrait": debut_texte[i]})
        debut_texte.pop(i, None)

    en_attente = []
    with open(source / "corpus" / f"{code}.jsonl", encoding="utf-8") as f:
        for i, ligne in enumerate(f):
            t = json.loads(ligne)["text"]
            res["textes"] += 1
            debut_texte[i] = t[:200].replace("\n", " / ")
            for p in t.split("\n"):
                if len(MOT.findall(p)) >= T5_MOTS:
                    lot_i.append(i)
                    lot_p.append(" ".join(p.split()))
            en_attente.append(i)
            if len(lot_p) >= 20000:
                traiter_lot()
                for j in en_attente:
                    conclure(j)
                en_attente = []
    traiter_lot()
    for j in en_attente:
        conclure(j)
    res["textes_marques"] = len(marques)
    res["etiquettes_des_textes_marques"] = dict(etiquettes_marques.most_common(10))
    with open(dossier / "drapeaux" / f"{code}.json", "w", encoding="utf-8", newline="\n") as f:
        json.dump({"language_mismatch": marques}, f)
    res["duree_s"] = round(time.time() - t0)
    return res


def analyse_langue_glotlid(config, source, processus, langues=None):
    d = nouveau_dossier(config, "langue")
    (d / "drapeaux").mkdir()
    t0 = time.time()
    n_proc = config.get("qualite", {}).get("processus_langue") or 4
    langues = langues or langues_source(source)
    langues = sorted(langues, key=lambda c: -(source / "corpus" / f"{c}.jsonl").stat().st_size)
    log.info("7.5 Mauvaise langue (GlotLID %s) : %s langue(s), %s processus (modèle chargé dans chacun)",
             GLOTLID_FICHIER, len(langues), min(n_proc, len(langues)))
    resultats = []
    with Pool(min(n_proc, len(langues)), initializer=_init_glotlid) as pool:
        for r in pool.imap_unordered(langue_langue, [(c, source, d) for c in langues]):
            resultats.append(r)
            log.info("%-4s %8s textes, %8s avec verdict, %7s marqués %s (%s s) ; %s", r["langue"], r["textes"],
                     r["textes_avec_verdict"], r["textes_marques"],
                     "" if r["verifiable"] else "[non vérifiable : grandes langues seulement]", r["duree_s"],
                     ", ".join(f"{k} {v}" for k, v in list(r["etiquettes_des_textes_marques"].items())[:3]))
    resultats.sort(key=lambda r: r["langue"])
    total = sum(r["textes"] for r in resultats)
    marques = sum(r["textes_marques"] for r in resultats)
    ecrire_json(d / "bilan.json", {
        "etape": "7.5 mauvaise langue", "version": VERSION, "source": str(source.relative_to(ROOT)),
        "modele": f"{GLOTLID_REPO}/{GLOTLID_FICHIER}",
        "parametres": {"mots_min_paragraphe": T5_MOTS, "part_min_langue_attendue": T5_PART,
                       "proba_min_grande_langue": T5_PROBA, "grandes_langues": sorted(GRANDES_LANGUES),
                       "non_fiables": sorted(NON_FIABLES)},
        "fin_utc": maintenant(), "duree_s": round(time.time() - t0), "textes": total, "language_mismatch": marques,
        "langues": resultats})
    log.info("%s textes marqués language_mismatch sur %s (%.2f %%) ; %s s", marques, total,
             100 * marques / max(1, total), round(time.time() - t0))
    return d


# 7.7 : écriture finale des quality_flags + contrôles
# Combine les résultats des analyses retenues (7.2 quasi-doublons, 7.3+7.4 ébauches/texte répété,
# 7.5 mauvaise langue) en un seul quality_flags par texte. Écrit une NOUVELLE provenance dans
# data/processed/qualite/analyses/<run>_ecriture_drapeaux/provenance/ : la provenance et le corpus
# d'origine (qualite.source) ne sont jamais modifiés.
# Les 2 messages d'erreur de collecte identifiés en 7.6 (motif resserré) ne reçoivent pas de drapeau
# (ce ne sont pas des doublons, gabarits, texte répété ou mauvaise langue) : ils sont listés à part dans
# exclusions_export.jsonl pour être écartés à l'export (tâche 8) avec la raison "erreur de collecte"
# (décision validée le 19/11) — même format que les exclusions de la tâche 6 (normaliser.py).
FLAGS_VALIDES = ("boilerplate", "language_mismatch", "near_duplicate", "template_generated")
DOSSIER_QUASI_DOUBLONS_DEFAUT = "20261118T073610Z_quasi_doublons"
DOSSIER_ANALYSE_QUALITE_DEFAUT = "20261119T001340Z_analyse_qualite"
DOSSIER_LANGUE_DEFAUT = "20261121T200132Z_langue"


def marques_near_duplicate(dossier_quasi, code):
    chemin = dossier_quasi / "quasi_doublons" / f"{code}.jsonl"
    marques = set()
    if chemin.exists():
        with open(chemin, encoding="utf-8") as f:
            for ligne in f:
                marques.update(json.loads(ligne)["marques"])
    return marques


def marques_t3_t4(dossier_analyse, code):
    chemin = dossier_analyse / "drapeaux" / f"{code}.json"
    if not chemin.exists():
        return set(), set()
    d = json.loads(chemin.read_text(encoding="utf-8"))
    return set(d.get("template_generated", [])), set(d.get("boilerplate", []))


def marques_langue(dossier_langue, code):
    chemin = dossier_langue / "drapeaux" / f"{code}.json"
    if not chemin.exists():
        return set()
    return set(json.loads(chemin.read_text(encoding="utf-8")).get("language_mismatch", []))


def ecrire_drapeaux_langue(args):
    from itertools import zip_longest
    code, source, dossier_quasi, dossier_analyse, dossier_langue, dossier = args
    t0 = time.time()
    nd = marques_near_duplicate(dossier_quasi, code)
    t3, t4 = marques_t3_t4(dossier_analyse, code)
    lm = marques_langue(dossier_langue, code)
    compte = Counter()
    au_moins_un, plusieurs, n = 0, 0, 0
    erreurs_collecte, erreurs = [], []
    with open(source / "corpus" / f"{code}.jsonl", encoding="utf-8") as f_corpus, \
         open(source / "provenance" / f"{code}.jsonl", encoding="utf-8") as f_prov, \
         open(dossier / "provenance" / f"{code}.jsonl", "w", encoding="utf-8", newline="\n") as f_out:
        for i, (ligne_c, ligne_p) in enumerate(zip_longest(f_corpus, f_prov)):
            if ligne_c is None or ligne_p is None:
                erreurs.append(f"{code} : corpus et provenance n'ont pas le même nombre de lignes (arrêt ligne {i})")
                break
            t = json.loads(ligne_c)["text"]
            d = json.loads(ligne_p)
            drapeaux = sorted(
                (["near_duplicate"] if i in nd else [])
                + (["template_generated"] if i in t3 else [])
                + (["boilerplate"] if i in t4 else [])
                + (["language_mismatch"] if i in lm else []))
            d["quality_flags"] = drapeaux
            for dr in drapeaux:
                compte[dr] += 1
            au_moins_un += bool(drapeaux)
            plusieurs += len(drapeaux) > 1
            m = ERREUR.search(t)
            if m:
                erreurs_collecte.append({"texte": i, "source_url": d.get("source_url"),
                                         "text_sha256_collecte": d.get("text_sha256_collecte", d.get("text_sha256")),
                                         "extrait": extrait(t, m)})
            f_out.write(json.dumps(d, ensure_ascii=False) + "\n")
            n += 1
    for cle, s in (("near_duplicate", nd), ("template_generated", t3), ("boilerplate", t4), ("language_mismatch", lm)):
        hors = [i for i in s if i < 0 or i >= n]
        if hors:
            erreurs.append(f"{code} : {len(hors)} indice(s) {cle} hors limites (0..{n - 1})")
    return {"langue": code, "textes": n, **{k: compte.get(k, 0) for k in FLAGS_VALIDES},
            "au_moins_un_drapeau": au_moins_un, "plusieurs_drapeaux": plusieurs,
            "erreurs_collecte": erreurs_collecte, "erreurs": erreurs, "duree_s": round(time.time() - t0)}


def ecrire_drapeaux(config, source, processus, dossier_quasi, dossier_analyse, dossier_langue, langues=None):
    d = nouveau_dossier(config, "ecriture_drapeaux")
    (d / "provenance").mkdir()
    t0 = time.time()
    langues = langues or langues_source(source)
    langues = sorted(langues, key=lambda c: -(source / "corpus" / f"{c}.jsonl").stat().st_size)
    log.info("7.7 Écriture des quality_flags : %s langue(s), %s processus ; sources near_duplicate=%s, "
             "template_generated/boilerplate=%s, language_mismatch=%s", len(langues), min(processus, len(langues)),
             dossier_quasi.name, dossier_analyse.name, dossier_langue.name)
    resultats = []
    args = [(c, source, dossier_quasi, dossier_analyse, dossier_langue, d) for c in langues]
    with Pool(min(processus, len(langues))) as pool:
        for r in pool.imap_unordered(ecrire_drapeaux_langue, args):
            resultats.append(r)
            log.info("%-4s %8s textes : %6s near_duplicate, %6s template_generated, %6s boilerplate, "
                     "%6s language_mismatch ; %6s avec >=1 drapeau, %2s erreur(s) collecte %s (%s s)",
                     r["langue"], r["textes"], r["near_duplicate"], r["template_generated"], r["boilerplate"],
                     r["language_mismatch"], r["au_moins_un_drapeau"], len(r["erreurs_collecte"]),
                     "[ANOMALIE]" if r["erreurs"] else "", r["duree_s"])
            for e in r["erreurs"]:
                log.error("  %s", e)
    resultats.sort(key=lambda r: r["langue"])
    total = {k: sum(r[k] for r in resultats) for k in FLAGS_VALIDES}
    textes = sum(r["textes"] for r in resultats)
    au_moins_un = sum(r["au_moins_un_drapeau"] for r in resultats)
    plusieurs = sum(r["plusieurs_drapeaux"] for r in resultats)
    erreurs_collecte = {r["langue"]: r["erreurs_collecte"] for r in resultats if r["erreurs_collecte"]}
    toutes_erreurs = [e for r in resultats for e in r["erreurs"]]
    with open(d / "exclusions_export.jsonl", "w", encoding="utf-8", newline="\n") as f:
        for code, items in erreurs_collecte.items():
            for it in items:
                f.write(json.dumps({"langue": code, "source_url": it["source_url"],
                                    "text_sha256_collecte": it["text_sha256_collecte"],
                                    "raison": "erreur de collecte", "extrait": it["extrait"]},
                                   ensure_ascii=False) + "\n")
    ecrire_json(d / "bilan.json", {
        "etape": "7.7 écriture des quality_flags", "version": VERSION, "source": str(source.relative_to(ROOT)),
        "sources_drapeaux": {"near_duplicate": dossier_quasi.name,
                             "template_generated_et_boilerplate": dossier_analyse.name,
                             "language_mismatch": dossier_langue.name},
        "fin_utc": maintenant(), "duree_s": round(time.time() - t0), "textes": textes, **total,
        "au_moins_un_drapeau": au_moins_un, "plusieurs_drapeaux": plusieurs,
        "erreurs_collecte_total": sum(len(v) for v in erreurs_collecte.values()),
        "anomalies": toutes_erreurs, "langues": resultats})
    log.info("Total : %s textes, %s avec au moins un drapeau (%.2f %%) ; %s erreur(s) de collecte pour "
             "l'export (tâche 8) ; %s anomalie(s) ; %s s", textes, au_moins_un, 100 * au_moins_un / max(1, textes),
             sum(len(v) for v in erreurs_collecte.values()), len(toutes_erreurs), round(time.time() - t0))
    if toutes_erreurs:
        log.error("ANOMALIES — à corriger avant de valider la tâche 7 :")
        for e in toutes_erreurs:
            log.error("  %s", e)
    return d


#  préparation de 7.5 : GlotLID et sondage 
def charger_glotlid():
    import fasttext                                   # paquet fasttext-predict-v2
    from huggingface_hub import hf_hub_download
    chemin = hf_hub_download(repo_id=GLOTLID_REPO, filename=GLOTLID_FICHIER)
    return fasttext.load_model(chemin), chemin


def echantillon_paragraphes(args):
    """100 paragraphes d'au moins 10 mots tirés au hasard (graine fixe) dans une langue."""
    code, source, n = args
    rng = random.Random(f"42-{code}")
    choisis, vus = [], 0
    with open(source / "corpus" / f"{code}.jsonl", encoding="utf-8") as f:
        for ligne in f:
            for p in json.loads(ligne)["text"].split("\n"):
                if len(p.split()) >= 10:
                    vus += 1
                    if len(choisis) < n:
                        choisis.append(p)
                    else:
                        k = rng.randrange(vus)
                        if k < n:
                            choisis[k] = p
    return code, choisis


def sonder_langues(config, source, processus):
    d = nouveau_dossier(config, "sondage_langues")
    t0 = time.time()
    langues = langues_source(source)
    log.info("Tirage de 100 paragraphes d'au moins 10 mots par langue…")
    with Pool(min(processus, len(langues))) as pool:
        echantillons = dict(pool.imap_unordered(echantillon_paragraphes, [(c, source, 100) for c in langues]))
    log.info("Téléchargement (1,69 Go, une seule fois) ou chargement de GlotLID (%s, %s)…", GLOTLID_REPO, GLOTLID_FICHIER)
    modele, chemin = charger_glotlid()
    log.info("Modèle chargé : %s", chemin)
    resultats = []
    for code in langues:
        par = echantillons[code]
        attendues = ETIQUETTES_GLOTLID.get(code, [])
        top = Counter()
        ok = 0
        exemples = []
        for p in par:
            labels, probs = modele.predict(p.replace("\n", " "), k=1)
            lab = labels[0].replace("__label__", "")
            top[lab] += 1
            ok += lab in attendues
            if lab not in attendues and len(exemples) < 3:
                exemples.append({"etiquette": lab, "proba": round(float(probs[0]), 3), "extrait": p[:150]})
        r = {"langue": code, "etiquettes_attendues": attendues, "paragraphes": len(par),
             "dans_la_langue_attendue": ok, "etiquettes_trouvees": dict(top.most_common(5)), "exemples_autres": exemples}
        resultats.append(r)
        log.info("%-4s %3s paragraphes, %3s dans la langue attendue %s ; trouvé : %s", code, len(par), ok,
                 attendues or "(langue absente du modèle)", ", ".join(f"{k} {v}" for k, v in top.most_common(3)))
    ecrire_json(d / "sondage_langues.json", {"modele": f"{GLOTLID_REPO}/{GLOTLID_FICHIER}", "source": str(source.relative_to(ROOT)),
                                             "fin_utc": maintenant(), "langues": resultats})
    log.info("Sondage terminé en %s s", round(time.time() - t0))
    return d


#programme principal
def main(argv=None):
    ap = argparse.ArgumentParser(description="Contrôle qualité du corpus (tâche 7)")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--doublons-langues", action="store_true", help="7.1 : textes identiques dans deux éditions")
    g.add_argument("--sonder-langues", action="store_true", help="prépare GlotLID et sonde 100 paragraphes par langue")
    g.add_argument("--quasi-doublons", action="store_true", help="7.2 : analyse des quasi-doublons")
    g.add_argument("--analyse-qualite", action="store_true", help="7.3 + 7.4 + 7.6 : ébauches, texte répété, contrôles")
    g.add_argument("--langue", action="store_true", help="7.5 : mauvaise langue (GlotLID)")
    g.add_argument("--ecrire-drapeaux", action="store_true",
                   help="7.7 : écrit les quality_flags dans une nouvelle provenance")
    ap.add_argument("--langues", nargs="+", metavar="CODE", help="limiter l'analyse à ces langues")
    ap.add_argument("--dossier-quasi-doublons", default=DOSSIER_QUASI_DOUBLONS_DEFAUT,
                    help="7.7 : dossier d'analyse 7.2 à utiliser (défaut : %(default)s)")
    ap.add_argument("--dossier-analyse-qualite", default=DOSSIER_ANALYSE_QUALITE_DEFAUT,
                    help="7.7 : dossier d'analyse 7.3+7.4 à utiliser (défaut : %(default)s)")
    ap.add_argument("--dossier-langue", default=DOSSIER_LANGUE_DEFAUT,
                    help="7.7 : dossier d'analyse 7.5 à utiliser (défaut : %(default)s)")
    args = ap.parse_args(argv)
    config, source, processus = charger_config()
    if args.doublons_langues:
        doublons_langues(config, source, processus)
    elif args.sonder_langues:
        sonder_langues(config, source, processus)
    elif args.quasi_doublons:
        quasi_doublons(config, source, processus, args.langues)
    elif args.analyse_qualite:
        analyse_qualite(config, source, processus, args.langues)
    elif args.ecrire_drapeaux:
        base = ROOT / config["paths"]["processed"] / "qualite" / "analyses"
        ecrire_drapeaux(config, source, processus, base / args.dossier_quasi_doublons,
                        base / args.dossier_analyse_qualite, base / args.dossier_langue, args.langues)
    else:
        analyse_langue_glotlid(config, source, processus, args.langues)
    return 0


if __name__ == "__main__":
    sys.exit(main())
