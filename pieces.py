"""Lecture PDF progressive, OCR borné et indices avec provenance par page."""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from qualification import DATE_PATTERN, MONEY_PATTERN, amount, french_date, normalize, occupancy, works

SALE_LABEL = r"(?:date de (?:la )?vente|date de l'audience|vente(?: aux encheres(?: publiques)?| sur (?:liquidation judiciaire|licitation|saisie immobiliere|surenchere))?(?: du| le)?|audience)"


def page_evidence(text, url, page, method):
    """Conserver des libellés courts, pas le texte complet ni les noms des occupants."""
    clean = normalize(text)
    evidence = []

    def add(field, value, excerpt):
        evidence.append({"champ": field, "valeur": value, "url": url, "page": page,
                         "methode": method, "extrait": excerpt[:240], "validation": "a_verifier"})

    for field, label in [("date_vente", SALE_LABEL), ("date_limite_surenchere", r"surenchere possible jusqu'au")]:
        for match in re.finditer(label + r"\s*:?\s*(?:[a-z]+\s+)?(" + DATE_PATTERN + ")", clean):
            day = french_date(match.group(1))
            if day:
                add(field, day.isoformat(), match.group())
    for field, label in [("prix_adjuge_eur", r"(?:adjuge|adjudication)"),
                         ("mise_a_prix_eur", r"mise a prix(?: initiale)?"),
                         ("frais_prealables_publies_eur", r"frais pre(?:a)?lables")]:
        for match in re.finditer(label + r"\s*:?\s*" + MONEY_PATTERN, clean):
            value = amount(match.group(1))
            if value is not None:
                add(field, str(value), match.group())
    status, extracts = occupancy(text)
    if status != "inconnue":
        add("occupation", status, "; ".join(extracts))
    status, extracts = works(text)
    if status == "necessaires":
        add("travaux", status, "; ".join(extracts))
    return evidence


def summarize_evidence(evidence):
    values = {}
    for item in evidence:
        values.setdefault(item["champ"], set()).add(item["valeur"])
    return ({field: next(iter(items)) for field, items in values.items() if len(items) == 1},
            {field: sorted(items) for field, items in values.items() if len(items) > 1})


