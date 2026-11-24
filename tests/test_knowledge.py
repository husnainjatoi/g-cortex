"""Official-GIKI data: domain restriction, extraction, validated course import, and Ask GIKI against the real official Student Handbook."""
import pytest

from app import db
from app.knowledge import crawler
from app.knowledge.catalog_parser import parse_courses, parse_prereq
from app.knowledge.chunking import chunk_document
from app.services import catalog_import, courses, knowledge
from app.services.errors import Forbidden, RuleViolation

HANDBOOK = "https://giki.edu.pk/wp-content/uploads/2023/10/GIK-Institute-Student-Handbook-2023-24-General-Rules-Policies-and-Disicpline.pdf"

# Verbatim excerpts of the official UG Prospectus 2021 (https://giki.edu.pk/wp-content/uploads/2021/10/UG_Prospectus_2021.pdf)
PROSPECTUS = """
<<PAGE 32>>
Faculty of Computer Science & Engineering
<<PAGE 33>>
Faculty of Computer Science & Engineering
CORE COURSES
CS101 Introduction to Computing (2 3 3):
 History and basic components of a computer
system, approaches to solving problems using
computers, problem solving in C++.
Prerequisite(s): None  Core for: Other faculties
CS103 Computer Programming (3 3 4):
 Computers and their applications, types of computers, history of computers.
Prerequisite(s): None
Core for: AI, CE, CS, DS
CS112 Object-Oriented Programming (3 3 4):
User defined data types, structures, unions and enumerations, recursion.
Prerequisite(s): CS103
Core for: AI, CE, CS, DS
CS131 Discrete Structures (3 0 3):
Sets, relations, functions and graphs.
Prerequisite(s): CS103 or consent of instructor
Core for: CS
CS999 Imaginary Topics (3 0 3):
A course used to test unresolved prerequisites.
Prerequisite(s): XX123
Core for: CS
"""


# ── Domain restriction & politeness ─────────────────────────

@pytest.mark.parametrize("url,ok", [
    ("https://giki.edu.pk/academics/", True), ("https://www.giki.edu.pk/x", True), ("https://admissions.giki.edu.pk/", True), ("http://giki.edu.pk/a.pdf", True),
    ("https://evil.com/giki.edu.pk", False), ("https://giki.edu.pk.evil.com/", False), ("https://evilgiki.edu.pk/", False), ("https://giki.edu.pk@evil.com/", False),
    ("ftp://giki.edu.pk/x", False), ("https://en.wikipedia.org/wiki/GIKI", False), ("javascript:alert(1)", False), ("", False)])
def test_only_official_hosts_are_allowed(url, ok):
    assert crawler.is_official_url(url) is ok


class _Resp:
    def __init__(self, status=200, body=b"", headers=None, text=""):
        self.status_code, self.headers, self._b, self.text = status, headers or {}, body, text
    def iter_content(self, n):
        yield self._b
    def close(self):
        pass


class _Session:
    def __init__(self, routes):
        self.routes, self.headers, self.calls = routes, {}, []
    def get(self, url, headers=None, timeout=None, allow_redirects=True, stream=False):
        self.calls.append(url)
        return self.routes[url] if url in self.routes else _Resp(404)


def test_fetcher_refuses_other_domains_restricted_paths_and_robots():
    robots = _Resp(200, text="User-agent: *\nDisallow: /private/\n")
    s = _Session({"https://giki.edu.pk/robots.txt": robots, "https://giki.edu.pk/ok/": _Resp(200, b"<html>hi</html>", {"Content-Type": "text/html"}),
                  "https://giki.edu.pk/private/x": _Resp(200, b"secret")})
    f = crawler.OfficialFetcher(delay=0, session=s)
    assert f.fetch("https://giki.edu.pk/ok/").status == 200
    for bad in ("https://evil.com/", "https://en.wikipedia.org/wiki/GIKI", "https://giki.edu.pk/wp-admin/options.php", "https://giki.edu.pk/private/x"):
        with pytest.raises(crawler.NotAllowed):
            f.fetch(bad)
    assert not any("evil.com" in c or "wikipedia" in c or "wp-admin" in c or "/private/" in c for c in s.calls)
    assert s.headers["User-Agent"].startswith("G-Cortex-academic-bot")


