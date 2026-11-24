# Liste des tâches (ordre strict, validation avant chaque étape)

Périmètre : toutes les langues africaines disposant d'une édition Wikipédia + français (comparaison).

| # | Tâche | Statut |
|---|---|---|
| 1 | Inventorier toutes les langues africaines présentes sur Wikipédia et leurs ressources Wikimedia | Terminée (reports/Rapport_inventaire_tache1.docx) |
| 2 | Définir le schéma de document et les critères d'acceptation | Terminée (reports/Rapport_tache2_schema.docx) |
| 3 | Construire le registre des sources et licences | Terminée (reports/Rapport_tache3_sources_licences.docx) |
| 4 | Réaliser un premier run de collecte (prototype, checkpoint S2) | Terminée (reports/Rapport_tache4_prototype_S2.docx) ; décisions S2 : arabe standard gardé, français en échantillon |
| 5 | Construire le pipeline d'extraction reproductible | Terminée (src/collecter.py, run de référence collecte/20261105T065025Z) |
| 6 | Normaliser le texte en préservant les spécificités linguistiques | Terminée (reports/Rapport_tache6_normalisation.docx ; src/normaliser.py, run de référence normalise/20261113T141439Z : 3 948 957 textes, contrôles OK, contrôle complet 0 erreur) |
| 7 | Détecter les doublons et exécuter les contrôles qualité | Terminée (reports/Rapport_tache7_controle_qualite.docx ; src/controle_qualite.py v0.7, run de référence qualite/analyses/20261122T070453Z_ecriture_drapeaux : 965 559/3 948 957 textes avec au moins un quality_flags, 4 erreurs de collecte à exclure à l'export) |
| 8 | Exporter en JSONL/Parquet et valider le schéma | Terminée (src/exporter.py ; run de référence qualite/analyses/20261122T081323Z_export : 3 948 957 → 3 948 953 textes exportés, 4 exclusions justifiées, 0 anomalie) |
| 9 | Produire le rapport qualité et les statistiques par langue | Terminée (reports/Rapport_tache9_qualite_statistiques.docx ; src/rapport_qualite.py ; couverture 65/68 langues, 95,6 % — aa/hz/kr justifiées) |
| 10 | Documenter les splits train/validation/test sans fuite | Terminée (reports/Rapport_tache10_splits.docx ; src/split.py ; run de référence qualite/analyses/20261123T212903Z_split : 80/10/10 par langue, 3 944 groupes anti-fuite/11 096 textes, 2 langues sous le seuil (ak, kj) tout en train, 0 anomalie) |
| 11 | Rédiger le README et faire valider la reproductibilité | À faire |

## Points ouverts (tâche 1)
- Signification de « EV » (Ewe ? autre langue ? environnement de test ?).
- Périmètre : toutes les langues africaines disposant d'une édition Wikipédia (pas seulement les 6 langues initiales) ; exclusions éventuelles justifiées.

## Difficultés
Voir reports/Difficultes_rencontrees.docx (mis à jour à chaque tâche).
