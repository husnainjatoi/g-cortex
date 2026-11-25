"""Parse semester-wise degree plans from the GIKI Undergraduate Prospectus text (pure functions).

Plans are tables:  `Course Code | Course Title | Credit/Lec/Lab hours | Pre-req [| Co-req]` under `Nth Semester` headings. Column ORDER differs between
faculties (read from each table's header). Anything that does not parse unambiguously is returned in `rejected` - never guessed.
"""
import re

CODE = r"[A-Z]{2}(?:/[A-Z][A-Za-z]?\w?)*\s?(?:\d{3}|\d{0,2}[xX]{1,3})L?"
_SEM = re.compile(r"(?:(?:^|\b)([1-8])(?:st|nd|rd|th)\s*Semester\b|Semester\s*([1-8])(?:st|nd|rd|th)\b)", re.I)
_PAGE = re.compile(r"^<<PAGE (\d+)>>$")
_ROW = re.compile(rf"^((?:{CODE})(?:\s*,\s*(?:{CODE}))?)\s+(\S.*?)\s+(\d{{1,2}})\s+(\d{{1,2}})\s+(\d{{1,2}})(?:\s+(?!\d)(\S.*))?$")
_SLOT = re.compile(r"^(\*{0,3}(?:[A-Z][A-Za-z&/ ]{3,60}?\*{0,2}|[A-Z][A-Za-z ]+ (?:Elective|Electives)[^0-9]*))\s+(\d{1,2})\s+(\d{1,2})\s+(\d{1,2})(?:\s+(?!\d)(\S.*))?$")
_SLOT_SHORT = re.compile(r"^([A-Z][A-Za-z&/ ,.\-()]{3,80}?\*{0,3})\s+(\d)\s+(\d)\s*$")
_MS_CODE = r"(?:[A-Z]{2}(?:/[A-Z]{2})+\s?\d[Xx]{2}|[A-Z]{2}\s?\d{3}(?:-?L)?|[A-Z]{2}\d[Xx]{2})"
_MS_ROW = re.compile(rf"^(\S.*?)\s+({_MS_CODE})\s+(\d{{1,2}}(?:\+\d)?)(?:\s+(\S.*))?$")      # School of Management Sciences: `Title  CODE  Credits  Pre-req`
_TOTAL = re.compile(r"^\s*Total\b", re.I)

# Where each degree plan starts. FCSE plans are identified by their position (six `Degree Plan` headings in the programme order below).
PLAN_MARKER = re.compile(r"^(Degree Plan|Semester-wise Degree Plan:?|.*SEMESTER WISE BREAKDOWN.*|Electrical Engineering- Semester wise Breakdown)\s*(?:Semester 1st)?$|SEMESTER WISE BREAKDOWNSemester 1st|Semester-wise Degree PlanSemester 1st", re.I)
FCSE_ORDER = [("BSAI", "BS Artificial Intelligence"), ("BSCE", "BS Computer Engineering"), ("BSCS", "BS Computer Science"), ("BSCyS", "BS Cyber Security"), ("BSDS", "BS Data Science"), ("BSSE", "BS Software Engineering")]
FCSE_FACULTY = "Faculty of Computer Science and Engineering"
OTHER_PROGRAMS = [  # anchor regex -> programme (matched against each plan-start line in order of appearance, after the FCSE ones)
    (r"Electrical Engineering- Semester wise Breakdown", "BSEE", "BS Electrical Engineering", "Faculty of Electrical Engineering"),
    (r"^Degree Plan$", "BSMSE", "BS Materials Engineering", "Faculty of Materials and Chemical Engineering"),
    (r"Semester-wise Degree Plan:", "BSChE", "BS Chemical Engineering", "Faculty of Materials and Chemical Engineering"),
    (r"^Degree Plan$", "BSME", "BS Mechanical Engineering", "Faculty of Mechanical Engineering"),
    (r"SEMESTER WISE BREAKDOWN", "BSCV", "BS Civil Engineering", "Department of Civil Engineering"),
    (r"Semester-wise Degree Plan", "BSMS", "BS Management Sciences", "School of Management Sciences"),
]
STOP_LINE = re.compile(r"fundamental courses offered to all students|Courses Faculty of Basic Sciences offers|^Service Courses", re.I)


