"""Official semester-wise curricula (Undergraduate Prospectus 2025) -> database, plus degree-structure queries for the planner.

Three layers are kept apart:
  1. the official course CATALOGUE  (`courses`, catalog_only rows are visible but not open for registration),
  2. the semester-wise DEGREE CURRICULUM per programme (`academic_programs` / `program_tracks` / `program_courses`),
  3. actual term OFFERINGS (`courses.slot`, `capacity`, enrolments) - never created or changed by this import.
Rules: idempotent; manually created (seeded) courses are never overwritten, differences are flagged for an administrator; nothing is guessed.
"""
import json
import re
from collections import Counter

from app import db
from app.knowledge import plan_parser
from app.knowledge.catalog_parser import parse_prereq
from . import audit
from .actor import Actor
from .audit import op
from .catalog_import import _flag, _would_cycle
from .errors import NotFound, ValidationFailed

PROSPECTUS_2025_URL = "https://giki.edu.pk/wp-content/uploads/2026/01/Undergraduate-Prospectus-2025.pdf"
PROSPECTUS_2025_PAGE = "https://giki.edu.pk/2026/01/06/undergraduate-prospectus-2025/"
SYSTEM = Actor("SYSTEM", "admin", frozenset({"super"}), "system")
_NOPREREQ = {"-", "–", "none", "nil", "n/a"}


def _norm_title(t: str) -> str:
    return re.sub(r"[^a-z0-9]", "", t.lower().replace("&", "and"))


def _similar(a: str, b: str) -> bool:
    a, b = _norm_title(a), _norm_title(b)
    return a == b or a in b or b in a


def expand_rows(prog: dict) -> list[dict]:
    """Plan rows -> one row per catalogue course. A merged `LEC, LAB` row is split; the lab credit is derived (3 lab hours = 1 credit), and marked as derived."""
    out = []
    default_track = None
    if prog["tracks"]:
        default_track = prog.get("first_track_label") or f"{prog['name']} (standard plan)"
    for r in prog["rows"]:
        track = r["track"] if r["track"] else (default_track if (r["semester"] >= 5 and default_track) else None)
        base = {**r, "track": track}
        if r["kind"] == "course" and r.get("lab_code"):
            lab_cr = max(1, round((r["lab_hours"] or 3) / 3)) if (r["lab_hours"] or 0) > 0 else 1
            lec_cr = r["credits"] - lab_cr
            out.append({**base, "lab_code": None, "credits": lec_cr, "lab_hours": 0})
            out.append({**base, "code": r["lab_code"], "lab_code": None, "title": f"{r['title']} Lab", "credits": lab_cr, "lecture_hours": 0, "credit_derived": True, "lab_of": r["code"],
                        "prereq_raw": "", "coreq_raw": ""})                  # a lab split off a combined row: its own prerequisites are not printed, so none are assumed
        else:
            if r["kind"] == "course" and r["code"] and r["code"].endswith("L") and re.match(r"^[A-Z]{2}\d{3}L$", r["code"]):
                base["lab_of"] = r["code"][:-1]
            out.append(base)
    return out


def _exists(cur, code: str) -> bool:
    cur.execute("SELECT 1 FROM courses WHERE course_code=%s", (code,))
    return cur.fetchone() is not None


def _prereq_info(raw: str) -> dict:
    r = (raw or "").strip()
    if not r:
        return {"status": "unstated", "codes": [], "raw": r}                         # the table prints nothing: unknown, not 'none'
    if r.lower() in _NOPREREQ:
        return {"status": "none", "codes": [], "raw": r}
    if re.fullmatch(r"[A-Z]{2}\s?\d{3}L?\s*-\s*Co", r, re.I):                      # 'EE211-Co': a co-requisite marker, not a prerequisite
        return {"status": "none", "codes": [], "raw": r}
    r = re.sub(r"^(None|-)\s+(None|-)$", "None", r, flags=re.I)
    if re.fullmatch(r"\*+", r) or "vary" in r.lower():
        return {"status": "varies", "codes": [], "raw": r}
    p = parse_prereq(r)
    return {"status": p["status"], "codes": p["codes"], "raw": r}


