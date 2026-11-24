"""Course Management business logic - the single implementation behind the web portal AND the AI assistant."""
import re

from app import db
from . import audit, events, settings
from .actor import Actor
from .audit import op
from .errors import Conflict, Forbidden, NotFound, RuleViolation, ValidationFailed

COURSE_ENROLLED = "course.enrolled"
COURSE_DROPPED = "course.dropped"

PASSING = {"A+": 4.0, "A": 4.0, "A-": 3.67, "B+": 3.33, "B": 3.0, "B-": 2.67, "C+": 2.33, "C": 2.0, "C-": 1.67, "D": 1.0}
DAY_ORDER = ["M", "T", "W", "Th", "F", "Sa", "Su"]
CODE_RE = re.compile(r"^[A-Z]{2,4}\d{3}$")
_SLOT_RE = re.compile(r"^\s*((?:Th|Sa|Su|M|T|W|F)+)\s+(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*$")


# ── Timetable primitives ────────────────────────────────────

def parse_slot(slot: str | None) -> list[tuple[str, int, int]]:
    """'MWF 11:00-12:00' -> [('M',660,720), ('W',660,720), ('F',660,720)]. Returns [] for empty slots."""
    if not slot:
        return []
    m = _SLOT_RE.match(slot)
    if not m:
        raise ValidationFailed(f"Invalid time slot '{slot}'. Use e.g. 'MWF 11:00-12:00' or 'TTh 14:00-15:30'.")
    days = re.findall(r"Th|Sa|Su|M|T|W|F", m.group(1))
    start, end = int(m.group(2)) * 60 + int(m.group(3)), int(m.group(4)) * 60 + int(m.group(5))
    if not (0 <= start < end <= 24 * 60):
        raise ValidationFailed(f"Invalid time range in slot '{slot}'.")
    return [(d, start, end) for d in days]


def _meetings(course: dict) -> list[tuple[str, int, int, str]]:
    out = [(d, s, e, "lecture") for d, s, e in parse_slot(course.get("slot"))]
    out += [(d, s, e, "lab") for d, s, e in parse_slot(course.get("lab_slot"))]
    return out


def find_conflicts(a: dict, b: dict) -> list[dict]:
    """Every overlapping meeting between two courses (lecture/lecture, lecture/lab, lab/lab)."""
    found = []
    for da, sa, ea, ka in _meetings(a):
        for db_, sb, eb, kb in _meetings(b):
            if da == db_ and sa < eb and sb < ea:
                found.append({"course_a": a["course_code"], "course_b": b["course_code"], "day": da,
                              "kind": f"{ka}/{kb}", "overlap": f"{_hm(max(sa, sb))}-{_hm(min(ea, eb))}"})
    return found


