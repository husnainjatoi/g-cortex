"""python -m app.knowledge refresh [--force] [--no-courses] [--url URL ...]  - refresh the official GIKI knowledge base."""
import argparse
import json
import logging

from app.services import knowledge

logging.basicConfig(level=logging.INFO, format="%(message)s")
ap = argparse.ArgumentParser(prog="python -m app.knowledge")
sub = ap.add_subparsers(dest="cmd", required=True)
r = sub.add_parser("refresh")
r.add_argument("--force", action="store_true", help="re-process documents even if unchanged")
r.add_argument("--no-courses", action="store_true", help="skip the course-catalogue import")
r.add_argument("--url", action="append", help="refresh only these giki.edu.pk URLs")
sub.add_parser("import-curriculum", help="import the semester-wise degree plans, tracks and official minor from the stored Undergraduate Prospectus 2025")
sub.add_parser("embed", help="back-fill missing embeddings (resumable; respects API quota)")
sub.add_parser("status")
sub.add_parser("verify", help="re-check every imported course against the stored official source page it cites")
a = ap.parse_args()
if a.cmd == "refresh":
    print(json.dumps(knowledge.refresh(None, a.url, a.force, not a.no_courses), indent=2, default=str))
elif a.cmd == "import-curriculum":
    from app.services import curriculum
    print(json.dumps(curriculum.import_prospectus_2025(None), indent=2, default=str))
elif a.cmd == "embed":
    print(json.dumps({"embedded_now": knowledge.backfill_embeddings()}))
elif a.cmd == "verify":
    import re
    from app import db
    bad, ok = [], 0
    pages_cache: dict[int, dict[int, str]] = {}
    for r in db.query("""SELECT cs.course_code, cs.source_id, cs.source_page, cs.official_title, cs.official_credits, cs.official_prereq_text, cs.prereq_status, cs.source_url, c.title, c.credits, c.catalog_only
                         FROM course_sources cs JOIN courses c USING (course_code) ORDER BY 1"""):
        if r["source_id"] not in pages_cache:
            text = db.query_one("SELECT full_text FROM knowledge_sources WHERE source_id=%s", (r["source_id"],))["full_text"]
            pages_cache[r["source_id"]] = {int(m.group(1)): m.group(2) for m in re.finditer(r"<<PAGE (\d+)>>(.*?)(?=<<PAGE \d+>>|\Z)", text, re.S)}
        pg = pages_cache[r["source_id"]]
        page = re.sub(r"\s+", " ", pg.get(r["source_page"], ""))
        page_and_next = page + " " + re.sub(r"\s+", " ", pg.get(r["source_page"] + 1, ""))     # an entry may continue on the next page
        problems = []
        if not r["source_url"].startswith("https://giki.edu.pk/"):
            problems.append("source not official")
        if not re.search(re.escape(r["course_code"]) + r"(?:\s*(?:/|&)\s*[A-Z]{2,4}\s?\d{3})?\s+" + re.escape(re.sub(r"\s+", " ", r["official_title"])[:25]), page):
            problems.append("code+title not found on cited page")
        if not re.search(r"\(\s*\d\s+\d\s+%d\s*\)" % r["official_credits"], page):
            problems.append("credit hours not found on cited page")
        for code in re.findall(r"[A-Z]{2,4}\d{3}", r["official_prereq_text"] or ""):
            if code not in page_and_next:
                problems.append(f"prerequisite {code} not on cited page")
        if r["catalog_only"] and (r["title"] != r["official_title"][:100] or r["credits"] != r["official_credits"]):
            problems.append("stored course differs from the official record")
        if r["catalog_only"]:
            have = sorted(x["prereq_code"] for x in db.query("SELECT prereq_code FROM course_prerequisites WHERE course_code=%s", (r["course_code"],)))
            allowed = set(re.findall(r"[A-Z]{2,4}\d{3}", r["official_prereq_text"] or ""))
            if r["prereq_status"] in ("ambiguous", "missing") and have:
                problems.append("prerequisites linked although the official text is ambiguous/absent")
            if not set(have) <= allowed:
                problems.append(f"invented prerequisite(s): {sorted(set(have) - allowed)}")
        if problems:
            bad.append({"course": r["course_code"], "problems": problems})
        else:
            ok += 1
    print(json.dumps({"verified_ok": ok, "problems": bad}, indent=2))
else:
    from app import db
    print(json.dumps(db.query_one("SELECT (SELECT COUNT(*) FROM knowledge_sources) AS sources, (SELECT COUNT(*) FROM knowledge_chunks) AS chunks, (SELECT COUNT(*) FROM knowledge_chunks WHERE embedding IS NOT NULL) AS embedded, (SELECT COUNT(*) FROM course_sources) AS courses"), indent=2))