def _ensure_source(cur) -> dict:
    cur.execute("SELECT * FROM knowledge_sources WHERE url=%s", (PROSPECTUS_2025_URL,))
    s = cur.fetchone()
    if not s or not s["full_text"]:
        raise ValidationFailed("The Undergraduate Prospectus 2025 has not been ingested yet. Run: python -m app.knowledge refresh --no-courses --url " + PROSPECTUS_2025_URL)
    cur.execute("""UPDATE knowledge_sources SET series='ug_prospectus', version_label='2025', published_at='2026-01-06', published_precision='day', landing_url=%s WHERE source_id=%s""",
                (PROSPECTUS_2025_PAGE, s["source_id"]))
    # document versioning: older prospectuses of the same series are superseded (kept for history, excluded from current answers)
    cur.execute("""UPDATE knowledge_sources SET series='ug_prospectus', version_label=COALESCE(version_label, '2021'), status='superseded', superseded_by=%s
                   WHERE url ~* 'UG_Prospectus_20(21|2[0-4])' AND source_id<>%s""", (s["source_id"], s["source_id"]))
    return dict(s)


def _thrust_areas(text: str) -> dict[str, list[str]]:
    """Official 'Thrust Areas' bullet lists, keyed by the programme heading that follows them (FCSE programmes appear in plan order)."""
    out, lines = [], text.split("\n")
    for i, ln in enumerate(lines):
        if re.fullmatch(r"\s*Thrust Areas\s*", ln):
            items = []
            for x in lines[i + 1:i + 14]:
                m = re.match(r"^\s*[••]\s*(.+?)\s*$", x)
                if m:
                    items.append(m.group(1))
                elif x.strip() and items:
                    break
            if items:
                out.append((i, items))
    return {str(i): it for i, it in out}


def _descriptions(text: str) -> dict[str, str]:
    flat = re.sub(r"<<PAGE \d+>>", " ", text)
    flat = re.sub(r"Ghulam Ishaq Khan Institute of Engineering Sciences and Technology", " ", flat)
    flat = re.sub(r"\s+", " ", flat)
    pat = re.compile(r"([A-Z]{2}\d{3}L?)\s+([A-Z][^():]{2,110}?)\s*\((?:\d-\d-\d|L)\)\s*:\s*")
    ms = list(pat.finditer(flat))
    out: dict[str, str] = {}
    for k, m in enumerate(ms):
        body = flat[m.end(): ms[k + 1].start() if k + 1 < len(ms) else m.end() + 1500].strip()
        body = re.sub(r"\b(?:Faculty of|School of|Department of) [A-Za-z ,&]+\s+\d{1,3}\s*$", "", body).strip()[:1800]
        out.setdefault(m.group(1), body)
    return out


def _minor_pool(text: str) -> list[dict]:
    i = text.find("Introduction: Minors in Management for Engineers")
    if i < 0:
        return []
    j = text.find("Description of Courses", i)
    seg = text[i:j]
    rows = []
    for ln in seg.split("\n"):
        m = re.match(r"^(\S.*?)\s+([A-Z]{2}\d{3})\s+(\d)\s*$", ln.strip())
        if m:
            rows.append({"title": re.sub(r"\s+", " ", m.group(1)), "code": m.group(2), "credits": int(m.group(3))})
    return rows