def _hm(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


# ── Reads ───────────────────────────────────────────────────

def _prereqs(cur, code: str) -> list[str]:
    cur.execute("SELECT prereq_code FROM course_prerequisites WHERE course_code=%s ORDER BY 1", (code,))
    return [r["prereq_code"] for r in cur.fetchall()]


def _course(cur, code: str, lock: bool = False) -> dict:
    cur.execute("SELECT * FROM courses WHERE course_code=%s" + (" FOR UPDATE" if lock else ""), ((code or "").upper().strip(),))
    row = cur.fetchone()
    if not row:
        raise NotFound(f"Course '{code}' does not exist.")
    row = dict(row)
    row.pop("policy_embedding", None)
    return row


def _term(cur) -> str:
    return settings.get(cur, "current_term")


def _passed(cur, user_id: str) -> set[str]:
    cur.execute("SELECT course_code FROM course_history WHERE user_id=%s AND grade = ANY(%s)", (user_id, list(PASSING)))
    return {r["course_code"] for r in cur.fetchall()}


def _registered(cur, user_id: str, term: str) -> list[dict]:
    cur.execute(
        """SELECT c.*, e.enrollment_id, e.registered_at FROM enrollments e JOIN courses c USING (course_code)
           WHERE e.user_id=%s AND e.term=%s AND e.status='registered' ORDER BY c.course_code""", (user_id, term))
    return [{k: v for k, v in dict(r).items() if k != "policy_embedding"} for r in cur.fetchall()]


def _seats_taken(cur, code: str, term: str) -> int:
    cur.execute("SELECT COUNT(*) AS n FROM enrollments WHERE course_code=%s AND term=%s AND status='registered'", (code, term))
    return cur.fetchone()["n"]


def _check_student(cur, user_id: str) -> None:
    cur.execute("SELECT role, is_active FROM users WHERE user_id=%s", (user_id,))
    u = cur.fetchone()
    if not u:
        raise NotFound("Student not found.")
    if u["role"] != "student":
        raise ValidationFailed("Course registration applies to student accounts only.")
    if not u["is_active"]:
        raise RuleViolation("This student account is deactivated.")


def list_courses(actor: Actor, q: str | None = None, category: str | None = None, include_inactive: bool = False,
                 student_id: str | None = None, offered_only: bool = False) -> list[dict]:
    if include_inactive:
        actor.require_scope("academic")
    with db.tx() as cur:
        term = _term(cur)
        where, params = [], []
        if not include_inactive:
            where.append("c.is_active")
        if q:
            where.append("(c.course_code ILIKE %s OR c.title ILIKE %s OR COALESCE(c.domains,'') ILIKE %s OR COALESCE(c.description,'') ILIKE %s)")
            params += [f"%{q}%"] * 4
        if category:
            where.append("c.category = %s"); params.append(category)
        if offered_only:
            where.append("NOT c.catalog_only")
        cur.execute(
            f"""SELECT c.course_code, c.title, c.credits, c.slot, c.lab_slot, c.category, c.domains, c.description,
                       c.instructor, c.capacity, c.is_active, c.catalog_only, c.department,
                       (SELECT COUNT(*) FROM enrollments e WHERE e.course_code=c.course_code AND e.term=%s AND e.status='registered') AS seats_taken,
                       COALESCE((SELECT array_agg(p.prereq_code ORDER BY p.prereq_code) FROM course_prerequisites p WHERE p.course_code=c.course_code), '{{}}') AS prerequisites
                FROM courses c {('WHERE ' + ' AND '.join(where)) if where else ''} ORDER BY c.course_code""", [term] + params)
        rows = [dict(r) for r in cur.fetchall()]
        sid = actor.resolve_student(student_id, "academic") if (actor.is_student or student_id) else None
        passed, regs = (_passed(cur, sid), {c["course_code"] for c in _registered(cur, sid, term)}) if sid else (set(), set())
        for r in rows:
            r["seats_left"] = max(0, r["capacity"] - r["seats_taken"])
            if sid:
                missing = [p for p in r["prerequisites"] if p not in passed]
                r["student_status"] = ("registered" if r["course_code"] in regs else "completed" if r["course_code"] in passed
                                       else "not_offered" if r["catalog_only"] else "blocked_prerequisites" if missing else "full" if r["seats_left"] == 0 else "available")
                r["missing_prerequisites"] = missing
        return rows


def get_course(actor: Actor, code: str, student_id: str | None = None) -> dict:
    rows = [c for c in list_courses(actor, student_id=student_id, include_inactive=actor.has_scope("academic")) if c["course_code"] == (code or "").upper().strip()]
    if not rows:
        raise NotFound(f"Course '{code}' does not exist.")
    course = rows[0]
    course["prerequisite_chain"] = prerequisite_chain(code)["chain"]
    from .catalog_import import course_source
    course["official_source"] = course_source(code)
    return course


def prerequisite_chain(code: str) -> dict:
    """Recursive prerequisite DAG traversal (cycle-safe)."""
    with db.tx() as cur:
        root = _course(cur, code)

        def build(c: str, seen: frozenset) -> list[dict]:
            nodes = []
            for p in _prereqs(cur, c):
                cur.execute("SELECT title FROM courses WHERE course_code=%s", (p,))
                title = cur.fetchone()["title"]
                nodes.append({"course_code": p, "title": title,
                              "prerequisites": [] if p in seen else build(p, seen | {p})})
            return nodes
        return {"course_code": root["course_code"], "title": root["title"], "chain": build(root["course_code"], frozenset({root["course_code"]}))}


def _all_prereqs(cur, code: str) -> set[str]:
    seen, stack = set(), [code]
    while stack:
        for p in _prereqs(cur, stack.pop()):
            if p not in seen:
                seen.add(p); stack.append(p)
    return seen


def check_prerequisites(actor: Actor, code: str, student_id: str | None = None) -> dict:
    sid = actor.resolve_student(student_id, "academic")
    with db.tx() as cur:
        course = _course(cur, code)
        passed = _passed(cur, sid)
        direct = _prereqs(cur, course["course_code"])
        missing = [p for p in direct if p not in passed]
        return {"course_code": course["course_code"], "title": course["title"], "student_id": sid,
                "required": direct, "completed": [p for p in direct if p in passed], "missing": missing,
                "eligible": not missing,
                "message": (f"All prerequisites for {course['course_code']} are satisfied." if not missing else
                            f"Missing prerequisites for {course['course_code']}: {', '.join(missing)}.")}


def timetable(actor: Actor, student_id: str | None = None, extra_course: str | None = None) -> dict:
    """Weekly timetable plus a clash matrix between every pair of registered courses (and an optional candidate)."""
    sid = actor.resolve_student(student_id, "academic")
    with db.tx() as cur:
        term = _term(cur)
        courses = _registered(cur, sid, term)
        candidate = _course(cur, extra_course) if extra_course else None
        everything = courses + ([candidate] if candidate and candidate["course_code"] not in {c["course_code"] for c in courses} else [])
        conflicts = []
        for i in range(len(everything)):
            for j in range(i + 1, len(everything)):
                conflicts += find_conflicts(everything[i], everything[j])
        entries = []
        for c in courses:
            for d, s, e, kind in _meetings(c):
                entries.append({"day": d, "start": _hm(s), "end": _hm(e), "kind": kind, "course_code": c["course_code"],
                                "title": c["title"], "instructor": c.get("instructor")})
        entries.sort(key=lambda x: (DAY_ORDER.index(x["day"]), x["start"]))
        return {"term": term, "student_id": sid, "entries": entries, "conflicts": conflicts,
                "has_conflicts": bool(conflicts), "courses": [c["course_code"] for c in courses],
                "total_credits": sum(c["credits"] for c in courses)}


def check_conflicts(actor: Actor, code: str, student_id: str | None = None) -> dict:
    t = timetable(actor, student_id, extra_course=code)
    code = code.upper().strip()
    mine = [c for c in t["conflicts"] if code in (c["course_a"], c["course_b"])]
    return {"course_code": code, "student_id": t["student_id"], "has_conflicts": bool(mine), "conflicts": mine,
            "message": ("No timetable clash." if not mine else
                        "Clash: " + "; ".join(f"{c['course_a']} vs {c['course_b']} on {c['day']} ({c['overlap']}, {c['kind']})" for c in mine))}


def my_registrations(actor: Actor, student_id: str | None = None) -> dict:
    sid = actor.resolve_student(student_id, "academic")
    with db.tx() as cur:
        term = _term(cur)
        regs = _registered(cur, sid, term)
        return {"term": term, "student_id": sid, "courses": [
            {k: c[k] for k in ("course_code", "title", "credits", "slot", "lab_slot", "instructor", "enrollment_id", "registered_at")} for c in regs],
            "total_credits": sum(c["credits"] for c in regs), "max_credits": settings.get(cur, "max_credits_per_term")}


def academic_record(actor: Actor, student_id: str | None = None) -> dict:
    sid = actor.resolve_student(student_id, "academic")
    with db.tx() as cur:
        cur.execute("""SELECT h.course_code, c.title, c.credits, h.grade, h.term FROM course_history h
                       JOIN courses c USING (course_code) WHERE h.user_id=%s ORDER BY h.term NULLS LAST, h.course_code""", (sid,))
        rows = [dict(r) for r in cur.fetchall()]
        graded = [r for r in rows if r["grade"] in PASSING or r["grade"] == "F"]
        pts = sum(PASSING.get(r["grade"], 0.0) * r["credits"] for r in graded)
        creds = sum(r["credits"] for r in graded)
        cur.execute("SELECT program, current_semester, interests, allergies FROM student_profiles WHERE user_id=%s", (sid,))
        prof = cur.fetchone() or {}
        return {"student_id": sid, "history": rows, "cgpa": round(pts / creds, 2) if creds else None,
                "credits_earned": sum(r["credits"] for r in rows if r["grade"] in PASSING), "profile": dict(prof)}


# ── Student writes ──────────────────────────────────────────

@op("course.register", "course", "course_code")
def register(actor: Actor, course_code: str, student_id: str | None = None, dry_run: bool = False) -> dict:
    sid = actor.resolve_student(student_id, "academic")
    code = (course_code or "").upper().strip()
    with db.tx() as cur:
        _check_student(cur, sid)
        cur.execute("SELECT 1 FROM users WHERE user_id=%s FOR UPDATE", (sid,))  # serialise this student's registrations
        term = _term(cur)
        course = _course(cur, code, lock=True)                                  # serialise seat counting per course
        if not course["is_active"]:
            raise RuleViolation(f"{code} is not open for registration.")
        if course["catalog_only"]:
            raise RuleViolation(f"{code} is in the official GIKI catalogue but is not scheduled for {_term(cur)}; it cannot be registered yet.", code="not_offered")
        regs = _registered(cur, sid, term)
        if any(c["course_code"] == code for c in regs):
            raise Conflict(f"You are already registered for {code}.")
        passed = _passed(cur, sid)
        if code in passed:
            raise RuleViolation(f"You have already completed {code}.")
        missing = [p for p in _prereqs(cur, code) if p not in passed]
        if missing:
            raise RuleViolation(f"Prerequisites not met for {code}: you still need {', '.join(missing)}.",
                                code="prerequisites_unmet", details={"missing": missing})
        total = sum(c["credits"] for c in regs) + course["credits"]
        limit = settings.get(cur, "max_credits_per_term")
        if total > limit:
            raise RuleViolation(f"Credit limit exceeded: registering {code} would make {total} credit hours (limit {limit}).",
                                code="credit_limit")
        clashes = [x for c in regs for x in find_conflicts(course, c)]
        if clashes:
            raise RuleViolation("Timetable clash: " + "; ".join(
                f"{x['course_a']} vs {x['course_b']} on {x['day']} ({x['overlap']})" for x in clashes),
                code="schedule_conflict", details={"conflicts": clashes})
        if _seats_taken(cur, code, term) >= course["capacity"]:
            raise RuleViolation(f"{code} is full ({course['capacity']} seats).", code="course_full")
        if dry_run:
            return {"ok": True, "dry_run": True, "course_code": code, "title": course["title"], "credits": course["credits"], "term": term,
                    "total_credits_after": total, "seats_left_after": course["capacity"] - _seats_taken(cur, code, term) - 1}
        cur.execute("INSERT INTO enrollments (user_id, course_code, term, channel) VALUES (%s,%s,%s,%s) RETURNING enrollment_id",
                    (sid, code, term, actor.channel))
        eid = cur.fetchone()["enrollment_id"]
        audit.log(cur, actor, "course.register", "course", code, detail={"student_id": sid, "term": term, "enrollment_id": eid})
        events.emit(cur, COURSE_ENROLLED, {"user_id": sid, "course_code": code, "title": course["title"], "term": term, "enrollment_id": eid})
        return {"ok": True, "enrollment_id": eid, "course_code": code, "title": course["title"], "term": term,
                "total_credits": total, "message": f"Registered {sid} for {code} - {course['title']} ({term})."}


@op("course.drop", "course", "course_code")
def drop(actor: Actor, course_code: str, student_id: str | None = None) -> dict:
    sid = actor.resolve_student(student_id, "academic")
    code = (course_code or "").upper().strip()
    with db.tx() as cur:
        cur.execute("SELECT 1 FROM users WHERE user_id=%s FOR UPDATE", (sid,))
        term = _term(cur)
        cur.execute("SELECT enrollment_id FROM enrollments WHERE user_id=%s AND course_code=%s AND term=%s AND status='registered' FOR UPDATE",
                    (sid, code, term))
        row = cur.fetchone()
        if not row:
            raise NotFound(f"You are not registered for {code} this term.")
        import datetime
        deadline = datetime.date.fromisoformat(settings.get(cur, "drop_deadline"))
        if datetime.date.today() > deadline and not actor.has_scope("academic"):
            raise RuleViolation(f"The drop deadline ({deadline}) has passed. Contact the registrar.", code="drop_deadline")
        cur.execute("UPDATE enrollments SET status='dropped', dropped_at=now() WHERE enrollment_id=%s", (row["enrollment_id"],))
        audit.log(cur, actor, "course.drop", "course", code, detail={"student_id": sid, "term": term})
        events.emit(cur, COURSE_DROPPED, {"user_id": sid, "course_code": code, "term": term, "enrollment_id": row["enrollment_id"]})
        return {"ok": True, "course_code": code, "message": f"Dropped {code} for {term}."}


# ── Intelligence: recommender, what-if, Ask GIKI ────────────

_STOP = {"i", "am", "the", "a", "an", "and", "or", "in", "on", "of", "to", "for", "with", "about", "into", "like", "want",
         "interested", "interest", "my", "me", "is", "are", "learn", "study", "course", "courses", "class", "classes"}


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9+#]+", (text or "").lower()) if t not in _STOP and len(t) > 1}


def recommend_electives(actor: Actor, interests: str | None = None, student_id: str | None = None, limit: int = 5) -> dict:
    sid = actor.resolve_student(student_id, "academic")
    with db.tx() as cur:
        term = _term(cur)
        cur.execute("SELECT interests FROM student_profiles WHERE user_id=%s", (sid,))
        prof = cur.fetchone()
        text = interests or (prof["interests"] if prof else "") or ""
        want = _tokens(text)
        passed = _passed(cur, sid)
        regs = {c["course_code"] for c in _registered(cur, sid, term)}
        cur.execute("SELECT * FROM courses WHERE is_active AND category <> 'core' ORDER BY course_code")
        cands = []
        for c in cur.fetchall():
            if c["course_code"] in passed or c["course_code"] in regs:
                continue
            hay = _tokens(" ".join([c["title"], c["domains"] or "", c["description"] or ""]))
            hits = sorted(want & hay)
            missing = [p for p in _prereqs(cur, c["course_code"]) if p not in passed]
            score = len(hits) * 10 + (5 if not missing else 0) + (2 if c["category"] == "elective" else 0) + (3 if not c["catalog_only"] else 0)
            if want and not hits:
                continue
            cands.append({"course_code": c["course_code"], "title": c["title"], "credits": c["credits"], "category": c["category"], "offered_this_term": not c["catalog_only"],
                          "domains": c["domains"], "matched_interests": hits, "missing_prerequisites": missing,
                          "eligible_now": not missing, "score": score,
                          "reason": (f"Matches your interest in {', '.join(hits)}" if hits else "Open elective you are eligible for")
                                    + ("" if not c["catalog_only"] else " (official catalogue course, not yet scheduled)")
                                    + ("" if not missing else f"; requires {', '.join(missing)} first")})
        cands.sort(key=lambda x: (-x["score"], x["course_code"]))
        return {"student_id": sid, "interests_used": text, "recommendations": cands[:max(1, min(limit, 10))]}


def what_if(actor: Actor, minors: list[str] | None = None, hypothetical_courses: list[str] | None = None,
            student_id: str | None = None) -> dict:
    """What-if degree / minor simulator over the prerequisite DAG, completed work and the current registration."""
    sid = actor.resolve_student(student_id, "academic")
    with db.tx() as cur:
        term = _term(cur)
        passed = _passed(cur, sid)
        regs = {c["course_code"] for c in _registered(cur, sid, term)}
        extra = {c.upper().strip() for c in (hypothetical_courses or [])}
        for c in extra:
            _course(cur, c)
        cur.execute("SELECT * FROM degree_minors ORDER BY minor_name")
        all_minors = {m["minor_name"]: m for m in cur.fetchall()}
        wanted = minors or list(all_minors)
        unknown = [m for m in wanted if m not in all_minors]
        if unknown:
            raise NotFound(f"Unknown minor(s): {', '.join(unknown)}. Available: {', '.join(all_minors)}.")
        effective = passed | regs | extra
        out, union_required = [], set()
        for name in wanted:
            req = [c.strip() for c in all_minors[name]["required_courses"].split(",") if c.strip()]
            union_required |= set(req)
            cur.execute("SELECT course_code, credits FROM courses WHERE course_code = ANY(%s)", (req,))
            credits = {r["course_code"]: r["credits"] for r in cur.fetchall()}
            done = [c for c in req if c in passed]
            planned = [c for c in req if c not in passed and c in (regs | extra)]
            remaining = [c for c in req if c not in effective]
            blockers = {}
            for c in remaining:
                miss = sorted(p for p in _all_prereqs(cur, c) if p not in effective)
                if miss:
                    blockers[c] = miss
            out.append({"minor": name, "required_courses": req, "required_credits": all_minors[name]["required_credits"],
                        "completed": done, "in_progress_or_planned": planned, "remaining": remaining,
                        "credits_completed": sum(credits.get(c, 0) for c in done),
                        "credits_remaining": sum(credits.get(c, 0) for c in remaining),
                        "prerequisite_blockers": blockers,
                        "eligible_next": [c for c in remaining if c not in blockers],
                        "progress_pct": round(100 * (len(done) + len(planned)) / max(1, len(req))),
                        "feasible": True, "description": all_minors[name]["description"]})
        overlap = {}
        if len(wanted) > 1:
            for i, a in enumerate(out):
                for b in out[i + 1:]:
                    shared = sorted(set(a["required_courses"]) & set(b["required_courses"]))
                    if shared:
                        overlap[f"{a['minor']} + {b['minor']}"] = shared
        return {"student_id": sid, "term": term, "simulated_extra_courses": sorted(extra), "minors": out,
                "shared_courses_between_minors": overlap,
                "combined_remaining": sorted(c for c in union_required if c not in effective)}


def ask_giki(question: str, limit: int = 4) -> dict:
    """Ask GIKI: hybrid (pgvector + full-text) retrieval over OFFICIAL giki.edu.pk documents only, with source references."""
    from . import knowledge
    return knowledge.ask(question, limit)


# ── Administration ──────────────────────────────────────────

def _validate_course_fields(f: dict, creating: bool) -> dict:
    out = {}
    if "course_code" in f or creating:
        code = (f.get("course_code") or "").upper().strip()
        if not CODE_RE.match(code):
            raise ValidationFailed("Course code must look like CS101 (2-4 letters followed by 3 digits).")
        out["course_code"] = code
    if "title" in f or creating:
        t = (f.get("title") or "").strip()
        if not t or len(t) > 100:
            raise ValidationFailed("Title is required (max 100 characters).")
        out["title"] = t
    if "credits" in f or creating:
        try:
            cr = int(f.get("credits"))
        except (TypeError, ValueError):
            raise ValidationFailed("Credit hours must be a whole number.")
        if not 1 <= cr <= 6:
            raise ValidationFailed("Credit hours must be between 1 and 6.")
        out["credits"] = cr
    for key in ("slot", "lab_slot"):
        if key in f:
            v = (f.get(key) or "").strip() or None
            parse_slot(v)
            out[key] = v
    if "capacity" in f:
        try:
            cap = int(f["capacity"])
        except (TypeError, ValueError):
            raise ValidationFailed("Capacity must be a whole number.")
        if not 0 <= cap <= 1000:
            raise ValidationFailed("Capacity must be between 0 and 1000.")
        out["capacity"] = cap
    if "category" in f:
        if f["category"] not in ("core", "elective", "math", "humanities", "science"):
            raise ValidationFailed("Category must be core, elective, math, humanities or science.")
        out["category"] = f["category"]
    for key in ("instructor", "description", "domains"):
        if key in f:
            out[key] = (f.get(key) or "").strip() or None
    if "is_active" in f:
        out["is_active"] = bool(f["is_active"])
    if "catalog_only" in f:
        out["catalog_only"] = bool(f["catalog_only"])
    if "department" in f:
        out["department"] = (f.get("department") or "").strip()[:120] or None
    return out


def _would_cycle(cur, course: str, prereqs: list[str]) -> bool:
    return any(p == course or course in _all_prereqs(cur, p) for p in prereqs)


@op("course.create", "course", "course_code")
def create_course(actor: Actor, course_code: str, title: str, credits: int, prerequisites: list[str] | None = None, **fields) -> dict:
    actor.require_scope("academic")
    f = _validate_course_fields({"course_code": course_code, "title": title, "credits": credits, **fields}, creating=True)
    with db.tx() as cur:
        cur.execute("SELECT 1 FROM courses WHERE course_code=%s", (f["course_code"],))
        if cur.fetchone():
            raise Conflict(f"Course {f['course_code']} already exists.")
        cols = list(f)
        cur.execute(f"INSERT INTO courses ({','.join(cols)}) VALUES ({','.join(['%s'] * len(cols))})", [f[c] for c in cols])
        _set_prereqs(cur, f["course_code"], prerequisites or [])
        audit.log(cur, actor, "course.create", "course", f["course_code"], detail={**f, "prerequisites": prerequisites or []})
    return get_course(actor, f["course_code"])


def _set_prereqs(cur, code: str, prereqs: list[str]) -> None:
    prereqs = sorted({p.upper().strip() for p in prereqs if p and p.strip()})
    for p in prereqs:
        _course(cur, p)
    if _would_cycle(cur, code, prereqs):
        raise RuleViolation(f"Those prerequisites would create a circular dependency for {code}.", code="prerequisite_cycle")
    cur.execute("DELETE FROM course_prerequisites WHERE course_code=%s", (code,))
    for p in prereqs:
        cur.execute("INSERT INTO course_prerequisites (course_code, prereq_code) VALUES (%s,%s)", (code, p))


@op("course.update", "course", "course_code")
def update_course(actor: Actor, course_code: str, prerequisites: list[str] | None = None, **fields) -> dict:
    actor.require_scope("academic")
    fields.pop("course_code", None)
    f = _validate_course_fields(fields, creating=False)
    with db.tx() as cur:
        course = _course(cur, course_code, lock=True)
        code = course["course_code"]
        if "capacity" in f:
            taken = _seats_taken(cur, code, _term(cur))
            if f["capacity"] < taken:
                raise RuleViolation(f"Capacity cannot be lower than the {taken} students already registered.")
        if f:
            cur.execute(f"UPDATE courses SET {','.join(k + '=%s' for k in f)} WHERE course_code=%s", [*f.values(), code])
        if prerequisites is not None:
            _set_prereqs(cur, code, prerequisites)
        audit.log(cur, actor, "course.update", "course", code, detail={**f, "prerequisites": prerequisites})
    return get_course(actor, code)


@op("course.delete", "course", "course_code")
def delete_course(actor: Actor, course_code: str) -> dict:
    """Hard-delete only a course with no history; otherwise it is deactivated (kept for academic records)."""
    actor.require_scope("academic")
    with db.tx() as cur:
        course = _course(cur, course_code, lock=True)
        code = course["course_code"]
        cur.execute("""SELECT (SELECT COUNT(*) FROM enrollments WHERE course_code=%s) AS e,
                              (SELECT COUNT(*) FROM course_history WHERE course_code=%s) AS h,
                              (SELECT COUNT(*) FROM course_prerequisites WHERE prereq_code=%s) AS p""", (code, code, code))
        used = cur.fetchone()
        if used["e"] or used["h"] or used["p"]:
            cur.execute("UPDATE courses SET is_active=FALSE WHERE course_code=%s", (code,))
            audit.log(cur, actor, "course.deactivate", "course", code, detail=dict(used))
            return {"ok": True, "course_code": code, "action": "deactivated",
                    "message": f"{code} has enrollment/academic history or dependants, so it was deactivated instead of deleted."}
        cur.execute("DELETE FROM course_prerequisites WHERE course_code=%s", (code,))
        cur.execute("DELETE FROM courses WHERE course_code=%s", (code,))
        audit.log(cur, actor, "course.delete", "course", code)
        return {"ok": True, "course_code": code, "action": "deleted", "message": f"{code} deleted."}


def course_students(actor: Actor, course_code: str) -> list[dict]:
    actor.require_scope("academic")
    with db.tx() as cur:
        course = _course(cur, course_code)
        cur.execute("""SELECT u.user_id, u.full_name, u.email, e.registered_at, e.channel FROM enrollments e
                       JOIN users u USING (user_id) WHERE e.course_code=%s AND e.term=%s AND e.status='registered'
                       ORDER BY u.user_id""", (course["course_code"], _term(cur)))
        return [dict(r) for r in cur.fetchall()]


@op("course.record_grade", "user", "student_id")
def set_course_history(actor: Actor, student_id: str, course_code: str, grade: str | None, term: str | None = None) -> dict:
    """Registrar function: record (or remove, with grade=None) a completed course for a student."""
    actor.require_scope("academic")
    grade = grade.strip().upper() if grade else None
    if grade is not None and grade not in PASSING and grade not in ("F", "W", "I"):
        raise ValidationFailed("Grade must be one of A+, A, A-, B+, B, B-, C+, C, C-, D, F, W, I.")
    with db.tx() as cur:
        _check_student(cur, student_id)
        course = _course(cur, course_code)
        if grade is None:
            cur.execute("DELETE FROM course_history WHERE user_id=%s AND course_code=%s", (student_id, course["course_code"]))
        else:
            cur.execute("""INSERT INTO course_history (user_id, course_code, grade, term) VALUES (%s,%s,%s,%s)
                           ON CONFLICT (user_id, course_code) DO UPDATE SET grade=EXCLUDED.grade, term=EXCLUDED.term""",
                        (student_id, course["course_code"], grade, term))
        audit.log(cur, actor, "course.record_grade", "user", student_id, detail={"course": course["course_code"], "grade": grade})
    return {"ok": True, "student_id": student_id, "course_code": course["course_code"], "grade": grade}


def search_students(actor: Actor, q: str = "") -> list[dict]:
    actor.require_scope("academic")
    return db.query("""SELECT u.user_id, u.full_name, u.email FROM users u WHERE u.role='student' AND
                       (u.user_id ILIKE %s OR u.full_name ILIKE %s) ORDER BY u.user_id LIMIT 50""", (f"%{q}%", f"%{q}%"))
