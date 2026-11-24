import pytest

from app import db
from app.services import equipment, events, requisitions as rq, sports
from app.services.errors import Conflict, Forbidden, NotFound, RuleViolation, ValidationFailed


def _submit(actor, **kw):
    args = dict(society_name="ACM GIKI Chapter", venue_id="SEM_HALL_1", event_title="Talk", booking_date="2026-12-10",
                start_time="14:00", duration_hours=2, expected_attendees=80, justification="x")
    args.update(kw)
    return rq.submit_requisition(actor, **args)


# ── Multi-tier approval chain ───────────────────────────────

def test_full_two_tier_approval_creates_venue_booking(actors, at):
    s3, fac, adm = actors["s3"], actors["fac"], actors["req"]
    r = _submit(s3)
    rid = r["requisition_id"]
    assert r["status"] == "pending_advisor_approval"
    view = rq.get_requisition(s3, rid)
    assert view["pending_authority"] == "Farah Saeed" and view["stages"][0]["state"] == "current"
    assert db.query_one("SELECT COUNT(*) AS n FROM bookings WHERE requisition_id=%s", (rid,))["n"] == 0
    with pytest.raises(Forbidden):
        rq.review_requisition(adm if False else actors["s1"], rid, "approve")          # a student cannot review
    with pytest.raises(Forbidden):
        rq.review_requisition(actors["mess"], rid, "approve")                          # wrong admin scope
    with pytest.raises(Forbidden):
        rq.review_requisition(actors["fac"].__class__("FAC002", "faculty", frozenset()), rid, "approve")   # not this society's advisor
    assert rq.review_requisition(fac, rid, "approve", "ok")["status"] == "pending_admin_approval"
    with pytest.raises(Forbidden):
        rq.review_requisition(fac, rid, "approve")                                     # faculty cannot do tier 2
    done = rq.review_requisition(adm, rid, "approve", "cleared")
    assert done["status"] == "approved"
    b = db.query_one("SELECT status, slot_key FROM bookings WHERE requisition_id=%s", (rid,))
    assert b == {"status": "approved", "slot_key": "SEM_HALL_1"}
    titles = [n["title"] for n in db.query("SELECT title FROM notifications WHERE user_id='2024662'")]
    assert any("approved" in t for t in titles)
    with pytest.raises(RuleViolation):
        rq.review_requisition(adm, rid, "reject", "late")


def test_rejection_requires_reason_and_blocks_booking(actors, at):
    rid = _submit(actors["s3"])["requisition_id"]
    with pytest.raises(ValidationFailed):
        rq.review_requisition(actors["fac"], rid, "reject", "")
    assert rq.review_requisition(actors["fac"], rid, "reject", "clashes with exams")["status"] == "rejected"
    assert db.query_one("SELECT COUNT(*) AS n FROM bookings")["n"] == 0


def test_submit_rules(actors, at):
    s1, s3 = actors["s1"], actors["s3"]
    with pytest.raises(Forbidden):
        _submit(s1, society_name="IEEE Student Branch")                  # s1 is not a member
    with pytest.raises(RuleViolation) as e:
        _submit(s3, expected_attendees=500)
    assert e.value.code == "capacity_exceeded"
    with pytest.raises(RuleViolation):
        _submit(s3, booking_date="2026-11-26")                           # too little notice
    with pytest.raises(RuleViolation):
        _submit(s3, venue_id="SPORTS_C1")                                # sports go through sports booking
    with pytest.raises(NotFound):
        _submit(s3, venue_id="NOPE")
    with pytest.raises(Forbidden):
        _submit(actors["fac"])                                           # only students


def test_venue_double_booking_prevented_at_approval(actors, at):
    a = _submit(actors["s3"])["requisition_id"]
    b = _submit(actors["s3"], society_name="IEEE Student Branch", event_title="Other", start_time="15:00")["requisition_id"]
    for rid in (a, b):
        rq.review_requisition(actors["fac"], rid, "approve")
    rq.review_requisition(actors["req"], a, "approve")
    with pytest.raises(RuleViolation) as e:
        rq.review_requisition(actors["req"], b, "approve")              # overlaps 14:00-16:00
    assert e.value.code == "venue_conflict"
    # new submissions for the taken slot are refused up-front
    with pytest.raises(RuleViolation):
        _submit(actors["s3"], start_time="14:30")


