"""Authentication: scrypt password hashing, server-side revocable sessions, lockout, provisioning."""
import hashlib
import datetime as dt
import hmac
import re
import secrets
from datetime import datetime, timedelta

from app import config, db
from . import audit
from .actor import Actor, SCOPES
from .errors import Forbidden, NotFound, Unauthenticated, ValidationFailed, Conflict

_SCRYPT = dict(n=2 ** 14, r=8, p=1, dklen=32)
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    if not stored:
        return False
    try:
        scheme, salt_hex, digest_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), **_SCRYPT)
        return hmac.compare_digest(digest.hex(), digest_hex)
    except Exception:
        return False


def validate_password_strength(password: str) -> None:
    if len(password) < 8 or not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        raise ValidationFailed("Your password must be at least 8 characters long and include both letters and numbers.")


def parse_dob(value) -> datetime.date:
    try:
        d = value if isinstance(value, dt.date) else dt.date.fromisoformat(str(value).strip())
    except (ValueError, TypeError):
        raise ValidationFailed("Please enter the date of birth as a valid date (YYYY-MM-DD).")
    if d >= dt.date.today() or d.year < 1930:
        raise ValidationFailed("Please enter a realistic date of birth.")
    return d


def initial_password_for(role: str, date_of_birth) -> str | None:
    """Temporary initial credential: DOB as YYYYMMDD for students/faculty, the configured bootstrap value for administrators.
    Returns None when it cannot be determined (no usable DOB) - callers must NOT invent a fallback."""
    if role == "admin":
        return config.ADMIN_INITIAL_PASSWORD or None
    if not date_of_birth:
        return None
    try:
        return parse_dob(date_of_birth).strftime("%Y%m%d")
    except ValidationFailed:
        return None


def _is_initial_password(role: str, dob, candidate: str) -> bool:
    ini = initial_password_for(role, dob)
    return bool(ini) and hmac.compare_digest(ini, candidate)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _load_scopes(cur, user_id: str) -> frozenset:
    cur.execute("SELECT scope FROM admin_scopes WHERE user_id=%s", (user_id,))
    return frozenset(r["scope"] for r in cur.fetchall())


def login(email: str, password: str, user_agent: str = "") -> tuple[str, Actor, bool]:
    """Verify credentials and open a session. Returns (token, actor, must_change_password).

    The role is read from the database record - never from anything the client sends.
    Error messages are deliberately identical for unknown user / wrong password.
    """
    generic = Unauthenticated("Invalid email or password.")
    email = (email or "").strip().lower()
    if not email or not password:
        raise generic
    with db.tx() as cur:
        cur.execute("SELECT * FROM users WHERE lower(email) = %s FOR UPDATE", (email,))
        user = cur.fetchone()
        if not user:
            hash_password(password)  # equalise timing
            audit.log_independent(None, "auth.login", "user", email, "failed", {"reason": "unknown email"})
            raise generic
        if user["locked_until"] and user["locked_until"] > datetime.now():
            audit.log_independent(None, "auth.login", "user", user["user_id"], "denied", {"reason": "locked"})
            raise Unauthenticated("Account temporarily locked after repeated failed sign-ins. Try again later.")
        if not user["is_active"] or not user["password_hash"] or not verify_password(password, user["password_hash"]):
            fails = user["failed_logins"] + 1
            locked = datetime.now() + timedelta(minutes=config.LOCKOUT_MINUTES) if fails >= config.MAX_FAILED_LOGINS else None
            cur.execute("UPDATE users SET failed_logins=%s, locked_until=%s WHERE user_id=%s",
                        (0 if locked else fails, locked, user["user_id"]))
            audit.log(cur, None, "auth.login", "user", user["user_id"], "failed", {"reason": "bad credentials or inactive"})
            # commit the failure counters even though we raise
            cur.connection.commit()
            raise generic
        cur.execute("UPDATE users SET failed_logins=0, locked_until=NULL WHERE user_id=%s", (user["user_id"],))
        token = secrets.token_urlsafe(32)
        cur.execute("INSERT INTO auth_sessions (token_hash, user_id, expires_at, user_agent) VALUES (%s,%s,%s,%s)",
                    (_hash_token(token), user["user_id"], datetime.now() + timedelta(hours=config.SESSION_TTL_HOURS), user_agent[:200]))
        actor = Actor(user["user_id"], user["role"], _load_scopes(cur, user["user_id"]), "web", user["full_name"], bool(user["must_change_password"]))
        audit.log(cur, actor, "auth.login", "user", user["user_id"], "success", {"must_change_password": bool(user["must_change_password"])})
        return token, actor, user["must_change_password"]


