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
python -m app.knowledge refresh   # OPTIONAL but recommended: crawl giki.edu.pk (official sources only), index Ask GIKI, import the course catalogue
```
Migrations add credentials, admin scopes, revocable sessions, enrolments, course history, mess-out records, TOTP redemptions, booking
**exclusion constraints** (no double booking), a transactional event outbox, notifications and an **append-only audit log** (database trigger
rejects UPDATE/DELETE/TRUNCATE). Migration 003 adds monthly billing (immutable invoices, payments, challans, deposit ledger, clearances);
004/005 add the official-GIKI knowledge base (pgvector) and course provenance. All migrations are additive: the retired rebate tables
(`mess_rebates`, `rebated` ledger rows, `est_rebate_pkr`) are kept **read-only for audit** and are never written.

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
pytest -m "not llm and not network"   # services, billing, HTTP/RBAC, concurrency, audit, events, routing fallback, parser/import, headless-Chrome navigation
pytest -m network                     # downloads the official Student Handbook from giki.edu.pk, embeds it and tests Ask GIKI against it
pytest -m llm                         # cross-interface + routing tests that call the real Gemini model (slow; free tier ~15 requests/min, tests back off)
```
The frontend test (`tests/test_frontend.py`) drives headless Chrome through puppeteer (set `CHROME` / `PUPPETEER` if not auto-detected) and is skipped when unavailable.
Tests never use mocked service responses. They run against a throwaway `gcortex_test` database derived from `DATABASE_URL`.

## Using the portal
Navigation is a **top bar** (brand left; sections; notifications, assistant and account on the right; dropdowns group related pages; it collapses to a hamburger on small screens). Only sections the signed-in user may use are shown — the backend still enforces every permission.
* **Student** — *Dashboard*; *Courses* (catalogue with prerequisite/clash warnings, register/drop, timetable, elective recommender, what-if simulator, **Ask GIKI** with cited official sources, academic record); *Mess* (**monthly bill estimate**, invoices + challan + receipt submission, **security deposit** and final clearance, dining QR, meal records/macros/allergens, mess-out declarations); *Requisitions*; *Facilities* (Sports Booking, Lab Equipment); *AI Assistant*; notifications and activity from the bell/account menu.
* **Faculty** — advisor approval queue (Tier 1), mess demand forecast.
* **Administrator** — dashboard of pending work; *Academics* (course administration, **Official GIKI Data** refresh and review flags); *Student Management*; *Mess Administration* (rates, invoice generation, payment verification, deposits, final clearance, attendance, menu, forecast, gate-pass sync); *Requisition Approvals*; *Resources* (sports & venues, equipment); *Reports & System* (charts, rules, audit & events).

## Mess billing and security deposit
GIKI students pay **nothing in advance**. The system implements exactly that (the old rebate model has been removed from the code, API, UI and assistant):
1. Every verified turnstile entry (TOTP QR, single-use, anti-passback) is one consumed meal in `mess_ledger`; duplicates/invalid scans can never be counted.
2. Students see an **estimate** for the open month (days eaten × configured daily rate, or meals × per-meal rates) that is clearly labelled as an estimate.
3. After month end, **invoices** are generated from the verified records (automatically, or by the Mess Office), once per student and month (unique constraint), and are **immutable** (database trigger). Only students who actually ate are billed. Corrections are signed, reasoned, audited **adjustments**.
4. Payment tracking: challan generation → student submits the bank receipt reference → Mess Office verifies/rejects → balance and status update. There is **no card/bank processing** (out of scope).
5. **Rates are configured by the Mess Office** (versioned by effective date, not changeable retroactively once invoices exist). No prices are seeded or hard-coded; until configured, estimates say so.
6. The **refundable security deposit** is a separate ledger (configured amount, administrator-recorded payments with verification status). It is never prepaid credit and never auto-deducted for monthly bills.
7. **Final clearance**: student requests → administrator reviews → settlement nets unpaid dues against the deposit (recorded as deductions) and reserves the refundable remainder → the refund is marked returned only when the administrator records the actual return. Nothing is refunded automatically.
Mess-outs remain as informational absence declarations for kitchen planning; they have no financial effect.

