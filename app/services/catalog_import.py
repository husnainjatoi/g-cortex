"""Validated import of official course-catalogue data (GIKI prospectus) into the Course Management database.

Rules (all enforced here):
* a course that does not exist is created as `catalog_only` (visible, usable for prerequisites/planning, NOT open for registration until an
  administrator schedules it) with its source URL/page/section/retrieval date recorded;
* an existing course is NEVER overwritten: differences in title, credits or prerequisites are raised as review flags for an administrator;
* prerequisites are linked only when the official text is an unambiguous list of codes that exist in the catalogue and create no cycle;
  ambiguous / unresolved / missing prerequisite information is flagged, never guessed.
"""
import hashlib
import json
import re

from app import db
from app.knowledge.catalog_parser import parse_courses
from . import audit
from .actor import Actor
from .audit import op
from .errors import NotFound, ValidationFailed


def _fp(code: str, kind: str, detail: dict) -> str:
    return hashlib.md5(f"{code}|{kind}|{json.dumps(detail, sort_keys=True, default=str)}".encode()).hexdigest()


def _flag(cur, code, kind, detail, url):
    cur.execute("""INSERT INTO course_import_flags (course_code, kind, detail, source_url, fingerprint) VALUES (%s,%s,%s,%s,%s) ON CONFLICT (fingerprint) DO NOTHING""",
                (code, kind, json.dumps(detail, default=str), url, _fp(code, kind, detail)))
    return cur.rowcount


def _would_cycle(cur, code: str, prereqs: list[str]) -> bool:
    for p in prereqs:
        if p == code:
            return True
        seen, stack = set(), [p]
        while stack:
            x = stack.pop()
            cur.execute("SELECT prereq_code FROM course_prerequisites WHERE course_code=%s", (x,))
            for r in cur.fetchall():
                if r["prereq_code"] == code:
                    return True
                if r["prereq_code"] not in seen:
                    seen.add(r["prereq_code"]); stack.append(r["prereq_code"])
    return False


def import_courses(actor: Actor | None, parsed: list[dict], source: dict) -> dict:
    """`source` = {source_id, url, title, retrieved_at}. Idempotent: re-running changes nothing that is already recorded."""
    stats = {"parsed": len(parsed), "created": 0, "matched": 0, "conflicts": 0, "prereq_links": 0, "flags_raised": 0, "skipped_duplicates": 0}
    seen: dict[str, dict] = {}
    for c in parsed:                                     # duplicate codes inside the source
        if c["course_code"] in seen:
            if seen[c["course_code"]]["title"].lower() != c["title"].lower() or seen[c["course_code"]]["credits"] != c["credits"]:
                with db.tx() as cur:
                    stats["flags_raised"] += _flag(cur, c["course_code"], "duplicate_in_source", {"first": seen[c["course_code"]]["title"], "second": c["title"], "credits": [seen[c["course_code"]]["credits"], c["credits"]]}, source["url"])
            stats["skipped_duplicates"] += 1
            continue
        seen[c["course_code"]] = c
    with db.tx() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(hashtext('catalog_import'))")
        for code, c in seen.items():
            cur.execute("SELECT course_code, title, credits, catalog_only FROM courses WHERE course_code=%s", (code,))
            ex = cur.fetchone()
            section = f"{c.get('faculty') or ''} · {c['category']} courses".strip(" ·")
            if ex is None:
                cur.execute("""INSERT INTO courses (course_code, title, credits, slot, category, domains, description, capacity, is_active, catalog_only, department, lecture_hours, lab_hours)
                               VALUES (%s,%s,%s,NULL,%s,NULL,%s,0,TRUE,TRUE,%s,%s,%s)""",
                            (code, c["title"][:100], c["credits"], c["category"], c["description"], c.get("faculty"), c["lecture_hours"], c["lab_hours"]))
                outcome = "imported"; stats["created"] += 1
            else:
                same = ex["title"].strip().lower() == c["title"].strip().lower() and ex["credits"] == c["credits"]
                outcome = "matched" if same else "conflict"
                stats["matched" if same else "conflicts"] += 1
                if not same:
                    if ex["title"].strip().lower() != c["title"].strip().lower():
                        stats["flags_raised"] += _flag(cur, code, "title_mismatch", {"existing": ex["title"], "official": c["title"]}, source["url"])
                    if ex["credits"] != c["credits"]:
                        stats["flags_raised"] += _flag(cur, code, "credits_mismatch", {"existing": ex["credits"], "official": c["credits"]}, source["url"])
            cur.execute("""INSERT INTO course_sources (course_code, source_id, source_url, source_title, source_section, source_page, official_title, official_credits,
                           official_prereq_text, prereq_status, outcome) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                           ON CONFLICT (course_code) DO UPDATE SET last_checked_at=now(), source_id=EXCLUDED.source_id, source_url=EXCLUDED.source_url, source_title=EXCLUDED.source_title,
                           source_section=EXCLUDED.source_section, source_page=EXCLUDED.source_page, official_title=EXCLUDED.official_title, official_credits=EXCLUDED.official_credits,
                           official_prereq_text=EXCLUDED.official_prereq_text, prereq_status=EXCLUDED.prereq_status, outcome=EXCLUDED.outcome""",
                        (code, source.get("source_id"), source["url"], source.get("title"), section, c["page"], c["title"], c["credits"], c["prereq"]["raw"], c["prereq"]["status"], outcome))
        # prerequisites second, once every code of this import exists
        for code, c in seen.items():
            pr = c["prereq"]
            cur.execute("SELECT catalog_only FROM courses WHERE course_code=%s", (code,))
            is_catalog = cur.fetchone()["catalog_only"]
            cur.execute("SELECT prereq_code FROM course_prerequisites WHERE course_code=%s", (code,))
            current = sorted(r["prereq_code"] for r in cur.fetchall())
            if pr["status"] == "ambiguous":
                stats["flags_raised"] += _flag(cur, code, "prereq_ambiguous", {"official_text": pr["raw"], "codes_mentioned": pr["codes"]}, source["url"]); continue
            if pr["status"] == "missing":
                continue                                    # the source states nothing: unknown, not "none"
            if not is_catalog:                              # an existing/scheduled course: compare, never change
                if current != pr["codes"]:
                    stats["flags_raised"] += _flag(cur, code, "prereq_mismatch", {"existing": current, "official": pr["codes"], "official_text": pr["raw"]}, source["url"])
                continue
            unresolved = []
            for p in pr["codes"]:
                cur.execute("SELECT 1 FROM courses WHERE course_code=%s", (p,))
                if not cur.fetchone():
                    unresolved.append(p)
            if unresolved:
                stats["flags_raised"] += _flag(cur, code, "prereq_unresolved", {"official_text": pr["raw"], "missing_from_catalogue": unresolved}, source["url"]); continue
            if current == pr["codes"]:
                continue
            if _would_cycle(cur, code, pr["codes"]):
                stats["flags_raised"] += _flag(cur, code, "prereq_cycle", {"official_text": pr["raw"]}, source["url"]); continue
            cur.execute("DELETE FROM course_prerequisites WHERE course_code=%s", (code,))
            for p in pr["codes"]:
                cur.execute("INSERT INTO course_prerequisites (course_code, prereq_code) VALUES (%s,%s) ON CONFLICT DO NOTHING", (code, p)); stats["prereq_links"] += 1
        audit.log(cur, actor or Actor("SYSTEM", "admin", frozenset({"super"}), "system"), "catalog.import", "knowledge", str(source.get("title") or "catalogue")[:60], detail={**stats, "source_url": source["url"]})
    return stats