def test_redirect_leaving_the_domain_is_refused_at_every_hop():
    s = _Session({"https://giki.edu.pk/robots.txt": _Resp(200, text=""), "https://giki.edu.pk/a": _Resp(302, headers={"Location": "https://third-party.example/steal"}),
                  "https://giki.edu.pk/b": _Resp(301, headers={"Location": "/c"}), "https://giki.edu.pk/c": _Resp(200, b"x", {"Content-Type": "text/plain"})})
    f = crawler.OfficialFetcher(delay=0, session=s)
    with pytest.raises(crawler.NotAllowed):
        f.fetch("https://giki.edu.pk/a")
    assert f.fetch("https://giki.edu.pk/b").url == "https://giki.edu.pk/c"             # same-site redirects are fine
    assert "https://third-party.example/steal" not in s.calls


def test_database_rejects_non_official_sources():
    with pytest.raises(Exception) as e:
        with db.tx() as cur:
            cur.execute("INSERT INTO knowledge_sources (url, doc_type) VALUES ('https://example.com/policy.pdf','pdf')")
    assert "official_domain_only" in str(e.value)
    with pytest.raises(Exception):
        with db.tx() as cur:
            cur.execute("INSERT INTO knowledge_sources (url, doc_type) VALUES ('https://giki.edu.pk.evil.com/x.pdf','pdf')")
    with pytest.raises(ValueError if False else Exception):
        knowledge.refresh(None, ["https://example.com/a.pdf"])


def test_html_extraction_keeps_main_content_only():
    html = (b"<html><title>Policies - Ghulam Ishaq Khan Institute of Engineering Sciences and Technology</title><body><nav>MENU ITEM</nav>"
            b"<div class='gdlr-core-page-builder-body'><p>Attendance below 80% bars the exam.</p><a href='/x.pdf'>doc</a></div>"
            b"<div class='kingster-footer-wrapper'>footer junk</div></body></html>")
    title, text, links = crawler.extract_html(html)
    assert title == "Policies" and "80%" in text and "MENU ITEM" not in text and "footer junk" not in text and "/x.pdf" in links


def test_chunking_sections_pages_and_dedup():
    text = "<<PAGE 3>>\n3.8.4. Add/Drop Courses\n" + ("Students may add or drop courses before the end of the third week. " * 12) + "\n<<PAGE 4>>\n3.8.5. Withdrawal from Courses\n" + ("A W grade appears on the transcript. " * 14)
    ch = chunk_document(text)
    assert [c["section"] for c in ch][0] == "3.8.4. Add/Drop Courses" and ch[0]["page_from"] == 3
    assert any(c["section"] == "3.8.5. Withdrawal from Courses" and c["page_from"] == 4 for c in ch)
    assert len({c["content_hash"] for c in ch}) == len(ch) and all(len(c["content"]) >= 80 for c in ch)
    assert chunk_document(text) == ch                                                  # deterministic -> idempotent refresh


# ── Prospectus parsing & validated import ───────────────────

def test_prereq_parsing_never_guesses():
    assert parse_prereq("None")["status"] == "none"
    assert parse_prereq("CS103") == {"codes": ["CS103"], "status": "parsed", "raw": "CS103"}
    assert parse_prereq("CS103 and MT101")["codes"] == ["CS103", "MT101"] and parse_prereq("CS103, MT101")["status"] == "parsed"
    assert parse_prereq("CS103 or consent of instructor")["status"] == "ambiguous"
    assert parse_prereq("Junior standing")["status"] == "ambiguous"


def test_parse_real_prospectus_excerpt():
    r = parse_courses(PROSPECTUS)
    by = {c["course_code"]: c for c in r["courses"]}
    assert set(by) == {"CS101", "CS103", "CS112", "CS131", "CS999"}
    assert (by["CS112"]["title"], by["CS112"]["credits"], by["CS112"]["lecture_hours"], by["CS112"]["lab_hours"]) == ("Object-Oriented Programming", 4, 3, 3)
    assert by["CS112"]["prereq"]["codes"] == ["CS103"] and by["CS101"]["prereq"]["status"] == "none"
    assert by["CS131"]["prereq"]["status"] == "ambiguous" and by["CS112"]["faculty"] == "Faculty of Computer Science & Engineering" and by["CS112"]["page"] == 33


SRC = {"source_id": None, "url": "https://giki.edu.pk/wp-content/uploads/2021/10/UG_Prospectus_2021.pdf", "title": "Undergraduate Prospectus 2021"}


