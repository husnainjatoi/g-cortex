"""Administrative command-line tools (secure provisioning - no credentials live in source code).

  python -m app.cli create-admin ADMIN02 admin@giki.edu.pk "Registrar Office" --scopes academic
  python -m app.cli create-student 2024999 u2024999@giki.edu.pk "New Student"
  python -m app.cli set-password USER_ID
  python -m app.cli set-dob USER_ID 2003-05-17
  python -m app.cli provision-all            # initial passwords: DOB (YYYYMMDD) for students/faculty, ADMIN_INITIAL_PASSWORD for admins
  python -m app.cli reset-initial USER_ID    # re-issue the temporary initial credential (forces a change at next sign-in)
Passwords are read from a hidden prompt (or GCORTEX_PASSWORD) and are never accepted as command-line arguments; initial passwords are never printed.
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
    d = sub.add_parser("set-dob"); d.add_argument("user_id"); d.add_argument("dob")
    r = sub.add_parser("reset-initial"); r.add_argument("user_id")
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
    elif args.cmd == "set-dob":
        auth.set_date_of_birth(None, args.user_id, args.dob)
        print("date of birth saved")
    elif args.cmd == "reset-initial":
        auth.provision_initial_password(None, args.user_id)
        print("initial password re-issued; the user must change it at next sign-in")
    elif args.cmd == "provision-all":
        res = auth.provision_all_initial(None, only_missing=True)
        print(f"initial credentials issued to {len(res['provisioned'])} account(s); each user must change the password at first sign-in")
        if res["needs_date_of_birth"]:
            print("NO credential issued (no valid date of birth on file - set one with set-dob):", ", ".join(res["needs_date_of_birth"]))

if __name__ == "__main__":
    main()