def test_requisition_visibility_and_cancel(actors, at):
    rid = _submit(actors["s3"])["requisition_id"]
    with pytest.raises(Forbidden):
        rq.get_requisition(actors["s1"], rid)
    assert [r["requisition_id"] for r in rq.list_requisitions(actors["s3"])] == [rid]
    assert rq.list_requisitions(actors["s1"]) == []
    assert [r["requisition_id"] for r in rq.list_requisitions(actors["fac"], "assigned")] == [rid]
    with pytest.raises(Forbidden):
        rq.list_requisitions(actors["s3"], "all")
    with pytest.raises(Forbidden):
        rq.cancel_requisition(actors["s1"], rid)
    rq.cancel_requisition(actors["s3"], rid)
    assert rq.get_requisition(actors["s3"], rid)["status"] == "cancelled"


def test_venue_recommender_natural_language(actors):
    r = rq.recommend_venue(actors["s3"], "hackathon with coding and a projector", 40, "projector")
    assert r["recommendations"][0]["venue_id"] in ("LAB_SE", "LAB_MEDS")
    big = rq.recommend_venue(actors["s3"], "annual cultural festival concert", 400)
    assert big["recommendations"][0]["venue_id"] == "OAT_MAIN" and all(x["capacity"] >= 400 for x in big["recommendations"])
    with pytest.raises(ValidationFailed):
        rq.recommend_venue(actors["s3"], "x", 0)


def test_admin_venue_management(actors):
    with pytest.raises(Forbidden):
        rq.create_venue(actors["s1"], "NEW_HALL", "New Hall", 50, "seminar_hall")
    rq.create_venue(actors["req"], "NEW_HALL", "New Hall", 50, "seminar_hall", "projector")
    assert any(v["venue_id"] == "NEW_HALL" for v in rq.list_venues(actors["s1"]))
    rq.update_venue(actors["req"], "NEW_HALL", is_active=False)
    assert not any(v["venue_id"] == "NEW_HALL" for v in rq.list_venues(actors["s1"]))
    db.query("SELECT 1")
    with db.tx() as cur:
        cur.execute("DELETE FROM venues WHERE venue_id='NEW_HALL'")


# ── Sports bookings ─────────────────────────────────────────

def test_sports_booking_cancel_and_slot_reopens(actors, at):
    s1 = actors["s1"]
    b = sports.book(s1, "SPORTS_C1", "2026-11-26", "17:00", 1)
    assert b["checkin_pin"].startswith("CHK-") and b["daily_remaining_hours"] == 1.0
    av = sports.availability(s1, "SPORTS_C1", "2026-11-26")
    slot = next(s for s in av["slots"] if s["start"] == "17:00")
    assert not slot["available"] and slot["booked_by_me"]
    with pytest.raises(RuleViolation) as e:
        sports.book(actors["s2"], "SPORTS_C1", "2026-11-26", "17:00", 1)
    assert e.value.code == "slot_taken"
    with pytest.raises(Forbidden):
        sports.cancel(actors["s2"], b["booking_code"])
    sports.cancel(s1, b["booking_code"])
    assert next(s for s in sports.availability(s1, "SPORTS_C1", "2026-11-26")["slots"] if s["start"] == "17:00")["available"]
    sports.book(actors["s2"], "SPORTS_C1", "2026-11-26", "17:00", 1)


