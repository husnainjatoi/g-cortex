"""Parse course entries from the official GIKI prospectus text (pure functions, no I/O).

An entry looks like:
    CS112 Object-Oriented Programming (3 3 4):
     <description ...>
    Prerequisite(s): CS103
    Core for: AI, CE, CS, DS
where the three numbers are lecture hours, lab hours and credit hours. Anything that does not parse unambiguously is returned
in `rejected` with a reason so an administrator can review it - nothing is guessed.
"""
import re

CODE = r"[A-Z]{2,4}\s?\d{3}"
_START = re.compile(rf"^({CODE})(?:\s*(?:/|&|,|and)\s*({CODE}))?\s+(\S.*)$")
_HEADER_END = re.compile(r"\(\s*(\d)\s+(\d)\s+(\d)\s*\)\s*:")
_PAGE = re.compile(r"<<PAGE (\d+)>>")
_FACULTY = re.compile(r"^(Faculty of [A-Za-z&,() ]+?|Department of [A-Za-z&,() ]+?|School of [A-Za-z&,() ]+?)\s*$")
_PREREQ = re.compile(r"Prerequisite\(s\)\s*:\s*(.*?)(?=\n\s*(?:Core for|Co-?requisite|Cross|$)|\Z)", re.S | re.I)
_CODE_RE = re.compile(r"\b([A-Z]{2,4})\s?(\d{3}L?)\b")


def norm_code(c: str) -> str:
    return re.sub(r"\s+", "", c.upper())


def parse_prereq(text: str | None) -> dict:
    """-> {'codes': [...], 'status': 'none'|'parsed'|'ambiguous', 'raw': str}. Only plain code lists are trusted."""
    raw = re.sub(r"[\x00-\x1f]+", " ", text or "")
    raw = re.sub(r"\s+", " ", raw).strip(" .;")
    if not raw or raw.lower() in ("none", "nil", "no", "n/a"):
        return {"codes": [], "status": "none", "raw": raw}
    codes = [norm_code(a + b) for a, b in _CODE_RE.findall(raw)]
    leftover = _CODE_RE.sub("", raw)
    leftover = re.sub(r"\b(and|&)\b|[,;/()]", " ", leftover, flags=re.I).strip()
    if codes and not leftover and not re.search(r"\bor\b", raw, re.I) and not re.search(rf"{CODE}\s*/\s*{CODE}", raw):
        return {"codes": sorted(set(codes)), "status": "parsed", "raw": raw}
    return {"codes": sorted(set(codes)), "status": "ambiguous", "raw": raw}


def parse_courses(text: str) -> dict:
    lines = text.split("\n")
    page, faculty, section = 0, None, "elective"
    starts: list[tuple[int, int, str | None, str]] = []   # (line index, page, faculty, section)
    for i, ln in enumerate(lines):
        m = _PAGE.match(ln.strip())
        if m:
            page = int(m.group(1)); faculty = None
            # the running header of a page names its faculty
            for j in range(i + 1, min(i + 25, len(lines))):
                fm = _FACULTY.match(lines[j].strip())
                if fm and "Prospectus" not in lines[j]:
                    faculty = re.sub(r"\s+", " ", fm.group(1)).strip(); break
            continue
        s = ln.strip()
        if re.fullmatch(r"CORE COURSES?", s, re.I):
            section = "core"
        elif re.fullmatch(r"(TECHNICAL |FREE |GENERAL )?ELECTIVES?( COURSES?)?", s, re.I):
            section = "elective"
        if _START.match(s):
            starts.append((i, page, faculty, section))
    courses, rejected = [], []
    for k, (i, pg, fac, sec) in enumerate(starts):
        end = starts[k + 1][0] if k + 1 < len(starts) else min(len(lines), i + 60)
        block = "\n".join(lines[i:end])
        flat = re.sub(r"\s+", " ", block)
        head = _HEADER_END.search(flat[:400])
        m0 = _START.match(lines[i].strip())
        if not head:
            continue                                   # a table row / list item, not a course description entry
        pre = flat[:head.start()]
        pm = re.match(rf"^({CODE})(?:\s*(?:/|&|,|and)\s*({CODE}))?\s+(.*?)\s*(?:\(\s*\d+\s+Credits?\s*\))?\s*$", pre, re.I)
        if not pm:
            rejected.append({"raw_head": pre[:120], "page": pg, "reason": "unparseable header"}); continue
        code1, code2, title = norm_code(pm.group(1)), (norm_code(pm.group(2)) if pm.group(2) else None), pm.group(3).strip(" -–")
        lec, lab, cr = int(head.group(1)), int(head.group(2)), int(head.group(3))
        body = flat[head.end():]
        pr = _PREREQ.search(block[block.find(":", block.find("(")) + 1:]) if False else re.search(r"Prerequisite\(s\)\s*:\s*(.*?)(?:\s*Core for:|\s*Specialization|\s*Co-?requisite|$)", body, re.I)
        desc = body[:pr.start()].strip() if pr else body.strip()
        desc = re.sub(r"\s+", " ", re.sub(r"UNDERGRADUATE PROSPECTUS \d{4}", "", desc)).strip()[:2000]
        base = {"title": title, "lecture_hours": lec, "lab_hours": lab, "credits": cr, "description": desc, "page": pg, "faculty": fac, "category": sec,
                "prereq": parse_prereq(pr.group(1) if pr else None) if pr else {"codes": [], "status": "missing", "raw": ""}}
        if code2 and re.search(r"&|,|\band\b", flat[len(code1):len(code1) + 8]) and (re.search(r"\b[IVX]+\s*(&|and)\s*[IVX]+\b", title) or "&" in title):
            rejected.append({"raw_head": pre[:120], "page": pg, "reason": "combined multi-course entry (credits are not per course)"}); continue
        if not title or cr < 1 or cr > 6:
            rejected.append({"raw_head": pre[:120], "page": pg, "reason": f"implausible title/credits ({cr})"}); continue
        for code in filter(None, (code1, code2)):
            courses.append({**base, "course_code": code, "cross_listed_with": [c for c in (code1, code2) if c and c != code]})
    return {"courses": courses, "rejected": rejected}
