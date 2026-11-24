import pytest

from app import db
from app.services import clock, events, mess
from app.services.errors import Forbidden, RuleViolation, ValidationFailed


def test_preview_enforces_notice_min_days_and_calculates_rebate(actors, at):
    s1 = actors["s1"]
    ok = mess.preview_mess_out(s1, "2026-11-28", "2026-11-30")
    assert ok["eligible"] and ok["days"] == 3 and ok["rebate_pkr"] == 1350.0
    assert not mess.preview_mess_out(s1, "2026-11-25", "2026-11-27")["eligible"]       # no notice
    assert not mess.preview_mess_out(s1, "2026-11-28", "2026-11-28")["eligible"]       # < 2 days
    with pytest.raises(ValidationFailed):
        mess.preview_mess_out(s1, "2026-12-05", "2026-12-01")
    with pytest.raises(ValidationFailed):
        mess.preview_mess_out(s1, "not-a-date", "2026-12-01")


def test_submit_pending_then_admin_approval_credits_ledger(actors, at):
    s1 = actors["s1"]
    r = mess.submit_mess_out(s1, "2026-11-28", "2026-11-30", "going home")
    assert r["status"] == "pending"
    assert db.query_one("SELECT COUNT(*) AS n FROM mess_rebates WHERE user_id='2024405'")["n"] == 0
    with pytest.raises(Forbidden):
        mess.decide_mess_out(s1, r["request_id"], True)                  # students cannot approve
    mess.decide_mess_out(actors["mess"], r["request_id"], True, "ok")
    led = mess.rebate_ledger(s1)
    assert led["total_credited_pkr"] == 1350.0 and led["transactions"][0]["mess_out_request_id"] == r["request_id"]
    assert db.query_one("SELECT COUNT(*) AS n FROM mess_ledger WHERE user_id='2024405' AND status='rebated'")["n"] == 9   # 3 days x 3 meals
    assert any("approved" in n["title"] for n in db.query("SELECT title FROM notifications WHERE user_id='2024405'"))
    with pytest.raises(RuleViolation):
        mess.decide_mess_out(actors["mess"], r["request_id"], False)     # cannot decide twice


def test_gate_pass_sync_auto_approves(actors, at):
    mess.sync_gate_pass(actors["mess"], "2024668", "2026-11-27", "2026-12-02", "Lahore", external_ref="GP-1")
    r = mess.submit_mess_out(actors["s2"], "2026-11-28", "2026-11-30")
    assert r["status"] == "approved" and r["gate_pass_verified"]
    assert mess.rebate_ledger(actors["s2"])["total_credited_pkr"] == 1350.0


def test_gate_pass_arriving_later_approves_pending(actors, at):
    r = mess.submit_mess_out(actors["s2"], "2026-12-01", "2026-12-03")
    assert r["status"] == "pending"
    mess.sync_gate_pass(None, "2024668", "2026-11-30", "2026-12-05", approved=True, external_ref="GP-2") if False else \
        mess.sync_gate_pass(actors["mess"], "2024668", "2026-11-30", "2026-12-05", approved=True, external_ref="GP-2")
    assert mess.list_mess_out(actors["s2"])[0]["status"] == "approved"
    assert mess.rebate_ledger(actors["s2"])["total_credited_pkr"] == 1350.0


def test_rebate_event_handler_is_idempotent(actors, at):
    r = mess.submit_mess_out(actors["s1"], "2026-11-28", "2026-11-29")
    mess.decide_mess_out(actors["mess"], r["request_id"], True)
    ev = db.query_one("SELECT event_id FROM domain_events WHERE event_type='mess.out_approved'")
    events.dispatch(str(ev["event_id"]))         # duplicate delivery
    events.dispatch(str(ev["event_id"]))
    assert db.query_one("SELECT COUNT(*) AS n FROM mess_rebates WHERE mess_out_request_id=%s", (r["request_id"],))["n"] == 1
    assert db.query_one("SELECT COUNT(*) AS n FROM mess_ledger WHERE status='rebated'")["n"] == 6


def test_overlapping_requests_rejected(actors, at):
    mess.submit_mess_out(actors["s1"], "2026-11-28", "2026-11-30")
    with pytest.raises(RuleViolation):
        mess.submit_mess_out(actors["s1"], "2026-11-30", "2026-12-02")