def test_fair_use_quotas(actors, at):
    s1 = actors["s1"]
    sports.book(s1, "SPORTS_C1", "2026-11-26", "10:00", 1)
    sports.book(s1, "SPORTS_C2", "2026-11-26", "12:00", 1)
    with pytest.raises(RuleViolation) as e:
        sports.book(s1, "SPORTS_SQ1", "2026-11-26", "15:00", 1)
    assert e.value.code == "daily_quota"
    q = sports.quota(s1, date="2026-11-26")
    assert q["daily_remaining_hours"] == 0 and q["weekly_used_hours"] == 2
    # weekly quota (6h): 3 more days of 2h hits the cap on the 4th day
    for d in ("27", "28", "29"):
        if d == "27":
            sports.book(s1, "SPORTS_C1", f"2026-11-{d}", "10:00", 2)
        else:
            try:
                sports.book(s1, "SPORTS_C1", f"2026-11-{d}", "10:00", 2)
            except RuleViolation as e:
                assert e.code == "weekly_quota"


def test_sports_rules_hours_and_past(actors, at):
    s1 = actors["s1"]
    with pytest.raises(RuleViolation):
        sports.book(s1, "SPORTS_C1", "2026-11-25", "09:00", 1)       # already past (it is 10:00)
    with pytest.raises(RuleViolation):
        sports.book(s1, "SPORTS_C1", "2026-11-26", "06:00", 1)       # before opening
    with pytest.raises(RuleViolation):
        sports.book(s1, "SPORTS_C1", "2026-12-20", "10:00", 1)       # too far ahead
    with pytest.raises(NotFound):
        sports.book(s1, "AHA_AUDI", "2026-11-26", "10:00", 1)        # not a sports facility
    with pytest.raises(ValidationFailed):
        sports.book(s1, "SPORTS_C1", "2026-11-26", "10:15", 1)
    with pytest.raises(Forbidden):
        sports.book(s1, "SPORTS_C1", "2026-11-26", "10:00", 1, student_id="2024668")


def test_cancel_cutoff_window(actors, at):
    b = sports.book(actors["s1"], "SPORTS_C1", "2026-11-25", "11:00", 1)    # starts in 60 min -> inside cutoff? exactly at cutoff
    at(2026, 11, 25, 10, 30)
    with pytest.raises(RuleViolation) as e:
        sports.cancel(actors["s1"], b["booking_code"])
    assert e.value.code == "cancel_window"
    sports.cancel(actors["req"], b["booking_code"])                          # admin may override


def test_checkin_and_no_show_auto_release_with_suspension(actors, at):
    s1 = actors["s1"]
    for i, day in enumerate(("26", "27", "28")):
        sports.book(s1, "SPORTS_C1", f"2026-11-{day}", "10:00", 1)
    # day 1: checks in properly
    b1 = sports.my_bookings(s1)[-1]
    at(2026, 11, 26, 10, 5)
    with pytest.raises(RuleViolation):
        sports.check_in(s1, b1["booking_code"], "CHK-0000" if b1["checkin_token"] != "CHK-0000" else "CHK-9999")
    assert sports.check_in(s1, b1["booking_code"], b1["checkin_token"])["status"] == "checked_in"
    at(2026, 11, 26, 11, 0)
    assert sports.auto_release(actors["req"])["released"] == 0               # checked-in booking is safe
    # days 2 and 3: no-shows
    at(2026, 11, 27, 10, 30)
    assert sports.auto_release(None)["released"] == 1
    at(2026, 11, 28, 10, 30)
    r = sports.auto_release(None)
    assert r["released"] == 1
    assert sports.auto_release(None)["released"] == 0                        # idempotent
    q = sports.quota(s1)
    assert q["no_show_strikes"] == 2 and not q["suspended"]
    # third strike suspends
    sports.book(s1, "SPORTS_C1", "2026-11-29", "18:00", 1)
    at(2026, 11, 29, 19, 0)
    sports.auto_release(None)
    assert sports.quota(s1)["suspended"]
    at(2026, 11, 29, 19, 5)
    with pytest.raises(RuleViolation) as e:
        sports.book(s1, "SPORTS_C1", "2026-11-30", "10:00", 1)
    assert e.value.code == "suspended"
    assert any("auto-released" in n["title"] for n in db.query("SELECT title FROM notifications WHERE user_id='2024405'"))
    with pytest.raises(Forbidden):
        sports.auto_release(actors["s1"])


