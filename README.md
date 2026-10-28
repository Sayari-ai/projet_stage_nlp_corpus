# African Languages Corpus  Projet 1 : African Language Data & LLM Readiness

Corpus de textes couvrant toutes les langues africaines présentes sur Wikipédia, construit à partir des ressources Wikimedia (Wikipédia en priorité), avec le français comme langue de comparaison.



## Périmètre linguistique

**Toutes les langues africaines disposant d'une édition Wikipédia**.
Le français sert de langue de comparaison.

La liste complète des éditions africaines est établie à la tâche 1 (inventaire). Toute langue écartée doit l'être avec une justification documentée.

## Structure

```
projet_stage_nlp/
├── README.md            # ce fichier
├── requirements.txt     # dépendances Python
├── config/              # paramètres du run (langues, date de dump, chemins) et schémas JSON
├── src/                 # code de collecte et de nettoyage
├── notebooks/           # notebooks de collecte (inventaire, prototype)
├── data/
│   ├── raw/             # dumps Wikimedia et réponses API brutes (non versionnés)
│   └── processed/       # textes collectés et résultats de chaque run (voir ci-dessous)
├── reports/             # rapports par tâche
└── docs/                # liste des tâches, schéma, registre sources/licences
```

## Dossier `data/processed`

Il contient les **textes bruts collectés** et les résultats de chaque run, dans un sous-dossier daté par run (`AAAAMMJJTHHMMSSZ`, heure UTC).

```
data/processed/
├── inventaire/<run>/                 # tâche 1 : inventaire des éditions Wikipédia africaines
│   ├── inventaire_synthese.json      # une ligne par édition : langue, codes, nombre d'articles et de mots, décision
│   ├── echantillon_articles.json     # échantillon d'articles par édition
│   ├── langues_en_preparation_incubator.json
│   ├── provenance.jsonl              # requêtes API faites pour l'inventaire
│   └── run_metadata.json
└── prototype/<run>/                  # tâche 4 : premier run de collecte (wo, yo, nqo, ha, am)
    ├── corpus/<code>.jsonl           # les textes collectés, un texte par ligne : {"text": "..."}
    ├── provenance.jsonl              # pour chaque texte : empreinte SHA-256, langue, URL de la révision,
    │                                 # source (dump ou API), date de la révision, date de collecte, licence
    ├── exclusions.jsonl              # pages écartées et raison
    ├── mesures.json                  # chiffres par langue (textes, mots, durée)
    ├── raisons_exclusion.json        # nombre de pages écartées par raison
    ├── echantillon_revue.jsonl       # échantillon relu pour le contrôle qualité
    ├── journal_requetes.jsonl        # requêtes faites aux serveurs Wikimedia
    └── run_metadata.json             # versions, paramètres, empreintes des dumps, résultats des contrôles
```

**Ce que sont les textes.** Chaque ligne de `corpus/<code>.jsonl` est le texte d'un article Wikipédia : paragraphes, titres de section, texte des liens et des cellules de tableau. Le balisage wiki, les images, les modèles, les références et les catégories sont retirés, et le texte est en Unicode NFC. Tous les textes sont gardés, sauf les doublons exacts. Les textes ne sont pas encore normalisés (tâche 6) ni contrôlés pour les quasi-doublons et la langue (tâche 7).

**Lien entre texte et source.** Le corpus ne contient que le texte. Pour retrouver l'article d'origine, calculer l'empreinte SHA-256 du texte et la chercher dans `provenance.jsonl`.

**Runs présents.**

| Run | Contenu |
|---|---|
| `prototype/20261027T154013Z` | **Run de référence.** 185 275 textes, 73 millions de mots |
| `inventaire/20261023T132917Z` | Inventaire de référence (tâche 1) |


Textes du run de référence (dumps Wikipédia du 1er octobre 2026) :

| Langue | Code | Textes | Mots | Taille du fichier |
|---|---|---|---|---|
| Wolof | `wo` | 1 809 | 608 966 | 3,4 Mo |
| Yoruba | `yo` | 41 803 | 5 637 920 | 40,3 Mo |
| N'ko | `nqo` | 1 726 | 812 329 | 8,6 Mo |
| Haoussa | `ha` | 123 651 | 64 034 554 | 377,9 Mo |
| Amharique | `am` | 16 286 | 1 913 602 | 24,6 Mo |
| **Total** | | **185 275** | **73 007 371** | |

**Licence.** Les textes viennent de Wikipédia et restent sous licence CC BY-SA 4.0. Toute réutilisation doit citer les articles d'origine (adresses dans `provenance.jsonl`), indiquer que le texte a été modifié et garder la même licence.

## Installation

```bash
python -m venv .venv
.venv\Scripts\activate           # Linux / macOS : source .venv/bin/activate
pip install -r requirements.txt
```


## Licences

Le texte Wikipédia est sous CC BY-SA 4.0 : tout corpus dérivé redistribué doit citer les articles d'origine (adresse de la révision), indiquer les modifications et être partagé sous la même licence. Détail dans le rapport de la tâche 3.
