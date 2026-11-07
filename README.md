# African Languages Corpus  Projet 1 : African Language Data & LLM Readiness

Corpus de textes couvrant toutes les langues africaines présentes sur Wikipédia, construit à partir des ressources Wikimedia (Wikipédia en priorité), avec le français comme langue de comparaison.


## Périmètre linguistique

**Toutes les langues africaines disposant d'une édition Wikipédia**,
Le français sert de langue de comparaison.

La liste complète des éditions africaines est établie à la tâche 1 (inventaire). Toute langue écartée doit l'être avec une justification documentée.

## Organisation du projet

À la racine, on trouve ce README, `requirements.txt` (les bibliothèques Python à installer), `.env.example` (le modèle du fichier qui contient l'adresse de contact) et les dossiers suivants :

- `config/` : les réglages des runs (`config.yaml` : langues, date des dumps, réglages du pipeline) et les deux schémas JSON du corpus et de la provenance.
- `src/` : le code de collecte et de nettoyage, décrit juste après.
- `notebooks/` : un notebook par étape : l'inventaire (`inventaire_wikimedia.ipynb`, tâche 1), le prototype (`prototype_collecte.ipynb`, tâche 4) et le lancement du pipeline (`collecte_pipeline.ipynb`, tâche 5).
- `data/processed/` : les résultats de chaque run (voir plus bas).
- `data/raw/` : supprimé (voir plus bas).
- `reports/` et `docs/` : les rapports de chaque tâche et la documentation, non versionnés pour l'instant.

## Le code (`src/`)

- `collecter.py` : le point d'entrée. Il choisit les langues, les traite des plus petites aux plus grandes, reprend un run interrompu et fait le bilan.
- `wikimedia.py` : parle aux serveurs de Wikimedia (API et téléchargement des dumps) et réessaie quand la connexion coupe.
- `dumps.py` : lit un dump compressé et en sort les articles un par un.
- `traitement.py` : traite une langue : extraction, retrait des doublons, écriture des fichiers et points de reprise.
- `extraire_texte.py` : les règles de nettoyage. Garde le texte d'un article et retire le balisage wiki.
- `extraire_html.py` : les mêmes règles appliquées à la page affichée, pour les articles faits uniquement de modèles.
- `francais.py` : tire au hasard les 100 000 articles de l'échantillon français.
- `valider_records.py` : vérifie chaque texte du corpus et sa provenance.

Pour lancer une collecte, créer d'abord le fichier `.env` à partir de `.env.example`, puis :

```bash
python src/collecter.py --langues wo yo     # quelques éditions
python src/collecter.py --tout              # les 68 éditions et l'échantillon français
python src/collecter.py --reprendre         # reprendre un run interrompu
```

Le notebook `collecte_pipeline.ipynb` lance les mêmes commandes et affiche les résultats.

## Dossier `data/processed`

**Les données sont sur Google Drive.** Trop volumineuses pour GitHub (environ 8 Go), elles sont rangées sur Drive avec la même organisation : [dossier processed](https://drive.google.com/drive/folders/1X9HGd6F25eF9CYrUrn5xehZAwgiCpp6Z?usp=sharing). Pour travailler dessus, télécharger son contenu et le placer dans `data/processed/` à la racine du projet.

Il contient les **textes extraits et nettoyés** et les résultats de chaque run, dans un sous-dossier daté par run (`AAAAMMJJTHHMMSSZ`, heure UTC).

**Brut ou nettoyé.** Le texte brut est dans `data/raw/dumps/<date>/` : ce sont les dumps Wikipédia, qui contiennent le wikitexte de chaque article avec son balisage. chaque article est lu dans le dump, son texte est extrait et nettoyé, puis écrit directement dans `data/processed/.../corpus/<code>.jsonl`. Les textes de `data/processed` ne sont donc plus bruts, mais ils ne sont pas encore normalisés (tâche 6) ni contrôlés (tâche 7).

Chaque run a son propre sous-dossier, nommé par sa date et son heure de début (UTC), par exemple `20261105T065025Z`.

- `inventaire/<run>/` : l'inventaire de la tâche 1 (une ligne par édition, un échantillon d'articles, les langues en préparation et les requêtes faites).
- `prototype/<run>/` : le premier run de la tâche 4, sur 5 langues.
- `collecte/<run>/` : les runs du pipeline (tâche 5). On y trouve :
  - `corpus/<code>.jsonl` : les textes, un par ligne, sous la forme `{"text": "..."}` ;
  - `provenance/<code>.jsonl` : pour chaque texte, son empreinte SHA-256, l'adresse de la révision, la source, les dates et la licence ;
  - `exclusions/<code>.jsonl` : les pages écartées et la raison ;
  - `mesures/<code>.json` et `mesures.json` : les chiffres de chaque langue ;
  - `run.log`, `journal_requetes.jsonl`, `etat.json`, `run_metadata.json` et `config.yaml` : le journal du run, les requêtes faites, l'état utilisé pour la reprise, les versions des outils et la configuration utilisée.
Ce que sont les textes. Chaque ligne de corpus/<code>.jsonl est le texte d'un article Wikipédia : paragraphes, titres de section, texte des liens et des cellules de tableau. Le wiki de balisage, les images, les modèles, les références et les catégories sont retirés, et le texte est en Unicode NFC. Tous les textes sont gardés, sauf les doublons exacts. Les textes ne sont pas encore normalisés (tâche 6) ni contrôlés pour les quasi-doublons et la langue (tâche 7).


**Lien entre texte et source.** Le corpus ne contient que le texte. Pour retrouver l'article d'origine, calculer l'empreinte SHA-256 du texte et la chercher dans le fichier de provenance de la langue.

**Runs présents.**

| Run | Contenu |
|---|---|
| `collecte/20261105T065025Z` | **Run de référence (tâche 5).** 68 éditions et 100 000 articles français : 3 948 960 textes, 721 millions de mots |
| `prototype/20261027T154013Z` | Prototype (tâche 4) : 5 langues, 185 275 textes |
| `inventaire/20261023T132917Z` | Inventaire de référence (tâche 1) |



**Licence.** Les textes viennent de Wikipédia et restent sous licence CC BY-SA 4.0. Toute réutilisation doit citer les articles d'origine (adresses dans `provenance.jsonl`), indiquer que le texte a été modifié et garder la même licence.

## Dossier `data/raw` (supprimé)

Le dossier `data/raw` contenait les données brutes téléchargées pendant les collectes. Il a été supprimé après le run complet de la tâche 5 (`data/processed/collecte/20261105T065025Z`).

**Ce qu'il contenait**

| Chemin | Contenu | Produit par |
|---|---|---|
| `data/raw/dumps/20261001/` | Les dumps Wikipédia `pages-articles` du 1er octobre 2026 : 77 fichiers `.xml.bz2` pour 68 éditions (3,2 Go). Ils contiennent le wikitexte de chaque article, avec son balisage | `src/collecter.py` (tâche 5), notebook du prototype (tâche 4) |
| `data/raw/api/parse/<code>/<révision>.json` | Réponses brutes de l'API (`action=parse`) pour les pages dont le texte a été lu dans la page affichée (environ 7 800 pages) | `src/collecter.py` |
| `data/raw/api/fr/` | L'échantillon français : liste des 100 000 articles tirés au hasard (`echantillon_100000.json`) et réponses brutes de l'API par lots de 50 (`revisions/`) | `src/collecter.py` |
| `data/raw/api/siteinfo/` | Informations de chaque wiki : noms des espaces Fichier et Catégorie, préfixes des liens vers les autres langues | `src/collecter.py` |
| `data/raw/inventaire/<run>/` | Réponses brutes de l'API pour l'inventaire de la tâche 1 | `notebooks/inventaire_wikimedia.ipynb` |

**Pourquoi on n'en a plus besoin**

- Le nettoyage suivant (tâches 6 et 7) part des textes déjà extraits dans `data/processed/collecte/<run>/corpus/`, pas des dumps.
- La provenance de chaque texte (adresse de la révision, dump d'origine, dates, licence) est conservée dans `data/processed/collecte/<run>/provenance/`.
- Le dossier pesait plus de 3 Go et n'était pas versionné : trop gros pour GitHub.

**Pour le retrouver**

Relancer la collecte : le pipeline recrée `data/raw` et retélécharge ce qui manque.

```bash
# fichier .env à la racine du projet (voir .env.example) :
# WIKIMEDIA_CONTACT=votre.adresse@exemple.com
python src/collecter.py --tout                  # 68 éditions + échantillon français
python src/collecter.py --langues wo yo ha     # ou seulement quelques éditions
```

À savoir :

- Les dumps sont retéléchargés à la date fixée dans `config/config.yaml` (`dump_date: "20261001"`), puis vérifiés par leur empreinte SHA-1. Wikimedia ne garde en ligne que les dumps récents (quelques mois). Si celui du 1er octobre 2026 n'est plus disponible, choisir une date plus récente sur https://dumps.wikimedia.org : le corpus obtenu sera alors différent.
- La liste française d'origine est perdue : un nouveau run tire un nouvel échantillon de 100 000 articles.
- Les pages lues par l'API sont relues dans leur état du jour : leur texte peut légèrement différer.
- Les réponses brutes de l'inventaire se retrouvent en relançant `notebooks/inventaire_wikimedia.ipynb`.

## Installation

```bash
python -m venv .venv
.venv\Scripts\activate           # Linux / macOS : source .venv/bin/activate
pip install -r requirements.txt
```

## Licences

Le texte Wikipédia est sous CC BY-SA 4.0 : tout corpus dérivé redistribué doit citer les articles d'origine (adresse de la révision), indiquer les modifications et être partagé sous la même licence. Détail dans le rapport de la tâche 3.
