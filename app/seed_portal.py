"""Idempotent reference data for the portal (instructors, lab slots, academic history, society memberships).

This only fills in NON-secret demo/reference data on top of seed_db.py. It never creates passwords.
Run:  python -m app.seed_portal
"""
from app import db

INSTRUCTORS = {"CS101": "Dr. Ayesha Khan", "CS102": "Dr. Ayesha Khan", "CS201": "Dr. Imran Qureshi", "CS232": "Dr. Sana Malik",
               "CS271": "Dr. Imran Qureshi", "CS325": "Farah Saeed", "CS330": "Dr. Bilal Ahmed", "CS412": "Dr. Bilal Ahmed",
               "EE213": "Dr. Usman Tariq", "HU101": "Ms. Hira Noor", "MT101": "Dr. Kamran Ali", "MT201": "Dr. Kamran Ali"}
LAB_SLOTS = {"CS232": "W 09:00-11:00", "EE213": "F 14:00-17:00", "CS102": "F 09:00-11:00"}
DESCRIPTIONS = {
    "CS201": "Core data structures and algorithm analysis.", "CS232": "Relational modelling, SQL and transactions.",
    "CS325": "Agile process, requirements, architecture and testing.", "CS330": "Search, knowledge representation and intelligent agents.",
    "CS412": "Supervised and deep learning with applications.", "EE213": "Microprocessor architecture, assembly and peripheral interfacing."}
HISTORY = {
    "2024405": [("CS101", "A"), ("CS102", "A-"), ("CS201", "B+"), ("CS232", "B"), ("MT101", "A"), ("HU101", "B+")],
    "2024668": [("CS101", "B+"), ("CS102", "A"), ("MT101", "B")],
    "2024662": [("CS101", "A"), ("CS102", "A"), ("CS201", "A-"), ("CS232", "B+"), ("CS271", "B"), ("MT101", "B+"), ("MT201", "A-"), ("HU101", "A")],
}
INTERESTS = {"2024405": "machine learning, software design", "2024668": "databases, backend, data", "2024662": "embedded, hardware, microcontrollers"}
SEMESTER = {"2024405": 5, "2024668": 3, "2024662": 7}
MEMBERS = [("ACM GIKI Chapter", "2024405", "member"), ("Netronix Networking Society", "2024405", "member"),
           ("ACM GIKI Chapter", "2024662", "office_bearer"), ("IEEE Student Branch", "2024662", "office_bearer"),
           ("IEEE Student Branch", "2024668", "member"), ("GIKI Sports Club", "2024668", "member")]


def seed() -> None:
    with db.tx() as cur:
        cur.execute("INSERT INTO student_profiles (user_id) SELECT user_id FROM users WHERE role='student' ON CONFLICT DO NOTHING")
        # Seeded administrator accounts (e.g. ADMIN01) that pre-date scope-based RBAC receive the 'super' scope once.
        cur.execute("INSERT INTO admin_scopes (user_id, scope) SELECT user_id, 'super' FROM users u WHERE role='admin' AND NOT EXISTS (SELECT 1 FROM admin_scopes s WHERE s.user_id=u.user_id)")
        cur.execute("UPDATE venues SET exclusive=FALSE WHERE venue_id='SPORTS_GYM'")
        for code, name in INSTRUCTORS.items():
            cur.execute("UPDATE courses SET instructor=COALESCE(instructor,%s) WHERE course_code=%s", (name, code))
        for code, lab in LAB_SLOTS.items():
            cur.execute("UPDATE courses SET lab_slot=COALESCE(lab_slot,%s) WHERE course_code=%s", (lab, code))
        for code, desc in DESCRIPTIONS.items():
            cur.execute("UPDATE courses SET description=COALESCE(description,%s) WHERE course_code=%s", (desc, code))
        cur.execute("UPDATE courses SET capacity=40 WHERE capacity=60 AND course_code IN ('CS330','CS412')")
        for uid in HISTORY:
            cur.execute("SELECT 1 FROM users WHERE user_id=%s", (uid,))
            if not cur.fetchone():
                continue
            for code, grade in HISTORY[uid]:
                cur.execute("INSERT INTO course_history (user_id, course_code, grade, term) VALUES (%s,%s,%s,'Previous') ON CONFLICT DO NOTHING", (uid, code, grade))
            cur.execute("UPDATE student_profiles SET interests=%s, current_semester=%s WHERE user_id=%s AND interests=''", (INTERESTS[uid], SEMESTER[uid], uid))
        # team-seeded societies (campus_societies) become legacy rows of the master societies table; official ones are imported by app.services.societies
        cur.execute("""INSERT INTO societies (name, name_key, category, origin, advisor_id)
                       SELECT society_name, lower(regexp_replace(society_name, '[^a-zA-Z0-9]+', '', 'g')), category, 'legacy', advisor_id FROM campus_societies ON CONFLICT (name_key) DO NOTHING""")
        for soc, uid, role in MEMBERS:
            cur.execute("""INSERT INTO society_memberships (society_id, user_id, member_role)
                           SELECT s.society_id, %s, %s FROM societies s WHERE s.name_key = lower(regexp_replace(%s, '[^a-zA-Z0-9]+', '', 'g'))
                           AND EXISTS (SELECT 1 FROM users WHERE user_id=%s) ON CONFLICT DO NOTHING""", (uid, role, soc, uid))
    from app.services import billing
    billing.ensure_all_student_deposits(paid=True)


if __name__ == "__main__":
    seed()
    print("portal reference data seeded")