def _norm(c: str) -> str:
    return re.sub(r"\s+", "", c.upper())


def _prereq_tokens(raw: str | None) -> str:
    raw = re.sub(r"\s+", " ", (raw or "")).strip(" .;")
    return "" if raw in ("-", "–", "—", "None", "none", "NIL", "Nil") else raw


def _header_info(hdr: str) -> dict:
    h = re.sub(r"\s+", " ", hdr)
    credit_first = bool(re.search(r"Credit\s*Hr[s]?\.?\s+(Lec|Class)", h, re.I))
    return {"credit_first": credit_first, "has_coreq": bool(re.search(r"Co-?\s*Req", h, re.I))}


def _plan_starts(lines: list[str]) -> list[int]:
    out = []
    for i, ln in enumerate(lines):
        s = ln.strip()
        if PLAN_MARKER.search(s) and not s.lower().startswith(("the scheme", "*")) and "follow" not in s.lower():
            # a plan marker must be followed shortly by a semester heading
            if any(_SEM.search(x) for x in lines[i:i + 12]):
                out.append(i)
    return out


def _first_track_name(lines: list[str], start: int, later_tracks: list[str]) -> str | None:
    """The first variant of a plan has no heading of its own; take its name from an explicit list in the text just above the plan
    ('1. Robotics ...' lists, or 'common for all three X, Y and Z streams'). Returns None when the document does not state it."""
    window = lines[max(0, start - 260):start + 14]
    numbered = [w.strip() for w in window if re.match(r"^\d\.\s+[A-Z][A-Za-z&, ]{5,60}$", w.strip())]
    ones = [i for i, w in enumerate(numbered) if w.startswith("1.")]
    if ones:                                                  # the LAST numbered run (1., 2., ...) is the thrust-area list; earlier runs are unrelated lists (e.g. labs)
        run = [re.sub(r"^\d\.\s*", "", w) for w in numbered[ones[-1]:]]
        if len(run) == len(later_tracks) + 1:
            return run[0]
    blob = " ".join(w.strip() for w in window)
    m = re.search(r"common for all (?:three|four|two)\s+([A-Za-z, ]+?)\s+streams", blob)
    if m:
        names = [n.strip() for n in re.split(r",| and ", m.group(1)) if n.strip()]
        if len(names) == len(later_tracks) + 1:
            return names[0] + " Stream"
    return None


def _join_wrapped_rows(lines: list[str]) -> list[str]:
    """A long course title can wrap, leaving its hour columns on a following line ('MM244L Computational Tools (Materials Lab' / 'IV)' / '0 3 1')."""
    out: list[str] = []
    i = 0
    while i < len(lines):
        ln = lines[i]
        if re.match(rf"^\s*{CODE}\s+\S", ln) and not re.search(r"\d\s*$", ln.strip()) and i + 1 < len(lines):
            nxt = lines[i + 1].strip()
            if re.match(r"^[A-Za-z0-9 ]{0,20}\)\s*$", nxt) and i + 2 < len(lines) and re.match(r"^\d{1,2}\s+\d{1,2}\s+\d{1,2}(?:\s+\S.*)?$", lines[i + 2].strip()):
                out.append(ln.rstrip() + " " + nxt + " " + lines[i + 2].strip()); i += 3; continue
        out.append(ln); i += 1
    return out