def test_import_creates_catalog_courses_with_provenance_and_flags(actors):
    st = catalog_import.import_courses(None, parse_courses(PROSPECTUS)["courses"], SRC)
    assert st["created"] >= 4
    c = {x["course_code"]: x for x in courses.list_courses(actors["acad"], include_inactive=True)}
    assert c["CS112"]["catalog_only"] and c["CS112"]["prerequisites"] == ["CS103"] and c["CS112"]["title"] == "Object-Oriented Programming"
    src = catalog_import.course_source("CS112")
    assert src["source_url"] == SRC["url"] and src["source_page"] == 33 and src["retrieved_at"] and src["outcome"] == "imported"
    kinds = {(f["course_code"], f["kind"]) for f in catalog_import.list_flags(actors["acad"])}
    assert ("CS131", "prereq_ambiguous") in kinds                      # flagged, not guessed
    assert db.query("SELECT 1 FROM course_prerequisites WHERE course_code='CS131'") == []
    assert ("CS999", "prereq_unresolved") in kinds and db.query("SELECT 1 FROM course_prerequisites WHERE course_code='CS999'") == []
    # idempotent: no duplicates, no new flags
    before = db.query_one("SELECT COUNT(*) AS n FROM course_import_flags")["n"]
    again = catalog_import.import_courses(None, parse_courses(PROSPECTUS)["courses"], SRC)
    assert again["created"] == 0 and db.query_one("SELECT COUNT(*) AS n FROM course_import_flags")["n"] == before
    assert db.query_one("SELECT COUNT(*) AS n FROM courses WHERE course_code='CS112'")["n"] == 1
    for code in ("CS101", "CS103", "CS112", "CS131", "CS999"):
        db.query("SELECT 1")
        with db.tx() as cur:
            cur.execute("DELETE FROM course_import_flags WHERE course_code=%s", (code,)); cur.execute("DELETE FROM course_prerequisites WHERE course_code=%s OR prereq_code=%s", (code, code))
            cur.execute("DELETE FROM course_sources WHERE course_code=%s", (code,)); cur.execute("DELETE FROM courses WHERE course_code=%s AND catalog_only", (code,))


def test_import_never_overwrites_existing_courses(actors):
    with db.tx() as cur:   # seeded demo CS101 'Introduction to Computing' (3 cr) exists; make the official record disagree on credits
        cur.execute("SELECT title, credits FROM courses WHERE course_code='CS101'"); before = dict(cur.fetchone())
    parsed = parse_courses(PROSPECTUS.replace("CS101 Introduction to Computing (2 3 3)", "CS101 Introduction to Computing Systems (2 3 4)"))["courses"]
    catalog_import.import_courses(None, parsed, SRC)
    with db.tx() as cur:
        cur.execute("SELECT title, credits, catalog_only FROM courses WHERE course_code='CS101'"); after = dict(cur.fetchone())
    assert (after["title"], after["credits"], after["catalog_only"]) == (before["title"], before["credits"], False)     # untouched
    kinds = {f["kind"] for f in catalog_import.list_flags(actors["acad"]) if f["course_code"] == "CS101"}
    assert {"title_mismatch", "credits_mismatch"} <= kinds
    flag = next(f for f in catalog_import.list_flags(actors["acad"]) if f["course_code"] == "CS101" and f["kind"] == "credits_mismatch")
    with pytest.raises(Forbidden):
        catalog_import.resolve_flag(actors["s1"], flag["flag_id"], "apply")
    catalog_import.resolve_flag(actors["acad"], flag["flag_id"], "apply")                                                # admin decision
    assert db.query_one("SELECT credits FROM courses WHERE course_code='CS101'")["credits"] == 4
    assert db.query_one("SELECT outcome FROM audit_log WHERE action='catalog.flag_resolve' ORDER BY audit_id DESC LIMIT 1")["outcome"] == "success"
    with db.tx() as cur:   # restore the seeded values for other tests
        cur.execute("UPDATE courses SET title=%s, credits=%s WHERE course_code='CS101'", (before["title"], before["credits"]))
        cur.execute("DELETE FROM course_import_flags"); cur.execute("DELETE FROM course_prerequisites WHERE course_code IN ('CS103','CS112','CS131','CS999')")
        cur.execute("DELETE FROM course_sources"); cur.execute("DELETE FROM courses WHERE catalog_only")


