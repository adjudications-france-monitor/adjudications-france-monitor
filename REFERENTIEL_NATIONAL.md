# Référentiel national des greffes et avocats

## Comptage et périmètre

La base de référence collectée le **30 septembre 2026** contient :

- **1 053 fiches de juridictions et contacts** dans `repertoire_greffes.csv`, dont 157 tribunaux judiciaires. Le reste comprend notamment des tribunaux de proximité, prud’hommes, tribunaux de commerce et activités économiques, juridictions administratives, greffes des associations et services spécialisés.
- **1 690 fiches d’avocats ou praticiens** dans `repertoire_avocats.csv`. La base marque **706 références prioritaires** (annonceurs ou ventes référencés) et 984 profils de praticiens dont la publication reste à vérifier.
- **3 536 références URL**, soit **1 975 adresses distinctes**, conservées dans les URL principales et secondaires des deux CSV.

## Ce qui est réellement surveillé

`sources.csv` est le fichier effectivement lu par le moteur. Il contient actuellement **41 lignes, dont 38 actives** : 9 pages de plateformes, 6 pages de journaux et 23 pages classées avocat; les 3 autres lignes restent désactivées. Vingt pages propres à des sites de cabinets proviennent de la base nationale; deux figuraient déjà dans la configuration avant la restauration, les 18 autres ont été ajoutées. Trois autres pages d’avocats issues de la sélection antérieure restent aussi actives.

Les 1 053 fiches de juridictions restent un répertoire de contacts et d’orientation. Elles ne sont pas chargées comme 1 053 pages de veille d’annonces : leurs URL officielles sont des fiches d’annuaire, pas des calendriers de ventes. Les 1 690 fiches d’avocats ne correspondent pas chacune à un site indépendant. Plusieurs renvoient à des profils sur les plateformes Licitor, Info-Enchères, Enchères Publiques ou Immo Enchère. Une fiche de praticien ou une mention d’annonceur ne prouve pas qu’une vente soit en cours.

## Fraîcheur et limites

La date de collecte du référentiel est le 30 septembre 2026. Les références sont conservées avec leur statut et leurs URL; la collecte du moteur confirme l’accessibilité des pages au moment de chaque passage. Une adresse dans le catalogue ne garantit ni son fonctionnement actuel, ni la publication d’une vente. Les résultats des collectes et incidents d’accès figurent dans `rapports/dernier.json` et `rapports/sources.csv`.
