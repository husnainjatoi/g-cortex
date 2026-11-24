"""Administrative command-line tools (secure provisioning - no credentials live in source code).

  python -m app.cli create-admin ADMIN02 admin@giki.edu.pk "Registrar Office" --scopes academic
  python -m app.cli create-student 2024999 u2024999@giki.edu.pk "New Student"
  python -m app.cli set-password USER_ID
  python -m app.cli provision-all            # generate one-time passwords for every account lacking one
Passwords are read from a hidden prompt (or GCORTEX_PASSWORD) and are never accepted as command-line arguments.
"""
import argparse
import getpass
import os
import secrets
import string
import sys

from app import db
from app.services import auth


def _password(confirm: bool = True) -> str:
    pw = os.environ.get("GCORTEX_PASSWORD")
    if pw:
        return pw
    pw = getpass.getpass("Password: ")
    if confirm and getpass.getpass("Repeat password: ") != pw:
        sys.exit("Passwords do not match.")
    return pw


def _temp_password() -> str:
    alphabet = string.ascii_letters + string.digits
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(12))
        if any(c.isdigit() for c in pw) and any(c.isalpha() for c in pw):
            return pw


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m app.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("create-admin"); a.add_argument("user_id"); a.add_argument("email"); a.add_argument("name")
    a.add_argument("--scopes", nargs="+", default=["super"], choices=["super", "academic", "mess", "requisition"])
    s = sub.add_parser("create-student"); s.add_argument("user_id"); s.add_argument("email"); s.add_argument("name")
    f = sub.add_parser("create-faculty"); f.add_argument("user_id"); f.add_argument("email"); f.add_argument("name")
    p = sub.add_parser("set-password"); p.add_argument("user_id")
    sub.add_parser("provision-all")
    args = ap.parse_args(argv)

    if args.cmd == "create-admin":
        auth.create_user(None, user_id=args.user_id, full_name=args.name, email=args.email, role="admin", scopes=args.scopes, password=_password(), must_change=False)
        print(f"created administrator {args.user_id} with scopes {', '.join(args.scopes)}")
    elif args.cmd in ("create-student", "create-faculty"):
        role = "student" if args.cmd == "create-student" else "faculty"
        auth.create_user(None, user_id=args.user_id, full_name=args.name, email=args.email, role=role, password=_password(), must_change=True)
        print(f"created {role} {args.user_id}")
    elif args.cmd == "set-password":
        auth.set_password(None, args.user_id, _password(), must_change=False)
        print("password updated")
    elif args.cmd == "provision-all":
        rows = db.query("SELECT user_id, email, role FROM users WHERE password_hash IS NULL AND is_active ORDER BY role, user_id")
        for r in rows:
            pw = _temp_password()
            auth.set_password(None, r["user_id"], pw, must_change=True)
            print(f"{r['role']:8} {r['user_id']:9} {r['email']:32} {pw}")
        print(f"\n{len(rows)} account(s) provisioned. Passwords are shown once; users must change them at first sign-in.")


if __name__ == "__main__":
    main()
