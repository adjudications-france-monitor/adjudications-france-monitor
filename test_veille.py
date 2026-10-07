"""Régressions métier, extraction et persistance; aucun réseau pendant les tests."""
import argparse
import contextlib
import io
import json
import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from qualification import amount, classify, economics, french_date, occupancy, works
from veille import Document, facts_from_text, hearing_records, parse_detail, run, seed_records

NOW = datetime(2026, 10, 7, 12, tzinfo=timezone.utc)
URL = "https://avoventes.fr/enchere/appartement-exemple"
SOURCE = {"nom": "Exemple", "url": "https://avoventes.fr/recherche/", "categorie": "plateforme"}
LIST = '''<div data-link="https://avoventes.fr/enchere/appartement-exemple">
<div class="font-bold text-16">Appartement exemple</div>12 rue Exemple, 75000 Paris
Mise à prix : 10 000,00 € Adjugé : 50 000,00 € Surenchère possible jusqu'au 16 octobre 2026
Date de la vente : mardi 06 octobre 2026 à 14h00</div>'''
DETAIL = '''<div id="content"><h1>Appartement exemple</h1>Mise à prix : 10 000,00 €
Adjudication : 50 000,00 euros Surenchère possible jusqu'au : 16 octobre 2026
Vente 06 octobre 2026 à 14h00 À propos du bien : Appartement de 45,10 m². Le bien est libre de toute occupation.
Cadastre Données des valeurs foncières : autre bien occupé et vendu pour 999 999 €
Documents <a href="/public/document.pdf">CCV</a></div>'''


def valuation():
    return {"date_verification": "2026-10-07", "source_occupation": "https://exemple.fr/pv.pdf",
            "occupation": "libre", "source_etat": "https://exemple.fr/diagnostics.pdf", "etat_sans_travaux": "oui",
            "source_delai": "https://exemple.fr/avocat", "surenchere_confirmee": "oui",
            "source_valeur": "https://exemple.fr/comparables", "source_frais": "https://exemple.fr/decompte",
            "revente_prudente_eur": "150000", "frais_acquisition_fixes_eur": "10000", "taux_frais_acquisition": "0.10",
            "frais_revente_eur": "5000", "frais_portage_eur": "2000"}


def record():
    return {"faits": {"date_vente": "2026-10-06", "prix_adjuge_eur": "50000", "occupation": "libre",
                      "date_limite_surenchere": "2026-10-16", "travaux": "a_verifier"}, "detail_lu_ce_passage": True}


class MoneyAndDateTests(unittest.TestCase):
    def test_french_dates_and_months(self):
        for text, expected in [("1er janvier 2026", "2026-01-01"), ("6 février 2026", "2026-02-06"),
                               ("06-10-2026", "2026-10-06"), ("23/09/26", "2026-09-23")]:
            self.assertEqual(french_date(text).isoformat(), expected)
        self.assertIsNone(french_date("31 février 2026"))

    def test_currency_and_missing(self):
        self.assertEqual(amount("24 636,48 €"), Decimal("24636.48"))
        self.assertEqual(amount("11 587.00 €"), Decimal("11587.00"))
        self.assertEqual(amount("1.100,00"), Decimal("1100.00"))
        self.assertEqual(amount("0"), Decimal(0))
        self.assertIsNone(amount("Non indiqué"))
        self.assertIsNone(amount("-100"))

    def test_finance_includes_all_costs_and_stress(self):
        result = economics(Decimal("50000"), valuation())
        self.assertEqual(result["surenchere_minimale_eur"], "55000.00")
        self.assertEqual(result["cout_total_au_minimum_eur"], "77500.00")
        self.assertEqual(result["benefice_au_minimum_eur"], "72500.00")
        self.assertEqual(result["benefice_stresse_eur"], "57500.00")
        cap = Decimal(result["plafond_marteau_eur"])
        cost = cap * Decimal("1.10") + Decimal(17000)
        self.assertGreaterEqual((Decimal(150000) - cost) / cost, Decimal("0.40"))
        next_cost = (cap + Decimal("0.01")) * Decimal("1.10") + Decimal(17000)
        self.assertLess((Decimal(150000) - next_cost) / next_cost, Decimal("0.40"))
        incomplete = valuation()
        incomplete["frais_portage_eur"] = ""
        self.assertIn("frais_portage_eur", economics(Decimal(50000), incomplete)["donnees_manquantes"])


