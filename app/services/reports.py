"""Unified reporting dashboard. Each section is only produced for administrators holding the matching scope."""
from app import db
from .actor import Actor
from . import billing, clock, settings


def overview(actor: Actor) -> dict:
    actor.require_role("admin")
    out: dict = {"generated_at": clock.now().isoformat(timespec="seconds"), "sections": []}
    term = settings.get_quick("current_term")
    out["term"] = term
    if actor.has_scope("academic"):
        out["sections"].append("courses")
        out["courses"] = {
            "totals": db.query_one("""SELECT (SELECT COUNT(*) FROM courses) AS courses, (SELECT COUNT(*) FROM courses WHERE is_active) AS active,
                (SELECT COUNT(*) FROM enrollments WHERE term=%s AND status='registered') AS registrations,
                (SELECT COUNT(DISTINCT user_id) FROM enrollments WHERE term=%s AND status='registered') AS students_registered,
                (SELECT COUNT(*) FROM users WHERE role='student' AND is_active) AS students""", (term, term)),
            "by_course": db.query("""SELECT c.course_code, c.title, c.capacity, c.category, c.is_active, COUNT(e.*) FILTER (WHERE e.status='registered') AS registered,
                COUNT(e.*) FILTER (WHERE e.status='dropped') AS dropped FROM courses c LEFT JOIN enrollments e ON e.course_code=c.course_code AND e.term=%s
                GROUP BY c.course_code ORDER BY registered DESC, c.course_code""", (term,)),
            "by_channel": db.query("SELECT channel, COUNT(*) AS n FROM enrollments WHERE term=%s GROUP BY channel", (term,)),
            "by_category": db.query("""SELECT c.category, COUNT(e.*) AS n FROM enrollments e JOIN courses c USING (course_code) WHERE e.term=%s AND e.status='registered' GROUP BY c.category ORDER BY n DESC""", (term,)),
        }
    if actor.has_scope("mess"):
        out["sections"].append("mess")
        out["mess"] = {
            "billing": billing.billing_summary(actor),
            "mess_out_by_status": db.query("SELECT status, COUNT(*) AS n FROM mess_out_requests GROUP BY status ORDER BY status"),
            "attendance_14d": db.query("""SELECT meal_date::text AS day, meal_type, COUNT(*) AS n FROM mess_ledger WHERE status='consumed' AND meal_date >= CURRENT_DATE - 13
                GROUP BY meal_date, meal_type ORDER BY meal_date, meal_type"""),
            "entries_today": db.query_one("SELECT COUNT(*) AS n FROM mess_ledger WHERE status='consumed' AND meal_date=CURRENT_DATE")["n"],
        }
    if actor.has_scope("requisition"):
        out["sections"].append("requisitions")
        out["requisitions"] = {
            "by_status": db.query("SELECT status, COUNT(*) AS n FROM society_requisitions GROUP BY status ORDER BY n DESC"),
            "by_society": db.query("SELECT society_name, COUNT(*) AS n, COUNT(*) FILTER (WHERE status='approved') AS approved FROM society_requisitions GROUP BY society_name ORDER BY n DESC"),
            "pending_admin": db.query_one("SELECT COUNT(*) AS n FROM society_requisitions WHERE status='pending_admin_approval'")["n"],
        }
        out["sports"] = {
            "utilization": db.query("""SELECT v.venue_id, v.name, COALESCE(SUM(b.duration_hours) FILTER (WHERE b.status IN ('approved','checked_in')),0)::float AS booked_hours,
                COUNT(b.*) FILTER (WHERE b.status='checked_in') AS checked_in, COUNT(b.*) FILTER (WHERE b.status='auto-released') AS no_shows,
                COUNT(b.*) FILTER (WHERE b.status='cancelled') AS cancelled FROM venues v LEFT JOIN bookings b ON b.venue_id=v.venue_id AND b.source='sports_reservation'
                AND b.start_ts >= now() - interval '30 days' WHERE v.category='sports' GROUP BY v.venue_id ORDER BY booked_hours DESC"""),
            "suspended_students": db.query_one("SELECT COUNT(*) AS n FROM no_show_records WHERE suspended")["n"],
        }
        out["equipment"] = {
            "by_status": db.query("SELECT status, COUNT(*) AS n FROM lab_equipment GROUP BY status ORDER BY n DESC"),
            "overdue": db.query("""SELECT e.barcode, e.name, e.custodian_id, u.full_name, e.due_date FROM lab_equipment e JOIN users u ON u.user_id=e.custodian_id
                WHERE e.status='in_custody' AND e.due_date < CURRENT_DATE ORDER BY e.due_date"""),
            "by_lab": db.query("SELECT lab, COUNT(*) AS total, COUNT(*) FILTER (WHERE status='in_custody') AS in_custody FROM lab_equipment GROUP BY lab ORDER BY lab"),
        }
    if actor.has_scope("super"):
        out["sections"].append("activity")
        out["activity"] = {
            "daily_14d": db.query("""SELECT at::date::text AS day, COUNT(*) FILTER (WHERE outcome='success') AS ok, COUNT(*) FILTER (WHERE outcome<>'success') AS rejected
                FROM audit_log WHERE at >= now() - interval '14 days' AND action NOT LIKE 'auth.%%' GROUP BY 1 ORDER BY 1"""),
            "by_channel": db.query("SELECT channel, COUNT(*) AS n FROM audit_log WHERE at >= now() - interval '30 days' AND outcome='success' AND action NOT LIKE 'auth.%%' GROUP BY channel"),
            "active_students_7d": db.query_one("SELECT COUNT(DISTINCT actor_id) AS n FROM audit_log WHERE at >= now() - interval '7 days' AND actor_role='student'")["n"],
            "failed_event_handlers": db.query_one("SELECT COUNT(*) AS n FROM event_handler_runs WHERE status='failed'")["n"],
            "users": db.query("SELECT role, COUNT(*) AS n FROM users WHERE is_active GROUP BY role ORDER BY role"),
        }
    return out


def student_dashboard(actor: Actor) -> dict:
    """Personal summary for the student home page (own data only)."""
    from . import billing, courses, mess, sports, equipment, notifications, requisitions
    uid = actor.user_id
    regs = courses.my_registrations(actor)
    tt = courses.timetable(actor)
    q = sports.quota(actor)
    upcoming = [b for b in sports.my_bookings(actor, upcoming_only=True)][:5]
    return {
        "courses": {"term": regs["term"], "count": len(regs["courses"]), "credits": regs["total_credits"], "max_credits": regs["max_credits"], "has_conflicts": tt["has_conflicts"]},
        "mess": {"bill": billing.estimate_bill(actor), "deposit": billing.deposit_summary(actor), "upcoming_mess_out": [r for r in mess.list_mess_out(actor) if r["status"] in ("pending", "approved") and r["end_date"] >= clock.today()][:3]},
        "bookings": upcoming, "quota": q,
        "equipment": equipment.my_custody(actor),
        "requisitions": [r for r in requisitions.list_requisitions(actor, "mine") if r["status"].startswith("pending")][:5],
        "unread_notifications": notifications.unread_count(actor),
    }
