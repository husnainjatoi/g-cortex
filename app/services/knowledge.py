"""Official-GIKI knowledge base: refresh (crawl + chunk + embed), hybrid retrieval and source listing.

Only pages/documents hosted on giki.edu.pk are fetched or stored (crawler + database CHECK constraint). Updated documents replace
their own chunks inside one transaction, so refreshes never leave duplicate or stale knowledge behind.
"""
import json
import logging
import re
import threading
from datetime import datetime

from app import db
from app.knowledge import crawler, embeddings
from app.knowledge.chunking import chunk_document
from . import audit
from .actor import Actor
from .audit import op
from .errors import Conflict, ValidationFailed

logger = logging.getLogger("g_cortex.knowledge")

SEED_PAGES = [
    "https://giki.edu.pk/academics/", "https://giki.edu.pk/academics/academic-calendar/", "https://giki.edu.pk/academics/student-affairs/",
    "https://giki.edu.pk/academics/office-of-admission-examination/", "https://giki.edu.pk/academics/dsa/", "https://giki.edu.pk/policies/",
    "https://giki.edu.pk/fcse/fcs-undergraduate/", "https://giki.edu.pk/fcse/fcs-undergraduate/bs-cs/", "https://giki.edu.pk/fcse/fcs-undergraduate/bs-ai/",
    "https://giki.edu.pk/fcse/fcs-undergraduate/bs-ce/", "https://giki.edu.pk/fcse/fcs-undergraduate/bs-data-science/", "https://giki.edu.pk/fcse/fcs-graduate/",
    "https://giki.edu.pk/admissions/admissions-undergraduates/eligibility-criteria/", "https://giki.edu.pk/admissions/admissions-undergraduates/faqs/",
]
SEED_DOCS = [
    "https://giki.edu.pk/wp-content/uploads/2023/10/GIK-Institute-Student-Handbook-2023-24-General-Rules-Policies-and-Disicpline.pdf",
    "https://giki.edu.pk/wp-content/uploads/2021/10/UG_Prospectus_2021.pdf",
    "https://giki.edu.pk/wp-content/uploads/2021/10/Graduate_Prospectus.pdf",
    "https://giki.edu.pk/wp-content/uploads/2026/08/Academic-Calendar-2026-2027.pdf",
]
_DOC_ALLOW = re.compile(r"(handbook|prospectus|calendar|curricul|scheme|regulation|rules|ordinance|syllabus|academic)", re.I)
_DOC_DENY = re.compile(r"(newsletter|annual-report|tender|sdgiki|energy|waste|water|procurement|sustainab|bribery|transportation|equality)", re.I)
CATALOG_SOURCE = re.compile(r"UG_Prospectus", re.I)

_running = threading.Lock()
STOP = {"the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "is", "are", "what", "which", "how", "can", "i", "my", "me", "do", "does", "be", "by", "with",
        "at", "it", "this", "that", "if", "will", "there", "any", "from", "as", "have", "has", "about", "tell", "give", "student", "students", "giki", "gik"}