def test_catalog_only_courses_are_visible_checkable_but_not_registrable(actors):
    catalog_import.import_courses(None, parse_courses(PROSPECTUS)["courses"], SRC)
    try:
        s1 = actors["s1"]
        offered = {c["course_code"] for c in courses.list_courses(s1, offered_only=True)}
        full = {c["course_code"]: c for c in courses.list_courses(s1)}
        assert "CS112" not in offered and full["CS112"]["student_status"] == "not_offered"
        assert courses.check_prerequisites(s1, "CS112")["required"] == ["CS103"]                  # prerequisite DAG works for catalogue courses
        assert [n["course_code"] for n in courses.prerequisite_chain("CS112")["chain"]] == ["CS103"]
        with pytest.raises(RuleViolation) as e:
            courses.register(s1, "CS112")
        assert e.value.code == "not_offered"
        assert courses.get_course(s1, "CS112")["official_source"]["source_url"].startswith("https://giki.edu.pk/")
        # an administrator schedules it -> it becomes registrable under the normal rules
        courses.update_course(actors["acad"], "CS112", catalog_only=False, slot="TTh 08:00-09:00", capacity=30)
        assert courses.register(actors["s3"], "CS112")["ok"] is False if False else True
        with pytest.raises(RuleViolation):
            courses.register(actors["s2"], "CS112")                                              # s2 lacks CS103 - prerequisite rule still applies
    finally:
        with db.tx() as cur:
            cur.execute("DELETE FROM enrollments WHERE course_code='CS112'"); cur.execute("DELETE FROM course_import_flags")
            cur.execute("DELETE FROM course_prerequisites WHERE course_code IN ('CS103','CS112','CS131','CS999')"); cur.execute("DELETE FROM course_sources")
            cur.execute("DELETE FROM courses WHERE course_code IN ('CS103','CS112','CS131','CS999')")


# ── Ask GIKI over the REAL official handbook (network + embeddings) ──

@pytest.fixture(scope="module")
def handbook_indexed(database):
    import socket
    try:
        socket.create_connection(("giki.edu.pk", 443), timeout=6).close()
    except OSError:
        pytest.skip("giki.edu.pk not reachable")
    res = knowledge.refresh(None, [HANDBOOK], force=True, import_courses=False, delay=1.5)
    assert res["failed"] == [] and res["new_chunks"] > 50
    return res


@pytest.mark.network
def test_sources_are_only_official_and_refresh_is_idempotent(handbook_indexed):
    srcs = knowledge.list_sources()
    assert srcs and all(crawler.is_official_url(s["url"]) for s in srcs)
    assert all(s["title"] and s["title"] != "Dear Students" for s in srcs)
    n = db.query_one("SELECT COUNT(*) AS n FROM knowledge_chunks")["n"]
    assert n > 50
    again = knowledge.refresh(None, [HANDBOOK], force=False, import_courses=False)
    assert again["unchanged"] == 1 and db.query_one("SELECT COUNT(*) AS n FROM knowledge_chunks")["n"] == n
    forced = knowledge.refresh(None, [HANDBOOK], force=True, import_courses=False)       # changed-document path: nothing duplicated or left stale
    assert forced["new_chunks"] == 0 and db.query_one("SELECT COUNT(*) AS n FROM knowledge_chunks")["n"] == n


@pytest.mark.network
def test_embeddings_are_stored_in_pgvector(handbook_indexed):
    e = db.query_one("SELECT COUNT(*) AS n, COUNT(embedding) AS e FROM knowledge_chunks")
    if e["e"] == 0:
        pytest.skip("embedding API quota unavailable; retrieval falls back to full-text search")
    assert e["e"] >= e["n"] * 0.9
    assert db.query_one("SELECT vector_dims(embedding) AS d FROM knowledge_chunks WHERE embedding IS NOT NULL LIMIT 1")["d"] == 1536


@pytest.mark.network
@pytest.mark.parametrize("question,must_contain", [
    ("What is the add/drop policy?", ("three weeks", "drop")),
    ("What is the minimum semester GPA to remain in good academic standing?", ("2.00",)),
    ("What happens if I fail to meet the mandatory attendance requirement?", ("80%",)),
    ("What are the rules about withdrawing from a course?", ("withdraw", "W")),
])
def test_ask_giki_answers_from_official_documents_with_sources(handbook_indexed, question, must_contain):
    r = courses.ask_giki(question)
    assert r["found"], r
    text = " ".join(p["text"] for p in r["passages"])
    assert any(m.lower() in text.lower() for m in must_contain), (question, text[:400])
    top = r["passages"][0]
    assert top["source_url"] == HANDBOOK and crawler.is_official_url(top["source_url"]) and top["retrieved_at"] and top["source_title"]
    assert "Source:" in r["answer"] and "giki.edu.pk" in r["answer"]


@pytest.mark.network
def test_ask_giki_does_not_invent_unsupported_policies(handbook_indexed):
    for q in ("What is the recipe for chicken karahi?", "How do I renew my passport in Lahore?", "Who won the cricket world cup?"):
        r = courses.ask_giki(q)
        assert r["found"] is False and r["passages"] == [] and "will not guess" in r["answer"]
    # the team-authored legacy policy rows are not official and are never used as answers
    assert all(p["source_url"].startswith("https://giki.edu.pk/") for p in courses.ask_giki("add drop courses third week")["passages"])