def test_concurrent_duplicate_submissions_create_one_request(actors, at):
    import threading
    res = []

    def go():
        try:
            mess.submit_mess_out(actors["s1"], "2026-12-10", "2026-12-12"); res.append("ok")
        except Exception as e:
            res.append(type(e).__name__)
    ts = [threading.Thread(target=go) for _ in range(5)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert res.count("ok") == 1
    assert db.query_one("SELECT COUNT(*) AS n FROM mess_out_requests")["n"] == 1


def test_cancel_reverses_rebate(actors, at):
    r = mess.submit_mess_out(actors["s1"], "2026-11-28", "2026-11-29")
    mess.decide_mess_out(actors["mess"], r["request_id"], True)
    mess.cancel_mess_out(actors["s1"], r["request_id"])
    assert mess.rebate_ledger(actors["s1"])["total_credited_pkr"] == 0
    assert db.query_one("SELECT COUNT(*) AS n FROM mess_ledger WHERE status='rebated'")["n"] == 0
    with pytest.raises(Forbidden):
        mess.cancel_mess_out(actors["s2"], r["request_id"])


def test_cannot_read_or_act_for_other_students(actors, at):
    with pytest.raises(Forbidden):
        mess.rebate_ledger(actors["s1"], "2024668")
    with pytest.raises(Forbidden):
        mess.submit_mess_out(actors["s1"], "2026-11-28", "2026-11-30", student_id="2024668")
    with pytest.raises(Forbidden):
        mess.list_mess_out(actors["s1"], all_students=True)


def test_qr_generation_and_turnstile_validation(actors, at):
    qr = mess.generate_qr(actors["s1"], "lunch")
    assert qr["payload"].startswith("GCQR:2024405:lunch:") and "<svg" in qr["svg"] and qr["expires_in_seconds"] <= 30
    with pytest.raises(Forbidden):
        mess.verify_entry(actors["s1"], qr["payload"], "lunch")           # students cannot operate the turnstile
    ok = mess.verify_entry(actors["mess"], qr["payload"], "lunch")
    assert ok["status"] == "access_granted"
    assert db.query_one("SELECT COUNT(*) AS n FROM mess_ledger WHERE status='consumed' AND user_id='2024405'")["n"] == 1
    with pytest.raises(RuleViolation) as e:                                 # replay of the very same code
        mess.verify_entry(actors["mess"], qr["payload"], "lunch")
    assert "already" in str(e.value)
    at(2026, 11, 25, 10, 1)                                                 # a fresh code, but same meal today
    qr2 = mess.generate_qr(actors["s1"], "lunch")
    with pytest.raises(RuleViolation) as e:
        mess.verify_entry(actors["mess"], qr2["payload"], "lunch")
    assert "Anti-passback" in str(e.value)


def test_qr_rejects_wrong_code_expired_and_wrong_meal(actors, at):
    qr = mess.generate_qr(actors["s2"], "lunch")
    with pytest.raises(RuleViolation):
        mess.verify_entry(actors["mess"], qr["payload"][:-1] + ("0" if qr["payload"][-1] != "0" else "1"), "lunch")
    with pytest.raises(RuleViolation):
        mess.verify_entry(actors["mess"], qr["payload"], "dinner")
    at(2026, 11, 25, 10, 5)                                                 # five minutes later: expired
    with pytest.raises(RuleViolation):
        mess.verify_entry(actors["mess"], qr["payload"], "lunch")
    with pytest.raises(RuleViolation):
        mess.verify_entry(actors["mess"], "garbage", "lunch")


def test_no_entry_or_qr_during_approved_mess_out(actors, at):
    qr = mess.generate_qr(actors["s1"], "dinner")       # valid now
    r = mess.submit_mess_out(actors["s1"], "2026-11-26", "2026-11-28")
    mess.decide_mess_out(actors["mess"], r["request_id"], True)
    at(2026, 11, 27, 18, 0)
    with pytest.raises(RuleViolation):
        mess.generate_qr(actors["s1"], "dinner")


def test_gate_key_path_for_turnstile_controller(actors, at):
    qr = mess.generate_qr(actors["s3"], "lunch")
    assert mess.verify_entry(None, qr["payload"], "lunch", gate_key_ok=True)["status"] == "access_granted"
    with pytest.raises(Forbidden):
        mess.verify_entry(None, qr["payload"], "lunch", gate_key_ok=False)


def test_allergen_shield_and_menu(actors):
    mess.set_allergies(actors["s1"], "Dairy, Eggs")
    m = {x["meal_period"]: x for x in mess.menu(actors["s1"])["meals"]}
    assert not m["breakfast"]["allergen_shield"]["safe"] and set(m["breakfast"]["allergen_shield"]["triggered"]) == {"dairy", "eggs"}
    assert m["dinner"]["allergen_shield"]["safe"]
    assert m["lunch"]["calories"] > 0


def test_menu_update_requires_mess_scope(actors):
    with pytest.raises(Forbidden):
        mess.update_menu(actors["acad"], "lunch", "x", 1, 1, 1, 1, "none")
    mess.update_menu(actors["mess"], "lunch", "Chicken Biryani, Raita", 800, 35, 90, 25, "dairy")
    assert mess.menu(actors["s1"])["meals"][1]["calories"] == 800


def test_meal_records_nutrition_and_attendance(actors, at):
    qr = mess.generate_qr(actors["s1"], "lunch")
    mess.verify_entry(actors["mess"], qr["payload"], "lunch")
    rec = mess.meal_records(actors["s1"])
    assert rec["summary"]["meals_consumed"] == 1 and rec["daily_nutrition"][0]["calories"] > 0
    att = mess.attendance(actors["mess"])
    assert att["counts"]["lunch"] == 1
    with pytest.raises(Forbidden):
        mess.attendance(actors["s1"])


def test_forecast_uses_ledger_and_mess_outs(actors, at):
    with pytest.raises(Forbidden):
        mess.forecast(actors["s1"], "lunch")
    base = mess.forecast(actors["mess"], "lunch", "2026-12-02")
    assert base["predicted_headcount"] > 0
    r = mess.submit_mess_out(actors["s1"], "2026-12-01", "2026-12-04")
    mess.decide_mess_out(actors["mess"], r["request_id"], True)
    after = mess.forecast(actors["mess"], "lunch", "2026-12-02")
    assert after["approved_mess_outs"] == 1 and after["predicted_headcount"] <= base["predicted_headcount"]