def authenticate(token: str | None) -> Actor:
    if not token:
        raise Unauthenticated("Sign in required.")
    with db.tx() as cur:
        cur.execute(
            """SELECT u.user_id, u.role, u.full_name, u.is_active, u.must_change_password FROM auth_sessions s JOIN users u USING (user_id)
               WHERE s.token_hash=%s AND NOT s.revoked AND s.expires_at > now()""", (_hash_token(token),))
        row = cur.fetchone()
        if not row or not row["is_active"]:
            raise Unauthenticated("Session expired. Please sign in again.")
        return Actor(row["user_id"], row["role"], _load_scopes(cur, row["user_id"]), "web", row["full_name"], bool(row["must_change_password"]))


def logout(token: str | None, actor: Actor | None = None) -> None:
    if token:
        with db.tx() as cur:
            cur.execute("UPDATE auth_sessions SET revoked=TRUE WHERE token_hash=%s", (_hash_token(token),))
            if actor:
                audit.log(cur, actor, "auth.logout", "user", actor.user_id)


def _new_session(cur, user_id: str, user_agent: str = "") -> str:
    token = secrets.token_urlsafe(32)
    cur.execute("INSERT INTO auth_sessions (token_hash, user_id, expires_at, user_agent) VALUES (%s,%s,%s,%s)",
                (_hash_token(token), user_id, dt.datetime.now() + dt.timedelta(hours=config.SESSION_TTL_HOURS), user_agent[:200]))
    return token


def change_password(actor: Actor, current: str, new: str, confirm: str | None = None, user_agent: str = "") -> str:
    """Change the caller's password. All older sessions are revoked and a fresh session token is returned (session rotation)."""
    if confirm is not None and confirm != new:
        raise ValidationFailed("The new password and its confirmation do not match.")
    validate_password_strength(new)
    with db.tx() as cur:
        cur.execute("SELECT password_hash, role, date_of_birth FROM users WHERE user_id=%s FOR UPDATE", (actor.user_id,))
        u = cur.fetchone()
        if not verify_password(current, u["password_hash"]):
            audit.log_independent(actor, "auth.change_password", "user", actor.user_id, "failed", {"reason": "bad current password"})
            raise Forbidden("Your current password is not correct.")
        if hmac.compare_digest(current, new):
            raise ValidationFailed("Your new password must be different from your current password.")
        if _is_initial_password(u["role"], u["date_of_birth"], new):
            raise ValidationFailed("Please choose a personal password; the temporary default password cannot be reused.")
        cur.execute("UPDATE users SET password_hash=%s, must_change_password=FALSE, failed_logins=0, locked_until=NULL WHERE user_id=%s", (hash_password(new), actor.user_id))
        cur.execute("UPDATE auth_sessions SET revoked=TRUE WHERE user_id=%s", (actor.user_id,))
        token = _new_session(cur, actor.user_id, user_agent)
        audit.log(cur, actor, "auth.change_password", "user", actor.user_id)          # never records any password material
        return token


# ── Provisioning (CLI + admin portal) ───────────────────────

