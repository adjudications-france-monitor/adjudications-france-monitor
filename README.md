# Adjudications France Monitor

Première base technique du projet. Ce dépôt ne constitue pas encore une veille nationale opérationnelle : il faut d'abord renseigner et vérifier les sources dans `sources.csv`.

## Ce que fait cette version

- Lit les pages web publiques indiquées dans `sources.csv`.
- Repère les liens dont le texte ou l'adresse contient des termes liés aux ventes judiciaires.
- Enregistre les résultats dans `data/annonces.jsonl` et les erreurs de consultation dans `data/erreurs.jsonl`.
- Évite de réenregistrer deux fois le même lien.

## Limites

Cette première version ne consulte pas les sites nécessitant une connexion, un CAPTCHA ou l'exécution de JavaScript. Elle ne vérifie pas encore les dates d'adjudication, l'ouverture du délai de surenchère, l'occupation du bien, les frais ni la rentabilité. Ces contrôles nécessitent les annonces et dossiers complets et seront ajoutés après validation des sources.

## Ajouter une source

Dans `sources.csv`, ajouter une ligne avec :

`nom, categorie, url, active`

Mettre `1` dans `active` pour lancer la consultation. N'ajouter que des pages publiques et autorisées à être consultées automatiquement. Commencer par une seule source, vérifier le résultat, puis élargir.

## Lancer un essai

Sur un ordinateur avec Python 3.10 ou plus récent :

```bash
python monitor.py
```

Le script ne s'exécute pas automatiquement en arrière-plan. La planification quotidienne ou horaire sera configurée après validation des sources et du fuseau horaire.
