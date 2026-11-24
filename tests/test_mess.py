import pytest

from app import db
from app.services import clock, events, mess
from app.services.errors import Forbidden, RuleViolation, ValidationFailed







def test_overlapping_declarations_rejected(actors, at):
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



def test_cannot_read_or_act_for_other_students(actors, at):
    with pytest.raises(Forbidden):
        mess.profile(actors["s1"], "2024668")
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
    mess.submit_mess_out(actors["s1"], "2026-12-01", "2026-12-04")
    after = mess.forecast(actors["mess"], "lunch", "2026-12-02")
    assert after["approved_mess_outs"] == 1 and after["predicted_headcount"] <= base["predicted_headcount"]
