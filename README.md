# Adjudications France Monitor

Première base technique du projet. `sources.csv` contient désormais 23 pages vérifiées le 7 octobre 2026, dont 20 actives et 3 désactivées. Voir [SOURCES.md](SOURCES.md) pour le détail des essais et des réserves. Cette base ne constitue pas encore une veille nationale exhaustive ou planifiée.

## Ce que fait cette version

- Lit les pages web publiques indiquées dans `sources.csv`.
- Repère les liens de fiches, de publications et d'audiences à l'aide de règles adaptées aux sources connues. Pour les autres sources, utilise des mots-clés.
- Lit les cartes `data-link` d'Avoventes sans exécuter de JavaScript et accepte des pages HTML jusqu'à 10 Mo.
- Enregistre les résultats dans `data/annonces.jsonl` et les erreurs de consultation dans `data/erreurs.jsonl`.
- Évite de réenregistrer deux fois le même lien.
- Signale les erreurs de consultation et termine avec un code de sortie non nul lorsqu'une source échoue.

## Limites

Cette première version ne consulte pas les sites nécessitant une connexion, un CAPTCHA ou l'exécution de JavaScript. Certains descriptifs et résultats de Vench sont réservés aux abonnés. Elle ne vérifie pas encore les dates d'adjudication, l'ouverture du délai de surenchère, l'occupation du bien, les frais ni la rentabilité. Elle ne parcourt pas automatiquement toutes les pages de résultats ni les fiches liées. Les audiences peuvent regrouper plusieurs biens et des annonces anciennes peuvent être collectées. Chaque résultat garde le statut `à vérifier manuellement`.

La déduplication porte sur l'URL: un même bien publié sur deux sites peut encore apparaître deux fois. Les modifications d'une fiche déjà enregistrée ne sont pas suivies. Les règles de liens doivent être ajustées lorsque les éditeurs changent leurs pages. Aucun envoi de courriel ni notification n'est effectué par cette version.

## Ajouter une source

Dans `sources.csv`, ajouter une ligne avec :

`nom, categorie, url, active`

Mettre `1` dans `active` pour lancer la consultation et `0` pour conserver une source sans la consulter. N'ajouter que des pages publiques et autorisées à être consultées automatiquement. Vérifier que les résultats correspondent à des fiches ou audiences, et pas seulement à des menus. Une réponse HTTP 200 ne garantit pas qu'une page fournit des annonces exploitables.

## Lancer un essai

Sur un ordinateur avec Python 3.10 ou plus récent :

```bash
python monitor.py
```

Le script ne s'exécute pas automatiquement en arrière-plan. La planification quotidienne ou horaire sera configurée après validation des sources et du fuseau horaire.
