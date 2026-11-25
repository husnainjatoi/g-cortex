"""Prospectus 2025 plan parser (verbatim excerpts) and the curriculum import service (idempotence, supersession, no overwrite of seeded courses)."""
import pytest

from app import db
from app.knowledge import plan_parser as pp
from app.services import curriculum
from tests.data import prospectus2025_excerpts as X

FCSE = "Faculty of Computer Science and Engineering\n"


def parse(text):
    return {p["code"]: p for p in pp.parse_plans(text)["programs"]}


def test_credit_last_layout_reads_hours_credits_and_prerequisites():
    p = parse(FCSE + X.AI)["BSAI"]
    r = {x["code"]: x for x in p["rows"]}
    assert r["CS101"]["credits"] == 2 and r["CS101"]["lecture_hours"] == 2 and r["CS101"]["lab_hours"] == 0
    assert r["CS101L"]["credits"] == 1 and r["PH101L"]["lab_hours"] == 3
    assert {x["semester"] for x in p["rows"]} <= {1, 2}
    assert pp.check_totals(p) == []                                    # parsed credits equal the printed 'Total' line (17)


def test_credit_first_layout_and_merged_lab_pairs_are_split_with_derived_lab_credit():
    p = parse(FCSE + X.CE)["BSAI"]                                      # the first FCSE plan is assigned by position
    r = {x["code"]: x for x in p["rows"]}
    assert r["CS101"]["credits"] == 3 and r["CS101"]["lab_code"] == "CS101L" and r["CS101"]["lecture_hours"] == 2 and r["CS101"]["lab_hours"] == 3
    rows = curriculum.expand_rows(p)
    by = {x["code"]: x for x in rows}
    assert by["CS101"]["credits"] == 2 and by["CS101L"]["credits"] == 1 and by["CS101L"]["credit_derived"] and by["CS101L"]["lab_of"] == "CS101"
    assert sum(x["credits"] for x in rows) == p["declared"][(None, 1)] == 17            # the split keeps the semester total intact


def test_placeholder_and_uncoded_rows_become_slots_not_courses():
    p = parse(FCSE + X.CE_SEM6.replace("6th Semester", "Degree Plan\n6th Semester", 1))["BSAI"]
    slots = [x for x in p["rows"] if x["kind"] == "slot"]
    assert any("Ideology" in s["title"] for s in slots) and all(s["code"] is None for s in slots)


def test_management_sciences_layout_codes_with_spaces_and_lab_suffix():
    p = parse("School of Management Sciences\n" + X.MS)["BSMS"]
    codes = [x["code"] for x in p["rows"] if x["semester"] == 1]
    assert "HM101" in codes and "HM101L" in codes and "IF101" in codes and "MS101" in codes
    assert pp.check_totals(p) == []


def test_unreadable_row_is_reported_not_guessed():
    bad = FCSE + X.AI.replace("MT101 Calculus I 3 0 3 None None", "M1 / MT1 Calculus I 3 0 3")
    res = pp.parse_plans(bad)
    assert any("Calculus" in x["line"] for x in res["rejected"])
    assert pp.check_totals(res["programs"][0])                          # and the credit total no longer reconciles, so it is flagged for review


# ── import service ──
URL = curriculum.PROSPECTUS_2025_URL


def drop_2025_source():
    with db.tx() as cur:
        cur.execute("UPDATE knowledge_sources SET superseded_by=NULL WHERE superseded_by IN (SELECT source_id FROM knowledge_sources WHERE url=%s)", (URL,))
        for t in ("course_sources", "course_source_history", "academic_programs", "degree_minors"):
            cur.execute(f"UPDATE {t} SET source_id=NULL WHERE source_id IN (SELECT source_id FROM knowledge_sources WHERE url=%s)" if t in ("course_source_history", "degree_minors") else f"DELETE FROM {t} WHERE source_id IN (SELECT source_id FROM knowledge_sources WHERE url=%s)", (URL,))
        cur.execute("DELETE FROM knowledge_sources WHERE url=%s", (URL,))


