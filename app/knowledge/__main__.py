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
sub.add_parser("status")
a = ap.parse_args()
if a.cmd == "refresh":
    print(json.dumps(knowledge.refresh(None, a.url, a.force, not a.no_courses), indent=2, default=str))
else:
    from app import db
    print(json.dumps(db.query_one("SELECT (SELECT COUNT(*) FROM knowledge_sources) AS sources, (SELECT COUNT(*) FROM knowledge_chunks) AS chunks, (SELECT COUNT(*) FROM knowledge_chunks WHERE embedding IS NOT NULL) AS embedded, (SELECT COUNT(*) FROM course_sources) AS courses"), indent=2))
