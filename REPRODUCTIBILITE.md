# README de reproductibilité African Languages Corpus (Projet 1)

## Objectif et philosophie d'exécution

Ce document guide un **2e ingénieur** pour reproduire le pipeline de bout en bout, de la tâche 1 à la livraison finale, en exécutant les notebooks dans l'ordre.

- **Principe clé** : chaque notebook relance le script `src/*.py` correspondant, qui crée lui-même ses dossiers et fichiers de sortie (datés `AAAAMMJJTHHMMSSZ`, heure UTC) rien à créer à la main.
- **Mises à jour Wikimedia** : les dumps et l'API évoluent (nouveaux articles, révisions). Une réexécution à une autre date peut donc produire un corpus légèrement différent du run de référence (voir `dump_date` dans `config/config.yaml` et la section « Limites » plus bas). C'est attendu, pas une anomalie.
- **Ce qu'on ne transmet pas en bloc** : les dossiers de travail intermédiaires (`data/processed/collecte/`, `normalise/`, `qualite/analyses/*`). le 2e ingénieur les recrée en rejouant les notebooks. Seul le **livrable** (voir fin de ce document) est  transmis.
- **`config/config.yaml`** : chaque étape lit la sortie de la précédente via un chemin **figé** dans ce fichier (ex. `qualite.source: data/processed/normalise/20261113T141439Z`), jamais « le dernier run » implicitement. **Après chaque tâche, le 2e ingénieur met à jour le chemin correspondant dans `config.yaml` avec le nom du dossier que SON run vient de créer**, avant de passer à la tâche suivante — exactement comme ça a été fait pour produire le run de référence.

## Prérequis généraux (avant la tâche 1)