@pytest.fixture
def ingested(actors):
    text = "<<PAGE 47>>\n" + FCSE + X.AI
    drop_2025_source()
    with db.tx() as cur:
        cur.execute("DELETE FROM program_courses"); cur.execute("DELETE FROM academic_programs")
        cur.execute("INSERT INTO knowledge_sources (url, title, doc_type, status, full_text) VALUES (%s,'Undergraduate Prospectus 2025','pdf','active',%s) ON CONFLICT (url) DO UPDATE SET full_text=EXCLUDED.full_text, status='active'", (URL, text))
        cur.execute("INSERT INTO knowledge_sources (url, title, doc_type, status, full_text) VALUES ('https://giki.edu.pk/wp-content/uploads/2021/10/UG_Prospectus_2021.pdf','UG Prospectus 2021','pdf','active','x') ON CONFLICT (url) DO UPDATE SET status='active', superseded_by=NULL")
    yield
    with db.tx() as cur:
        cur.execute("DELETE FROM program_courses"); cur.execute("DELETE FROM academic_programs")
        cur.execute("DELETE FROM course_prerequisites WHERE course_code IN (SELECT course_code FROM courses WHERE origin='official')")
        cur.execute("DELETE FROM course_sources"); cur.execute("DELETE FROM course_import_flags")
        cur.execute("DELETE FROM courses WHERE origin='official' AND course_code NOT IN (SELECT course_code FROM course_history)")
    drop_2025_source()
    with db.tx() as cur:
        cur.execute("DELETE FROM degree_minors WHERE minor_name='Management for Engineers'"); cur.execute("UPDATE knowledge_sources SET status='active' WHERE url ~ 'UG_Prospectus_2021'")


def test_import_builds_catalogue_curriculum_and_versions_and_is_idempotent(actors, ingested):
    r1 = curriculum.import_prospectus_2025(actors["acad"])
    assert r1["programs"] == 1 and r1["courses_created"] > 5
    prog = db.query_one("SELECT * FROM academic_programs WHERE code='BSAI'")
    assert prog["plan_status"] == "parsed" and prog["source_url"] == URL and prog["source_page"] == 47
    pc = db.query("SELECT * FROM program_courses WHERE program_id=%s AND semester=1", (prog["program_id"],))
    assert sum(x["credit_hours"] for x in pc) == 17 and all(x["course_code"] for x in pc)
    c = db.query_one("SELECT * FROM courses WHERE course_code='AI102'") or db.query_one("SELECT * FROM courses WHERE course_code='MM141L'")
    assert c["catalog_only"] and c["origin"] == "official" and c["capacity"] == 0                 # catalogue / curriculum is NOT an offering
    assert db.query_one("SELECT status, superseded_by FROM knowledge_sources WHERE url ~ 'UG_Prospectus_2021'")["status"] == "superseded"
    assert db.query_one("SELECT version_label, series FROM knowledge_sources WHERE url=%s", (URL,)) == {"version_label": "2025", "series": "ug_prospectus"}
    before = (db.query_one("SELECT COUNT(*) AS n FROM program_courses")["n"], db.query_one("SELECT COUNT(*) AS n FROM courses")["n"], db.query_one("SELECT COUNT(*) AS n FROM course_import_flags")["n"])
    r2 = curriculum.import_prospectus_2025(actors["acad"])
    assert r2["courses_created"] == 0 and r2["courses_updated"] == 0
    assert before == (db.query_one("SELECT COUNT(*) AS n FROM program_courses")["n"], db.query_one("SELECT COUNT(*) AS n FROM courses")["n"], db.query_one("SELECT COUNT(*) AS n FROM course_import_flags")["n"])


def test_seeded_course_is_never_overwritten_and_difference_is_flagged(actors, ingested):
    seeded = db.query_one("SELECT title, credits, origin, catalog_only FROM courses WHERE course_code='CS101'")
    curriculum.import_prospectus_2025(actors["acad"])
    after = db.query_one("SELECT title, credits, origin, catalog_only FROM courses WHERE course_code='CS101'")
    assert after == seeded and seeded["origin"] == "manual" and not seeded["catalog_only"]
    flag = db.query_one("SELECT detail FROM course_import_flags WHERE course_code='CS101' AND kind='seed_conflict'")
    assert flag["detail"]["official_2025"]["credits"] == 2


def test_import_requires_academic_scope_and_a_stored_document(actors):
    from app.services.errors import Forbidden, ValidationFailed
    with pytest.raises(Forbidden):
        curriculum.import_prospectus_2025(actors["s1"])
    drop_2025_source()
    with pytest.raises(ValidationFailed):
        curriculum.import_prospectus_2025(actors["acad"])