@op("curriculum.import", "knowledge", "prospectus_2025")
def import_prospectus_2025(actor: Actor | None = None) -> dict:
    actor = actor or SYSTEM
    actor.require_scope("academic")
    stats = {"programs": 0, "program_rows": 0, "courses_created": 0, "courses_updated": 0, "courses_matched": 0, "seed_conflicts": 0, "prereq_links": 0, "flags": 0,
             "descriptions_set": 0, "tracks": 0}
    with db.tx() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(hashtext('curriculum_import'))")
        src = _ensure_source(cur)
        sid, url, text = src["source_id"], src["url"], src["full_text"]
        # open review items produced by an earlier run of THIS import are regenerated from the current data (dismissed/applied ones are kept)
        cur.execute("""DELETE FROM course_import_flags WHERE status='open' AND source_url=%s AND kind IN ('program_variant','prereq_ambiguous','prereq_variant','prereq_unresolved','prereq_mismatch','prereq_cycle','seed_conflict','unparsed_row','credit_total_mismatch')""", (url,))
        parsed = plan_parser.parse_plans(text)
        desc = _descriptions(text)
        # one-off, idempotent: catalogue rows that came from an official document (not hand-made) may be refreshed by a newer official document
        cur.execute("UPDATE courses SET origin='official' WHERE catalog_only AND origin='manual' AND EXISTS (SELECT 1 FROM course_sources s WHERE s.course_code=courses.course_code)")
        # 1. gather every course occurrence across programmes
        occ: dict[str, list[dict]] = {}
        per_prog = {}
        for prog in parsed["programs"]:
            rows = expand_rows(prog)
            per_prog[prog["code"]] = rows
            for r in rows:
                if r["code"]:
                    occ.setdefault(r["code"], []).append({**r, "program": prog["code"], "faculty": prog["faculty"]})
        minor_rows = _minor_pool(text)
        for m in minor_rows:
            occ.setdefault(m["code"], []).append({"code": m["code"], "title": m["title"], "credits": m["credits"], "lecture_hours": None, "lab_hours": None, "prereq_raw": "", "program": "MINOR",
                                                  "faculty": "School of Management Sciences", "page": None, "semester": 0})
        # 2. catalogue
        chosen: dict[str, dict] = {}
        for code, items in occ.items():
            variants = Counter((i["title"], i["credits"], i["lecture_hours"], i["lab_hours"]) for i in items)
            (title, credits, lec, lab), _ = variants.most_common(1)[0]
            first = next(i for i in items if (i["title"], i["credits"]) == (title, credits))
            chosen[code] = {"code": code, "title": title, "credits": credits, "lec": lec, "lab": lab, "faculty": first["faculty"], "page": first["page"]}
            distinct_credits = {i["credits"] for i in items}
            titles = [i["title"] for i in items]
            if len(distinct_credits) > 1 or not all(_similar(titles[0], t) for t in titles[1:]):
                stats["flags"] += _flag(cur, code, "program_variant", {"variants": [{"programs": sorted({i["program"] for i in items if (i["title"], i["credits"]) == (t, c)}), "title": t, "credits": c} for (t, c, _l, _b) in variants]}, url)
        for code, c in chosen.items():
            cur.execute("SELECT course_code, title, credits, catalog_only, origin FROM courses WHERE course_code=%s", (code,))
            ex = cur.fetchone()
            d = desc.get(code)
            if ex is None:
                cur.execute("""INSERT INTO courses (course_code, title, credits, category, description, capacity, is_active, catalog_only, department, lecture_hours, lab_hours, origin)
                               VALUES (%s,%s,%s,'core',%s,0,TRUE,TRUE,%s,%s,%s,'official')""", (code, c["title"][:100], c["credits"], d, c["faculty"], c["lec"], c["lab"]))
                outcome = "imported"; stats["courses_created"] += 1; stats["descriptions_set"] += bool(d)
            elif ex["origin"] == "official" and ex["catalog_only"]:
                cur.execute("SELECT * FROM course_sources WHERE course_code=%s", (code,))
                old = cur.fetchone()
                if old and old["source_id"] != sid:
                    cur.execute("""INSERT INTO course_source_history (course_code, source_id, source_url, source_title, source_page, official_title, official_credits, official_prereq_text, retrieved_at, superseded_by_url)
                                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", (code, old["source_id"], old["source_url"], old["source_title"], old["source_page"], old["official_title"], old["official_credits"],
                                                                              old["official_prereq_text"], old["retrieved_at"], url))
                changed = ex["title"] != c["title"][:100] or ex["credits"] != c["credits"]
                cur.execute("UPDATE courses SET title=%s, credits=%s, department=COALESCE(%s, department), lecture_hours=COALESCE(%s, lecture_hours), lab_hours=COALESCE(%s, lab_hours), description=COALESCE(%s, description) WHERE course_code=%s",
                            (c["title"][:100], c["credits"], c["faculty"], c["lec"], c["lab"], d, code))
                outcome = "imported" if changed or (old and old["source_id"] != sid) else "matched"
                stats["courses_updated" if changed else "courses_matched"] += 1; stats["descriptions_set"] += bool(d)
            else:                                      # a hand-made / scheduled course: never overwritten, differences go to review
                same = ex["title"].strip().lower() == c["title"].strip().lower()[:100] and ex["credits"] == c["credits"]
                outcome = "matched" if same else "conflict"
                stats["courses_matched" if same else "seed_conflicts"] += 1
                if not same:
                    stats["flags"] += _flag(cur, code, "seed_conflict", {"existing": {"title": ex["title"], "credits": ex["credits"]}, "official_2025": {"title": c["title"], "credits": c["credits"]}}, url)
            cur.execute("""INSERT INTO course_sources (course_code, source_id, source_url, source_title, source_section, source_page, official_title, official_credits, official_prereq_text, prereq_status, outcome)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,NULL,'missing',%s) ON CONFLICT (course_code) DO UPDATE SET source_id=EXCLUDED.source_id, source_url=EXCLUDED.source_url, source_title=EXCLUDED.source_title,
                           source_section=EXCLUDED.source_section, source_page=EXCLUDED.source_page, official_title=EXCLUDED.official_title, official_credits=EXCLUDED.official_credits, outcome=EXCLUDED.outcome, last_checked_at=now(),
                           retrieved_at=CASE WHEN course_sources.source_id IS DISTINCT FROM EXCLUDED.source_id THEN now() ELSE course_sources.retrieved_at END""",
                        (code, sid, url, src["title"], f"{c['faculty']} · degree plan", c["page"], c["title"], c["credits"], outcome))
        # 3. prerequisites at catalogue level: only when every programme that lists the course agrees on a plain code list
        cur.execute("SELECT course_code FROM courses WHERE origin='official' AND catalog_only")
        refreshable = {r["course_code"] for r in cur.fetchall()}
        for code, items in occ.items():
            infos = [_prereq_info(i["prereq_raw"]) for i in items if i["program"] != "MINOR"]
            infos = [i for i in infos if i["status"] != "unstated"]
            if not infos:
                continue
            stat = Counter(i["status"] for i in infos)
            sigs = {tuple(i["codes"]) for i in infos if i["status"] in ("parsed", "none")}
            raw = " | ".join(sorted({i["raw"] for i in infos if i["raw"]}))[:300]
            if stat.get("ambiguous"):
                stats["flags"] += _flag(cur, code, "prereq_ambiguous", {"official_text": raw, "programs": sorted({i['program'] for i in items})}, url); final = None
            elif stat.get("varies") and not (stat.get("parsed")):
                final = None
            elif len(sigs) > 1:
                stats["flags"] += _flag(cur, code, "prereq_variant", {"official_text": raw, "per_program": {i["program"]: i["prereq_raw"] for i in items if i["program"] != "MINOR"}}, url); final = None
            else:
                final = list(next(iter(sigs))) if sigs else None
            cur.execute("UPDATE course_sources SET official_prereq_text=%s, prereq_status=%s WHERE course_code=%s AND source_id=%s",
                        (raw, "ambiguous" if stat.get("ambiguous") else "parsed" if (final and stat.get("parsed")) else "none" if final == [] else "missing", code, sid))
            if final is None or code not in refreshable:
                if final is not None and code not in refreshable:
                    cur.execute("SELECT prereq_code FROM course_prerequisites WHERE course_code=%s", (code,))
                    cur_p = sorted(r["prereq_code"] for r in cur.fetchall())
                    if cur_p != sorted(final):
                        stats["flags"] += _flag(cur, code, "prereq_mismatch", {"existing": cur_p, "official": sorted(final), "official_text": raw}, url)
                continue
            missing = [p for p in final if p not in chosen and not _exists(cur, p)]
            if missing:
                stats["flags"] += _flag(cur, code, "prereq_unresolved", {"official_text": raw, "missing_from_catalogue": missing}, url); continue
            cur.execute("SELECT prereq_code FROM course_prerequisites WHERE course_code=%s", (code,))
            have = sorted(r["prereq_code"] for r in cur.fetchall())
            if have == sorted(final):
                continue
            cur.execute("DELETE FROM course_prerequisites WHERE course_code=%s", (code,))
            if _would_cycle(cur, code, final):
                stats["flags"] += _flag(cur, code, "prereq_cycle", {"official_text": raw}, url)
                if have:
                    cur.executemany("INSERT INTO course_prerequisites (course_code, prereq_code) VALUES (%s,%s) ON CONFLICT DO NOTHING", [(code, p) for p in have])
                continue
            for p in final:
                cur.execute("INSERT INTO course_prerequisites (course_code, prereq_code) VALUES (%s,%s) ON CONFLICT DO NOTHING", (code, p)); stats["prereq_links"] += 1
        # 4. programmes, tracks, semester-wise rows
        for prog in parsed["programs"]:
            mism = plan_parser.check_totals(prog)
            rej = [x for x in parsed["rejected"] if x["program"] == prog["code"]]
            notes = [{"kind": "credit_total_mismatch", **m} for m in mism] + [{"kind": "unparsed_row", **x} for x in rej]
            cur.execute("""INSERT INTO academic_programs (code, name, faculty, source_id, source_url, source_page, plan_status, retrieved_at, review_notes) VALUES (%s,%s,%s,%s,%s,%s,%s,now(),%s)
                           ON CONFLICT (code) DO UPDATE SET name=EXCLUDED.name, faculty=EXCLUDED.faculty, source_id=EXCLUDED.source_id, source_url=EXCLUDED.source_url, source_page=EXCLUDED.source_page,
                           plan_status=EXCLUDED.plan_status, retrieved_at=now(), review_notes=EXCLUDED.review_notes RETURNING program_id""",
                        (prog["code"], prog["name"], prog["faculty"], sid, url, prog["page"], "needs_review" if notes else "parsed", json.dumps(notes, default=str)))
            pid = cur.fetchone()["program_id"]
            for n in notes:
                stats["flags"] += _flag(cur, prog["code"], n["kind"], n, url)
            rows = per_prog[prog["code"]]
            track_ids = {}
            for t in sorted({r["track"] for r in rows if r["track"]}):
                cur.execute("INSERT INTO program_tracks (program_id, name, kind, source_page) VALUES (%s,%s,'track',%s) ON CONFLICT (program_id, name, kind) DO UPDATE SET source_page=EXCLUDED.source_page RETURNING track_id", (pid, t[:160], prog["page"]))
                track_ids[t] = cur.fetchone()["track_id"]; stats["tracks"] += 1
            keep = set()
            for r in rows:
                tid = track_ids.get(r["track"])
                pr = _prereq_info(r["prereq_raw"])
                if r["code"]:
                    cur.execute("SELECT id FROM program_courses WHERE program_id=%s AND COALESCE(track_id,0)=%s AND semester=%s AND course_code=%s", (pid, tid or 0, r["semester"], r["code"]))
                else:
                    cur.execute("SELECT id FROM program_courses WHERE program_id=%s AND COALESCE(track_id,0)=%s AND semester=%s AND course_code IS NULL AND title=%s", (pid, tid or 0, r["semester"], r["title"][:160]))
                ex = cur.fetchone()
                vals = (r["title"][:160], r["credits"], r["lecture_hours"], r["lab_hours"], r["prereq_raw"] or None, r.get("coreq_raw") or None, r["kind"], r["page"], r.get("lab_of"),
                        bool(r.get("credit_derived")), r.get("slot_code"), pr["status"])
                if ex:
                    cur.execute("""UPDATE program_courses SET title=%s, credit_hours=%s, lecture_hours=%s, lab_hours=%s, prereq_text=%s, coreq_text=%s, kind=%s, source_page=%s, lab_of=%s, credit_derived=%s, slot_code=%s, prereq_status=%s WHERE id=%s""",
                                (*vals, ex["id"])); keep.add(ex["id"])
                else:
                    cur.execute("""INSERT INTO program_courses (title, credit_hours, lecture_hours, lab_hours, prereq_text, coreq_text, kind, source_page, lab_of, credit_derived, slot_code, prereq_status, program_id, track_id, semester, course_code)
                                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""", (*vals, pid, tid, r["semester"], r["code"])); keep.add(cur.fetchone()["id"])
                stats["program_rows"] += 1
            cur.execute("DELETE FROM program_courses WHERE program_id=%s AND NOT (id = ANY(%s))", (pid, list(keep) or [0]))
            stats["programs"] += 1
        # 5. official minor (the only one the 2025 prospectus states)
        if minor_rows:
            codes = [m["code"] for m in minor_rows]
            cur.execute("""INSERT INTO degree_minors (minor_name, required_courses, required_credits, description, status, source_url, source_id, source_page, offered_by, course_pool, requirements_stated)
                           VALUES ('Management for Engineers', '', 0, %s, 'official', %s, %s, 271, 'School of Management Sciences', %s, FALSE)
                           ON CONFLICT (minor_name) DO UPDATE SET description=EXCLUDED.description, status='official', source_url=EXCLUDED.source_url, source_id=EXCLUDED.source_id, source_page=EXCLUDED.source_page,
                           offered_by=EXCLUDED.offered_by, course_pool=EXCLUDED.course_pool, requirements_stated=FALSE""",
                        ("Minor in Management for Engineers offered by the School of Management Sciences. The prospectus lists the courses below but does not state how many credit hours or which courses are required, so no completion target is assumed.", url, sid, ",".join(dict.fromkeys(codes))))
        cur.execute("UPDATE degree_minors SET status='unverified' WHERE status<>'official' OR source_url IS NULL")
        audit.log(cur, actor, "curriculum.import", "knowledge", "prospectus_2025", detail=stats)
    return {**stats, "rejected_rows": len(parsed["rejected"]), "source_id": sid}


# ═══ Degree-structure queries (planner / what-if / assistant) ═══

def _program(cur, code: str) -> dict:
    cur.execute("SELECT * FROM academic_programs WHERE code=%s OR lower(name)=lower(%s)", ((code or "").strip(), (code or "").strip()))
    p = cur.fetchone()
    if not p:
        raise NotFound(f"Programme '{code}' was not found. Use list_programs to see the available degree programmes.")
    return dict(p)


def list_programs() -> list[dict]:
    rows = db.query("""SELECT p.code, p.name, p.faculty, p.plan_status, p.source_url, p.source_page, p.retrieved_at,
                              (SELECT COALESCE(json_agg(t.name ORDER BY t.name) FILTER (WHERE t.kind='track'), '[]') FROM program_tracks t WHERE t.program_id=p.program_id) AS tracks,
                              (SELECT COUNT(*) FROM program_courses c WHERE c.program_id=p.program_id AND c.track_id IS NULL) AS common_rows
                       FROM academic_programs p WHERE p.status='active' ORDER BY p.faculty, p.name""")
    return rows


def _rows(cur, program_id: int, track: str | None) -> tuple[list[dict], list[str], str | None]:
    cur.execute("SELECT track_id, name FROM program_tracks WHERE program_id=%s AND kind='track' ORDER BY track_id", (program_id,))
    tracks = [dict(r) for r in cur.fetchall()]
    chosen = None
    if tracks:
        names = {t["name"].lower(): t for t in tracks}
        chosen = names.get((track or "").lower()) or tracks[0]
        if track and (track or "").lower() not in names:
            raise NotFound(f"Track '{track}' does not exist for this programme. Available: {', '.join(t['name'] for t in tracks)}.")
    cur.execute("""SELECT pc.*, c.catalog_only, c.slot, c.title AS catalogue_title FROM program_courses pc LEFT JOIN courses c ON c.course_code=pc.course_code
                   WHERE pc.program_id=%s AND (pc.track_id IS NULL OR pc.track_id=%s) ORDER BY pc.semester, pc.kind DESC, pc.course_code NULLS LAST, pc.title""", (program_id, chosen["track_id"] if chosen else 0))
    return [dict(r) for r in cur.fetchall()], [t["name"] for t in tracks], (chosen["name"] if chosen else None)


def get_program(code: str, track: str | None = None) -> dict:
    """Semester-wise curriculum of a programme exactly as printed in the prospectus (per track where the programme has tracks)."""
    with db.tx() as cur:
        p = _program(cur, code)
        rows, tracks, chosen = _rows(cur, p["program_id"], track)
        cur.execute("SELECT name, kind FROM program_tracks WHERE program_id=%s ORDER BY kind, name", (p["program_id"],))
        sems: dict[int, list] = {}
        for r in rows:
            sems.setdefault(r["semester"], []).append({"course_code": r["course_code"], "title": r["title"], "credit_hours": r["credit_hours"], "lecture_hours": r["lecture_hours"], "lab_hours": r["lab_hours"],
                                                       "prerequisites": r["prereq_text"], "prereq_status": r["prereq_status"], "kind": r["kind"], "lab_of": r["lab_of"], "credit_derived": r["credit_derived"],
                                                       "offered_this_term": bool(r["course_code"] and r["catalog_only"] is False and r["slot"])})
        return {"code": p["code"], "name": p["name"], "faculty": p["faculty"], "plan_status": p["plan_status"], "review_notes": p["review_notes"], "source_url": p["source_url"], "source_page": p["source_page"],
                "tracks": tracks, "selected_track": chosen, "track_note": "Semesters 5-8 differ per track; semesters 1-4 are common." if tracks else None,
                "semesters": [{"semester": k, "credits": sum(x["credit_hours"] for x in v), "courses": v} for k, v in sorted(sems.items())],
                "total_credits": sum(r["credit_hours"] for r in rows)}


def degree_progress(actor: Actor, student_id: str | None = None, program_code: str | None = None, track: str | None = None) -> dict:
    """Where a student stands in a programme's official curriculum: completed, in progress, eligible, blocked by prerequisites, remaining.
    With `program_code` of another programme this is the what-if for changing programme: passed courses count wherever the same code appears."""
    from . import courses as C
    sid = actor.resolve_student(student_id, "academic")
    with db.tx() as cur:
        cur.execute("SELECT program, program_id, current_semester, is_demo FROM student_profiles WHERE user_id=%s", (sid,))
        prof = cur.fetchone() or {}
        if program_code:
            p = _program(cur, program_code)
        elif prof.get("program_id"):
            cur.execute("SELECT * FROM academic_programs WHERE program_id=%s", (prof["program_id"],)); p = dict(cur.fetchone())
        else:
            cur.execute("SELECT * FROM academic_programs WHERE lower(name)=lower(%s)", (prof.get("program") or "",))
            p = cur.fetchone()
            if not p:                                   # not an error: the curriculum for this student's programme just has not been imported / set up
                return {"student_id": sid, "program": None, "message": "Your degree programme has no imported curriculum yet, so degree progress cannot be shown."}
            p = dict(p)
        rows, tracks, chosen = _rows(cur, p["program_id"], track)
        passed = C._passed(cur, sid)
        term = C._term(cur)
        regs = {c["course_code"]: c for c in C._registered(cur, sid, term)}
        cur.execute("SELECT program_course_id FROM student_course_progress WHERE user_id=%s AND status='in_progress'", (sid,))
        marked = {r["program_course_id"] for r in cur.fetchall()}
        out_sems: dict[int, list] = {}
        totals = {"completed": 0, "in_progress": 0, "remaining": 0}
        conflicts = []
        for r in rows:
            code, info = r["course_code"], _prereq_info(r["prereq_text"] or "")
            if code and code in passed:
                st = "completed"
            elif (code and code in regs) or r["id"] in marked:
                st = "in_progress"
            elif r["kind"] == "slot":
                st = "open_slot"
            else:
                miss = [c for c in info["codes"] if c not in passed] if info["status"] == "parsed" else []
                st = "blocked" if miss else "eligible"
            row = {"program_course_id": r["id"], "course_code": code, "title": r["title"], "credit_hours": r["credit_hours"], "status": st, "kind": r["kind"], "prerequisites": r["prereq_text"],
                   "prereq_status": r["prereq_status"], "missing_prerequisites": [c for c in info["codes"] if c not in passed] if (st == "blocked") else [],
                   "offered_this_term": bool(code and r["catalog_only"] is False and r["slot"])}
            if row["offered_this_term"] and st in ("eligible", "blocked") and regs:
                for reg in regs.values():
                    cf = C.find_conflicts({"course_code": code, "slot": r["slot"], "lab_slot": None}, reg)
                    if cf:
                        conflicts.append({"course": code, "conflicts_with": reg["course_code"], "detail": cf}); row["timetable_conflict"] = True
            out_sems.setdefault(r["semester"], []).append(row)
            totals["completed" if st == "completed" else "in_progress" if st == "in_progress" else "remaining"] += r["credit_hours"]
        total = sum(totals.values())
        cur.execute("SELECT status, minor_name FROM degree_minors")
        return {"student_id": sid, "program": {"code": p["code"], "name": p["name"], "faculty": p["faculty"], "plan_status": p["plan_status"], "source_url": p["source_url"], "source_page": p["source_page"]},
                "is_students_own_programme": (prof.get("program_id") == p["program_id"]) or (prof.get("program") == p["name"]), "tracks": tracks, "selected_track": chosen,
                "current_semester": prof.get("current_semester"), "term": term, "credits": {**totals, "total": total},
                "semesters": [{"semester": k, "courses": v, "credits_completed": sum(x["credit_hours"] for x in v if x["status"] == "completed")} for k, v in sorted(out_sems.items())],
                "remaining_courses": [x["course_code"] or x["title"] for v in out_sems.values() for x in v if x["status"] in ("eligible", "blocked", "open_slot")],
                "timetable_conflicts": conflicts, "demo_assumption": bool(prof.get("is_demo")),
                "notes": ["Elective slots have no fixed course code; they are completed by choosing an eligible course.",
                          "Timetable conflicts are only checked for courses that have a scheduled offering this term." if conflicts == [] else "Some remaining courses clash with your current timetable."]}


@op("curriculum.init_demo_progress", "user", "student_ids")
def init_demo_progress(actor: Actor, student_ids: list[str], program_code: str | None = None, track: str | None = None) -> dict:
    """Repeatable, idempotent initialisation for EXPLICITLY designated demo/seed students: every earlier semester of their programme is marked passed
    (grade 'P', term 'Demo assumption': no letter grade is invented) and the current semester is marked in progress. Existing transcript rows are never changed,
    nothing is registered or timetabled, and accounts that are not listed are untouched."""
    actor.require_scope("academic")
    if not student_ids:
        raise ValidationFailed("Please name the demo student accounts to initialise.")
    report = []
    with db.tx() as cur:
        for sid in student_ids:
            cur.execute("SELECT u.role, sp.program, sp.current_semester FROM users u LEFT JOIN student_profiles sp USING (user_id) WHERE u.user_id=%s", (sid,))
            u = cur.fetchone()
            if not u or u["role"] != "student":
                raise ValidationFailed(f"{sid} is not a student account.")
            p = _program(cur, program_code or u["program"] or "")
            rows, _, _ = _rows(cur, p["program_id"], track)
            sem = u["current_semester"] or 1
            cur.execute("UPDATE student_profiles SET program_id=%s, is_demo=TRUE WHERE user_id=%s", (p["program_id"], sid))
            done = prog = 0
            for r in rows:
                if r["semester"] < sem and r["course_code"] and r["kind"] == "course":
                    cur.execute("INSERT INTO course_history (user_id, course_code, grade, term) VALUES (%s,%s,'P','Demo assumption') ON CONFLICT (user_id, course_code) DO NOTHING", (sid, r["course_code"]))
                    done += cur.rowcount
                elif r["semester"] == sem:
                    cur.execute("INSERT INTO student_course_progress (user_id, program_course_id) VALUES (%s,%s) ON CONFLICT DO NOTHING", (sid, r["id"]))
                    prog += cur.rowcount
            report.append({"student_id": sid, "program": p["code"], "current_semester": sem, "newly_marked_passed": done, "newly_marked_in_progress": prog})
        audit.log(cur, actor, "curriculum.init_demo_progress", "user", ",".join(student_ids), detail={"report": report})
    return {"ok": True, "students": report}


def official_minors() -> list[dict]:
    return db.query("SELECT minor_name, description, status, offered_by, source_url, source_page, requirements_stated, required_credits, course_pool FROM degree_minors ORDER BY (status='official') DESC, minor_name")