- Python 3.11+, environnement virtuel : `python -m venv .venv` puis `pip install -r requirements.txt`.
- Fichier `.env` créé à partir de `.env.example` (`WIKIMEDIA_CONTACT=votre.adresse@exemple.com` exigé par la politique Wikimedia).
- Accès réseau sortant vers `dumps.wikimedia.org` et les API `*.wikipedia.org`.
- Dépôt cloné avec `src/`, `notebooks/`, `config/` ; `data/`, `reports/`, `docs/` vides ou absents (recréés à l'exécution).

## Parcours d'exécution : de la tâche 1 à la livraison

### Tâche 1 — Inventorier les langues et ressources Wikimedia

- **Notebook** : `notebooks/inventaire_wikimedia.ipynb`
- **Pourquoi** : lister toutes les éditions Wikipédia africaines disponibles (articles, dumps/API), poser le périmètre linguistique du projet.
- **Entrées** : aucune donnée du projet — interroge directement l'API Wikimedia (liste des wikis).
- **Sortie** : `data/processed/inventaire/<run>/` (une ligne par édition, échantillon d'articles) ; `reports/Rapport_inventaire_tache1.docx`.

### Tâche 2: Définir le schéma de document et les critères d'acceptation

- **Pas de notebook** : décision documentée, pas de donnée à produire.
- **Pourquoi** : fixer le schéma du corpus (`{"text": ...}` seul) et de la provenance (`text_sha256`, `language_code`, `source_url`, `source_dump`, `revision_date`, `collected_at`, `license`, `quality_flags`, `split`), reliés par empreinte SHA-256, jamais par position de ligne.
- **Sortie** : `config/schema_corpus.json`, `config/schema_provenance.json` ; `reports/Rapport_tache2_schema.docx`.

### Tâche 3: Construire le registre des sources et licences

- **Pas de notebook** : décision documentée.
- **Pourquoi** : choisir dumps/API officiels (pas de scraping), respecter rate limits et licences (CC BY-SA 4.0).
- **Sortie** : `reports/Rapport_tache3_sources_licences.docx`.

### Tâche 4: Premier run de collecte (prototype, checkpoint S2)

- **Notebook** : `notebooks/prototype_collecte.ipynb`
- **Pourquoi** : valider la méthode d'extraction sur un sous-ensemble avant le run complet ; décisions prises au checkpoint S2 (arabe standard gardé, français en échantillon).
- **Entrées** : `config/config.yaml` (langues du prototype).
- **Sortie** : `data/processed/prototype/<run>/` ; `reports/Rapport_tache4_prototype_S2.docx`.

### Tâche 5: Construire le pipeline d'extraction reproductible

- **Notebook** : `notebooks/collecte_pipeline.ipynb` (lance `src/collecter.py`)
- **Pourquoi** : collecter tout le périmètre (68 éditions + échantillon français), avec logging, reprise sur plantage, et provenance par texte dès la collecte.
- **Entrées** : `config/config.yaml` (`scope.editions.incluses`, `dump_date`, `pipeline.echantillon_fr`), `.env`.
- **Sortie** : `data/processed/collecte/<run>/` (`corpus/`, `provenance/`, `exclusions/`, `mesures/`, `etat.json`, `run_metadata.json`).
- **À faire avant la tâche 6** : reporter `<run>` dans `config.yaml` → `normalisation.source`.

**Mise à jour ciblée (une langue a de nouveaux articles)** : pas besoin de tout recollecter. `--langues` ne retraite que les langues données ; les autres sont automatiquement reprises telles quelles (copiées, jamais modifiées) depuis le dernier run terminé, donc le nouveau run reste complet.

```bash
python src/collecter.py --langues ha   # recollecte seulement le haoussa ; le reste est repris tel quel
python src/collecter.py --tout         # recollecte tout (comportement par défaut, inchangé)
```

**Ajouter une langue hors des 69 actuelles** : le pipeline refuse toute langue absente de `config.yaml` - `scope.editions.incluses` (« Langue(s) hors périmètre »). Il faut d'abord l'y ajouter, puis la collecter — les autres langues restent inchangées (reprises telles quelles).

```yaml
# config/config.yaml
scope:
  editions:
    incluses: [..., nouveau_code]   # 1. ajouter le code ici
```

```bash
python src/collecter.py --langues nouveau_code   # 2. collecter seulement la nouvelle langue
```

Penser aussi à l'ajouter dans l'inventaire des langues (tâche 1) et le registre sources/licences (tâche 3), pour que le rapport qualité (tâche 9) et la documentation restent cohérents.

### Tâche 6: Normaliser le texte

- **Notebook** : `notebooks/normalisation.ipynb` (lance `src/normaliser.py`)
- **Pourquoi** : uniformiser Unicode (NFC), ponctuation et formatage en préservant les spécificités de chaque langue.
- **Entrées** : `normalisation.source` (sortie de la tâche 5).
- **Sortie** : `data/processed/normalise/<run>/` (`corpus/`, `provenance/`) ; `reports/Rapport_tache6_normalisation.docx`.
- **À faire avant la tâche 7** : reporter `<run>` dans `config.yaml` → `qualite.source` et `export.source`.

### Tâche 7: Détecter les doublons et exécuter les contrôles qualité

- **Notebook** : `notebooks/controle_qualite.ipynb` (lance `src/controle_qualite.py`)
- **Pourquoi** : repérer doublons exacts inter-langues, quasi-doublons, ébauches en série, texte répété, mauvaise langue (GlotLID v3) — sans rien retirer du corpus, uniquement des `quality_flags`.
- **Entrées** : `qualite.source` (sortie de la tâche 6).
- **Sortie** : plusieurs sous-dossiers `data/processed/qualite/analyses/<run>_*` (`doublons_langues`, `quasi_doublons`, `analyse_qualite`, `langue`, `ecriture_drapeaux` — ce dernier contient la provenance avec `quality_flags` complets) ; `reports/Rapport_tache7_controle_qualite.docx`.
- **À faire avant la tâche 8** : reporter le dossier `..._ecriture_drapeaux` dans `config.yaml` → `export.qualite`.
- **À faire avant la tâche 10** : reporter les dossiers `..._quasi_doublons` et `..._doublons_langues` dans `config.yaml` → `split.quasi_doublons` et `split.doublons_langues`.

### Tâche 8: Exporter en JSONL/Parquet et valider le schéma

- **Notebook** : `notebooks/exporter.ipynb` (lance `src/exporter.py`)
- **Pourquoi** : produire le corpus et la provenance **définitifs**, en écartant les erreurs de collecte et en validant chaque ligne contre le schéma (tâche 2).
- **Entrées** : `export.source` (tâche 6), `export.qualite` (tâche 7).
- **Sortie** : `data/processed/corpus/<code>.jsonl`, `data/processed/provenance/<code>.jsonl` + `provenance.parquet`, `exclusions.jsonl`, `export_exemples.jsonl`.

### Tâche 9: Produire le rapport qualité et les statistiques par langue

- **Notebook** : `notebooks/rapport_qualite.ipynb` (lance `src/rapport_qualite.py`)
- **Pourquoi** : volume, doublons, textes courts, anomalies et couverture du périmètre, par langue — agrège les bilans déjà produits, ne relit aucune donnée brute.
- **Entrées** : bilans des tâches 5 à 8.
- **Sortie** : `data/processed/qualite/rapport_qualite_par_langue.csv`/`.jsonl`, `couverture.json`, `manifeste_collecte.csv`/`.jsonl` ; `reports/Rapport_tache9_qualite_statistiques.docx`.

### Tâche 10: Documenter les splits train/validation/test sans fuite

- **Notebook** : `notebooks/split.ipynb` (lance `src/split.py`)
- **Pourquoi** : ajouter le split (80/10/10 par langue) à la provenance, sans jamais séparer un texte de ses quasi-doublons ou de son doublon exact dans une autre langue.
- **Entrées** : `data/processed/provenance/` (tâche 8), `split.quasi_doublons` et `split.doublons_langues` (tâche 7).
- **Sortie** : champ `split` ajouté dans `provenance/<code>.jsonl` et `provenance.parquet` ; `data/processed/qualite/analyses/<run>_split/` (`clusters_regroupes.jsonl`, `bilan.json`) ; `reports/Rapport_tache10_splits.docx`.

### Tâche 11: Rédiger le README et faire valider la reproductibilité

- **Pas de notebook** : ce document, plus la démonstration de re-run (section suivante).

## Démonstration de re-run (KPI tâche 11)

1. **Rejouer une étape déterministe seule** (rapide, pas de téléchargement) : `python src/split.py` sur les données déjà en place. Comparer le `bilan.json` produit au run de référence (`20261123T212903Z_split`) totaux et nombre de groupes anti-fuite doivent être identiques (attribution par hachage, reproductible à l'identique).
2. **Rejouer la chaîne complète sur un sous-ensemble** (quelques langues, pour valider le parcours sans attendre des heures) :
   ```bash
   python src/collecter.py --langues wo yo
   python src/normaliser.py
   python src/controle_qualite.py
   python src/exporter.py --langues wo yo
   ```
   Comparer les comptes obtenus pour `wo`/`yo` à ceux du run de référence.

## Périmètre de la livraison

**À livrer** (le corpus et ce qui l'accompagne) :

- `data/processed/corpus/` le corpus final (texte)
- `data/processed/provenance/`, `provenance.parquet` provenance + licences + quality_flags + split
- `data/processed/exclusions.jsonl`, `export_exemples.jsonl`
- `data/processed/qualite/rapport_qualite_par_langue.*`, `couverture.json`, `manifeste_collecte.*`
- `reports/` (tous les rapports de tâches), `docs/taches.md`
- `config/` (config.yaml, schémas JSON), `README.md`, ce document, `requirements.txt`, `src/`, `notebooks/`

**ce qui n'est pas transmi** : `data/processed/collecte/`, `data/processed/normalise/`, `data/processed/qualite/analyses/*` (dossiers de travail intermédiaires) recréés en rejouant les notebooks depuis la tâche 5.

## Limites à connaître

- Un run complet dépend de la disponibilité des dumps à la date figée (`dump_date`) : Wikimedia ne garde en ligne que les dumps récents. Si la date de référence n'est plus disponible, en choisir une plus récente sur https://dumps.wikimedia.org — le corpus obtenu sera alors différent (pages modifiées/supprimées entretemps).
- L'échantillon français est un tirage aléatoire : un nouveau run tire un nouvel échantillon (la liste d'origine n'est pas conservée) seul son volume (≈100 000 articles) est reproductible, pas son contenu exact.
- Les pages lues via l'API (page affichée, pour les articles faits de modèles) sont relues dans leur état du jour : leur texte peut légèrement différer d'un run à l'autre.
