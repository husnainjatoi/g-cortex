"""Official GIKI student societies: import from https://giki.edu.pk/campus-life/student-socities/ and administration."""
import html as htmllib
import re
from datetime import datetime

from app import db
from app.knowledge import crawler
from . import audit
from .actor import Actor
from .audit import op
from .errors import Conflict, NotFound, ValidationFailed

SOURCE_URL = "https://giki.edu.pk/campus-life/student-socities/"
CATEGORY_HINTS = [  # only applied when the society's own official description states it - never guessed
    ("technical", r"\b(computing machinery|photonics|aeronautics|mechanical engineers|electrical and electronics|heating, refrigerating|chemical engineers|network|computer network|mathematics)\b"),
    ("cultural", r"\b(dramatics|entertainment|arts society|literary|debating)\b"),
    ("sports", r"\bsports society\b"), ("welfare", r"\b(uplift|character building|higher education)\b"),
]


def name_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.lower())[:200]


def _clean(t: str) -> str:
    return re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", t))).strip()


def parse_societies(page_html: str) -> list[dict]:
    """Every <h6 class="subheading"> block on the official page is one society: name, logo (if present) and full description."""
    i = page_html.find("gdlr-core-page-builder-body")
    body = page_html[i:] if i >= 0 else page_html
    out, seen = [], set()
    for part in re.split(r'<h6 class="subheading">', body)[1:]:
        head, _, rest = part.partition("</h6>")
        name = _clean(head).replace("\xa0", " ")
        if not name:
            continue
        rest = re.split(r'<div class="kingster-footer-wrapper|<footer', rest)[0]
        logo = next((u for u in re.findall(r'(?:data-src|src)="(https?://[^"]+\.(?:png|jpe?g|gif|svg|webp)[^"]*)"', rest, re.I) if crawler.is_official_url(u)), None)
        desc = _clean(re.sub(r"<(script|style)[\s\S]*?</\1>", " ", re.sub(r"</(p|div|li)>", "\n", rest)))
        k = name_key(name)
        if k in seen:
            continue
        seen.add(k)
        cat = next((c for c, pat in CATEGORY_HINTS if re.search(pat, f"{name} {desc}", re.I)), None)
        out.append({"name": name, "description": desc or None, "logo_url": logo, "category": cat})
    return out


def import_official(actor: Actor | None = None, fetcher=None, html_override: str | None = None) -> dict:
    """Idempotent: re-running updates official rows in place (no duplicates); manually edited societies keep their edits."""
    if actor is not None:
        actor.require_scope("requisition")
    if html_override is None:
        f = (fetcher or crawler.OfficialFetcher()).fetch(SOURCE_URL)
        if f.status != 200 or not f.content:
            raise ValidationFailed("The official societies page could not be retrieved right now. Please try again later.")
        page = f.content.decode("utf-8", errors="ignore")
    else:
        page = html_override
    items = parse_societies(page)
    if not items:
        raise ValidationFailed("No societies were found on the official page; the import was cancelled so existing data is untouched.")
    created = updated = kept = 0
    keys = []
    with db.tx() as cur:
        for it in items:
            k = name_key(it["name"]); keys.append(k)
            cur.execute("SELECT society_id, manually_edited, origin FROM societies WHERE name_key=%s FOR UPDATE", (k,))
            ex = cur.fetchone()
            if ex is None:
                cur.execute("""INSERT INTO societies (name, name_key, description, category, logo_url, source_url, origin, retrieved_at, last_checked_at)
                               VALUES (%s,%s,%s,%s,%s,%s,'official',now(),now())""", (it["name"], k, it["description"], it["category"], it["logo_url"], SOURCE_URL))
                created += 1
            elif ex["manually_edited"]:
                cur.execute("UPDATE societies SET last_checked_at=now(), source_url=COALESCE(source_url,%s) WHERE society_id=%s", (SOURCE_URL, ex["society_id"])); kept += 1
            else:
                cur.execute("""UPDATE societies SET name=%s, description=%s, category=COALESCE(%s, category), logo_url=COALESCE(%s, logo_url), source_url=%s, origin='official',
                               retrieved_at=now(), last_checked_at=now() WHERE society_id=%s""", (it["name"], it["description"], it["category"], it["logo_url"], SOURCE_URL, ex["society_id"]))
                updated += 1
        cur.execute("SELECT name FROM societies WHERE origin='official' AND NOT (name_key = ANY(%s))", (keys,))
        gone = [r["name"] for r in cur.fetchall()]
        linked = _link_legacy(cur)
        audit.log(cur, actor or Actor("SYSTEM", "admin", frozenset({"super"}), "system"), "societies.import", "societies", "official",
                  detail={"listed": len(items), "created": created, "updated": updated, "kept_manual": kept, "no_longer_listed": gone, "legacy_linked": linked})
    return {"listed": len(items), "created": created, "updated": updated, "kept_manual_edits": kept, "no_longer_listed": gone, "legacy_linked": linked, "source": SOURCE_URL}


