#!/usr/bin/env python3
"""Veille bornée des pages publiques, qualification et rapports auditables."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urldefrag, urljoin, urlparse
from urllib.request import Request, urlopen

from monitor import MAX_BYTES, TIMEOUT_SECONDS, is_candidate_link, load_sources
from qualification import PARIS, amount, classify, french_date, labelled_amount, labelled_date, normalize, occupancy, works

ROOT = Path(__file__).resolve().parent
VOID = set("area base br col embed hr img input link meta param source track wbr".split())
IGNORED = {"script", "style", "nav", "footer", "form", "noscript"}


class Node:
    def __init__(self, tag="root", attrs=None, parent=None):
        self.tag, self.attrs, self.parent = tag, attrs or {}, parent
        self.children: list[Node | str] = []

    def all(self):
        yield self
        for child in self.children:
            if isinstance(child, Node):
                yield from child.all()

    def has_class(self, name):
        return name in self.attrs.get("class", "").split()

    def text(self):
        if self.tag in IGNORED:
            return ""
        return " ".join(" ".join(child.text() if isinstance(child, Node) else child for child in self.children).split())

    def closest(self, predicate):
        node = self
        while node:
            if predicate(node):
                return node
            node = node.parent
        return None


class Document(HTMLParser):
    def __init__(self, markup):
        super().__init__(convert_charrefs=True)
        self.root = Node()
        self.stack = [self.root]
        self.feed(markup)
        self.close()
        self.nodes = list(self.root.all())

    def handle_starttag(self, tag, attrs):
        node = Node(tag, dict(attrs), self.stack[-1])
        self.stack[-1].children.append(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)

    def find(self, predicate):
        return next((node for node in self.nodes if predicate(node)), None)


def canonical(url, base=""):
    parsed = urldefrag(urljoin(base, url.strip()))[0]
    return parsed if urlparse(parsed).scheme in {"http", "https"} else ""


def digest(url):
    return hashlib.sha256(url.encode()).hexdigest()


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def facts_from_text(text, host, *, detail=False):
    normalized = normalize(text)
    facts = {
        "date_vente": labelled_date(text, r"(?:date de la vente|date de l'audience|vente aux encheres publiques|vente|audience)"),
        "date_limite_surenchere": labelled_date(text, r"surenchere possible jusqu'au"),
        "prix_adjuge_eur": labelled_amount(text, r"(?:adjuge|adjudication)"),
        "mise_a_prix_eur": labelled_amount(text, r"mise a prix(?: initiale)?"),
        "frais_prealables_publies_eur": labelled_amount(text, r"frais pre(?:a)?lables"),
        "occupation": "inconnue", "travaux": "a_verifier",
        "retiree": bool(re.search(r"\b(?:retiree|non requise|vente reportee|vente annulee)\b", normalized)),
        "seconde_adjudication": bool(re.search(r"\b(?:vente sur surenchere|adjudication sur surenchere|seconde adjudication)\b", normalized)),
        "surenchere_impossible": "aucune surenchere possible" in normalized or "surenchere impossible" in normalized,
    }
    surface = re.search(r"\b(\d+(?:[.,]\d+)?)\s*m[²2]\b", text)
    if surface:
        facts["surface_m2"] = surface.group(1).replace(",", ".")
    if detail:
        facts["occupation"], facts["preuve_occupation"] = occupancy(text)
        facts["travaux"], facts["preuve_travaux"] = works(text)
    # Ne pas prendre les prix de DVF, les mises à prix ou la date de publication
    # pour le prix/date de l'adjudication du dossier.
    return {key: value for key, value in facts.items() if value is not None}


def seed_records(markup, page_url, source, timestamp):
    doc = Document(markup)
    host = (urlparse(page_url).hostname or "").removeprefix("www.")
    records = {}
    for node in doc.nodes:
        href = node.attrs.get("data-link") or (node.attrs.get("href") if node.tag == "a" else "")
        url = canonical(href or "", page_url)
        if not url or not is_candidate_link(page_url, url, node.text()):
            continue
        card = node if node.attrs.get("data-link") else node.closest(
            lambda x: x.has_class("featured-item") or x.tag == "article" or x.tag == "li"
        ) or node
        text = card.text()
        title = node.text() or urlparse(url).path.rsplit("/", 1)[-1]
        if node.attrs.get("data-link"):
            heading = next((x for x in node.all() if x.has_class("font-bold") and x.has_class("text-16")), None)
            title = heading.text() if heading else title
        facts = facts_from_text(text, host)
        if host == "licitor.com":
            inferred_date = french_date(urlparse(url).path.replace("-", " "))
            if inferred_date:
                facts["date_vente"] = inferred_date.isoformat()
        address = ""
        if host == "avoventes.fr":
            # L'adresse est affichée entre le titre et la mise à prix sur la carte.
            before_price = text.split("Mise à prix", 1)[0]
            if title in before_price:
                address = before_price.split(title, 1)[1].strip()
        record = {"id": digest(url), "url_annonce": url, "titre": title[:350],
                  "adresse": address, "sources": [source], "faits": facts,
                  "controle_liste_utc": timestamp, "detail_lu_ce_passage": False}
        if url not in records or len(text) > records[url].get("_text_length", 0):
            record["_text_length"] = len(text)
            records[url] = record
    for record in records.values():
        record.pop("_text_length", None)
    return records, doc


def hearing_records(markup, page_url, source, timestamp):
    doc = Document(markup)
    container = doc.find(lambda x: x.attrs.get("id") == "hearings-list")
    if not container:
        raise ValueError("structure Licitor : bloc hearings-list absent")
    header = next((n for n in container.all() if n.tag == "h1"), None)
    sale_date = french_date(header.text()) if header else None
    records, pages = {}, set()
    for node in container.all():
        if node.tag != "a" or not node.attrs.get("href"):
            continue
        url = canonical(node.attrs["href"], page_url)
        if "/annonce/" in urlparse(url).path:
            text = node.text()
            facts = facts_from_text(text, "licitor.com")
            if sale_date:
                facts["date_vente"] = sale_date.isoformat()
                # Le libellé du résultat de cette audience comprend sa date.
                result = re.search(r"\b\d{2}-\d{2}-\d{4}\s*:\s*([\d\s.,]+)\s*€", text)
                if result and amount(result.group(1)):
                    facts["prix_adjuge_eur"] = str(amount(result.group(1)))
            records[url] = {"id": digest(url), "url_annonce": url, "titre": text[:350],
                            "adresse": "", "sources": [source], "faits": facts,
                            "controle_liste_utc": timestamp, "detail_lu_ce_passage": False}
        elif urlparse(url).path == urlparse(page_url).path and "p=" in urlparse(url).query:
            pages.add(url)
    return records, sorted(pages)


def parse_detail(markup, page_url, record):
    doc = Document(markup)
    host = (urlparse(page_url).hostname or "").removeprefix("www.")
    if host == "licitor.com":
        container = doc.find(lambda x: x.tag == "article" and x.has_class("LegalAd") and len(x.text()) > 50)
    elif host == "avoventes.fr":
        container = doc.find(lambda x: x.attrs.get("id") == "content")
    else:
        container = doc.find(lambda x: x.tag == "main") or doc.find(lambda x: x.tag == "body")
    if not container or len(container.text()) < 80:
        raise ValueError("structure de fiche non reconnue ou contenu vide")
    text = container.text()
    if host == "avoventes.fr":
        primary = text.split("Cadastre", 1)[0]
        extras = text.split("Informations complémentaires :", 1)
        if len(extras) == 2:
            primary += " " + extras[1].split("Documents", 1)[0]
        text = primary
    elif host == "licitor.com":
        text = text.split("Prix constatés", 1)[0]
    else:
        # Menus, formulaires, biens voisins et simulateurs de frais sont exclus
        # dès que le site fournit un bloc de description propre au dossier.
        description = doc.find(lambda x: x.has_class("descriptionContener"))
        if description:
            text = container.text().split("Signaler une erreur", 1)[0]
    result = dict(record)
    old_facts = record.get("faits", {})
    detail_facts = facts_from_text(text, host, detail=True)
    if host == "avoventes.fr" and not detail_facts.get("date_vente"):
        raise ValueError("structure Avoventes modifiée : date Vente absente")
    if host == "licitor.com" and not detail_facts.get("date_vente"):
        raise ValueError("structure Licitor modifiée : date de vente absente")
    # Les faits manquants restent inconnus; la liste du même passage peut
    # compléter une fiche qui n'affiche pas elle-même le résultat.
    result["faits"] = {**old_facts, **detail_facts}
    for key in ["retiree", "seconde_adjudication", "surenchere_impossible"]:
        result["faits"][key] = bool(old_facts.get(key) or detail_facts.get(key))
    result["detail_lu_ce_passage"] = True
    result["controle_detail_utc"] = datetime.now(timezone.utc).isoformat()
    result["erreur_detail"] = None
    result["niveau_preuve"] = "C — publication, documents non vérifiés automatiquement"
    result["documents"] = list({canonical(n.attrs["href"], page_url) for n in container.all()
                                if n.tag == "a" and ".pdf" in n.attrs.get("href", "").lower()})[:30]
    result["documents_controles_automatiquement"] = False
    headings = [n.text() for n in container.all() if n.tag in {"h1", "h2"} and n.text()]
    if headings:
        result["titre"] = headings[0][:350]
    if host == "licitor.com":
        location = doc.find(lambda x: x.has_class("Address"))
        if location:
            result["adresse"] = location.text()
    # Pour la métropole, exclure les codes ultramarins explicites dans l'adresse.
    if re.search(r"\b(?:97[0-9]{3}|98[0-9]{3})\b", result.get("adresse", "")):
        result["faits"]["hors_metropole"] = True
    result["extrait_dossier"] = text[:6500]
    result["empreinte_contenu"] = hashlib.sha256(text.encode()).hexdigest()
    if host == "vench.fr" and "devez etre abonne" in normalize(text):
        result["restriction_acces"] = "description complète réservée aux abonnés"
    return result


class Fetcher:
    def __init__(self, fixture_map=None, pause=0.8):
        self.fixture_map = fixture_map
        self.pause = pause
        self.locks = {}
        self.guard = threading.Lock()
        self.last_request = {}
        self.memo = {}

    def get(self, url):
        if self.fixture_map is not None:
            path = self.fixture_map.get(url)
            if not path:
                raise ValueError("page non fournie dans le jeu de vérification")
            return Path(path).read_text(encoding="utf-8"), url
        host = urlparse(url).hostname
        with self.guard:
            lock = self.locks.setdefault(host, threading.Lock())
        with lock:
            if url in self.memo:
                return self.memo[url]
            wait = self.pause - (time.monotonic() - self.last_request.get(host, 0))
            if wait > 0:
                time.sleep(wait)
            self.last_request[host] = time.monotonic()
            request = Request(url, headers={"User-Agent": "AdjudicationsFranceMonitor/0.1 (public page monitor)"})
            with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                resolved = response.geturl()
                kind = response.headers.get("Content-Type", "")
                if "html" not in kind.lower():
                    raise ValueError("contenu non HTML : " + kind)
                body = response.read(MAX_BYTES + 1)
                if len(body) > MAX_BYTES:
                    raise ValueError("page dépassant 10 Mo")
                markup = body.decode(response.headers.get_content_charset() or "utf-8", errors="replace")
            self.memo[url] = markup, resolved
            return markup, resolved


def merge_records(target, incoming):
    for url, record in incoming.items():
        if url in target:
            sources = target[url]["sources"] + record["sources"]
            record["sources"] = list({s["url"]: s for s in sources}.values())
            # Préférer une valeur explicite à un champ non renseigné.
            previous_facts = target[url]["faits"]
            record["faits"] = {**previous_facts, **record["faits"]}
            for key in ["retiree", "seconde_adjudication", "surenchere_impossible"]:
                record["faits"][key] = bool(previous_facts.get(key) or record["faits"].get(key))
            record["adresse"] = record.get("adresse") or target[url].get("adresse", "")
        target[url] = record


def load_valuations(path):
    if not path.exists():
        return {}
    with path.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    return {canonical(row.get("url_annonce", "")): row for row in rows if row.get("url_annonce")}


def money(value):
    parsed = amount(value)
    return (f"{parsed:,.2f}".replace(",", " ") + " €") if parsed is not None else "À calculer — données manquantes"


def cell(value):
    return str("À vérifier" if value is None or value == "" else value).replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def save_report(folder, report):
    folder.mkdir(parents=True, exist_ok=True)
    atomic_json(folder / "dernier.json", report)
    rows = report["dossiers"]
    columns = ["id", "statut", "adresse", "titre", "date_vente", "jours_apres_audience", "occupation",
               "prix_adjuge_eur", "date_limite_surenchere", "plafond_marteau_eur", "marge_au_minimum_pct",
               "motifs_rejet", "a_verifier", "url_annonce"]
    with (folder / "dernier.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            output = {key: row.get(key, "") for key in columns}
            output.update({key: row["faits"].get(key, "") for key in ["date_vente", "occupation", "prix_adjuge_eur", "date_limite_surenchere"]})
            output.update({key: row.get("calcul_financier", {}).get(key, "") for key in ["plafond_marteau_eur", "marge_au_minimum_pct"]})
            for key in ["motifs_rejet", "a_verifier"]:
                output[key] = "; ".join(row[key])
            writer.writerow(output)
    with (folder / "sources.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        cols = ["nom", "url", "statut", "liens_detectes", "pages_audiences_lues", "fiches_lues", "erreur", "controle_utc"]
        writer = csv.DictWriter(stream, fieldnames=cols, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(report["sources"])
    count = report["comptages"]
    lines = ["# Veille adjudications — dernier passage", "", f"Contrôle : **{report['controle_paris']}** (Europe/Paris).",
             "", f"**{count['retenus']} dossier(s) retenu(s) sur données validées**, {count['a_verifier']} à vérifier, "
             f"{count['rejetes_recents']} rejet(s) dans la fenêtre J+1 à J+4.", "",
             f"{count['sources_ok']}/{count['sources_actives']} pages sources accessibles ; {count['liens_distincts']} liens distincts ; "
             f"{count['audiences_lues']} pages d'audience et {count['fiches_lues']} fiches détaillées lues.", "",
             f"Nouveautés : {count['nouveaux_liens']} liens ; {count['fiches_modifiees']} fiches modifiées depuis le passage précédent.", "",
             "Le nombre de liens inclut des audiences et des annonces anciennes ou futures. La couverture est limitée aux pages listées et au budget de lecture indiqué ci-dessous.", "",
             "## Achat-revente sans travaux", "",
             "Marge minimale : bénéfice prévisionnel avant fiscalité / (acquisition + frais de revente + portage) ≥ 40 %. "
             "Le plafond est calculé avec la valeur prudente en l'état et tous les frais renseignés. Le scénario stressé diminue la revente de 10 %.", "",
             "| Adresse du bien | Descriptif du bien | Montant maximal du marteau | Estimation des frais | Montant acte en mains | Estimation de la revente | Montant de la plus-value | Temps de revente | Descriptif | Source | Analyse du marché complète |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    prospective = [r for r in rows if r["statut"] != "rejete"]
    for row in prospective:
        facts, finance = row["faits"], row.get("calcul_financier", {})
        valuation = finance.get("hypotheses", {})
        profit = money(finance.get("benefice_au_plafond_eur"))
        if finance.get("marge_au_plafond_pct"):
            profit += f" ; {finance['marge_au_plafond_pct']} % des coûts engagés (au plafond) ; stress {finance['marge_stressee_au_plafond_pct']} %"
        description = (f"{row['statut']} ; {facts.get('occupation', 'inconnue')} ; audience {facts.get('date_vente', '?')} ; "
                       f"adjugé {money(facts.get('prix_adjuge_eur'))} ; délai publié {facts.get('date_limite_surenchere', '?')}. "
                       + "; ".join(row["a_verifier"]))
        values = [row.get("adresse") or row["titre"], row["titre"], money(finance.get("plafond_marteau_eur")),
                  money(finance.get("frais_acquisition_au_plafond_eur")), money(finance.get("acte_en_mains_au_plafond_eur")),
                  money(finance.get("revente_prudente_eur")), profit, valuation.get("delai_revente_mois", "À documenter"),
                  description, f"[Annonce]({row['url_annonce']})", valuation.get("analyse_marche", "À documenter : DVF et comparables locaux en l'état, liquidité, prix sur cinq ans et délai de revente.")]
        lines.append("| " + " | ".join(cell(value) for value in values) + " |")
    if not prospective:
        lines += ["", "Aucun dossier retenu ou restant à vérifier dans les fiches récentes analysées."]
    lines += ["", "## Rejets récents", "", "| Bien | Prix adjugé publié | Motif | Source |", "|---|---|---|---|"]
    for row in rows:
        if row["statut"] == "rejete":
            lines.append("| " + " | ".join(cell(x) for x in [row.get("adresse") or row["titre"],
                         money(row["faits"].get("prix_adjuge_eur")), "; ".join(row["motifs_rejet"]), f"[Annonce]({row['url_annonce']})"]) + " |")
    lines += ["", "## Sources effectivement contrôlées", "", "| Source | Accès | Liens | Audiences | Fiches | Incident |", "|---|---|---|---|---|---|"]
    for source in report["sources"]:
        lines.append("| " + " | ".join(cell(x) for x in [f"[{source['nom']}]({source['url']})", source["statut"], source["liens_detectes"],
                     source["pages_audiences_lues"], source["fiches_lues"], source.get("erreur") or "—"]) + " |")
    lines += ["", "## Couverture restante et contrôles", ""]
    lines += ["- " + warning for warning in report["limites"]]
    lines += ["", "Les pièces PDF sont référencées dans le JSON mais ne sont pas lues automatiquement. "
              "L'absence d'une mention de travaux ne prouve pas que le bien est revendable sans travaux. "
              "La mise à prix ne remplace jamais le prix adjugé.", "",
              "Règles de surenchère : [R322-50 à R322-55](https://www.legifrance.gouv.fr/codes/id/LEGISCTA000025939177) ; "
              "[délais, article 642](https://www.legifrance.gouv.fr/codes/article_lc/LEGIARTI000006411003). "
              "Une date publiée reste à confirmer avec l'avocat et les pièces du dossier.", ""]
    (folder / "dernier.md").write_text("\n".join(lines), encoding="utf-8")


def run(args):
    now = datetime.fromisoformat(args.now.replace("Z", "+00:00")) if args.now else datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("--now doit préciser un fuseau horaire")
    stamp, today = now.isoformat(), now.astimezone(PARIS).date()
    fixture_map = json.loads(Path(args.fixtures).read_text()) if args.fixtures else None
    fetcher = Fetcher(fixture_map, pause=args.pause)
    state_path = Path(args.state)
    state = json.loads(state_path.read_text()) if state_path.exists() else {"liens": {}, "fiches": {}}
    if state.get("version") not in {None, 1}:
        raise ValueError("version d'état non prise en charge")
    sources = load_sources()
    records, logs, incidents = {}, [], []

    def listing(source):
        log = {**source, "controle_utc": stamp, "statut": "ok", "liens_detectes": 0,
               "pages_audiences_lues": 0, "fiches_lues": 0, "erreur": ""}
        try:
            markup, resolved = fetcher.get(source["url"])
            incoming, _ = seed_records(markup, resolved, source, stamp)
            log["liens_detectes"] = len(incoming)
            if not incoming:
                log["statut"] = "aucun lien détecté — structure à contrôler"
            return incoming, log
        except Exception as exc:
            log["statut"], log["erreur"] = "echec", str(exc)
            return {}, log

    with ThreadPoolExecutor(max_workers=4) as executor:
        for incoming, log in executor.map(listing, sources):
            merge_records(records, incoming)
            logs.append(log)
            print(f"{log['nom']} : {log['liens_detectes']} lien(s), {log['statut']}", flush=True)
    log_by_url = {log["url"]: log for log in logs}
    hearing_urls = []
    for url, record in list(records.items()):
        if "licitor.com" in urlparse(url).netloc and urlparse(url).path.startswith("/ventes-judiciaires-immobilieres/"):
            day = french_date(urlparse(url).path.replace("-", " "))
            if day and 1 <= (today - day).days <= 4:
                hearing_urls.append((url, record["sources"][0]))
    remaining_pages = hearing_urls[:args.max_hearings]
    seen_pages = set()
    while remaining_pages and len(seen_pages) < args.max_hearings:
        url, source = remaining_pages.pop(0)
        if url in seen_pages:
            continue
        seen_pages.add(url)
        try:
            markup, resolved = fetcher.get(url)
            incoming, pages = hearing_records(markup, resolved, source, stamp)
            merge_records(records, incoming)
            log_by_url[source["url"]]["pages_audiences_lues"] += 1
            remaining_pages.extend((page, source) for page in pages if page not in seen_pages)
        except Exception as exc:
            incidents.append({"url": url, "erreur": str(exc), "phase": "audience"})
    print(f"Audiences lues : {len(seen_pages)}", flush=True)
    candidates = []
    for url, record in records.items():
        if urlparse(url).path.startswith("/ventes-judiciaires-immobilieres/"):
            continue
        date_value = record["faits"].get("date_vente")
        day = None
        if date_value:
            try:
                day = datetime.fromisoformat(date_value).date()
            except ValueError:
                day = None
        age = (today - day).days if day else None
        if age is None or 1 <= age <= 4:
            priority = 0 if age is not None else 1
            previous = state.get("fiches", {}).get(url, {})
            candidates.append((priority, previous.get("controle_detail_utc", ""), url))
    candidates.sort()
    chosen = [url for _, _, url in candidates[:args.max_details]]
    failures, modified, details_read = 0, 0, 0

    def read_detail(url):
        try:
            markup, resolved = fetcher.get(url)
            return url, parse_detail(markup, resolved, records[url]), None
        except Exception as exc:
            return url, None, str(exc)

    with ThreadPoolExecutor(max_workers=4) as executor:
        for url, parsed, error in executor.map(read_detail, chosen):
            previous = state.get("fiches", {}).get(url, {})
            if error:
                failures += 1
                records[url]["erreur_detail"] = error
                incidents.append({"url": url, "erreur": error, "phase": "fiche"})
            else:
                details_read += 1
                modified += bool(previous.get("empreinte_contenu") and previous["empreinte_contenu"] != parsed["empreinte_contenu"])
                records[url] = parsed
                state.setdefault("fiches", {})[url] = parsed
                for source in parsed["sources"]:
                    log_by_url[source["url"]]["fiches_lues"] += 1
    valuations = load_valuations(Path(args.valuations))
    qualified = [classify(record, now, valuations.get(url)) for url, record in records.items()
                 if not urlparse(url).path.startswith("/ventes-judiciaires-immobilieres/")]
    recent = [row for row in qualified if row["jours_apres_audience"] is not None and 1 <= row["jours_apres_audience"] <= 4]
    recent.sort(key=lambda x: (x["statut"], -(x.get("jours_apres_audience") or 0), x.get("adresse") or x["titre"]))
    new_count = sum(url not in state.get("liens", {}) for url in records)
    for url, record in records.items():
        state.setdefault("liens", {})[url] = {"premiere_detection": state.get("liens", {}).get(url, {}).get("premiere_detection", stamp), "dernier_controle": stamp}
    state["version"], state["dernier_passage"] = 1, stamp
    atomic_json(state_path, state)
    unknown = sum(row["jours_apres_audience"] is None for row in qualified)
    limits = [f"Budget : {args.max_hearings} pages d'audience et {args.max_details} fiches par passage ; "
              f"{max(0, len(candidates) - len(chosen))} fiches candidates non lues ce passage.",
              f"{unknown} liens de fiches sans date d'audience extraite restent hors sélection ; "
              "ils sont explorés par rotation aux passages suivants.",
              "Les paginations Licitor des audiences récentes sont parcourues dans le budget. "
              "Les autres listes ne sont pas paginées automatiquement.",
              "Ces pages ne constituent pas une couverture exhaustive de la France ni des 706 fiches de la base d'avocats.",
              "Le registre central Registre_Adjudications.xlsx n'est pas modifié par ce programme.",
              "Les publications restent de niveau C tant que les pièces et hypothèses ne sont pas vérifiées et renseignées dans estimations.csv."]
    if remaining_pages:
        limits.append(f"{len(remaining_pages)} pages d'audience restent en file après le budget.")
    if incidents:
        limits.append(f"{len(incidents)} incident(s) lors de la lecture des audiences ou des fiches ; voir dernier.json.")
    report = {
        "version": 1, "controle_utc": stamp, "controle_paris": now.astimezone(PARIS).isoformat(),
        "comptages": {"sources_actives": len(sources), "sources_ok": sum(x["statut"] == "ok" for x in logs),
                      "liens_distincts": len(records), "nouveaux_liens": new_count, "audiences_lues": sum(x["pages_audiences_lues"] for x in logs),
                      "fiches_lues": details_read, "fiches_en_echec": failures, "fiches_modifiees": modified,
                      "dossiers_recents": len(recent), "retenus": sum(x["statut"] == "retenu_sur_donnees_validees" for x in recent),
                      "a_verifier": sum(x["statut"] == "a_verifier" for x in recent), "rejetes_recents": sum(x["statut"] == "rejete" for x in recent),
                      "sans_date": unknown, "fiches_candidates_non_lues": max(0, len(candidates) - len(chosen))},
        "dossiers": recent, "sources": logs, "incidents": incidents, "limites": limits,
    }
    save_report(Path(args.output), report)
    # Le catalogue complet reste dans l'artefact, pas dans le rapport public résumé.
    atomic_json(Path(args.catalogue), {"controle_utc": stamp, "liens": qualified})
    print(json.dumps(report["comptages"], ensure_ascii=False), flush=True)
    return 1 if any(x["statut"] == "echec" for x in logs) or incidents else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", default=str(ROOT / "data/etat.json"))
    parser.add_argument("--output", default=str(ROOT / "rapports"))
    parser.add_argument("--catalogue", default=str(ROOT / "data/catalogue.json"))
    parser.add_argument("--valuations", default=str(ROOT / "estimations.csv"))
    parser.add_argument("--max-hearings", type=int, default=40)
    parser.add_argument("--max-details", type=int, default=120)
    parser.add_argument("--pause", type=float, default=0.8)
    parser.add_argument("--now", help="date ISO avec fuseau, pour vérification reproductible")
    parser.add_argument("--fixtures", help="JSON URL -> fichier HTML local pour vérification sans réseau")
    args = parser.parse_args()
    if args.max_hearings < 0 or args.max_details < 0 or args.pause < 0:
        parser.error("budgets et pause doivent être positifs ou nuls")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
