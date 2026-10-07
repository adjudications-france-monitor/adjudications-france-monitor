"""Accès public borné et délais persistants, sans contournement des refus."""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from monitor import MAX_BYTES, TIMEOUT_SECONDS


def utc_iso(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat()


def retry_after(value, now, default):
    """Le délai du serveur peut être un nombre de secondes ou une date HTTP."""
    if value:
        try:
            return max(60, int(value.strip()))
        except (ValueError, AttributeError):
            try:
                day = parsedate_to_datetime(value)
                if day.tzinfo is None:
                    day = day.replace(tzinfo=timezone.utc)
                return max(60, day.timestamp() - now)
            except (ValueError, TypeError, OverflowError):
                pass
    return default


class AccessDeferred(Exception):
    def __init__(self, host, restriction):
        self.until = restriction["attendre_jusqua_utc"]
        self.code = restriction["code_http"]
        super().__init__(f"Accès différé à {host} jusqu'au {self.until} après HTTP {self.code}")


class PdfDocument(Exception):
    """Le contenu est un PDF ; les octets restent dans le cache du passage."""


class Fetcher:
    def __init__(self, fixture_map=None, pause=0.8, access_state=None, clock=None):
        self.fixture_map, self.pause = fixture_map, pause
        self.access_state = access_state if access_state is not None else {}
        self.clock = clock or time.time
        self.locks, self.last_request, self.memo = {}, {}, {}
        self.guard = threading.Lock()
        self.metrics = {"requetes_http": 0, "reponses_429": 0, "acces_differes": 0}

    def _count(self, name):
        with self.guard:
            self.metrics[name] += 1

    def _resource(self, url):
        if self.fixture_map is not None:
            path = self.fixture_map.get(url)
            if not path:
                raise ValueError("page non fournie dans le jeu de vérification")
            body = Path(path).read_bytes()
            kind = "application/pdf" if body.startswith(b"%PDF-") else "text/html"
            return body, url, kind, "utf-8"
        host = (urlparse(url).hostname or "").lower().removeprefix("www.")
        if urlparse(url).scheme not in {"http", "https"} or not host:
            raise ValueError("URL publique HTTP(S) requise")
        with self.guard:
            lock = self.locks.setdefault(host, threading.Lock())
        with lock:
            if url in self.memo:
                return self.memo[url]
            restriction = self.access_state.get(host)
            if restriction:
                try:
                    until = datetime.fromisoformat(restriction["attendre_jusqua_utc"]).timestamp()
                except (KeyError, ValueError, TypeError):
                    until = 0
                if until > self.clock():
                    self._count("acces_differes")
                    raise AccessDeferred(host, restriction)
            wait = self.pause - (time.monotonic() - self.last_request.get(host, 0))
            if wait > 0:
                time.sleep(wait)
            self.last_request[host] = time.monotonic()
            request = Request(url, headers={"User-Agent": "AdjudicationsFranceMonitor/0.2 (public page monitor)"})
            self._count("requetes_http")
            try:
                with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                    resolved = response.geturl()
                    kind = response.headers.get("Content-Type", "")
                    body = response.read(MAX_BYTES + 1)
                    if len(body) > MAX_BYTES:
                        raise ValueError("page ou document dépassant 10 Mo")
                    encoding = response.headers.get_content_charset() or "utf-8"
            except HTTPError as exc:
                if exc.code == 429 or exc.code == 403 or 500 <= exc.code <= 599:
                    previous = (restriction or {}).get("echecs_consecutifs", 0)
                    fallback = 21600 if exc.code == 403 else (3600 if exc.code == 429 else min(3600, 300 * 2 ** min(previous, 4)))
                    delay = retry_after(exc.headers.get("Retry-After") if exc.headers else None, self.clock(), fallback)
                    # Un refus 403 n'est pas réessayé immédiatement, même sans Retry-After.
                    if exc.code == 403:
                        delay = max(21600, delay)
                    self.access_state[host] = {"attendre_jusqua_utc": utc_iso(self.clock() + delay),
                                               "code_http": exc.code, "dernier_echec_utc": utc_iso(self.clock()),
                                               "echecs_consecutifs": previous + 1}
                    if exc.code == 429:
                        self._count("reponses_429")
                raise
            self.access_state.pop(host, None)
            self.memo[url] = body, resolved, kind, encoding
            return self.memo[url]

    def get(self, url):
        body, resolved, kind, encoding = self._resource(url)
        if "application/pdf" in kind.lower() or body.startswith(b"%PDF-"):
            raise PdfDocument("PDF accessible — lecture documentaire")
        if "html" not in kind.lower():
            raise ValueError("contenu non HTML : " + kind)
        return body.decode(encoding, errors="replace"), resolved

    def get_pdf(self, url):
        body, resolved, _, _ = self._resource(url)
        if not body.startswith(b"%PDF-"):
            raise ValueError("le lien ne renvoie pas un PDF lisible")
        return body, resolved


def incident(exc, url, phase):
    result = {"url": url, "phase": phase, "erreur": str(exc)}
    if isinstance(exc, AccessDeferred):
        result.update(differe=True, nouvel_essai_utc=exc.until, code_http=exc.code)
    elif isinstance(exc, HTTPError):
        result["code_http"] = exc.code
    return result