def import_from_sources(actor: Actor | None = None) -> dict:
    """Re-parse every stored official catalogue document (the undergraduate prospectus) and import."""
    out = {"documents": 0}
    total: dict = {}
    for s in db.query("SELECT source_id, url, title, fetched_at, full_text FROM knowledge_sources WHERE status='active' AND doc_type='pdf' AND url ~* 'UG_Prospectus' AND full_text IS NOT NULL"):
        res = parse_courses(s["full_text"])
        stats = import_courses(actor, res["courses"], {"source_id": s["source_id"], "url": s["url"], "title": s["title"], "retrieved_at": s["fetched_at"]})
        stats["rejected_entries"] = len(res["rejected"])
        out["documents"] += 1
        for k, v in stats.items():
            total[k] = total.get(k, 0) + v
    return {**out, **total}


# ── Administrator review ────────────────────────────────────

def list_flags(actor: Actor, status: str | None = "open") -> list[dict]:
    actor.require_scope("academic")
    return db.query(f"""SELECT f.*, c.title AS current_title, c.credits AS current_credits FROM course_import_flags f LEFT JOIN courses c USING (course_code)
                        {'WHERE f.status=%s' if status else ''} ORDER BY f.created_at DESC, f.flag_id DESC LIMIT 500""", (status,) if status else ())


@op("catalog.flag_resolve", "course", "flag_id")
def resolve_flag(actor: Actor, flag_id: int, action: str) -> dict:
    """action='apply' updates the course from the verified official record; 'dismiss' keeps the current data. Both are audited."""
    actor.require_scope("academic")
    if action not in ("apply", "dismiss"):
        raise ValidationFailed("Action must be apply or dismiss.")
    with db.tx() as cur:
        cur.execute("SELECT * FROM course_import_flags WHERE flag_id=%s FOR UPDATE", (flag_id,))
        f = cur.fetchone()
        if not f:
            raise NotFound("Flag not found.")
        if f["status"] != "open":
            raise ValidationFailed(f"Flag is already {f['status']}.")
        d = f["detail"]
        if action == "apply":
            if f["kind"] == "title_mismatch":
                cur.execute("UPDATE courses SET title=%s WHERE course_code=%s", (d["official"][:100], f["course_code"]))
            elif f["kind"] == "credits_mismatch":
                cur.execute("UPDATE courses SET credits=%s WHERE course_code=%s", (d["official"], f["course_code"]))
            elif f["kind"] == "prereq_mismatch":
                from .courses import _set_prereqs
                _set_prereqs(cur, f["course_code"], d["official"])
            else:
                raise ValidationFailed("This kind of flag needs manual editing in Course Administration; dismiss it once handled.")
        cur.execute("UPDATE course_import_flags SET status=%s, resolved_by=%s, resolved_at=now() WHERE flag_id=%s", ("applied" if action == "apply" else "dismissed", actor.user_id, flag_id))
        audit.log(cur, actor, "catalog.flag_resolve", "course", f["course_code"], detail={"flag_id": flag_id, "kind": f["kind"], "action": action})
    return {"ok": True, "flag_id": flag_id, "status": "applied" if action == "apply" else "dismissed"}


def course_source(course_code: str) -> dict | None:
    return db.query_one("SELECT source_url, source_title, source_section, source_page, retrieved_at, last_checked_at, official_prereq_text, prereq_status, outcome FROM course_sources WHERE course_code=%s", (course_code.upper(),))
