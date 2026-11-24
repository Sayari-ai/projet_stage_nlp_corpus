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
- `notebooks/` : un notebook par étape, l'inventaire (`inventaire_wikimedia.ipynb`, tâche 1), le prototype (`prototype_collecte.ipynb`, tâche 4), le lancement du pipeline (`collecte_pipeline.ipynb`, tâche 5), la normalisation (`normalisation.ipynb`, tâche 6), le contrôle qualité (`controle_qualite.ipynb`, tâche 7), l'export final (`exporter.ipynb`, tâche 8), le rapport qualité (`rapport_qualite.ipynb`, tâche 9) et les splits (`split.ipynb`, tâche 10).
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
- `normaliser.py` : la normalisation du texte (tâche 6), décrite plus bas.
- `controle_qualite.py` : détection des doublons et contrôle qualité (tâche 7) quasi-doublons, ébauches en série, texte répété, mauvaise langue (GlotLID v3). N'enlève rien : écrit des `quality_flags` dans la provenance.
- `exporter.py` : export final (tâche 8) corpus + provenance définitifs (JSONL et Parquet), écarte les erreurs de collecte, valide le schéma de chaque ligne.
- `rapport_qualite.py` : statistiques et couverture par langue (tâche 9) agrège les bilans déjà produits par les étapes précédentes, ne relit aucune donnée brute.
- `split.py` : splits train/validation/test (tâche 10) ajoute le champ `split` à la provenance, sans jamais séparer un texte de ses quasi-doublons ou de son doublon exact dans une autre langue.

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

**Licence.** Les textes viennent de Wikipédia et restent sous licence CC BY-SA 4.0. Toute réutilisation doit citer les articles d'origine (adresses dans `provenance.jsonl`), indiquer que le texte a été modifié et garder la même licence.

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

## Contrôle qualité (tâche 7)

```bash
python src/controle_qualite.py --sonder-langues      # une seule fois : prépare GlotLID
python src/controle_qualite.py                       # doublons, quasi-doublons, ébauches, texte répété, mauvaise langue
```

Source : `data/processed/normalise/20261113T141439Z` (sortie de la tâche 6, jamais modifiée). Rien n'est retiré du corpus : chaque texte reçoit un champ `quality_flags` (0 à 4 valeurs parmi `near_duplicate`, `template_generated`, `boilerplate`, `language_mismatch`) écrit dans la provenance. Run de référence : `qualite/analyses/20261122T070453Z_ecriture_drapeaux` — 965 559 / 3 948 957 textes avec au moins un drapeau ; 4 erreurs de collecte identifiées, à écarter à l'export (tâche 8).

## Export final (tâche 8)

```bash
python src/exporter.py
```

Combine le corpus normalisé (tâche 6) et les `quality_flags` (tâche 7) en corpus et provenance **définitifs** :

- `data/processed/corpus/<code>.jsonl` : `{"text": ...}` seul (schéma tâche 2).
- `data/processed/provenance/<code>.jsonl` et `provenance.parquet` : `text_sha256`, `language_code`, `source_url`, `source_dump`, `revision_date`, `collected_at`, `license`, `quality_flags`, `split` (ajouté tâche 10) — relié au corpus par empreinte SHA-256, jamais par position de ligne.
- `data/processed/exclusions.jsonl` : textes écartés (erreurs de collecte), avec leur raison.

Run de référence : `qualite/analyses/20261122T081323Z_export` — 3 948 957 → **3 948 953 textes exportés**, 4 exclusions justifiées, 0 anomalie.

## Rapport qualité et statistiques (tâche 9)

```bash
python src/rapport_qualite.py
```

Agrège les bilans déjà produits (tâches 5 à 8) — ne relit aucune donnée brute. Sorties : `data/processed/qualite/rapport_qualite_par_langue.csv`/`.jsonl`, `couverture.json`, `manifeste_collecte.csv`/`.jsonl`. Rapport : `reports/Rapport_tache9_qualite_statistiques.docx`. Couverture : **65/68 langues (95,6 %)** ; `aa`, `hz`, `kr` justifiées à 0 texte (dumps sans contenu réel).

## Splits train/validation/test (tâche 10)

```bash
python src/split.py
```