class PdfReader:
    def __init__(self, stamp, max_pages=80, max_ocr_pages=6, pages_per_document=8):
        self.stamp = stamp
        self.pages_left, self.ocr_left = max_pages, max_ocr_pages
        self.pages_per_document = pages_per_document
        self.metrics = {"pages_pdf_traitees": 0, "pages_pdf_extraites": 0,
                        "pages_ocr_tentees": 0, "pages_ocr": 0, "documents_cache_reutilise": 0}
        self._languages = None

    @staticmethod
    def _run(command, timeout=20):
        process = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 timeout=timeout, env={**os.environ, "OMP_THREAD_LIMIT": "1"})
        if process.returncode:
            raise ValueError("échec du lecteur " + Path(command[0]).name + " : " + process.stderr.decode("utf-8", errors="replace")[:200])
        return process.stdout.decode("utf-8", errors="replace")

    def _ocr(self, source, page, folder):
        for tool in ["pdftoppm", "tesseract"]:
            if not shutil.which(tool):
                raise ValueError("outil OCR manquant : " + tool)
        if self._languages is None:
            listing = self._run(["tesseract", "--list-langs"])
            available = set(listing.splitlines())
            self._languages = "+".join(lang for lang in ["fra", "eng"] if lang in available)
            if not self._languages:
                raise ValueError("aucune langue française ou anglaise installée pour l'OCR")
        prefix = str(folder / f"page-{page}")
        self._run(["pdftoppm", "-f", str(page), "-l", str(page), "-scale-to", "1800",
                   "-singlefile", "-png", str(source), prefix], timeout=35)
        return self._run(["tesseract", prefix + ".png", "stdout", "-l", self._languages,
                          "--psm", "3"], timeout=35)

    def read(self, body, url, previous=None):
        if not body.startswith(b"%PDF-"):
            raise ValueError("signature PDF absente")
        for tool in ["pdfinfo", "pdftotext"]:
            if not shutil.which(tool):
                raise ValueError("outil PDF manquant : " + tool)
        fingerprint = hashlib.sha256(body).hexdigest()
        same = previous and previous.get("empreinte_pdf") == fingerprint
        extractions = dict(previous.get("extractions", {})) if same else {}
        result = {"url": url, "controle_acces_utc": self.stamp, "empreinte_pdf": fingerprint,
                  "extractions": extractions, "pages_traitees_ce_passage": 0,
                  "pages_lues_ce_passage": 0, "pages_ocr_ce_passage": 0,
                  "cache_reutilise": bool(same and extractions), "incidents_pages": []}
        if result["cache_reutilise"]:
            self.metrics["documents_cache_reutilise"] += 1
        with tempfile.TemporaryDirectory(prefix="adjudications-pdf-") as name:
            folder = Path(name)
            source = folder / "piece.pdf"
            source.write_bytes(body)
            info = self._run(["pdfinfo", str(source)])
            if re.search(r"^Encrypted:\s+yes", info, re.MULTILINE):
                raise ValueError("PDF protégé : contrôle manuel requis")
            match = re.search(r"^Pages:\s+(\d+)", info, re.MULTILINE)
            if not match or not 1 <= int(match.group(1)) <= 300:
                raise ValueError("nombre de pages PDF absent ou supérieur à 300")
            total = result["pages_total"] = int(match.group(1))
            pending = [p for p in range(1, total + 1) if extractions.get(str(p), {}).get("methode") == "en_attente_ocr"]
            fresh = [p for p in range(1, total + 1) if str(p) not in extractions]
            selected = (pending + fresh)[:min(self.pages_per_document, self.pages_left)]
            for page in selected:
                self.pages_left -= 1
                self.metrics["pages_pdf_traitees"] += 1
                result["pages_traitees_ce_passage"] += 1
                try:
                    text = "" if page in pending else self._run(["pdftotext", "-f", str(page), "-l", str(page),
                                                                 "-layout", "-enc", "UTF-8", str(source), "-"])
                    method = "texte"
                    if len(normalize(text)) < 40:
                        if not self.ocr_left:
                            extractions[str(page)] = {"methode": "en_attente_ocr", "preuves": []}
                            continue
                        self.ocr_left -= 1
                        self.metrics["pages_ocr_tentees"] += 1
                        text, method = self._ocr(source, page, folder), "ocr"
                    if len(text) > 200000:
                        raise ValueError("texte de page anormalement volumineux")
                    extractions[str(page)] = {"methode": method, "controle_extraction_utc": self.stamp,
                                              "langues_ocr": self._languages if method == "ocr" else None,
                                              "texte_detecte": bool(text.strip()),
                                              "preuves": page_evidence(text, url, page, method)}
                    self.metrics["pages_pdf_extraites"] += 1
                    result["pages_lues_ce_passage"] += 1
                    if method == "ocr":
                        self.metrics["pages_ocr"] += 1
                        result["pages_ocr_ce_passage"] += 1
                except (ValueError, subprocess.TimeoutExpired, OSError) as exc:
                    extractions.pop(str(page), None)
                    result["incidents_pages"].append({"page": page, "erreur": str(exc)})
        read = [int(p) for p, data in extractions.items() if data["methode"] in {"texte", "ocr"}]
        result["pages_lues"] = sorted(read)
        result["pages_restantes"] = total - len(read)
        result["preuves"] = [proof for p in sorted(extractions, key=int) for proof in extractions[p].get("preuves", [])]
        result["faits_candidats"], result["contradictions"] = summarize_evidence(result["preuves"])
        result["statut"] = "lu" if not result["pages_restantes"] else "partiel"
        result["validation"] = "a_verifier"
        return result