class ClassificationTests(unittest.TestCase):
    def test_no_automatic_selection_without_documented_valuation(self):
        self.assertEqual(classify(record(), NOW)["statut"], "a_verifier")
        self.assertEqual(classify(record(), NOW, valuation())["statut"], "retenu_sur_donnees_validees")

    def test_occupancy_negation_future_and_contradiction(self):
        self.assertEqual(occupancy("Le bien est libre de toute occupation")[0], "libre")
        self.assertEqual(occupancy("Le bien est occupé")[0], "occupe")
        self.assertEqual(occupancy("Loué depuis le 01.09.2024")[0], "loue")
        self.assertEqual(occupancy("Le bien sera libre après la vente")[0], "a_verifier")
        self.assertEqual(occupancy("Le bien n'est pas libre de toute occupation")[0], "a_verifier")
        self.assertEqual(occupancy("Libre de toute occupation. Les biens sont occupés.")[0], "contradictoire")
        self.assertEqual(occupancy("Filtres Libre Occupé Loué")[0], "inconnue")

    def test_occupied_and_works_cannot_be_overridden(self):
        for field, value in [("occupation", "occupe"), ("travaux", "necessaires")]:
            row = record()
            row["faits"][field] = value
            self.assertEqual(classify(row, NOW, valuation())["statut"], "rejete")
        self.assertEqual(works("Maison en cours de construction")[0], "necessaires")

    def test_window_uses_paris_not_utc_and_includes_j4(self):
        row = record()
        row["faits"]["date_vente"] = "2026-10-03"
        self.assertEqual(classify(row, NOW)["jours_apres_audience"], 4)
        row["faits"]["date_vente"] = "2026-10-07"
        late_utc = datetime(2026, 10, 7, 23, tzinfo=timezone.utc)
        self.assertEqual(classify(row, late_utc)["jours_apres_audience"], 1)
        self.assertEqual(classify(row, NOW)["statut"], "rejete")

    def test_expired_deadline_second_auction_and_excess_price(self):
        for field, value in [("date_limite_surenchere", "2026-10-06"), ("seconde_adjudication", True),
                             ("prix_adjuge_eur", "200000")]:
            row = record()
            row["faits"][field] = value
            self.assertEqual(classify(row, NOW, valuation())["statut"], "rejete")

    def test_stale_validation_and_unread_detail(self):
        val = valuation()
        val["date_verification"] = "2026-09-29"
        self.assertEqual(classify(record(), NOW, val)["statut"], "a_verifier")
        row = record()
        row["detail_lu_ce_passage"] = False
        self.assertEqual(classify(row, NOW, valuation())["statut"], "a_verifier")


