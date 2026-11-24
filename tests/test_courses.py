import pytest

from app import db
from app.services import auth, courses
from app.services.errors import Conflict, Forbidden, NotFound, RuleViolation, ValidationFailed


def test_slot_parsing_and_lab_lecture_conflict():
    assert [d for d, *_ in courses.parse_slot("MWF 10:00-11:00")] == ["M", "W", "F"]
    assert [d for d, *_ in courses.parse_slot("TTh 10:00-11:30")] == ["T", "Th"]
    a = {"course_code": "A1", "slot": "MWF 10:00-11:00", "lab_slot": None}
    b = {"course_code": "B1", "slot": "TTh 10:00-11:00", "lab_slot": "W 10:30-12:00"}
    c = courses.find_conflicts(a, b)
    assert len(c) == 1 and c[0]["day"] == "W" and c[0]["kind"] == "lecture/lab"
    with pytest.raises(ValidationFailed):
        courses.parse_slot("whenever")


def test_register_and_drop_roundtrip(actors, at):
    s1 = actors["s1"]
    r = courses.register(s1, "CS271")  # CS201 passed
    assert r["ok"]
    regs = courses.my_registrations(s1)
    assert [c["course_code"] for c in regs["courses"]] == ["CS271"]
    with pytest.raises(Conflict):
        courses.register(s1, "CS271")
    courses.drop(s1, "CS271")
    assert courses.my_registrations(s1)["courses"] == []
    row = db.query_one("SELECT status FROM enrollments WHERE user_id='2024405' AND course_code='CS271'")
    assert row["status"] == "dropped"


def test_prerequisites_enforced(actors):
    s2 = actors["s2"]  # has only CS101, CS102, MT101
    with pytest.raises(RuleViolation) as e:
        courses.register(s2, "CS325")
    assert e.value.code == "prerequisites_unmet" and "CS201" in e.value.details["missing"]
    chk = courses.check_prerequisites(s2, "CS325")
    assert not chk["eligible"] and "CS232" in chk["missing"]
    assert courses.check_prerequisites(actors["s3"], "CS325")["eligible"] is True
    # no enrollment row was written by the rejected attempt
    assert db.query_one("SELECT COUNT(*) AS n FROM enrollments")["n"] == 0


def test_already_completed_cannot_reregister(actors):
    with pytest.raises(RuleViolation):
        courses.register(actors["s1"], "CS201")


def test_schedule_conflict_rejected(actors):
    s3 = actors["s3"]
    courses.register(s3, "CS325")   # MWF 10:00-11:00
    # create a clashing course through the admin API, then try to register for it
    courses.create_course(actors["acad"], "ZZ101", "Clash Course", 3, slot="MWF 10:30-11:30", capacity=5)
    with pytest.raises(RuleViolation) as e:
        courses.register(s3, "ZZ101")
    assert e.value.code == "schedule_conflict"
    assert courses.check_conflicts(s3, "ZZ101")["has_conflicts"]


def test_lab_slot_clash_detected(actors):
    s3 = actors["s3"]
    courses.register(s3, "CS325")
    courses.create_course(actors["acad"], "ZZ102", "Lab Clasher", 2, slot="TTh 08:00-09:00", lab_slot="W 10:00-12:00", capacity=5)
    with pytest.raises(RuleViolation) as e:
        courses.register(s3, "ZZ102")
    assert any("lab" in c["kind"] for c in e.value.details["conflicts"])


def test_capacity_limit(actors):
    courses.create_course(actors["acad"], "ZZ103", "Tiny", 1, slot="Sa 09:00-10:00", capacity=1)
    courses.register(actors["s1"], "ZZ103")
    with pytest.raises(RuleViolation) as e:
        courses.register(actors["s3"], "ZZ103")
    assert e.value.code == "course_full"


def test_credit_limit(actors):
    from app.services import settings
    settings.update(actors["acad"], "max_credits_per_term", "4")
    try:
        courses.register(actors["s3"], "CS325")  # 3 credits
        with pytest.raises(RuleViolation) as e:
            courses.register(actors["s3"], "CS330")
        assert e.value.code == "credit_limit"
    finally:
        settings.update(actors["acad"], "max_credits_per_term", "18")


def test_student_cannot_act_for_another_student(actors):
    with pytest.raises(Forbidden):
        courses.register(actors["s1"], "CS271", student_id="2024668")
    with pytest.raises(Forbidden):
        courses.my_registrations(actors["s1"], student_id="2024668")
    # an academic admin may
    assert courses.register(actors["acad"], "CS102".replace("CS102", "CS201"), student_id="2024668")["ok"]
    # but a mess admin may not
    with pytest.raises(Forbidden):
        courses.register(actors["mess"], "CS201", student_id="2024668")


def test_denied_attempts_are_audited(actors):
    with pytest.raises(Forbidden):
        courses.register(actors["s1"], "CS271", student_id="2024668")
    row = db.query_one("SELECT outcome, actor_id FROM audit_log WHERE action='course.register' ORDER BY audit_id DESC LIMIT 1")
    assert row == {"outcome": "denied", "actor_id": "2024405"}


