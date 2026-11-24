"""Polite, domain-restricted fetcher for the official GIKI website (https://giki.edu.pk/).

Guarantees (enforced in code, covered by tests):
* only hosts equal to or under giki.edu.pk are ever requested - including every redirect hop;
* robots.txt is fetched and obeyed; login/admin areas are never requested;
* identified user agent, >=1.5 s between requests per host, hard size/time limits, conditional requests (ETag / Last-Modified).
"""
import io
import re
import threading
import time
import urllib.robotparser
from dataclasses import dataclass
from urllib.parse import urldefrag, urljoin, urlparse

import requests

ALLOWED_SUFFIX = "giki.edu.pk"
USER_AGENT = "G-Cortex-academic-bot/1.0 (GIKI student project; honours robots.txt)"
MIN_DELAY = 1.5
MAX_HTML = 5 * 1024 * 1024
MAX_PDF = 200 * 1024 * 1024
BLOCKED_PATH = re.compile(r"/(wp-admin|wp-login|login|my-account|cart|checkout)(/|$|\?)", re.I)


class NotAllowed(Exception):
    pass


def is_official_url(url: str) -> bool:
    try:
        u = urlparse(url)
    except Exception:
        return False
    host = (u.hostname or "").lower()
    return u.scheme in ("http", "https") and (host == ALLOWED_SUFFIX or host.endswith("." + ALLOWED_SUFFIX)) and not u.username and not u.password


def normalise(url: str, base: str | None = None) -> str:
    url = urldefrag(urljoin(base or "", url))[0].strip()
    u = urlparse(url)
    if u.scheme == "http" and is_official_url(url):
        url = url.replace("http://", "https://", 1)
    return url


@dataclass
class Fetched:
    url: str
    status: int
    content: bytes | None
    content_type: str
    etag: str | None
    last_modified: str | None
    not_modified: bool = False


class OfficialFetcher:
    def __init__(self, delay: float = MIN_DELAY, session: requests.Session | None = None):
        self.delay = delay
        self.s = session or requests.Session()
        self.s.headers["User-Agent"] = USER_AGENT
        self._last: dict[str, float] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser] = {}
        self._lock = threading.Lock()
        self.requests_made = 0

    def _wait(self, host: str) -> None:
        with self._lock:
            gap = time.monotonic() - self._last.get(host, 0)
            if gap < self.delay:
                time.sleep(self.delay - gap)
            self._last[host] = time.monotonic()

    def _allowed_by_robots(self, url: str) -> bool:
        host = urlparse(url).hostname
        rp = self._robots.get(host)
        if rp is None:
            rp = urllib.robotparser.RobotFileParser()
            robots_url = f"https://{host}/robots.txt"
            try:
                self._wait(host)
                r = self.s.get(robots_url, timeout=20)
                self.requests_made += 1
                rp.parse(r.text.splitlines() if r.status_code == 200 else [])
            except requests.RequestException:
                rp.parse([])
            self._robots[host] = rp
        return rp.can_fetch(USER_AGENT, url)

    def fetch(self, url: str, etag: str | None = None, last_modified: str | None = None) -> Fetched:
        url = normalise(url)
        if not is_official_url(url):
            raise NotAllowed(f"Refusing non-official URL: {url}")
        if BLOCKED_PATH.search(urlparse(url).path):
            raise NotAllowed(f"Refusing restricted path: {url}")
        if not self._allowed_by_robots(url):
            raise NotAllowed(f"robots.txt disallows: {url}")
        headers = {}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified
        current = url
        for _ in range(5):                                   # manual redirects so EVERY hop is domain-checked
            host = urlparse(current).hostname
            self._wait(host)
            r = self.s.get(current, headers=headers, timeout=60, allow_redirects=False, stream=True)
            self.requests_made += 1
            if r.status_code in (301, 302, 303, 307, 308):
                nxt = normalise(r.headers.get("Location", ""), current)
                r.close()
                if not is_official_url(nxt):
                    raise NotAllowed(f"Redirect leaves the official domain: {nxt}")
                if BLOCKED_PATH.search(urlparse(nxt).path) or not self._allowed_by_robots(nxt):
                    raise NotAllowed(f"Redirect target not allowed: {nxt}")
                current = nxt
                continue
            ctype = r.headers.get("Content-Type", "").split(";")[0].strip().lower()
            if r.status_code == 304:
                r.close()
                return Fetched(current, 304, None, ctype, etag, last_modified, True)
            if r.status_code != 200:
                r.close()
                return Fetched(current, r.status_code, None, ctype, None, None)
            limit = MAX_PDF if ("pdf" in ctype or current.lower().endswith(".pdf")) else MAX_HTML
            buf = io.BytesIO()
            for chunk in r.iter_content(1024 * 256):
                buf.write(chunk)
                if buf.tell() > limit:
                    r.close()
                    raise NotAllowed(f"Document exceeds the {limit // 1048576} MB limit: {current}")
            return Fetched(current, 200, buf.getvalue(), ctype, r.headers.get("ETag"), r.headers.get("Last-Modified"))
        raise NotAllowed("Too many redirects")


# ── Extraction ──────────────────────────────────────────────

def extract_html(content: bytes) -> tuple[str, str, list[str]]:
    """-> (title, main text, same-site links). Only the page-builder body is kept (menus/footers are dropped)."""
    import html as htmllib
    s = content.decode("utf-8", errors="ignore").replace("\x00", " ")
    tm = re.search(r"<title>(.*?)</title>", s, re.S | re.I)
    title = re.sub(r"\s*[-–|]\s*Ghulam Ishaq Khan Institute.*$", "", htmllib.unescape(tm.group(1)).strip()) if tm else ""
    i = s.find("gdlr-core-page-builder-body")
    body = s[s.rfind("<", 0, i):] if i >= 0 else s            # start at the opening tag, not mid-attribute
    j = body.find("kingster-footer-wrapper")
    body = body[:body.rfind("<", 0, j)] if j > 0 else body
    links = [l for l in re.findall(r'href=["\']([^"\'#]+)["\']', body)]
    body = re.sub(r"<(script|style|nav|form|noscript)[\s\S]*?</\1>", " ", body, flags=re.I)
    body = re.sub(r"</(p|div|li|h[1-6]|tr|br|table|ul)>|<br\s*/?>", "\n", body, flags=re.I)
    text = htmllib.unescape(re.sub(r"<[^>]+>", " ", body))
    text = re.sub(r"[ \t\xa0]+", " ", text)
    text = "\n".join(l.strip() for l in text.split("\n"))
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return title, text, links


def humanise_title(url: str, meta: str = "") -> str:
    """Stable, descriptive title: the document's file name (PDF metadata titles are often generic, e.g. 'Dear Students')."""
    stem = re.sub(r"\.pdf$", "", url.rstrip("/").rsplit("/", 1)[-1], flags=re.I)
    name = re.sub(r"[-_]+", " ", stem).strip()
    return name or meta or url


def extract_pdf(content: bytes) -> tuple[str, str, int]:
    """-> (title, text with <<PAGE n>> markers, page count)."""
    from pypdf import PdfReader
    r = PdfReader(io.BytesIO(content))
    meta = (r.metadata.title if r.metadata and r.metadata.title else "") or ""
    pages = [f"\n<<PAGE {i + 1}>>\n" + (p.extract_text() or "") for i, p in enumerate(r.pages)]
    clean = lambda t: re.sub(r"[\x00\ufffe\uffff]", " ", t)          # PostgreSQL cannot store NUL characters
    return clean(meta).strip(), clean("".join(pages)), len(r.pages)