def test_concurrent_booking_of_same_slot_yields_single_booking(actors, at):
    import threading
    res = []

    def go(k):
        try:
            sports.book(actors[k], "SPORTS_TC1", "2026-11-26", "16:00", 1); res.append("ok")
        except RuleViolation:
            res.append("taken")
    ts = [threading.Thread(target=go, args=(k,)) for k in ("s1", "s2", "s3")]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert sorted(res) == ["ok", "taken", "taken"]


def test_gym_allows_multiple_up_to_capacity(actors, at):
    sports.book(actors["s1"], "SPORTS_GYM", "2026-11-26", "10:00", 1)
    sports.book(actors["s2"], "SPORTS_GYM", "2026-11-26", "10:00", 1)


# ── Equipment custody ───────────────────────────────────────

def test_equipment_checkout_return_custody_trail(actors, at):
    s1 = actors["s1"]
    out = equipment.checkout(s1, "bar-fpga-302", "OS lab project", 7)
    assert out["due_date"] == "2026-12-02"
    mine = equipment.my_custody(s1)
    assert [m["barcode"] for m in mine] == ["BAR-FPGA-302"]
    with pytest.raises(RuleViolation):
        equipment.checkout(actors["s2"], "BAR-FPGA-302", "me too")
    # another student cannot see custody details
    assert equipment.lookup(actors["s2"], "BAR-FPGA-302")["custodian_id"] is None
    with pytest.raises(Forbidden):
        equipment.return_equipment(actors["s2"], "BAR-FPGA-302")
    at(2026, 12, 4)
    r = equipment.return_equipment(s1, "BAR-FPGA-302", "good", "thanks")
    assert r["late_days"] == 2 and r["status"] == "available"
    log = equipment.custody_log(actors["req"], "BAR-FPGA-302")
    assert [x["action"] for x in log] == ["return", "checkout"]


def test_equipment_limits_overdue_and_damage(actors, at):
    s1 = actors["s1"]
    for code in ("BAR-FPGA-302", "BAR-LA-401", "BAR-DMM-112"):
        equipment.checkout(s1, code, "project", 14)
    with pytest.raises(RuleViolation) as e:
        equipment.checkout(s1, "BAR-FG-102", "more")
    assert e.value.code == "item_limit"
    with pytest.raises(RuleViolation):
        equipment.checkout(s1, "BAR-FG-102", "x", 99)
    at(2026, 12, 20)                                       # s1's loans (due 2026-12-09) are now overdue
    equipment.checkout(actors["s2"], "BAR-FG-102", "ok")  # an unrelated student is unaffected
    equipment.return_equipment(s1, "BAR-DMM-112", "good")
    with pytest.raises(RuleViolation) as e:
        equipment.checkout(s1, "BAR-OSC-201", "overdue holder")
    assert e.value.code == "overdue"
    equipment.return_equipment(s1, "BAR-LA-401", "damaged")
    assert equipment.lookup(actors["req"], "BAR-LA-401")["status"] == "under_repair"
    assert any("damaged" in n["title"] for n in db.query("SELECT title FROM notifications WHERE user_id='ADMREQ'"))


def test_equipment_admin_inventory(actors):
    with pytest.raises(Forbidden):
        equipment.create_equipment(actors["s1"], "BAR-X-1", "Thing", "Lab")
    equipment.create_equipment(actors["req"], "BAR-X-1", "Thing", "Lab", "Misc")
    with pytest.raises(Conflict):
        equipment.create_equipment(actors["req"], "BAR-X-1", "Thing", "Lab")
    equipment.update_equipment(actors["req"], "BAR-X-1", status="under_repair")
    with pytest.raises(RuleViolation):
        equipment.checkout(actors["s1"], "BAR-X-1", "x")
    with db.tx() as cur:
        cur.execute("DELETE FROM equipment_custody_log WHERE barcode='BAR-X-1'"); cur.execute("DELETE FROM lab_equipment WHERE barcode='BAR-X-1'")