LEGACY_TO_OFFICIAL = {"ACM GIKI Chapter": "acm", "IEEE Student Branch": "ieee", "Netronix Networking Society": "netronix", "GIKI Sports Club": "gsssports",
                      "Literary and Performing Arts Society": "literaryanddebating", "Women in Engineering (WIE)": "womenengineers"}


def _link_legacy(cur) -> int:
    """Carry advisor/membership data of the team-seeded societies over to their official counterpart, then retire the legacy entry (history is kept)."""
    n = 0
    for legacy_name, prefix in LEGACY_TO_OFFICIAL.items():
        cur.execute("SELECT society_id, advisor_id FROM societies WHERE name_key=%s AND origin='legacy' AND status='active'", (name_key(legacy_name),))
        leg = cur.fetchone()
        cur.execute("SELECT society_id FROM societies WHERE origin='official' AND name_key LIKE %s ORDER BY society_id LIMIT 1", (prefix + "%",))
        off = cur.fetchone()
        if not leg or not off:
            continue
        cur.execute("INSERT INTO society_memberships (society_id, user_id, member_role) SELECT %s, user_id, member_role FROM society_memberships WHERE society_id=%s ON CONFLICT DO NOTHING", (off["society_id"], leg["society_id"]))
        cur.execute("UPDATE societies SET advisor_id=COALESCE(advisor_id,%s) WHERE society_id=%s", (leg["advisor_id"], off["society_id"]))
        cur.execute("UPDATE society_requisitions SET society_id=%s WHERE society_id=%s", (off["society_id"], leg["society_id"]))
        cur.execute("UPDATE societies SET status='inactive' WHERE society_id=%s", (leg["society_id"],))
        n += 1
    return n


def list_societies(actor: Actor, include_inactive: bool = False, q: str | None = None) -> list[dict]:
    if include_inactive:
        actor.require_scope("requisition")
    where, p = [], []
    if not include_inactive:
        where.append("s.status='active'")
    if q:
        where.append("(s.name ILIKE %s OR COALESCE(s.description,'') ILIKE %s)"); p += [f"%{q}%"] * 2
    return db.query(f"""SELECT s.society_id, s.name, s.description, s.category, s.logo_url, s.source_url, s.status, s.origin, s.advisor_id, s.manually_edited, s.retrieved_at,
                               (SELECT COUNT(*) FROM society_memberships m WHERE m.society_id=s.society_id) AS members
                        FROM societies s {('WHERE ' + ' AND '.join(where)) if where else ''} ORDER BY s.name""", p)


def my_societies(actor: Actor) -> list[dict]:
    return db.query("""SELECT s.society_id, s.name, s.category, m.member_role FROM society_memberships m JOIN societies s USING (society_id) WHERE m.user_id=%s AND s.status='active' ORDER BY s.name""", (actor.user_id,))


def get_society(society_id: int) -> dict:
    r = db.query_one("SELECT * FROM societies WHERE society_id=%s", (society_id,))
    if not r:
        raise NotFound("That society could not be found.")
    return r


def resolve(cur, society_id=None, society_name=None) -> dict:
    if society_id is not None:
        cur.execute("SELECT * FROM societies WHERE society_id=%s", (society_id,))
    elif society_name:
        cur.execute("SELECT * FROM societies WHERE name_key=%s OR lower(name)=lower(%s) OR name ILIKE %s ORDER BY (status='active') DESC, society_id LIMIT 1",
                    (name_key(society_name), society_name, f"{society_name}%"))
    else:
        raise ValidationFailed("Please choose a society.")
    r = cur.fetchone()
    if not r:
        raise NotFound("That society could not be found. Please choose one from the list.")
    return dict(r)


