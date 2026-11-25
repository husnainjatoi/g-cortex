"""Live checks against giki.edu.pk: the societies page, the Undergraduate Prospectus 2025 (retrieval + parser accuracy), official-domain and redirect restrictions."""
import socket

import pytest

from app import db
from app.knowledge import crawler, plan_parser
from app.services import curriculum, societies

PDF = curriculum.PROSPECTUS_2025_URL
pytestmark = pytest.mark.network


@pytest.fixture(scope="module")
def reachable():
    try:
        socket.create_connection(("giki.edu.pk", 443), timeout=6).close()
    except OSError:
        pytest.skip("giki.edu.pk not reachable")


@pytest.fixture(scope="module")
def prospectus(reachable):
    f = crawler.OfficialFetcher(delay=1.5).fetch(PDF)
    assert f.status == 200 and f.content and "pdf" in f.content_type
    title, text, pages = crawler.extract_pdf(f.content)
    return {"fetched": f, "text": text, "pages": pages}


def test_prospectus_2025_is_retrieved_from_the_official_domain(prospectus):
    assert crawler.is_official_url(prospectus["fetched"].url) and prospectus["fetched"].url == PDF
    assert prospectus["pages"] == 306 and "UNDERGRADUATE PROSPECTUS 2025" in prospectus["text"].upper()


def test_every_undergraduate_programme_is_parsed_and_credit_totals_reconcile(prospectus):
    res = plan_parser.parse_plans(prospectus["text"])
    codes = {p["code"] for p in res["programs"]}
    assert codes == {"BSAI", "BSCE", "BSCS", "BSCyS", "BSDS", "BSSE", "BSEE", "BSMSE", "BSChE", "BSME", "BSCV", "BSMS"}
    for p in res["programs"]:
        assert p["semesters_found"] == [1, 2, 3, 4, 5, 6, 7, 8], p["code"]
        assert len(p["rows"]) > 40 and len(p["declared"]) >= 8
    mismatched = {p["code"]: plan_parser.check_totals(p) for p in res["programs"] if plan_parser.check_totals(p)}
    assert set(mismatched) <= {"BSCE", "BSMSE"}, mismatched                                  # only the two rows the prospectus itself prints garbled
    assert {(x["program"], x["line"].split()[0]) for x in res["rejected"]} == {("BSCE", "S494"), ("BSMSE", "M453")}      # reported for review, never guessed
    by = {p["code"]: p for p in res["programs"]}
    assert {t for t in by["BSEE"]["tracks"]} == {"Digital Design & Embedded Systems", "Smart Communication & Signal Processing", "Smart Grid"} and by["BSEE"]["first_track_label"] == "Robotics & Autonomous Systems"
    assert any(r["code"] == "MS101" for r in by["BSMS"]["rows"]) and any(r["code"] == "CS101" for r in by["BSCS"]["rows"])


def test_official_minor_is_found_in_the_prospectus(prospectus):
    pool = curriculum._minor_pool(prospectus["text"])
    assert {"MS412", "MS496"} <= {m["code"] for m in pool}
    assert "Minors in Management for Engineers" in prospectus["text"]


def test_course_descriptions_are_extracted(prospectus):
    d = curriculum._descriptions(prospectus["text"])
    assert len(d) > 150 and "communication" in d["HM101"].lower()


def test_non_official_urls_and_offsite_redirects_are_refused(reachable):
    f = crawler.OfficialFetcher(delay=0.1)
    for bad in ("https://example.com/Undergraduate-Prospectus-2025.pdf", "https://giki.edu.pk.evil.example/x.pdf", "http://127.0.0.1/x"):
        with pytest.raises(crawler.NotAllowed):
            f.fetch(bad)

    class R:                                                    # an official URL that redirects away must be refused at that hop
        status_code, headers = 302, {"Location": "https://evil.example/file.pdf"}
        def close(self): pass
    class S:
        headers = {}
        def get(self, *a, **k): return R()
    g = crawler.OfficialFetcher(delay=0, session=S()); g._robots["giki.edu.pk"] = type("RP", (), {"can_fetch": lambda *a: True})()
    with pytest.raises(crawler.NotAllowed):
        g.fetch(PDF)


def test_live_societies_page_import_is_complete_and_idempotent(reachable, actors):
    r1 = societies.import_official(actors["req"])
    assert r1["listed"] >= 20 and r1["source"].startswith("https://giki.edu.pk/")
    rows = db.query("SELECT name, logo_url, source_url, retrieved_at FROM societies WHERE origin='official'")
    assert len(rows) == r1["listed"] and sum(1 for x in rows if x["logo_url"]) >= 8
    assert all(x["source_url"].startswith("https://giki.edu.pk/") and x["retrieved_at"] for x in rows)
    r2 = societies.import_official(actors["req"])
    assert r2["created"] == 0 and db.query_one("SELECT COUNT(*) AS n FROM societies WHERE origin='official'")["n"] == len(rows)
    with db.tx() as cur:
        cur.execute("DELETE FROM society_memberships WHERE society_id IN (SELECT society_id FROM societies WHERE origin IN ('official','manual'))")
        cur.execute("DELETE FROM societies WHERE origin='official'"); cur.execute("UPDATE societies SET status='active' WHERE origin='legacy'")
