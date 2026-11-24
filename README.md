# G-Cortex — Campus Operating System for GIKI

G-Cortex is a campus management portal with an integrated multi-agent AI assistant. Students, faculty and administrators sign in to one
website to manage **courses**, **mess**, and **campus resources (requisitions, sports, lab equipment)** — manually through the portal, or by
asking the assistant to do the same thing for them.

> **One backend, two interfaces.** The portal pages and the assistant call the *same* service layer, authenticated by the *same* session,
> authorised by the *same* RBAC rules, writing to the *same* PostgreSQL database. There is no second implementation of any rule.

```
 Browser (SPA)                          Chat widget (every page)
      │  cookie session                       │  cookie session
      ▼                                       ▼
 REST API (FastAPI)  ───────────────►  Central Orchestrator ──► Course / Mess / Requisition subagents (Google ADK)
      │                                                              │  explicit, user-bound TOOLS only
      ▼                                                              ▼
 ┌────────────────────────────  app/services  (shared business logic)  ────────────────────────────┐
 │ auth · courses · mess · requisitions · sports · equipment · reports · settings · audit · events │
 └───────────────────────────────────────────────┬──────────────────────────────────────────────────┘
                                                 ▼
                       PostgreSQL (+ pgvector) — single source of truth, append-only audit log
```