class ParserAndStateTests(unittest.TestCase):
    def test_scripts_forms_and_filters_excluded(self):
        doc = Document('<script>Bien libre</script><form>Occupation : Libre</form><main>Réel</main>')
        self.assertEqual(doc.root.text(), "Réel")

    def test_avoventes_card_and_detail_exclude_other_property_dvf(self):
        rows, _ = seed_records(LIST, SOURCE["url"], SOURCE, NOW.isoformat())
        self.assertEqual(rows[URL]["faits"]["prix_adjuge_eur"], "50000.00")
        self.assertEqual(rows[URL]["faits"]["date_vente"], "2026-10-06")
        detail = parse_detail(DETAIL, URL, rows[URL])
        self.assertEqual(detail["faits"]["occupation"], "libre")
        self.assertEqual(detail["faits"]["prix_adjuge_eur"], "50000.00")
        self.assertEqual(detail["documents"], ["https://avoventes.fr/public/document.pdf"])

    def test_starting_price_is_never_hammer_result(self):
        facts = facts_from_text("Mise à prix : 70 000 € Adjugé : Non indiqué Vente 06 octobre 2026", "avoventes.fr", detail=True)
        self.assertEqual(facts["mise_a_prix_eur"], "70000")
        self.assertNotIn("prix_adjuge_eur", facts)

    def test_sale_variants_withdrawal_and_non_judicial_sale(self):
        for label in ["Vente sur liquidation judiciaire", "Vente sur licitation", "Vente sur saisie immobilière"]:
            facts = facts_from_text(label + " mardi 6 octobre 2026 à 14h", "licitor.com", detail=True)
            self.assertEqual(facts["date_vente"], "2026-10-06")
        rows, _ = seed_records(LIST, SOURCE["url"], SOURCE, NOW.isoformat())
        cancelled = DETAIL.replace("Vente 06 octobre 2026 à 14h00", "Vente non requise")
        detail = parse_detail(cancelled, URL, rows[URL])
        self.assertTrue(detail["faits"]["retiree"])
        self.assertEqual(classify(detail, NOW)["statut"], "rejete")
        amiable = DETAIL.replace("Vente 06 octobre 2026 à 14h00", "Vente amiable")
        detail = parse_detail(amiable, URL, {**rows[URL], "faits": {}})
        self.assertTrue(detail["faits"]["vente_amiable"])

    def test_pdf_links_are_queued_without_a_parser_error(self):
        from veille import is_pdf_url
        self.assertTrue(is_pdf_url("https://www.7jours.fr/annonces-legales/exemple/?justify=1"))
        self.assertTrue(is_pdf_url("https://exemple.fr/pv.pdf"))
        self.assertFalse(is_pdf_url(URL))

    def test_hearing_includes_date_result_and_next_page(self):
        markup = '<article id="hearings-list"><h1>TJ Exemple Mardi 6 octobre 2026 à 14h</h1><a href="/annonce/123.html">Appartement 06-10-2026 : 19 000 €</a><nav><a href="?p=2">Suivant</a></nav></article>'
        page = "https://www.licitor.com/ventes-judiciaires-immobilieres/tj-exemple/mardi-6-octobre-2026.html"
        rows, next_pages = hearing_records(markup, page, SOURCE, NOW.isoformat())
        self.assertEqual(next(iter(rows.values()))["faits"]["prix_adjuge_eur"], "19000")
        self.assertEqual(next(iter(rows.values()))["faits"]["date_vente"], "2026-10-06")
        self.assertEqual(next_pages, [page + "?p=2"])

    def test_repeat_run_is_idempotent_and_refreshes_details(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "listing.html").write_text(LIST)
            (root / "detail.html").write_text(DETAIL)
            mapping = {SOURCE["url"]: str(root / "listing.html"), URL: str(root / "detail.html")}
            (root / "mapping.json").write_text(json.dumps(mapping))
            args = argparse.Namespace(now=NOW.isoformat(), fixtures=str(root / "mapping.json"), pause=0,
                                     state=str(root / "etat.json"), output=str(root / "rapports"), catalogue=str(root / "catalogue.json"),
                                     valuations=str(root / "estimations.csv"), max_hearings=5, max_details=5)
            with patch("veille.load_sources", return_value=[SOURCE]), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(run(args), 0)
                self.assertEqual(run(args), 0)
                report = json.loads((root / "rapports/dernier.json").read_text())
                self.assertEqual(report["comptages"]["nouveaux_liens"], 0)
                self.assertEqual(report["comptages"]["fiches_modifiees"], 0)
                (root / "detail.html").write_text(DETAIL.replace("50 000,00 euros", "60 000,00 euros"))
                self.assertEqual(run(args), 0)
            report = json.loads((root / "rapports/dernier.json").read_text())
            self.assertEqual(report["comptages"]["fiches_modifiees"], 1)
            self.assertEqual(report["dossiers"][0]["faits"]["prix_adjuge_eur"], "60000.00")

    def test_fetch_failure_still_writes_partial_report(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "mapping.json").write_text("{}")
            args = argparse.Namespace(now=NOW.isoformat(), fixtures=str(root / "mapping.json"), pause=0,
                                     state=str(root / "etat.json"), output=str(root / "rapports"), catalogue=str(root / "catalogue.json"),
                                     valuations=str(root / "estimations.csv"), max_hearings=0, max_details=0)
            with patch("veille.load_sources", return_value=[SOURCE]), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(run(args), 1)
            report = json.loads((root / "rapports/dernier.json").read_text())
            self.assertEqual(report["sources"][0]["statut"], "echec")
            self.assertTrue((root / "rapports/dernier.md").exists())


if __name__ == "__main__":
    unittest.main()