## Official GIKI knowledge base and course catalogue
`python -m app.knowledge refresh` (or **Admin → Academics → Official GIKI Data → Refresh**) crawls **only `giki.edu.pk`** (and its subdomains): robots.txt is honoured, requests are rate-limited with an identified user agent, every redirect hop is domain-checked, login/admin paths are refused, and a database CHECK constraint rejects any other host. Sources: the Student Handbook, the UG/Graduate prospectuses, the academic calendar and relevant pages linked from the academic/policy pages.
* **Ask GIKI (RAG)** — documents are chunked by section, embedded (Gemini `gemini-embedding-001`, 1536-d) into pgvector and searched with hybrid vector + full-text retrieval. Answers quote the passage and cite document, section/page, URL and retrieval date; if the official documents do not answer, the system says so. Refreshes replace a changed document's chunks atomically (no duplicates, no stale text). The older team-written policy rows are flagged `verified = false` and are never used.
* **Courses** — prospectus entries (code, title, credit hours, description, prerequisites, faculty) are validated and imported as **catalogue-only** courses with source URL/page/retrieval date. Existing courses are **never overwritten**; title/credit/prerequisite differences and ambiguous (“CS103 or consent…”) or unresolvable prerequisites become **review flags** for an administrator (apply official / dismiss). Prerequisites are only linked when the official text is an unambiguous code list that creates no cycle. Catalogue-only courses are visible and count for prerequisite checking, what-if planning and recommendations, but cannot be registered until an administrator schedules them.

## How the assistant works
`Student → chat → Central Orchestrator → (Course | Mess | Requisition) subagent → tool → app.services → PostgreSQL`
* The orchestrator classifies each message (Gemini, with a deterministic fallback), routes to one or several subagents and returns a single reply.
  Follow-ups keep context (per-user chat session + per-subagent ADK session).
* **Routing**: the orchestrator's routing prompt is *generated from the real tool registries* (scope, example requests, supported operations per subagent), and a deterministic evidence-weighted fallback is used if the model is unavailable — equipment/barcode/custody requests always go to the Requisition agent, bills/deposit to Mess, course codes to Course, and unclear requests trigger a clarification instead of a default guess.
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
Examples: invoice generation → student notification; payment verification → invoice status; final requisition approval → venue booking + notifications; booking release → strike/suspension + notifications;
gate-pass sync is recorded for security reference; equipment damage → admin alert.

## Proposal coverage
| Proposal feature | Where |
|---|---|
| Prerequisite DAG evaluation | `courses.prerequisite_chain / check_prerequisites` (cycle-safe, admin edits rejected if circular) |
| Schedule conflict matrix | `courses.find_conflicts / timetable` (lecture/lecture, lecture/lab, lab/lab) |
| Interest-based elective recommendation | `courses.recommend_electives` (keyword relevance over course topics, eligibility-aware) |
| What-if degree/minor simulator | `courses.what_if` (single/combined minors, hypothetical courses, blockers, overlaps) |
| “Ask GIKI” RAG policy engine | `services/knowledge.py` — pgvector + full-text retrieval over official giki.edu.pk documents with citations |
| TOTP dynamic QR gatekeeping | `mess.generate_qr / verify_entry` — HMAC-SHA1 TOTP (30 s), single-use redemption table, anti-passback |
| ~~Automated rebate ledgering~~ → monthly billing | `services/billing.py` (retired rebates; see *Mess billing*) |
| Gate-pass synchronisation | `POST /api/mess/gate-pass-sync` (gate controller key or Mess Office; informational) |
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
* **Fee payments**: no bank/card processing (out of scope per proposal). Challans are generated and receipt *references* are submitted and verified by the Mess Office; the challan has no bank account details because none are configured in the system, and receipt images are not uploaded.
* **Billing prerequisites**: mess rates and the deposit amount must be configured by the Mess Office before estimates/invoices/deposits can be calculated; nothing is assumed.
* **Official data coverage**: the importer only parses what is in the GIKI documents that are publicly reachable. The `/course/...` pages listed in the site's sitemap returned HTTP 404 to the polite crawler and were not worked around, so the course catalogue is imported from the official UG Prospectus 2021 PDF (course-description entries in the prospectus's standard layout; entries in other layouts, and faculties whose course lists are tables, are not parsed). The prospectus is from 2021 and the handbook from 2023-24 — the system cites the document and date so users can judge currency. Embedding generation is subject to the Gemini API rate limit; unembedded chunks fall back to full-text search and are back-filled on the next refresh.
* **Elective recommender / forecaster** are transparent heuristics/statistics, not trained ML models.
* **Email/SMS** notifications are not sent; notifications are in-app only.
* The assistant depends on the Gemini API (rate-limited on free tier). Chat session history is held in server memory (lost on restart); staged actions and the audit trail are persisted.
* Faculty accounts can use the advisor queue and forecast; the assistant's action tools are student-oriented (administrators perform administration through the portal).
* The visual UI was checked with a headless Chrome walkthrough of every route for all three roles; it has not been through usability testing.