## Technologies
FastAPI · PostgreSQL 14+ (btree_gist, pgcrypto, pgvector extension available) · psycopg2 · Google ADK + Gemini · vanilla-JS SPA (no build step) · pytest.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt            # add: pytest qrcode  (see requirements.txt)
createdb gcortex_db                         # PostgreSQL with the `vector` extension installed
```

### Environment variables
Put these in a root `.env` (git-ignored). The legacy `app/agents/*/.env` files are still read as a fallback.

| Variable | Purpose | Default |
|---|---|---|
| `DATABASE_URL` | PostgreSQL connection string | `postgresql://postgres:postgres@localhost/gcortex_db` |
| `GEMINI_API_KEY` / `GOOGLE_API_KEY` | Model access for the assistant | – (assistant reports it is unavailable) |
| `ASSISTANT_MODEL` | Gemini model name | `gemini-3.5-flash-lite` |
| `SESSION_TTL_HOURS` | Sign-in session lifetime | `12` |
| `COOKIE_SECURE` | Set `true` behind HTTPS | `false` |
| `GATE_API_KEY` | Shared secret for the gate-controller REST integration | unset (integration disabled) |
| `MAX_FAILED_LOGINS` / `LOCKOUT_MINUTES` | Account lockout policy | `5` / `15` |
| `BACKGROUND_JOBS` | No-show sweeper and event retry loop | `true` |

### Database setup and migrations
```bash
python -m app.migrate          # applies migrations/000…NNN (forward-only, one transaction each, tracked in schema_migrations)
python seed_db.py              # reference campus data: users, courses, menu, venues, societies, equipment
python -m app.seed_portal      # non-secret reference data: instructors, lab slots, academic history, society memberships
```
Migrations add credentials, admin scopes, revocable sessions, enrolments, course history, mess-out requests, TOTP redemptions, booking
**exclusion constraints** (no double booking), a transactional event outbox, notifications and an **append-only audit log** (database trigger
rejects UPDATE/DELETE/TRUNCATE).

### Provisioning accounts (no credentials live in source code)
Roles are stored in `users.role` and `admin_scopes`; the login page never selects a role.
```bash
python -m app.cli provision-all                    # one-time random passwords for every account without one (shown once; change forced at first login)
python -m app.cli create-admin ADMIN02 registrar@giki.edu.pk "Registrar Office" --scopes academic
python -m app.cli create-student 2024999 u2024999@giki.edu.pk "New Student"
python -m app.cli set-password ADMIN01              # hidden prompt, or GCORTEX_PASSWORD env var; never a CLI argument
```
Admin scopes separate responsibilities: `academic` (courses), `mess`, `requisition` (approvals, venues, sports, equipment) and `super`
(users, settings, audit; implies all). Administrators can later manage users from **Users & Roles** in the portal.

### Run
```bash
uvicorn main:app --reload        # portal at http://localhost:8000/   ·   API docs at /docs   ·   health at /health
```

### Tests
```bash
pytest -m "not llm"      # services, HTTP/RBAC, concurrency, audit, events — real PostgreSQL (database gcortex_test is recreated)
pytest -m llm            # cross-interface tests that call the real Gemini model (slow; free tier is ~15 requests/min, tests back off)
```
Tests never use mocked service responses. They run against a throwaway `gcortex_test` database derived from `DATABASE_URL`.

## Using the portal
* **Student** — Dashboard; *Courses & Timetable* (catalogue with prerequisite/clash warnings, register/drop, timetable with lab/lecture clash matrix,
  elective recommender, what-if minor simulator, Ask GIKI policy retrieval, academic record); *Mess* (mess-out with live rebate preview, rebate ledger,
  rotating TOTP dining QR, meal records and macros, allergen shield); *Requisitions* (file, track stages and pending authority, venue recommender);
  *Sports Booking* (slot grid, daily/weekly quota, check-in PIN, cancel); *Lab Equipment* (borrow/return by barcode, custody history);
  *Notifications & Activity* (including your rejected attempts).
* **Faculty** — advisor approval queue (Tier 1), mess demand forecast.
* **Administrator** — scope-filtered reports (charts and tables), course/prerequisite/capacity administration with student lists, users & roles,
  mess approvals/attendance/menu/forecast/gate-pass sync, requisition Tier-2 approvals, venues & sports quotas, equipment inventory, rules & settings,
  audit trail and event pipeline.

## How the assistant works
`Student → chat → Central Orchestrator → (Course | Mess | Requisition) subagent → tool → app.services → PostgreSQL`
* The orchestrator classifies each message (Gemini, with a deterministic fallback), routes to one or several subagents and returns a single reply.
  Follow-ups keep context (per-user chat session + per-subagent ADK session).
* **Controlled tools only** (`app/agents/tools`): no SQL, no DB handle, no `user_id` parameter. Identity is bound server-side from the signed-in session
  (`app/agents/runtime.py`); the model cannot choose whose data it acts on. Other users' chat sessions and confirmations are unreachable.
* **Confirmation gate**: tools that change state only *stage* an action after a dry-run of the real rules. It executes only when the user replies
  “yes”/presses Confirm — handled deterministically, never by the model — then the outcome is read back from the database before success is reported.
* The backend remains the final authority: a refused rule (prerequisite, quota, notice period, approval chain, permission) is reported to the user, not worked around.
* If the model is rate-limited or unavailable the user is told so and nothing is changed — there is no simulated fallback data.
* Assistant actions are tagged `channel = assistant` in the audit log and show up immediately in the matching portal page.

## Event-driven pipeline
Services write domain events in the same transaction as the change (outbox); after commit, idempotent handlers (`app/services/handlers.py`) run in their own
transactions, recorded in `event_handler_runs` so duplicates are impossible and failures are retried (admin **Audit & Events** page / background loop).
Examples: mess-out approval → rebate ledger + `rebated` meal rows; final requisition approval → venue booking + notifications; booking release → strike/suspension + notifications;
gate-pass sync → auto-approval of covered pending mess-outs; equipment damage → admin alert.

## Proposal coverage
| Proposal feature | Where |
|---|---|
| Prerequisite DAG evaluation | `courses.prerequisite_chain / check_prerequisites` (cycle-safe, admin edits rejected if circular) |
| Schedule conflict matrix | `courses.find_conflicts / timetable` (lecture/lecture, lecture/lab, lab/lab) |
| Interest-based elective recommendation | `courses.recommend_electives` (keyword relevance over course topics, eligibility-aware) |
| What-if degree/minor simulator | `courses.what_if` (single/combined minors, hypothetical courses, blockers, overlaps) |
| “Ask GIKI” RAG policy engine | `courses.ask_giki` — PostgreSQL full-text retrieval over `university_policies`, extractive answers (see limitations) |
| TOTP dynamic QR gatekeeping | `mess.generate_qr / verify_entry` — HMAC-SHA1 TOTP (30 s), single-use redemption table, anti-passback |
| Automated rebate ledgering | event handler `credit_rebate_ledger` |
| Gate-pass synchronisation | `POST /api/mess/gate-pass-sync` (gate controller key or Mess Office) |
| Predictive demand forecasting | `mess.forecast` — weighted same-weekday moving average of real entries minus approved mess-outs |
| Nutritional macros & allergen shield | `mess.menu / meal_records / allergen_check` |
| Multi-tier approval chains | `requisitions.review_requisition` (Advisor → Student Affairs) |
| Sports reservations with fair-use quotas, no-show auto-release | `sports.book / auto_release` (+ background sweeper) |
| Lab equipment barcode custody | `equipment.*` with `equipment_custody_log` |
| NL venue recommender | `requisitions.recommend_venue` |
| Event-driven pipeline, centralized RBAC, unified reporting | `services/events.py`, `services/actor.py`, `services/reports.py` |

## Security notes
scrypt password hashing · server-side revocable sessions in HttpOnly SameSite=Strict cookies · lockout after repeated failures · backend RBAC on every operation
(`Actor` is built only from the session) · parameterised SQL only · origin/CSRF guard and security headers · row locks, advisory locks and exclusion constraints for
concurrency · append-only audit log (successes, denials and failures, with channel) · secrets only from the environment.

## Known limitations and external integrations
* **Gate/turnstile hardware** is not controlled. `POST /api/mess/verify-entry` and `/gate-pass-sync` are the REST integration points for an existing gate controller
  (authenticated by `X-Gate-Key`); until a real controller is connected, scans can be exercised from tests or the admin gate-pass form.
* **Fee payments**: no bank/card processing (out of scope per proposal); challan generation and receipt upload are *not* implemented.
* **Ask GIKI** retrieves from the policy table with full-text search; the `policy_embedding` pgvector column exists but no embedding model is wired, so semantic (vector) retrieval is not active.
* **Elective recommender / forecaster** are transparent heuristics/statistics, not trained ML models.
* **Email/SMS** notifications are not sent; notifications are in-app only.
* The assistant depends on the Gemini API (rate-limited on free tier). Chat session history is held in server memory (lost on restart); staged actions and the audit trail are persisted.
* Faculty accounts can use the advisor queue and forecast; the assistant's action tools are student-oriented (administrators perform administration through the portal).
* The visual UI was checked with a headless Chrome walkthrough of every route for all three roles; it has not been through usability testing.