Ajoute le champ `split` (`train`/`validation`/`test`) à `provenance/<code>.jsonl` et `provenance.parquet` — 80/10/10 par langue ; langues de moins de 20 textes forcées en train (val/test vides, justifié) ; quasi-doublons et doublons exacts inter-langues toujours regroupés dans un seul split (anti-fuite). Reproductible : attribution par hachage de `text_sha256` + `random_seed` (`config.yaml`). Rapport : `reports/Rapport_tache10_splits.docx`. Run de référence : `qualite/analyses/20261123T212903Z_split` — 3 944 groupes anti-fuite (11 096 textes), 0 anomalie.

## Installation

```bash
python -m venv .venv
.venv\Scripts\activate           # Linux / macOS : source .venv/bin/activate
pip install -r requirements.txt
```

## Pipeline complet

| Étape | Commande | Source | Sortie |
|---|---|---|---|
| 5. Collecte | `python src/collecter.py --tout` | dumps Wikimedia (`dump_date` config) | `data/processed/collecte/<run>/` |
| 6. Normalisation | `python src/normaliser.py` | `collecte/<run>` (config `normalisation.source`) | `data/processed/normalise/<run>/` |
| 7. Contrôle qualité | `python src/controle_qualite.py` | `normalise/<run>` (config `qualite.source`) | `quality_flags` dans `qualite/analyses/.../provenance/` |
| 8. Export final | `python src/exporter.py` | `normalise/<run>` + drapeaux tâche 7 (config `export`) | `corpus/`, `provenance/`, `provenance.parquet` |
| 9. Rapport qualité | `python src/rapport_qualite.py` | bilans des tâches 5 à 8 | `qualite/rapport_qualite_par_langue.*`, `couverture.json` |
| 10. Splits | `python src/split.py` | `provenance/` (config `split`) | champ `split` dans `provenance/*` |

Chaque étape lit la sortie de la précédente via le chemin figé dans `config.yaml` (jamais « le dernier run » implicitement) — un run antérieur n'est donc jamais modifié par l'étape suivante.

## Reproductibilité (tâche 11)

Un 2e ingénieur peut vérifier qu'il retrouve les mêmes résultats sans tout retélécharger :

1. **Installer** : `python -m venv .venv`, `pip install -r requirements.txt`, créer `.env` depuis `.env.example`.
2. **Récupérer les données** : télécharger `data/processed/` depuis le [lien Google Drive](https://drive.google.com/drive/folders/1X9HGd6F25eF9CYrUrn5xehZAwgiCpp6Z?usp=sharing) (contient tous les runs de référence cités dans ce README, avec leurs `bilan.json`).
3. **Rejouer une étape déterministe** sans tout relancer, par exemple la tâche 10 (quelques minutes, pas de téléchargement) :
   ```bash
   python src/split.py
   ```
   Comparer le `bilan.json` produit (`data/processed/qualite/analyses/<nouveau_run>_split/`) à celui du run de référence (`20261123T212903Z_split`) : mêmes totaux (3 948 953 textes, 3 158 096/395 938/394 919 train/validation/test), mêmes 3 944 groupes anti-fuite, 0 anomalie l'attribution est un hachage déterministe (`text_sha256` + `random_seed`), donc bit-à-bit identique.
4. **Rejouer sur un sous-ensemble** pour vérifier la chaîne complète sans attendre des heures :
   ```bash
   python src/collecter.py --langues wo yo
   python src/normaliser.py
   python src/controle_qualite.py
   python src/exporter.py --langues wo yo
   ```
   Comparer les comptes obtenus (`bilan.json` de chaque étape) à ceux du run de référence pour `wo` et `yo` uniquement.
5. **Limites à documenter** : un run complet (68 langues + échantillon français) dépend de la disponibilité des dumps à la date figée (`dump_date` dans `config.yaml`) Wikimedia ne garde en ligne que les dumps récents (voir section `data/raw`). L'échantillon français (tirage aléatoire) n'est pas reproductible à l'identique sans la liste d'origine (perdue, voir plus haut) ; seul son volume (≈100 000 articles) l'est.

## Licences

Le texte Wikipédia est sous CC BY-SA 4.0 : tout corpus dérivé redistribué doit citer les articles d'origine (adresse de la révision), indiquer les modifications et être partagé sous la même licence. Détail dans le rapport de la tâche 3.