def _words(q: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", q.lower()) if w not in STOP and len(w) > 2]


# ── Refresh pipeline ────────────────────────────────────────

def _discover(fetcher, seeds: list[str]) -> list[str]:
    """Seed pages -> linked official documents that look like policy/curriculum material (never other domains)."""
    docs: list[str] = []
    for u in seeds:
        try:
            f = fetcher.fetch(u)
        except crawler.NotAllowed:
            continue
        if f.status != 200 or not f.content:
            continue
        for l in crawler.extract_html(f.content)[2]:
            n = crawler.normalise(l, u)
            if n.lower().endswith(".pdf") and crawler.is_official_url(n) and _DOC_ALLOW.search(n) and not _DOC_DENY.search(n):
                docs.append(n)
    return docs


def _upsert_source(cur, url, **f):
    cols = ["url"] + list(f)
    cur.execute(f"""INSERT INTO knowledge_sources ({','.join(cols)}) VALUES ({','.join(['%s'] * len(cols))})
                    ON CONFLICT (url) DO UPDATE SET {','.join(f'{k}=EXCLUDED.{k}' for k in f)} RETURNING source_id""", [url, *f.values()])
    return cur.fetchone()["source_id"]


def ingest_url(fetcher, url: str, force: bool = False) -> dict:
    url = crawler.normalise(url)
    prior = db.query_one("SELECT source_id, content_hash, etag, last_modified, status FROM knowledge_sources WHERE url=%s", (url,))
    try:
        f = fetcher.fetch(url, None if force or not prior else prior["etag"], None if force or not prior else prior["last_modified"])
    except crawler.NotAllowed as exc:
        with db.tx() as cur:
            _upsert_source(cur, url, doc_type="pdf" if url.lower().endswith(".pdf") else "page", status="failed", error=str(exc)[:300], last_checked_at=datetime.now()) if crawler.is_official_url(url) else None
        return {"url": url, "result": "refused", "reason": str(exc)}
    except Exception as exc:                          # network trouble must not wipe existing knowledge
        return {"url": url, "result": "error", "reason": f"{type(exc).__name__}: {exc}"[:200]}
    doc_type = "pdf" if (f.content_type == "application/pdf" or url.lower().endswith(".pdf")) else "page"
    if f.not_modified:
        with db.tx() as cur:
            cur.execute("UPDATE knowledge_sources SET last_checked_at=now(), status='active', error=NULL WHERE url=%s", (url,))
        return {"url": url, "result": "unchanged"}
    if f.status != 200 or not f.content:
        with db.tx() as cur:
            _upsert_source(cur, url, doc_type=doc_type, http_status=f.status, status="failed" if not prior else "active",
                           error=f"HTTP {f.status}", last_checked_at=datetime.now())
        return {"url": url, "result": "http_error", "status": f.status}
    import hashlib
    h = hashlib.sha256(f.content).hexdigest()
    if prior and prior["content_hash"] == h and not force:
        with db.tx() as cur:
            cur.execute("UPDATE knowledge_sources SET last_checked_at=now(), status='active', error=NULL, etag=%s, last_modified=%s WHERE url=%s", (f.etag, f.last_modified, url))
        return {"url": url, "result": "unchanged"}
    if doc_type == "pdf":
        title, text, pages = crawler.extract_pdf(f.content)
        title = title or url.rsplit("/", 1)[-1]
    else:
        title, text, _ = crawler.extract_html(f.content)
        pages = None
    chunks = chunk_document(text)
    if not chunks:
        with db.tx() as cur:
            _upsert_source(cur, url, doc_type=doc_type, http_status=200, status="failed", error="no extractable text", last_checked_at=datetime.now())
        return {"url": url, "result": "no_text"}
    existing = {r["content_hash"]: r["chunk_id"] for r in db.query("SELECT content_hash, chunk_id FROM knowledge_chunks WHERE content_hash = ANY(%s)", ([c["content_hash"] for c in chunks],))}
    new = [c for c in chunks if c["content_hash"] not in existing]
    vectors: list = []                                    # vectors are back-filled in committed batches (resumable, rate-limit friendly)
    added = removed = 0
    with db.tx() as cur:
        sid = _upsert_source(cur, url, title=title, doc_type=doc_type, content_hash=h, etag=f.etag, last_modified=f.last_modified, http_status=200, byte_size=len(f.content),
                             page_count=pages, status="active", error=None, fetched_at=datetime.now(), last_checked_at=datetime.now(), full_text=text if doc_type == "pdf" else text[:200000])
        keep = [c["content_hash"] for c in chunks]
        cur.execute("DELETE FROM knowledge_chunks WHERE source_id=%s AND NOT (content_hash = ANY(%s))", (sid, keep))
        removed = cur.rowcount
        for idx, c in enumerate(chunks):
            if c["content_hash"] in existing:
                cur.execute("UPDATE knowledge_chunks SET chunk_index=%s, section=%s, page_from=%s WHERE content_hash=%s AND source_id=%s",
                            (idx, c["section"], c["page_from"], c["content_hash"], sid))
            else:
                vec = vectors[new.index(c)] if vectors else None
                cur.execute("""INSERT INTO knowledge_chunks (source_id, chunk_index, section, page_from, content, content_hash, embedding)
                               VALUES (%s,%s,%s,%s,%s,%s,%s::vector) ON CONFLICT (content_hash) DO NOTHING""",
                            (sid, idx, c["section"], c["page_from"], c["content"], c["content_hash"], embeddings.to_pg(vec) if vec else None))
                added += cur.rowcount
    return {"url": url, "result": "updated" if prior else "added", "source_id": sid, "chunks": len(chunks), "new_chunks": added, "removed_chunks": removed}


def backfill_embeddings(limit: int = 5000, batch: int = 50) -> int:
    """Embed chunks that have no vector yet, committing after every batch so progress survives crashes/rate limits."""
    done = 0
    while done < limit:
        rows = db.query("SELECT chunk_id, content FROM knowledge_chunks WHERE embedding IS NULL ORDER BY chunk_id LIMIT %s", (batch,))
        if not rows:
            break
        vecs = embeddings.embed([r["content"] for r in rows], batch=batch)
        ok = [(r, v) for r, v in zip(rows, vecs) if v]
        if not ok:
            break                                   # the service refused this batch (quota); full-text search still works, next refresh resumes
        with db.tx() as cur:
            for r, v in ok:
                cur.execute("UPDATE knowledge_chunks SET embedding=%s::vector WHERE chunk_id=%s", (embeddings.to_pg(v), r["chunk_id"]))
        done += len(ok)
    return done


def refresh(actor: Actor | None = None, urls: list[str] | None = None, force: bool = False, import_courses: bool = True, delay: float = crawler.MIN_DELAY) -> dict:
    """Crawl the configured official seeds (or the given official URLs), update chunks, backfill embeddings, then re-run the course import."""
    if actor is not None:
        actor.require_scope("academic")
    if not _running.acquire(blocking=False):
        raise Conflict("A refresh is already running.")
    run_id = None
    try:
        with db.tx() as cur:
            cur.execute("INSERT INTO knowledge_runs (started_by) VALUES (%s) RETURNING run_id", (getattr(actor, "user_id", "SYSTEM"),))
            run_id = cur.fetchone()["run_id"]
        for u in urls or []:
            if not crawler.is_official_url(u):
                raise ValidationFailed(f"Only pages on giki.edu.pk may be refreshed: {u}")
        fetcher = crawler.OfficialFetcher(delay=delay)
        targets = list(dict.fromkeys(urls or (SEED_DOCS + SEED_PAGES + _discover(fetcher, SEED_PAGES))))
        results = [ingest_url(fetcher, u, force) for u in targets]
        backfilled = backfill_embeddings()
        courses_summary = None
        if import_courses:
            from . import catalog_import
            courses_summary = catalog_import.import_from_sources(actor)
        summary = {"sources": len(results), "added": sum(r["result"] == "added" for r in results), "updated": sum(r["result"] == "updated" for r in results),
                   "unchanged": sum(r["result"] == "unchanged" for r in results), "failed": [r for r in results if r["result"] not in ("added", "updated", "unchanged")],
                   "new_chunks": sum(r.get("new_chunks", 0) for r in results), "embeddings_backfilled": backfilled, "http_requests": fetcher.requests_made, "courses": courses_summary}
        with db.tx() as cur:
            cur.execute("UPDATE knowledge_runs SET status='done', finished_at=now(), summary=%s WHERE run_id=%s", (json.dumps(summary, default=str), run_id))
            audit.log(cur, actor or Actor("SYSTEM", "admin", frozenset({"super"}), "system"), "knowledge.refresh", "knowledge", run_id, detail={k: v for k, v in summary.items() if k != "failed"})
        return {"run_id": run_id, **summary}
    except Exception as exc:
        if run_id:
            with db.tx() as cur:
                cur.execute("UPDATE knowledge_runs SET status='failed', finished_at=now(), summary=%s WHERE run_id=%s", (json.dumps({"error": str(exc)[:300]}), run_id))
        raise
    finally:
        _running.release()


def start_refresh_async(actor: Actor, force: bool = False) -> dict:
    actor.require_scope("academic")
    if _running.locked():
        raise Conflict("A refresh is already running.")
    threading.Thread(target=lambda: _safe(refresh, actor, None, force), daemon=True).start()
    return {"started": True}


def _safe(fn, *a):
    try:
        fn(*a)
    except Exception:
        logger.exception("background refresh failed")


def status(actor: Actor) -> dict:
    actor.require_scope("academic")
    return {"running": _running.locked(),
            "totals": db.query_one("""SELECT (SELECT COUNT(*) FROM knowledge_sources WHERE status='active') AS sources, (SELECT COUNT(*) FROM knowledge_chunks) AS chunks,
                                            (SELECT COUNT(*) FROM knowledge_chunks WHERE embedding IS NOT NULL) AS embedded,
                                            (SELECT COUNT(*) FROM course_sources) AS courses_with_source, (SELECT COUNT(*) FROM course_import_flags WHERE status='open') AS open_flags"""),
            "last_run": db.query_one("SELECT run_id, started_at, finished_at, status, summary FROM knowledge_runs ORDER BY run_id DESC LIMIT 1")}


def list_sources(actor: Actor | None = None) -> list[dict]:
    return db.query("""SELECT s.source_id, s.url, s.title, s.doc_type, s.status, s.error, s.page_count, s.fetched_at, s.last_checked_at,
                              (SELECT COUNT(*) FROM knowledge_chunks c WHERE c.source_id=s.source_id) AS chunks FROM knowledge_sources s ORDER BY s.doc_type DESC, s.title""")


# ── Retrieval (hybrid: pgvector cosine + full-text) ─────────

MIN_VECTOR_SIM = 0.62
MIN_FTS_RANK = 0.04


def search(question: str, k: int = 4) -> list[dict]:
    words = _words(question)
    if not words:
        return []
    tsq = " | ".join(dict.fromkeys(words))
    fts = db.query("""SELECT c.chunk_id, ts_rank_cd(c.tsv, to_tsquery('english', %s)) AS rank, c.section, c.page_from, c.content, s.url, s.title, s.fetched_at
                      FROM knowledge_chunks c JOIN knowledge_sources s USING (source_id)
                      WHERE s.status = 'active' AND c.tsv @@ to_tsquery('english', %s) ORDER BY rank DESC LIMIT 12""", (tsq, tsq))
    vec_rows = []
    qv = embeddings.embed([question], task="RETRIEVAL_QUERY")[0]
    if qv:
        v = embeddings.to_pg(qv)
        vec_rows = db.query("""SELECT c.chunk_id, 1 - (c.embedding <=> %s::vector) AS sim, c.section, c.page_from, c.content, s.url, s.title, s.fetched_at
                               FROM knowledge_chunks c JOIN knowledge_sources s USING (source_id) WHERE s.status='active' AND c.embedding IS NOT NULL
                               ORDER BY c.embedding <=> %s::vector LIMIT 12""", (v, v))
    scored: dict[int, dict] = {}
    for r, row in enumerate(vec_rows):
        d = scored.setdefault(row["chunk_id"], {**row, "score": 0.0, "vec": 0.0, "fts": 0.0})
        d["vec"] = float(row["sim"]); d["score"] += 1.0 / (60 + r)
    for r, row in enumerate(fts):
        d = scored.setdefault(row["chunk_id"], {**row, "score": 0.0, "vec": 0.0, "fts": 0.0})
        d["fts"] = float(row["rank"]); d["score"] += 1.0 / (60 + r)
    hits = [d for d in scored.values() if d["vec"] >= MIN_VECTOR_SIM or d["fts"] >= MIN_FTS_RANK]
    hits.sort(key=lambda d: -d["score"])
    return [{"text": d["content"], "section": d["section"], "page": d["page_from"], "source_url": d["url"], "source_title": d["title"],
             "retrieved_at": d["fetched_at"].date().isoformat() if d["fetched_at"] else None, "relevance": round(d["score"] * 60, 3),
             "similarity": round(d["vec"], 3), "text_match": round(d["fts"], 3)} for d in hits[:max(1, min(k, 8))]]


def ask(question: str, limit: int = 4) -> dict:
    question = (question or "").strip()
    if len(question) < 3:
        raise ValidationFailed("Please enter a question of at least 3 characters.")
    passages = search(question, limit)
    if not passages:
        return {"question": question, "found": False, "passages": [], "source": "official GIKI documents (giki.edu.pk)",
                "answer": "I could not find this in the official GIKI documents I have indexed (giki.edu.pk). I will not guess; please check with the relevant office "
                          "(Registrar / Academic Office) or the Student Handbook."}
    best = passages[0]
    ref = f"{best['source_title'] or best['source_url']}" + (f", section {best['section']}" if best["section"] else "") + (f", p. {best['page']}" if best["page"] else "")
    return {"question": question, "found": True, "passages": passages, "source": "official GIKI documents (giki.edu.pk)",
            "answer": f"{best['text']}\n\nSource: {ref} — {best['source_url']} (retrieved {best['retrieved_at']})"}
