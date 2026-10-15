#!/usr/bin/env python3
"""Construit l'inventaire dédoublonné des URL des répertoires nationaux."""
from __future__ import annotations
import csv
import json
import re
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
ROOT = Path(__file__).resolve().parent
URL_RE = re.compile(r"https?://[^\s;<>\"']+", re.IGNORECASE)
PRIORITIES = {"1", "true", "yes", "oui", "prioritaire_706"}

def canonical_url(value: str) -> str:
    value = value.strip().rstrip(".,)")
    parts = urlsplit(value)
    host = (parts.hostname or "").lower()
    port = parts.port
    if port and not ((parts.scheme.lower() == "https" and port == 443) or (parts.scheme.lower() == "http" and port == 80)):
        host += f":{port}"
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), host, path, parts.query, ""))

def read_rows(name: str, delimiter: str) -> list[dict[str, str]]:
    with (ROOT / name).open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream, delimiter=delimiter))

def add_reference(index, url: str, record_type: str, record_id: str, priority: str = "") -> None:
    canonical = canonical_url(url)
    if not canonical.startswith(("http://", "https://")):
        return
    row = index[canonical]
    row["types"].add(record_type)
    row["ids"].add(record_id)
    row["references"] += 1
    if priority.strip().lower() in PRIORITIES:
        row["priority"] = True

def build() -> int:
    index = defaultdict(lambda: {"types": set(), "ids": set(), "references": 0, "priority": False})
    for row in read_rows("repertoire_greffes.csv", ";"):
        record_id = row.get("id", "")
        add_reference(index, row.get("url_fiche_officielle", ""), "juridiction_greffe", record_id)
        for field in URL_RE.findall(row.get("site_information_general", "") or ""):
            add_reference(index, field, "juridiction_greffe", record_id)
    for row in read_rows("repertoire_avocats.csv", ";"):
        record_id = row.get("id", "")
        try:
            urls = json.loads(row.get("urls_sources_json") or "[]")
        except json.JSONDecodeError as error:
            raise ValueError(f"urls_sources_json invalide pour {record_id}") from error
        for item in urls:
            for url in URL_RE.findall(str(item)):
                add_reference(index, url, "fiche_avocat", record_id, row.get("prioritaire_706", ""))
    active = {canonical_url(row.get("url", "")) for row in read_rows("sources.csv", ",") if (row.get("active") or "").strip().lower() in {"1", "true", "yes", "oui"}}
    output = ROOT / "catalogue_urls.csv"
    fields = ["url", "domaine", "nombre_references", "types_references", "identifiants_json", "prioritaire_706", "source_active"]
    with output.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for url, data in sorted(index.items()):
            writer.writerow({"url": url, "domaine": urlsplit(url).hostname or "", "nombre_references": data["references"], "types_references": ";".join(sorted(data["types"])), "identifiants_json": json.dumps(sorted(data["ids"]), ensure_ascii=False), "prioritaire_706": "1" if data["priority"] else "0", "source_active": "1" if url in active else "0"})
    if not index:
        raise RuntimeError("Aucune URL n'a été trouvée dans les répertoires")
    print(f"URL canoniques : {len(index)} ; occurrences : {sum(x['references'] for x in index.values())} ; actives dans sources.csv : {sum(url in active for url in index)}")
    return 0

if __name__ == "__main__":
    raise SystemExit(build())
