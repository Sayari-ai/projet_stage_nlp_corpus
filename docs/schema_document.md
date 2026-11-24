# Tâche 2 — Schéma de document et critères d'acceptation

Projet 1 · African Language Data & LLM Readiness · 24/10/2026

## 1. Ce que contient le corpus : du texte seulement

Le corpus ne contient **que le texte** des articles, extrait, nettoyé et normalisé. Il ne contient ni titre d'article, ni numéro de page ou de révision, ni catégorie, ni lien, ni source.

- **Titres de section** : gardés dans le texte, chacun sur sa propre ligne (ex. « Ay digam »).
- **Aucun seuil de longueur** : un article est pris quelle que soit sa longueur, même s'il ne contient qu'un mot ou une phrase.

| Élément | Choix |
|---|---|
| Fichiers | Un fichier par langue : `data/processed/corpus/<code>.jsonl` (ex. `wo.jsonl`, `ha.jsonl`) |
| Langue | Donnée par le nom du fichier : code BCP 47 de l'édition. Les 69 codes Wikipédia retenus sont identiques à leur étiquette BCP 47 officielle (vérifié avec l'API `languageinfo`). |
| Une ligne | Le texte d'un article : `{"text": "..."}` |
| Schéma | `config/schema_document.json` : un seul champ, `text`, obligatoire ; tout autre champ est refusé |

Exemple (`wo.jsonl`, une ligne) :

```json
{"text": "Bene aw rëdduw suuf wu sew la, nekk ci diggante yamoo gi ak Gëwéelub Sànkar bi. …"}
```

Le format JSONL est gardé, plutôt qu'un fichier `.txt`, parce qu'un article contient des retours à la ligne. En JSONL, chaque article reste sur une seule ligne. C'est aussi le format demandé par le KPI « export JSONL validé ».

## 2. Ce qui est extrait d'une page, et ce qui ne l'est pas

Règles fixées à partir des captures de la page d'accueil de la Wikipédia en éwé (« Axa do Ŋgɔ »), et appliquées par `src/extraire_texte.py`.

| Élément de la page | Dans le corpus ? |
|---|---|
| Paragraphes de l'article | **Oui** |
| Titres de section | **Oui**, chacun sur sa ligne (un titre resté sans contenu est retiré) |
| Liens internes (ex. « Mahama », « Ghana ») | **Le texte du lien seulement** |
| Liens externes `[https://… texte]` | **Le texte du lien seulement** ; un lien sans texte est retiré |
| Gras, italique | Oui, le texte seul |
| Tableaux | **Le texte de chaque cellule**, une cellule par ligne (légende comprise) ; une cellule qui ne contient qu'une image est ignorée |
| Sauts de ligne `<br>` | Remplacés par un retour à la ligne (les mots ne sont jamais collés) |
| Logo et nom « Wikipedia », icônes (étoiles), images, photos et leurs légendes, galeries | Non |
| Boutons et formulaires (« Create New Article », champ de saisie), bannières, encadrés, palettes, infobox (modèles `{{ }}`) | Non |
| Menu, recherche, barre latérale, onglets, apparence | Non (absents du wikitexte) |
| Catégories, liens vers les autres langues | Non |
| Notes et références (`<ref>`) | Non |
| Adresses web (URL), commentaires, mots magiques (`__NOTOC__`), feuilles de style | Non |
| Texte de la **page d'accueil** de chaque wiki (ex. « Axa do Ŋgɔ ») : titres, paragraphes | Oui. Logo, bannière, boutons, champ de saisie et images sont retirés comme ailleurs |

Exemple de lien : `Dukplɔla [[John Mahama|Mahama]] ƒe` → `Dukplɔla Mahama ƒe`.

Exemple de tableau (article haoussa « Kazakistan ») : les photos des cellules sont retirées, le texte est gardé :

```
Kassym-Jomart Tokayev Shugaban kasar Kazakhstan
Askar Mamin Firaministan Kazakhstan
```

## 3. Règles que chaque texte doit respecter