def create_user(actor: Actor | None, *, user_id: str, full_name: str, email: str, role: str,
                password: str | None = None, scopes: list[str] | None = None, must_change: bool = True,
                date_of_birth=None, deposit_received: bool = False, use_initial_password: bool = False) -> dict:
    if actor is not None:
        actor.require_scope("super")
    if role not in ("student", "faculty", "admin"):
        raise ValidationFailed("Role must be student, faculty or admin.")
    if not re.fullmatch(r"[A-Za-z0-9]{3,10}", user_id or ""):
        raise ValidationFailed("User ID must be 3-10 letters/digits.")
    if not EMAIL_RE.match(email or ""):
        raise ValidationFailed("A valid email address is required.")
    if not (full_name or "").strip():
        raise ValidationFailed("Full name is required.")
    scopes = scopes or []
    if any(s not in SCOPES for s in scopes):
        raise ValidationFailed(f"Scopes must be among {', '.join(SCOPES)}.")
    if role == "admin" and not scopes:
        raise ValidationFailed("Administrators need at least one scope.")
    dob = parse_dob(date_of_birth) if date_of_birth else None
    if password:
        validate_password_strength(password)
    elif use_initial_password:
        password = initial_password_for(role, dob)        # None when no usable DOB -> no credential is invented
        must_change = True
    with db.tx() as cur:
        cur.execute("SELECT 1 FROM users WHERE user_id=%s OR lower(email)=lower(%s)", (user_id, email))
        if cur.fetchone():
            raise Conflict("A user with that ID or email already exists.")
        cur.execute(
            "INSERT INTO users (user_id, full_name, role, email, password_hash, must_change_password, date_of_birth) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (user_id, full_name.strip(), role, email.strip().lower(), hash_password(password) if password else None, must_change, dob))
        if role == "student":
            cur.execute("INSERT INTO student_profiles (user_id) VALUES (%s)", (user_id,))
            from . import billing
            billing.init_student_deposit(cur, user_id, paid=deposit_received, recorded_by=getattr(actor, "user_id", None))
        for s in scopes if role == "admin" else []:
            cur.execute("INSERT INTO admin_scopes (user_id, scope) VALUES (%s,%s)", (user_id, s))
        audit.log(cur, actor, "user.create", "user", user_id, detail={"role": role, "scopes": scopes, "has_dob": bool(dob)})
    return {"user_id": user_id, "role": role, "initial_password_set": bool(password)}


def set_password(actor: Actor | None, user_id: str, password: str, must_change: bool = True) -> None:
    if actor is not None:
        actor.require_scope("super")
    validate_password_strength(password)
    with db.tx() as cur:
        cur.execute("UPDATE users SET password_hash=%s, must_change_password=%s, failed_logins=0, locked_until=NULL WHERE user_id=%s",
                    (hash_password(password), must_change, user_id))
        if cur.rowcount == 0:
            raise NotFound("User not found.")
        cur.execute("UPDATE auth_sessions SET revoked=TRUE WHERE user_id=%s", (user_id,))
        audit.log(cur, actor, "user.set_password", "user", user_id)


def update_user(actor: Actor, user_id: str, *, role: str | None = None, scopes: list[str] | None = None,
                is_active: bool | None = None, full_name: str | None = None) -> dict:
    actor.require_scope("super")
    with db.tx() as cur:
        cur.execute("SELECT * FROM users WHERE user_id=%s FOR UPDATE", (user_id,))
        u = cur.fetchone()
        if not u:
            raise NotFound("User not found.")
        if user_id == actor.user_id and (is_active is False or (role and role != "admin")):
            raise Forbidden("You cannot deactivate or demote your own account.")
        if role is not None:
            if role not in ("student", "faculty", "admin"):
                raise ValidationFailed("Invalid role.")
            cur.execute("UPDATE users SET role=%s WHERE user_id=%s", (role, user_id))
        eff_role = role or u["role"]
        if scopes is not None:
            if any(s not in SCOPES for s in scopes):
                raise ValidationFailed("Invalid scope.")
            cur.execute("DELETE FROM admin_scopes WHERE user_id=%s", (user_id,))
            if eff_role == "admin":
                if not scopes:
                    raise ValidationFailed("Administrators need at least one scope.")
                for s in scopes:
                    cur.execute("INSERT INTO admin_scopes (user_id, scope) VALUES (%s,%s)", (user_id, s))
        if eff_role != "admin":
            cur.execute("DELETE FROM admin_scopes WHERE user_id=%s", (user_id,))
        if is_active is not None:
            cur.execute("UPDATE users SET is_active=%s WHERE user_id=%s", (is_active, user_id))
            if not is_active:
                cur.execute("UPDATE auth_sessions SET revoked=TRUE WHERE user_id=%s", (user_id,))
        if full_name:
            cur.execute("UPDATE users SET full_name=%s WHERE user_id=%s", (full_name.strip(), user_id))
        if role is not None or scopes is not None:
            cur.execute("UPDATE auth_sessions SET revoked=TRUE WHERE user_id=%s", (user_id,))  # force re-auth with new rights
        audit.log(cur, actor, "user.update", "user", user_id, detail={"role": role, "scopes": scopes, "is_active": is_active})
    return get_user(actor, user_id)


def get_user(actor: Actor, user_id: str) -> dict:
    actor.require_scope("super")
    with db.tx() as cur:
        cur.execute("SELECT user_id, full_name, role, email, is_active, created_at, (password_hash IS NOT NULL) AS has_password, (date_of_birth IS NOT NULL) AS has_dob, must_change_password FROM users WHERE user_id=%s", (user_id,))
        u = cur.fetchone()
        if not u:
            raise NotFound("User not found.")
        u = dict(u); u["scopes"] = sorted(_load_scopes(cur, user_id))
        return u


def list_users(actor: Actor, role: str | None = None, q: str | None = None) -> list[dict]:
    actor.require_scope("super")
    where, params = [], []
    if role:
        where.append("u.role=%s"); params.append(role)
    if q:
        where.append("(u.user_id ILIKE %s OR u.full_name ILIKE %s OR u.email ILIKE %s)"); params += [f"%{q}%"] * 3
    return db.query(
        f"""SELECT u.user_id, u.full_name, u.role, u.email, u.is_active, (u.password_hash IS NOT NULL) AS has_password, (u.date_of_birth IS NOT NULL) AS has_dob, u.must_change_password,
                   COALESCE(string_agg(s.scope, ',' ORDER BY s.scope), '') AS scopes
            FROM users u LEFT JOIN admin_scopes s USING (user_id)
            {('WHERE ' + ' AND '.join(where)) if where else ''} GROUP BY u.user_id ORDER BY u.role, u.user_id""", params)


def set_date_of_birth(actor: Actor | None, user_id: str, dob) -> None:
    if actor is not None:
        actor.require_scope("super")
    d = parse_dob(dob)
    with db.tx() as cur:
        cur.execute("UPDATE users SET date_of_birth=%s WHERE user_id=%s", (d, user_id))
        if cur.rowcount == 0:
            raise NotFound("User not found.")
        audit.log(cur, actor, "user.set_dob", "user", user_id)


def provision_initial_password(actor: Actor | None, user_id: str) -> dict:
    """(Re)issue the temporary initial credential and force a change at next sign-in. Never invents a fallback password."""
    if actor is not None:
        actor.require_scope("super")
    with db.tx() as cur:
        cur.execute("SELECT role, date_of_birth FROM users WHERE user_id=%s FOR UPDATE", (user_id,))
        u = cur.fetchone()
        if not u:
            raise NotFound("User not found.")
        ini = initial_password_for(u["role"], u["date_of_birth"])
        if not ini:
            raise ValidationFailed("This account has no valid date of birth on file, so no initial password can be issued. Set the date of birth first.")
        cur.execute("UPDATE users SET password_hash=%s, must_change_password=TRUE, failed_logins=0, locked_until=NULL WHERE user_id=%s", (hash_password(ini), user_id))
        cur.execute("UPDATE auth_sessions SET revoked=TRUE WHERE user_id=%s", (user_id,))
        audit.log(cur, actor, "user.reset_initial_password", "user", user_id)
    return {"ok": True, "user_id": user_id}


def provision_all_initial(actor: Actor | None = None, only_missing: bool = True) -> dict:
    """Give every account without a password its initial credential; accounts without a usable DOB are reported, not guessed."""
    done, skipped = [], []
    for r in db.query("SELECT user_id FROM users WHERE is_active " + ("AND password_hash IS NULL" if only_missing else "") + " ORDER BY user_id"):
        try:
            provision_initial_password(actor, r["user_id"]); done.append(r["user_id"])
        except ValidationFailed:
            skipped.append(r["user_id"])
    return {"provisioned": done, "needs_date_of_birth": skipped}