@op("society.create", "society", "name")
def create_society(actor: Actor, name: str, description: str = "", category: str = "", advisor_id: str | None = None, logo_url: str = "") -> dict:
    actor.require_scope("requisition")
    name = (name or "").strip()
    if len(name) < 3 or len(name) > 200:
        raise ValidationFailed("Please enter a society name between 3 and 200 characters.")
    with db.tx() as cur:
        cur.execute("SELECT 1 FROM societies WHERE name_key=%s", (name_key(name),))
        if cur.fetchone():
            raise Conflict("A society with that name already exists.")
        cur.execute("""INSERT INTO societies (name, name_key, description, category, logo_url, origin, manually_edited, advisor_id) VALUES (%s,%s,%s,%s,%s,'manual',TRUE,%s) RETURNING society_id""",
                    (name, name_key(name), description.strip() or None, category.strip() or None, logo_url.strip() or None, advisor_id or None))
        sid = cur.fetchone()["society_id"]
        audit.log(cur, actor, "society.create", "society", sid, detail={"name": name})
    return get_society(sid)


@op("society.update", "society", "society_id")
def update_society(actor: Actor, society_id: int, **fields) -> dict:
    actor.require_scope("requisition")
    allowed = {"name", "description", "category", "status", "logo_url", "advisor_id"}
    f = {k: (v.strip() if isinstance(v, str) else v) for k, v in fields.items() if k in allowed and v is not None}
    if "status" in f and f["status"] not in ("active", "inactive"):
        raise ValidationFailed("Status must be active or inactive.")
    if "name" in f and len(f["name"]) < 3:
        raise ValidationFailed("Please enter a society name of at least 3 characters.")
    with db.tx() as cur:
        cur.execute("SELECT society_id FROM societies WHERE society_id=%s FOR UPDATE", (society_id,))
        if not cur.fetchone():
            raise NotFound("That society could not be found.")
        if "name" in f:
            cur.execute("SELECT 1 FROM societies WHERE name_key=%s AND society_id<>%s", (name_key(f["name"]), society_id))
            if cur.fetchone():
                raise Conflict("Another society already uses that name.")
            f["name_key"] = name_key(f["name"])
        if "advisor_id" in f and f["advisor_id"]:
            cur.execute("SELECT 1 FROM users WHERE user_id=%s AND role IN ('faculty','admin')", (f["advisor_id"],))
            if not cur.fetchone():
                raise ValidationFailed("The faculty advisor must be an existing faculty account.")
        f = {k: (None if v == "" else v) for k, v in f.items()}
        if f:
            cur.execute(f"UPDATE societies SET {','.join(k + '=%s' for k in f)}, manually_edited=TRUE WHERE society_id=%s", [*f.values(), society_id])
        audit.log(cur, actor, "society.update", "society", society_id, detail={k: v for k, v in f.items() if k != "name_key"})
    return get_society(society_id)


@op("society.membership", "society", "society_id")
def set_membership(actor: Actor, society_id: int, user_id: str, role: str | None = "member") -> dict:
    """role=None removes the membership. Memberships define who may reserve venues on behalf of the society."""
    actor.require_scope("requisition")
    with db.tx() as cur:
        get_society(society_id)
        cur.execute("SELECT role FROM users WHERE user_id=%s", (user_id,))
        u = cur.fetchone()
        if not u or u["role"] != "student":
            raise ValidationFailed("Please enter the ID of an existing student.")
        if role is None:
            cur.execute("DELETE FROM society_memberships WHERE society_id=%s AND user_id=%s", (society_id, user_id))
        else:
            if role not in ("member", "office_bearer"):
                raise ValidationFailed("Role must be member or office bearer.")
            cur.execute("""INSERT INTO society_memberships (society_id, user_id, member_role) VALUES (%s,%s,%s) ON CONFLICT (society_id, user_id) DO UPDATE SET member_role=EXCLUDED.member_role""", (society_id, user_id, role))
        audit.log(cur, actor, "society.membership", "society", society_id, detail={"user_id": user_id, "role": role})
    return {"ok": True}


def members(actor: Actor, society_id: int) -> list[dict]:
    actor.require_scope("requisition")
    return db.query("""SELECT m.user_id, u.full_name, m.member_role FROM society_memberships m JOIN users u USING (user_id) WHERE m.society_id=%s ORDER BY u.user_id""", (society_id,))