def test_concurrent_registration_respects_capacity(actors):
    import threading
    courses.create_course(actors["acad"], "ZZ104", "Race", 1, slot="Su 09:00-10:00", capacity=1)
    outcomes = []

    def go(actor):
        try:
            courses.register(actor, "ZZ104"); outcomes.append("ok")
        except RuleViolation:
            outcomes.append("full")
    ts = [threading.Thread(target=go, args=(actors[k],)) for k in ("s1", "s2", "s3")]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert sorted(outcomes) == ["full", "full", "ok"]
    assert db.query_one("SELECT COUNT(*) AS n FROM enrollments WHERE course_code='ZZ104'")["n"] == 1


def test_admin_course_crud_is_visible_to_students(actors):
    acad, s1 = actors["acad"], actors["s1"]
    with pytest.raises(Forbidden):
        courses.create_course(s1, "ZZ200", "Nope", 3)
    with pytest.raises(Forbidden):
        courses.create_course(actors["mess"], "ZZ200", "Nope", 3)
    created = courses.create_course(acad, "ZZ200", "Quantum Computing", 3, prerequisites=["CS201"], slot="MWF 13:00-14:00",
                                    capacity=10, category="elective", domains="quantum, computing", instructor="Dr. Q")
    assert created["prerequisites"] == ["CS201"]
    # immediately visible to a student, and eligibility reflects the new prerequisite
    listing = {c["course_code"]: c for c in courses.list_courses(s1)}
    assert listing["ZZ200"]["student_status"] == "available"
    assert {c["course_code"]: c for c in courses.list_courses(actors["s2"])}["ZZ200"]["student_status"] == "blocked_prerequisites"
    courses.update_course(acad, "ZZ200", title="Quantum Computing II", prerequisites=["CS201", "MT201"])
    assert courses.get_course(s1, "ZZ200")["title"] == "Quantum Computing II"
    assert courses.check_prerequisites(s1, "ZZ200")["missing"] == ["MT201"]
    # students can search the new course for the recommender too
    rec = courses.recommend_electives(actors["s3"], interests="quantum")
    assert rec["recommendations"][0]["course_code"] == "ZZ200"
    # deleting a never-used course removes it; a used one is deactivated instead
    courses.update_course(acad, "ZZ200", prerequisites=[])
    courses.register(actors["s1"], "ZZ200")
    out = courses.delete_course(acad, "ZZ200")
    assert out["action"] == "deactivated"
    with pytest.raises(NotFound):
        courses.get_course(s1, "ZZ200")
    courses.create_course(acad, "ZZ201", "Throwaway", 1)
    assert courses.delete_course(acad, "ZZ201")["action"] == "deleted"


def test_prerequisite_cycle_rejected(actors):
    acad = actors["acad"]
    courses.create_course(acad, "ZZ210", "A", 1)
    courses.create_course(acad, "ZZ211", "B", 1, prerequisites=["ZZ210"])
    with pytest.raises(RuleViolation) as e:
        courses.update_course(acad, "ZZ210", prerequisites=["ZZ211"])
    assert e.value.code == "prerequisite_cycle"


def test_course_validation(actors):
    acad = actors["acad"]
    for bad in ({"course_code": "x1", "title": "t", "credits": 3}, {"course_code": "ZZ300", "title": "", "credits": 3},
                {"course_code": "ZZ300", "title": "t", "credits": 9}, {"course_code": "ZZ300", "title": "t", "credits": 3, "slot": "bad"}):
        with pytest.raises(ValidationFailed):
            courses.create_course(acad, **bad)


def test_what_if_and_recommend(actors):
    sim = courses.what_if(actors["s1"], ["Artificial Intelligence"])
    m = sim["minors"][0]
    assert "CS330" in m["remaining"] and m["prerequisite_blockers"]["CS330"] == ["MT201"]
    sim2 = courses.what_if(actors["s1"], ["Artificial Intelligence"], hypothetical_courses=["MT201"])
    assert "CS330" in sim2["minors"][0]["eligible_next"]
    rec = courses.recommend_electives(actors["s1"])
    assert rec["recommendations"] and all("course_code" in r for r in rec["recommendations"])


def test_ask_giki_retrieves_real_policy():
    r = courses.ask_giki("What is the policy on academic probation?")
    assert r["found"] and r["passages"][0]["topic"] == "probation"
    assert not courses.ask_giki("zzzzqqqq flibbertigibbet")["found"]


def test_record_grade_changes_eligibility(actors):
    courses.set_course_history(actors["acad"], "2024668", "CS201", "B")
    assert courses.check_prerequisites(actors["s2"], "CS271")["eligible"]
    with pytest.raises(Forbidden):
        courses.set_course_history(actors["s2"], "2024668", "CS271", "A")
