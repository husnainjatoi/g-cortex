"""Section-aware chunking of extracted document text (pure functions)."""
import hashlib
import re

TARGET = 1100
OVERLAP = 140
_PAGE = re.compile(r"^<<PAGE (\d+)>>$")
_HEADING = re.compile(r"^(?:\d{1,2}(?:\.(?!0\d)\d{1,2})+\.?\s+[A-Z][^\n.]{2,70}|[A-Z][A-Z0-9 &/,'()-]{6,80})$")


def content_hash(text: str) -> str:
    return hashlib.sha256(re.sub(r"\s+", " ", text).strip().lower().encode()).hexdigest()


def _clean(s: str) -> str:
    s = re.sub(r"[\x00-\x08\x0b-\x1f]", " ", s)
    s = re.sub(r"UNDERGRADUATE PROSPECTUS \d{4}(UNDERGRADUATE PROSPECTUS \d{4})?", " ", s)
    return re.sub(r"[ \t\xa0]+", " ", s)


def chunk_document(text: str) -> list[dict]:
    """-> [{'section', 'page_from', 'content', 'content_hash'}]. Never emits empty or near-empty chunks."""
    out: list[dict] = []
    section, page = None, None
    buf: list[str] = []
    buf_len = 0
    buf_page = None

    def flush():
        nonlocal buf, buf_len, buf_page
        body = re.sub(r"\s+", " ", " ".join(buf)).strip()
        if len(body) >= 80:
            out.append({"section": section, "page_from": buf_page, "content": body, "content_hash": content_hash(body)})
            tail = body[-OVERLAP:] if len(body) > TARGET else ""
            buf, buf_len, buf_page = ([tail] if tail else []), len(tail), page
        else:
            buf, buf_len, buf_page = [], 0, page

    for raw in _clean(text).split("\n"):
        ln = raw.strip()
        if not ln:
            continue
        m = _PAGE.match(ln)
        if m:
            page = int(m.group(1))
            if buf_page is None:
                buf_page = page
            continue
        if _HEADING.match(ln) and len(ln.split()) <= 14:
            if buf_len > 250:
                flush()
            section = ln.title() if ln.isupper() else ln
            buf_page = buf_page or page
        if buf_page is None:
            buf_page = page
        buf.append(ln)
        buf_len += len(ln) + 1
        if buf_len >= TARGET:
            flush()
    flush()
    seen, uniq = set(), []
    for c in out:                      # drop exact duplicates inside one document
        if c["content_hash"] not in seen:
            seen.add(c["content_hash"]); uniq.append(c)
    return uniq