def parse_plans(text: str) -> dict:
    raw_lines = text.split("\n")
    # join "CS101," + "CS101L Title ..." (a pair code broken across lines)
    lines: list[str] = []
    for ln in raw_lines:
        if lines and re.search(rf"{CODE}\s*,\s*$", lines[-1]) and re.match(rf"\s*{CODE}\b", ln):
            lines[-1] = lines[-1].rstrip() + " " + ln.strip()
        else:
            lines.append(ln)
    lines = _join_wrapped_rows(lines)
    starts = _plan_starts(lines)
    page_at: list[int] = []
    p = 0
    for ln in lines:
        m = _PAGE.match(ln.strip())
        if m:
            p = int(m.group(1))
        page_at.append(p)
    programs, rejected = [], []
    # de-duplicate markers that sit on top of each other (heading + glued heading)
    dedup = []
    for st in starts:
        if not dedup or st - dedup[-1] > 30:
            dedup.append(st)
    starts = dedup

    def faculty_before(i: int) -> str | None:
        for j in range(i, max(-1, i - 80), -1):
            t = lines[j].strip()
            for f in ("Faculty of Materials and Chemical Engineering", "Faculty of Mechanical Engineering", "School of Management Sciences", "Faculty of Basic Sciences",
                      "Faculty of Electrical Engineering", "Faculty of Computer Science and Engineering", "Department of Civil Engineering", "Faculty of Engineering Sciences"):
                if t.startswith(f):
                    return f
        return None

    seq, fcse_i = [], 0
    for st in starts:
        marker = lines[st].strip()
        fac = faculty_before(st)
        if "Electrical Engineering- Semester wise Breakdown" in marker:
            seq.append((st, "BSEE", "BS Electrical Engineering", "Faculty of Electrical Engineering"))
        elif "CIVIL ENGINEERING" in marker.upper():
            seq.append((st, "BSCV", "BS Civil Engineering", "Department of Civil Engineering"))
        elif fac == "Faculty of Computer Science and Engineering" and fcse_i < len(FCSE_ORDER) and marker.lower().startswith("degree plan"):
            code, name = FCSE_ORDER[fcse_i]; fcse_i += 1; seq.append((st, code, name, FCSE_FACULTY))
        elif fac == "Faculty of Materials and Chemical Engineering":
            if "semester-wise" in marker.lower():
                seq.append((st, "BSChE", "BS Chemical Engineering", fac))
            else:
                seq.append((st, "BSMSE", "BS Materials Engineering", fac))
        elif fac == "Faculty of Mechanical Engineering":
            seq.append((st, "BSME", "BS Mechanical Engineering", fac))
        elif fac == "School of Management Sciences":
            seq.append((st, "BSMS", "BS Management Sciences", fac))
        # plans that belong to a service faculty (e.g. Basic Sciences) are not degree programmes and are skipped on purpose
    for k, (st, code, name, fac) in enumerate(seq):
        end = seq[k + 1][0] if k + 1 < len(seq) else len(lines)
        # the plan ends at the next stop marker (e.g. service-course sections of another faculty) if one appears first
        for j in range(st + 1, end):
            if STOP_LINE.search(lines[j]):
                end = j; break
        prog = {"code": code, "name": name, "faculty": fac, "start_line": st, "page": page_at[st], "rows": [], "tracks": [], "semesters_found": set(), "declared": {}}
        sem, track, last_sem, hdr, hdr_info = None, None, 0, None, None
        i = st
        while i < end:
            s = lines[i].strip()
            m = _SEM.search(s)
            if m and len(s) < 70:
                new_sem = int(m.group(1) or m.group(2))
                if new_sem == 1 and last_sem >= 5:      # the degree plan is complete; whatever follows belongs to another section of the prospectus
                    break
                if new_sem >= 5 and new_sem <= last_sem:    # semester numbering restarted at 5+: a track / stream / section variant
                    label = ""
                    for back in range(i - 1, max(st, i - 6), -1):
                        t = lines[back].strip()
                        if t and not _TOTAL.match(t) and not t.startswith(("Faculty of", "Ghulam", "<<PAGE")) and not re.search(r"\d", t.replace("Section B", "")) and len(t) < 110 and re.search(r"[A-Za-z]{4,}", t):
                            label = re.sub(r"^[a-z]\.\s*", "", t).strip(" :"); break
                    track = label or f"Variant {len(prog['tracks']) + 2}"
                    if track not in prog["tracks"]:
                        prog["tracks"].append(track)
                sem, last_sem = new_sem, new_sem
                prog["semesters_found"].add(sem)
                hdr = " ".join(x.strip() for x in lines[i + 1:i + 4])
                if re.search(r"\bCode\b|\bTitle", hdr) or hdr_info is None:       # a semester without its own header row continues the previous table layout
                    hdr_info = _header_info(hdr)
                info = hdr_info
                i += 1
                continue
            if sem is not None and _TOTAL.match(s):
                nums = re.findall(r"\d+", s)
                if nums and (track if sem >= 5 else None, sem) not in prog["declared"]:        # the credit total is the first number in credit-first tables and the last otherwise; only the first total line of a semester counts
                    prog["declared"][(track if sem >= 5 else None, sem)] = int(nums[0] if (info["credit_first"] and len(nums) == 3) else nums[-1])
            if sem is not None and s and not _TOTAL.match(s) and not s.startswith(("Course", "Code", "<<PAGE", "Faculty of", "Ghulam")):
                ms = _MS_ROW.match(s) if prog["code"] == "BSMS" else None
                if ms:
                    code = _norm(ms.group(2)).replace("-", "")
                    title = re.sub(r"\s+", " ", ms.group(1)).strip(" ,")
                    pre = _prereq_tokens(ms.group(4))
                    pre = re.sub(r"\b([A-Z]{2})\s(\d{3})\b", r"\1\2", pre)
                    is_slot = "X" in code.upper()
                    credits = sum(int(x) for x in ms.group(3).split("+"))
                    prog["rows"].append({"semester": sem, "track": track if sem >= 5 else None, "code": None if is_slot else code, "lab_code": None, "title": title, "credits": credits,
                                         "lecture_hours": None, "lab_hours": None, "prereq_raw": pre, "coreq_raw": "", "kind": "slot" if is_slot else "course", "page": page_at[i]})
                    i += 1
                    continue
                r = _ROW.match(s)
                if r:
                    codes = [_norm(c) for c in re.split(r"\s*,\s*", r.group(1))]
                    a, b, c3 = int(r.group(3)), int(r.group(4)), int(r.group(5))
                    credit, lec, lab = (a, b, c3) if info["credit_first"] else (c3, a, b)
                    tail = (r.group(6) or "").strip()
                    pre, co = tail, ""
                    if info["has_coreq"] and tail:
                        parts = tail.rsplit(" ", 1)
                        pre, co = (parts[0], parts[1]) if len(parts) == 2 else ("", parts[0])
                    placeholder = bool(re.search(r"[xX]{2}", codes[0]))                       # e.g. HM3XX, AI/CX3xx: an elective slot, not a catalogue course
                    prog["rows"].append({"semester": sem, "track": track if sem >= 5 else None, "code": None if placeholder else codes[0], "slot_code": codes[0] if placeholder else None,
                                         "lab_code": codes[1] if len(codes) > 1 else None, "title": re.sub(r"\s+", " ", r.group(2)).strip(" ,"),
                                         "credits": credit, "lecture_hours": lec, "lab_hours": lab, "prereq_raw": _prereq_tokens(pre), "coreq_raw": _prereq_tokens(co), "kind": "slot" if placeholder else "course", "page": page_at[i]})
                else:
                    sm = _SLOT.match(s) or _SLOT_SHORT.match(s)
                    if sm and (re.search(r"elective|language|\*", s, re.I) or len(sm.groups()) > 3):     # 3 hour columns and no code: printed without a course code
                        g = sm.groups()
                        title = re.sub(r"\s+", " ", g[0]).strip(" *")
                        credit = int(g[1]) if len(g) > 3 and info["credit_first"] else (int(g[3]) if len(g) > 3 else 1)
                        prog["rows"].append({"semester": sem, "track": track if sem >= 5 else None, "code": None, "lab_code": None, "title": title, "credits": credit,
                                             "lecture_hours": int(g[2 if info["credit_first"] else 1]) if len(g) > 3 else None, "lab_hours": None, "prereq_raw": "", "coreq_raw": "", "kind": "slot", "page": page_at[i]})
                    elif re.search(r"\s\d\s+\d\s+\d(?:\s|$)", s):
                        rejected.append({"program": code, "semester": sem, "page": page_at[i], "line": s})
            i += 1
        prog["semesters_found"] = sorted(prog["semesters_found"])
        if prog["tracks"]:
            prog["first_track_label"] = _first_track_name(lines, st, prog["tracks"])
        programs.append(prog)
    return {"programs": programs, "rejected": rejected}


def check_totals(prog: dict) -> list[dict]:
    """Compare every semester's parsed credit sum with the 'Total ... Cr. Hrs.' line printed in the prospectus. Mismatches mean rows were missed or mis-read."""
    out = []
    for (track, sem), declared in sorted(prog["declared"].items(), key=lambda kv: (str(kv[0][0]), kv[0][1])):
        got = sum(r["credits"] for r in prog["rows"] if r["semester"] == sem and r["track"] == track)
        if got != declared:
            out.append({"track": track, "semester": sem, "declared": declared, "parsed": got})
    return out