| # | Règle |
|---|---|
| T1 | Le texte n'est pas vide. Aucune longueur minimale : un seul mot suffit. |
| T2 | Il est normalisé en Unicode NFC (diacritiques du yoruba, de l'éwé, du fon, etc. sous une forme unique). |
| T3 | Il ne contient plus de balisage : liens `[[ ]]`, modèles `{{ }}`, tableaux, balises HTML, `<ref>`, entités (`&nbsp;`, `&#…;`), mots magiques (`__TOC__`), adresses web, paramètres d'image (`thumb|`, `200px|`…). |
| T4 | Il ne contient pas le caractère de remplacement U+FFFD (texte mal décodé). |
| T5 | Il ne commence ni ne finit par des espaces. |
| T6 | Le même texte n'apparaît pas deux fois dans un fichier. |

Le nettoyage fait respecter ces règles sans écarter de texte : les restes de balisage laissés par une source mal écrite (crochets ou accolades non fermés, HTML collé, `&amp;`) et le caractère U+FFFD sont retirés, `{{PAGENAME}}` est remplacé par le titre de la page. Quand le wikitexte ne donne aucun texte (page faite uniquement de modèles, comme la page d'accueil ou les pages d'années en amharique), le texte est lu dans la page affichée (API MediaWiki `action=parse`), avec les mêmes règles.

**Seuls les doublons exacts sont écartés** (règle T6 : le premier texte est gardé). Une page sans aucun texte, même affichée, ne peut pas entrer dans le corpus. Chaque page écartée est inscrite dans un **journal d'exclusions**, avec la raison.

## 4. Hors du corpus : deux fichiers internes

Le corpus reste du texte pur. Deux fichiers **séparés**, jamais mélangés au texte, servent au suivi du projet :

| Fichier | Contenu | Pourquoi |
|---|---|---|
| `data/processed/provenance.jsonl` (registre interne) | Pour chaque texte : `text_sha256`, `language_code`, `source_url`, `source_dump`, `revision_date`, `collected_at`, `license`, `quality_flags` | KPI « 100 % des records avec provenance » ; la licence CC BY-SA de Wikipédia impose de pouvoir citer la source si le corpus est redistribué ; rapport qualité (tâches 7 et 9) |
| journal d'exclusions | Pour chaque article écarté : langue, `source_url`, raison | KPI « couverture complète ou exclusions justifiées » |

Le registre est relié au corpus par **l'empreinte SHA-256 du texte**, calculée sur le texte lui-même. Aucun identifiant n'est donc ajouté au corpus. Schéma du registre : `config/schema_provenance.json`.

`quality_flags` (vocabulaire fermé, signalement seulement, détection à la tâche 7) : `near_duplicate`, `boilerplate`, `language_mismatch`, `template_generated`. Aucun drapeau ne porte sur la longueur. Le doublon exact n'est pas un drapeau : il exclut le texte du corpus. Le balisage restant et le caractère U+FFFD sont retirés au nettoyage.

## 5. Ce que devient le schéma recommandé

| Champ recommandé | Devenir |
|---|---|
| `clean_text` | **Seul contenu du corpus**, sous le nom `text` |
| `language_code` | Nom du fichier du corpus (et dans le registre) |
| `title`, `categories` | Supprimés |
| `page_id`, `revision_id`, `document_id` | Supprimés : `source_url` (lien permanent vers la révision, `?oldid=`) suffit pour retrouver la page et la révision ; le lien texte → source se fait par l'empreinte SHA-256 |
| `source`, `source_url`, `revision_date`, `license` | Registre interne |
| `quality_flags` | Registre interne |
| `raw_content` | Hors corpus : les dumps bruts restent, non modifiés, dans `data/raw/` (tâche 5) |
| `split` | Pas un champ : à la tâche 10, chaque split devient un dossier (`corpus/train/<code>.jsonl`, etc.) |
| ajoutés au registre | `source_dump` (de quel dump vient le texte) et `collected_at` (date d'extraction) |

## 6. Test sur de vrais articles

Cinq articles tirés au hasard le 24/10/2026 (wolof, haoussa, amharique, yoruba, n'ko). L'exactitude de la copie du wikitexte a été vérifiée par son empreinte SHA-1 : elle est identique à celle de Wikipédia pour les 5 articles.

| Contrôle | Résultat |
|---|---|
| Textes valides dans le corpus d'exemple | 4 / 4 (wolof, haoussa, yoruba, n'ko) |
| Pages exclues et journalisées | 2 : amharique (texte vide après extraction) et page d'accueil de la Wikipédia en éwé |
| Textes avec provenance dans le registre | 4 / 4 |
| Article avec galerie, tableau, note et catégories (haoussa « Kazakistan ») : galerie, photos, note et catégories retirées, texte des cellules gardé, aucune règle enfreinte | conforme |
| Cas invalides rejetés (16) : champ en plus du texte (ex. titre), texte vide, non NFC, U+FFFD, restes de lien, de modèle, de paramètre d'image, entité HTML, balise `<ref>`, espaces en début, doublon, fichier hors périmètre, texte sans provenance, licence invalide dans le registre, provenance d'une autre langue, champ `text` absent | 16 / 16 |

**Constats, à traiter aux tâches 5 et 6**

| Constat | Exemple | Conséquence |
|---|---|---|
| Une extraction simple laisse les paramètres d'image, les catégories et les liens interlangues dans le texte, car leurs noms sont traduits dans chaque wiki | wolof `Dencukaay:`, n'ko `ߞߐߕߐ߮:` → `right|200px|thumb|…` | Corrigé dans `src/extraire_texte.py` (noms lus avec l'API `siteinfo`). La règle T3 détecte ce défaut. |
| Un article produit entièrement par des modèles donne un texte vide | amharique `792 እ.ኤ.አ.` : 0 mot extrait du wikitexte, 22 mots dans la page affichée (en anglais) | Décidé à la tâche 4 : wikitexte, et page affichée quand le wikitexte ne donne rien |
| Les sections « liens externes » et « références » contiennent souvent une autre langue | wolof : titres d'ouvrages en anglais | À décider à la tâche 6 : garder ou retirer ces sections |
| Espaces insécables (U+00A0) | haoussa : 14 occurrences | À traiter à la normalisation (tâche 6) |

## 7. Baseline

Le baseline est la référence fixe à laquelle chaque run du pipeline est comparé.

| Élément | Baseline |
|---|---|
| Données | Dumps `pages-articles` du 1er octobre 2026 (`20261001`), disponibles pour les 69 éditions (tâche 1) |
| Périmètre | 67 éditions incluses + `ar` (à décider) + `fr` (référence) : `config/config.yaml` |
| Comptes de référence | Run d'inventaire `20261023T132917Z` : articles et mots par édition (`data/processed/inventaire/20261023T132917Z/inventaire_synthese.json`) |
| Extraction de référence | `src/extraire_texte.py` : wikitexte → texte selon les règles de la section 2 (noms d'espaces de noms propres à chaque wiki, lus avec l'API `siteinfo`), puis Unicode NFC |
| Métriques de référence (calculées à la tâche 4) | Par langue : nombre de textes, nombre d'exclusions par raison, total de mots, médiane de mots par texte |

Les améliorations des tâches 5 à 7 se mesurent par rapport à ces métriques.

## 8. Critères d'acceptation

### Par texte (vérifiés automatiquement par `src/valider_records.py`)

| # | Critère | Cible |
|---|---|---|
| A1 | Chaque ligne est conforme au schéma : un seul champ, `text` | 100 % |
| A2 | Chaque texte respecte les règles T1 à T6 | 100 % |
| A3 | Chaque fichier porte le code d'une langue du périmètre | 100 % |
| A4 | Chaque texte a son entrée dans le registre, avec la même langue | 100 % |
| A5 | Chaque entrée du registre correspond à un texte du corpus | 100 % |

### Par corpus

| # | Critère | Cible | Vérifié à |
|---|---|---|---|
| C1 | Chaque langue du périmètre a un fichier de corpus, ou une exclusion justifiée | 69 / 69 | tâches 4-5 |
| C2 | Chaque page de l'espace principal (hors redirection) du dump est soit dans le corpus, soit dans le journal d'exclusions avec sa raison | 100 % des pages | tâche 5 |
| C3 | Fichiers JSONL validés par les tests de schéma, avec fichiers d'exemple | 100 % | tâche 8 |
| C4 | Rapport qualité produit pour chaque langue | 69 / 69 | tâche 9 |
| C5 | Deux runs sur le même dump produisent des fichiers identiques | identiques | tâche 11 |

### Décisions prises

| Point | Décision |
|---|---|
| Éléments retirés de la page | voir section 2 (captures de la page d'accueil éwé) |
| Liens | texte du lien gardé, adresse retirée |
| Tableaux | texte de chaque cellule gardé, une cellule par ligne |
| Titres de section | gardés dans le texte |
| Longueur minimale | aucune : tout texte non vide est pris |
| Pages écartées | uniquement les doublons exacts (et les pages sans aucun texte) |
| Page d'accueil | gardée, sans logo, bannière, boutons ni images |
| Source du texte | wikitexte ; page affichée (API) quand le wikitexte ne donne rien |

### Décisions à prendre

| Point | Statut |
|---|---|
| Seuils des quasi-doublons et de l'identification de langue | tâche 7 |
| Garder ou retirer les sections « références » / « liens externes » | tâche 6 |
| Inclusion de l'arabe standard (`ar`) | à décider |

## 9. Utilisation

```bash
pip install -r requirements.txt
python src/valider_records.py docs/exemples/corpus docs/exemples/provenance.jsonl
# → 4 fichier(s), 4/4 lignes valides, 4/4 avec provenance
```
