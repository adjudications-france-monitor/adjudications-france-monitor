# Adjudications France — moteur de veille

Surveillance des pages publiques déclarées dans `sources.csv`, lecture bornée des fiches, filtres prudents et rapport actualisé sur GitHub.

## Exécution automatique

Le workflow **Veille adjudications France** s'exécute chaque jour à **09 h, 10 h, …, 18 h, heure de Paris**, avec le fuseau `Europe/Paris` et ses changements d'heure. GitHub peut décaler une exécution programmée. Un passage peut aussi être lancé depuis **Actions → Veille adjudications France → Run workflow**.

Les changements du code, des sources, des estimations ou du workflow déclenchent les tests et une collecte. Les commits du rapport ne relancent pas la collecte.

- [Dernier rapport lisible](rapports/dernier.md)
- [Résultats structurés](rapports/dernier.json)
- [Tableau CSV](rapports/dernier.csv)
- [Journal des pages sources](rapports/sources.csv)
- [Exécutions et archives](https://github.com/adjudications-france-monitor/adjudications-france-monitor/actions)

Chaque exécution conserve le rapport, le catalogue de liens et l'état dans un artefact pendant 30 jours. Un cache permet de suivre les nouveautés et les modifications entre passages. Si le cache disparaît, la collecte et les filtres fonctionnent toujours ; le compteur de nouveautés repart de zéro.

Les tâches ChatGPT existantes de surveillance horaire et de bilan quotidien peuvent lire `rapports/dernier.json` et `rapports/dernier.md`. Le workflow ne constitue pas à lui seul une notification ChatGPT.

## Ce que le moteur vérifie

1. Il consulte les **20 pages actives** parmi les **23 pages configurées**. La liste et les limites de chaque source sont documentées dans [SOURCES.md](SOURCES.md).
2. Il détecte des liens d'audiences et de fiches. Les cartes Avoventes incluent date, prix publié et échéance lorsqu'ils sont affichés.
3. Il ouvre les audiences Licitor entre **J+1 et J+4** et leur pagination, dans la limite de **40 pages d'audience** par passage.
4. Il lit jusqu'à **120 fiches**, avec priorité aux dates récentes. Les fiches sans date identifiée sont explorées par rotation. Les requêtes sont limitées à quatre traitements concurrents, avec au moins 0,8 seconde entre départs de requêtes d'un même site.
5. Il extrait les mentions explicites de prix adjugé, audience, échéance, occupation et travaux. Les mises à prix, prix DVF de biens voisins et dates de visite ne remplacent pas ces données.
6. Il publie un rapport même si des sources échouent. Une couverture partielle est signalée par un avertissement visible et le code de collecte 2. Une erreur d'extracteur ou l'absence de toute source exploitable entraîne un échec du workflow (code 1).
7. Il consulte jusqu'à **12 PDF**, traite jusqu'à **80 pages** dont **6 par OCR**, avec **8 pages au plus par document et par passage**. La lecture reprend les pages restantes aux passages suivants. Le contenu du PDF est identifié par son empreinte : une modification remet sa lecture à zéro. Les pièces des fiches récentes sont prioritaires, puis la file tourne selon la dernière tentative.

Les indices PDF comportent **URL, numéro de page, méthode texte/OCR et extrait ciblé**. Ils sont conservés séparément des faits de l'annonce : plusieurs biens, dates ou prix peuvent figurer dans une pièce. Le rapport affiche les contradictions, pages restantes et erreurs dans ses données structurées. Une extraction ne confirme ni l'attribution d'une mention au bien, ni son actualité, ni une condition juridique. Les publications restent de **niveau C, à contrôler**. L'absence de mention de travaux ne suffit pas à confirmer une revente sans travaux.

## Délais et refus d'accès

Une réponse **429** arrête les requêtes suivantes du même site et respecte `Retry-After`, en secondes ou en date HTTP. Sans délai indiqué, le moteur diffère ce site d'une heure. Les refus **403** sont différés au moins six heures. Les erreurs serveur **5xx** ont un délai croissant, ou le délai indiqué par le serveur. Les autres sites continuent à être consultés.

Ces délais sont conservés dans l'état et restaurés au passage suivant. Le rapport distingue erreurs reçues et accès différés, avec l'heure du prochain essai. Le moteur ne contourne pas les refus. Les données antérieures ne sont pas présentées comme une nouvelle lecture réussie.

## Critères de sélection

- Audience entre **J+1 et J+4**, en jours calendaires et selon la date en France.
- Bien libre avec preuve ; un bien occupé, loué ou contradictoire est rejeté.
- Revente **en l'état, sans travaux** : toute nécessité explicite de travaux entraîne un rejet.
- Prix adjugé publié ; une vente retirée, reportée ou non requise est rejetée.
- Surenchère encore recevable et confirmée par les pièces et l'avocat. Une date annoncée est un indice à vérifier. Une vente déjà sur surenchère est écartée de ce scénario.
- Valeur prudente et ensemble des frais documentés ; marge minimale de **40 % sur tous les coûts engagés**, avant fiscalité du bénéfice.

Les informations manquantes produisent le statut `a_verifier`, jamais une opportunité confirmée. Le statut `retenu_sur_donnees_validees` suppose une validation documentée récente renseignée dans `estimations.csv` et une fiche relue lors du passage. Ce statut n'autorise aucune enchère automatiquement.

Le moteur n'invente pas une date de clôture à J+10. Les prorogations, jours fériés, règles locales et recevabilité restent à contrôler : [R322-50 à R322-55](https://www.legifrance.gouv.fr/codes/id/LEGISCTA000025939177), [article 642 du CPC](https://www.legifrance.gouv.fr/codes/article_lc/LEGIARTI000006411003).

## Calcul du plafond au marteau

Soit `R` la valeur prudente de revente en l'état, `F` les frais fixes d'acquisition, `t` le taux de frais proportionnels, `V` les frais de revente et `P` le portage.

```
Coût engagé = marteau × (1 + t) + F + V + P
Bénéfice prévisionnel avant fiscalité = R − coût engagé
Marge = bénéfice / coût engagé
Plafond marteau = max(0, (R / 1,40 − F − V − P) / (1 + t))
Plancher de surenchère = prix adjugé × 1,10
```

Le plafond est arrondi au centime inférieur. Si le plancher dépasse le plafond, le bien est rejeté pour le seuil de 40 %. Le rapport expose les frais et le montant acte en mains au plafond, le bénéfice correspondant et un scénario de revente diminuée de 10 %. Les calculs au plancher sont également conservés dans le JSON.

L'en-tête de `estimations.csv` décrit les champs à renseigner : URL du bien, date de vérification, occupation, preuve d'état sans travaux, confirmation de la surenchère, valeur prudente, frais et leurs sources. Les validations doivent dater de **sept jours au plus**. Les montants sont en euros ; `taux_frais_acquisition` est une fraction (`0.08` pour 8 %). Les frais fixes et proportionnels doivent couvrir tous les frais d'acquisition pertinents, sans double compte. Un zéro doit être renseigné explicitement et justifié ; un champ vide reste inconnu.

La valeur et les frais ne sont pas estimés à partir de simples mots-clés. La valeur exige des comparables locaux adaptés à la surface et à l'état, notamment DVF, et une analyse de liquidité. Le délai de revente et l'analyse du marché sont documentés dans les colonnes correspondantes. Le fichier est vide à l'installation tant que ces validations ne sont pas disponibles.

## Installation et test local

Python **3.10 ou plus**, sans bibliothèque Python externe. Pour lire les pièces : **Poppler** (`pdfinfo`, `pdftotext`, `pdftoppm`) et **Tesseract**, avec les langues française et anglaise. Le workflow installe ces outils sur Ubuntu. Un outil absent, un PDF protégé, un document dépassant 10 Mo ou 300 pages reste signalé pour contrôle manuel.

```sh
python -m unittest -v test_veille test_ameliore
python veille.py
```

Le collecteur initial reste disponible avec `python monitor.py`. Le workflow utilise `veille.py`, qui ajoute la lecture des fiches, la qualification et les rapports.

Options utiles :

```sh
python veille.py --max-hearings 40 --max-details 120 --max-documents 12 --max-pdf-pages 80 --max-ocr-pages 6
python veille.py --now 2026-10-07T12:00:00+02:00 --fixtures correspondances.json
```

Le second mode lit un JSON `URL → chemin HTML ou PDF local` pour vérifier les extracteurs sans réseau. Ajouter `--max-documents 0` pour désactiver la lecture des pièces. Les tests couvrent dates françaises, fuseau Paris, prix/frais, marge et stress, occupation contradictoire, travaux, délai expiré, seconde adjudication, données manquantes, mise à prix, isolation des comparables, pagination, erreurs et actualisation de l'état. Les régressions supplémentaires vérifient les délais HTTP, l'arrêt des requêtes déjà en file, la persistance, la reprise des pages, l'OCR différé, les changements de PDF, la provenance et l'absence de validation automatique d'une opportunité.

## Limites de couverture

La liste ne constitue pas un balayage exhaustif des tribunaux, des annonces ou des 706 fiches de la base nationale d'avocats. Seule la pagination des audiences Licitor récentes est parcourue automatiquement. Le rapport affiche les budgets, les lectures réellement effectuées, les fiches encore sans date et les erreurs d'accès.

La déduplication porte sur les URL. Deux publications du même bien sur deux sites peuvent rester distinctes ; elles ne doivent pas être comptées comme deux opportunités indépendantes sans contrôle. `Registre_Adjudications.xlsx` et la base nationale restent les outils de consolidation : ce programme ne les modifie pas.

Le dépôt est public. Il ne doit contenir ni credentials, ni coordonnées privées, ni document confidentiel : uniquement le code, les sources publiques et les résultats publics de la collecte.
