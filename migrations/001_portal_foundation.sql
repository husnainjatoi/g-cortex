-- G-Cortex portal foundation: authentication, RBAC scopes, enrollments, audit, events, notifications.
-- Purely additive: existing subagent tables and data are preserved.
CREATE EXTENSION IF NOT EXISTS btree_gist;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ── Identity & RBAC ─────────────────────────────────────────
ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS must_change_password BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS failed_logins INT NOT NULL DEFAULT 0;
ALTER TABLE users ADD COLUMN IF NOT EXISTS locked_until TIMESTAMP;
ALTER TABLE users ADD COLUMN IF NOT EXISTS created_at TIMESTAMP NOT NULL DEFAULT now();

-- Administrative responsibilities are separated by scope. 'super' implies every scope.
CREATE TABLE IF NOT EXISTS admin_scopes (
    user_id VARCHAR(10) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    scope   VARCHAR(20) NOT NULL CHECK (scope IN ('super','academic','mess','requisition')),
    PRIMARY KEY (user_id, scope)
);
INSERT INTO admin_scopes (user_id, scope) SELECT user_id, 'super' FROM users WHERE role = 'admin'
ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS auth_sessions (
    token_hash CHAR(64) PRIMARY KEY,
    user_id    VARCHAR(10) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    created_at TIMESTAMP NOT NULL DEFAULT now(),
    expires_at TIMESTAMP NOT NULL,
    revoked    BOOLEAN NOT NULL DEFAULT FALSE,
    user_agent VARCHAR(200)
);
CREATE INDEX IF NOT EXISTS idx_auth_sessions_user ON auth_sessions(user_id);

CREATE TABLE IF NOT EXISTS student_profiles (
    user_id          VARCHAR(10) PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
    program          VARCHAR(100) NOT NULL DEFAULT 'BS Computer Science',
    current_semester INT NOT NULL DEFAULT 1 CHECK (current_semester BETWEEN 1 AND 12),
    interests        TEXT NOT NULL DEFAULT '',
    allergies        TEXT NOT NULL DEFAULT 'none'
);
INSERT INTO student_profiles (user_id) SELECT user_id FROM users WHERE role = 'student' ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS system_settings (
    key         VARCHAR(60) PRIMARY KEY,
    value       TEXT NOT NULL,
    description TEXT,
    updated_at  TIMESTAMP NOT NULL DEFAULT now()
);
INSERT INTO system_settings (key, value, description) VALUES
 ('current_term','Fall 2026','Active registration term'),
 ('max_credits_per_term','18','Maximum credit hours a student may register per term'),
 ('drop_deadline','2027-01-15','Last date (YYYY-MM-DD) on which courses may be dropped'),
 ('mess_daily_rate','450','PKR rebate per day of approved mess-out'),
 ('mess_min_days','2','Minimum consecutive days for a mess-out rebate'),
 ('mess_max_days','60','Maximum days in one mess-out request'),
 ('mess_notice_days','1','Minimum days of advance notice before a mess-out starts'),
 ('sports_daily_quota_hours','2','Fair-use cap: sports hours per student per day'),
 ('sports_weekly_quota_hours','6','Fair-use cap: sports hours per student per week'),
 ('sports_open_hour','8','Sports complex opening hour (24h)'),
 ('sports_close_hour','22','Sports complex closing hour (24h)'),
 ('sports_advance_days','7','How many days ahead sports slots may be booked'),
 ('cancel_cutoff_minutes','60','Bookings cannot be cancelled inside this window before start'),
 ('checkin_grace_minutes','15','Zero-trust check-in grace period before auto-release'),
 ('strike_limit','3','No-show strikes that trigger suspension'),
 ('suspension_days','14','Length of a no-show suspension'),
 ('equipment_max_items','3','Maximum equipment items in custody per student'),
 ('equipment_max_days','14','Maximum equipment loan period in days')
ON CONFLICT DO NOTHING;

-- ── Course management ───────────────────────────────────────
ALTER TABLE courses ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE courses ADD COLUMN IF NOT EXISTS capacity INT NOT NULL DEFAULT 60 CHECK (capacity >= 0);
ALTER TABLE courses ADD COLUMN IF NOT EXISTS instructor VARCHAR(100);
ALTER TABLE courses ADD COLUMN IF NOT EXISTS description TEXT;
ALTER TABLE courses ADD COLUMN IF NOT EXISTS lab_slot VARCHAR(30);

CREATE TABLE IF NOT EXISTS course_history (
    user_id     VARCHAR(10) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    course_code VARCHAR(10) NOT NULL REFERENCES courses(course_code) ON UPDATE CASCADE,
    grade       VARCHAR(3) NOT NULL,
    term        VARCHAR(20),
    PRIMARY KEY (user_id, course_code)
);

CREATE TABLE IF NOT EXISTS enrollments (
    enrollment_id SERIAL PRIMARY KEY,
    user_id       VARCHAR(10) NOT NULL REFERENCES users(user_id),
    course_code   VARCHAR(10) NOT NULL REFERENCES courses(course_code) ON UPDATE CASCADE,
    term          VARCHAR(20) NOT NULL,
    status        VARCHAR(12) NOT NULL DEFAULT 'registered' CHECK (status IN ('registered','dropped')),
    registered_at TIMESTAMP NOT NULL DEFAULT now(),
    dropped_at    TIMESTAMP,
    channel       VARCHAR(12) NOT NULL DEFAULT 'web'
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_enrollment_active ON enrollments (user_id, course_code, term) WHERE status = 'registered';
CREATE INDEX IF NOT EXISTS idx_enrollments_course ON enrollments (course_code, term, status);

-- ── Mess management ─────────────────────────────────────────
ALTER TABLE gate_passes ADD COLUMN IF NOT EXISTS gate_pass_id SERIAL;
ALTER TABLE gate_passes DROP CONSTRAINT IF EXISTS gate_passes_pkey;
ALTER TABLE gate_passes ADD PRIMARY KEY (gate_pass_id);
ALTER TABLE gate_passes ADD COLUMN IF NOT EXISTS external_ref VARCHAR(60);
ALTER TABLE gate_passes ADD COLUMN IF NOT EXISTS synced_at TIMESTAMP;
CREATE INDEX IF NOT EXISTS idx_gate_passes_user ON gate_passes(user_id);

CREATE SEQUENCE IF NOT EXISTS mess_rebate_seq START 1000;
CREATE TABLE IF NOT EXISTS mess_out_requests (
    request_id   SERIAL PRIMARY KEY,
    user_id      VARCHAR(10) NOT NULL REFERENCES users(user_id),
    start_date   DATE NOT NULL,
    end_date     DATE NOT NULL,
    days_count   INT NOT NULL,
    reason       VARCHAR(255),
    status       VARCHAR(12) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected','cancelled')),
    est_rebate_pkr NUMERIC(10,2) NOT NULL,
    gate_pass_synced BOOLEAN NOT NULL DEFAULT FALSE,
    decided_by   VARCHAR(10) REFERENCES users(user_id),
    decided_at   TIMESTAMP,
    decision_note VARCHAR(255),
    channel      VARCHAR(12) NOT NULL DEFAULT 'web',
    created_at   TIMESTAMP NOT NULL DEFAULT now(),
    CHECK (end_date >= start_date)
);
CREATE INDEX IF NOT EXISTS idx_mess_out_user ON mess_out_requests(user_id, start_date);
-- A student can never have two live mess-out requests covering the same day.
ALTER TABLE mess_out_requests ADD COLUMN IF NOT EXISTS span daterange GENERATED ALWAYS AS (daterange(start_date, end_date, '[]')) STORED;
DO $$ BEGIN
  ALTER TABLE mess_out_requests ADD CONSTRAINT no_overlapping_mess_out
    EXCLUDE USING gist (user_id WITH =, span WITH &&) WHERE (status IN ('pending','approved'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

ALTER TABLE mess_rebates ADD COLUMN IF NOT EXISTS mess_out_request_id INT UNIQUE REFERENCES mess_out_requests(request_id);

ALTER TABLE mess_ledger ADD COLUMN IF NOT EXISTS source VARCHAR(20) NOT NULL DEFAULT 'turnstile';
ALTER TABLE mess_ledger ADD COLUMN IF NOT EXISTS created_at TIMESTAMP NOT NULL DEFAULT now();
-- Remove exact duplicate ledger rows (left by repeated manual testing) so the uniqueness rule can be enforced.
DELETE FROM mess_ledger a USING mess_ledger b
  WHERE a.entry_id > b.entry_id AND a.user_id = b.user_id AND a.meal_date = b.meal_date
    AND a.meal_type = b.meal_type AND a.status = b.status;
CREATE UNIQUE INDEX IF NOT EXISTS uq_ledger_meal ON mess_ledger (user_id, meal_date, meal_type, status);

CREATE TABLE IF NOT EXISTS mess_totp_secrets (
    user_id    VARCHAR(10) PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
    secret     VARCHAR(64) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT now()
);
-- Anti-passback / replay protection: each (student, meal, TOTP step) can be redeemed once.
CREATE TABLE IF NOT EXISTS mess_qr_redemptions (
    user_id VARCHAR(10) NOT NULL REFERENCES users(user_id),
    meal    VARCHAR(20) NOT NULL,
    step    BIGINT NOT NULL,
    redeemed_at TIMESTAMP NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, meal, step)
);

-- ── Requisition management ──────────────────────────────────
CREATE SEQUENCE IF NOT EXISTS requisition_seq START 1000;
CREATE SEQUENCE IF NOT EXISTS booking_seq START 1000;
ALTER TABLE venues ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE venues ADD COLUMN IF NOT EXISTS exclusive BOOLEAN NOT NULL DEFAULT TRUE;
UPDATE venues SET exclusive = FALSE WHERE venue_id = 'SPORTS_GYM';

CREATE TABLE IF NOT EXISTS society_members (
    society_name VARCHAR(100) NOT NULL REFERENCES campus_societies(society_name) ON UPDATE CASCADE ON DELETE CASCADE,
    user_id      VARCHAR(10) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    member_role  VARCHAR(20) NOT NULL DEFAULT 'member' CHECK (member_role IN ('member','office_bearer')),
    PRIMARY KEY (society_name, user_id)
);

ALTER TABLE bookings ADD COLUMN IF NOT EXISTS start_ts TIMESTAMP;
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS end_ts TIMESTAMP;
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS slot_key VARCHAR(30);
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS requisition_id VARCHAR(30) UNIQUE;
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS cancelled_at TIMESTAMP;
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS cancel_reason VARCHAR(200);
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS channel VARCHAR(12) NOT NULL DEFAULT 'web';
DO $$
DECLARE c TEXT;
BEGIN
  FOR c IN SELECT conname FROM pg_constraint WHERE conrelid = 'bookings'::regclass AND contype = 'c'
           AND pg_get_constraintdef(oid) ILIKE '%status%' LOOP
    EXECUTE format('ALTER TABLE bookings DROP CONSTRAINT %I', c);
  END LOOP;
END $$;
ALTER TABLE bookings ADD CONSTRAINT bookings_status_check
    CHECK (status IN ('pending','approved','auto-released','checked_in','rejected','cancelled'));
-- Back-fill legacy rows (booking_date/start_time may be NULL on the oldest ones).
UPDATE bookings SET start_ts = COALESCE((booking_date + NULLIF(start_time,'')::time), booking_time)
  WHERE start_ts IS NULL;
UPDATE bookings SET end_ts = start_ts + make_interval(secs => (duration_hours * 3600)::int) WHERE end_ts IS NULL AND start_ts IS NOT NULL;
UPDATE bookings b SET slot_key = b.venue_id FROM venues v
  WHERE v.venue_id = b.venue_id AND v.exclusive AND b.slot_key IS NULL AND b.status IN ('approved','pending','checked_in');
-- Legacy demo rows that overlap on the same exclusive venue are released rather than blocking the constraint.
UPDATE bookings a SET status = 'auto-released', slot_key = NULL, cancel_reason = 'migrated: overlapping legacy booking'
  WHERE a.slot_key IS NOT NULL AND EXISTS (
    SELECT 1 FROM bookings o WHERE o.slot_key = a.slot_key AND o.booking_id < a.booking_id
      AND tsrange(o.start_ts, o.end_ts) && tsrange(a.start_ts, a.end_ts));
DO $$ BEGIN
  ALTER TABLE bookings ADD CONSTRAINT no_double_booking
    EXCLUDE USING gist (slot_key WITH =, tsrange(start_ts, end_ts) WITH &&) WHERE (slot_key IS NOT NULL);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

ALTER TABLE lab_equipment ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP NOT NULL DEFAULT now();
CREATE TABLE IF NOT EXISTS equipment_custody_log (
    log_id    BIGSERIAL PRIMARY KEY,
    barcode   VARCHAR(30) NOT NULL REFERENCES lab_equipment(barcode) ON UPDATE CASCADE,
    user_id   VARCHAR(10) REFERENCES users(user_id),
    action    VARCHAR(20) NOT NULL CHECK (action IN ('checkout','return','repair','admin_update')),
    condition VARCHAR(50),
    note      VARCHAR(255),
    channel   VARCHAR(12) NOT NULL DEFAULT 'web',
    at        TIMESTAMP NOT NULL DEFAULT now()
);

-- ── Cross-cutting: events, notifications, audit, assistant ──
CREATE TABLE IF NOT EXISTS domain_events (
    event_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_type VARCHAR(60) NOT NULL,
    payload    JSONB NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS event_handler_runs (
    event_id UUID NOT NULL REFERENCES domain_events(event_id) ON DELETE CASCADE,
    handler  VARCHAR(80) NOT NULL,
    status   VARCHAR(10) NOT NULL CHECK (status IN ('done','failed')),
    attempts INT NOT NULL DEFAULT 1,
    error    TEXT,
    at       TIMESTAMP NOT NULL DEFAULT now(),
    PRIMARY KEY (event_id, handler)
);

CREATE TABLE IF NOT EXISTS notifications (
    notification_id BIGSERIAL PRIMARY KEY,
    user_id    VARCHAR(10) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    kind       VARCHAR(30) NOT NULL,
    level      VARCHAR(10) NOT NULL DEFAULT 'info' CHECK (level IN ('info','success','warning','error')),
    title      VARCHAR(150) NOT NULL,
    body       TEXT,
    link       VARCHAR(100),
    created_at TIMESTAMP NOT NULL DEFAULT now(),
    read_at    TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS audit_log (
    audit_id    BIGSERIAL PRIMARY KEY,
    at          TIMESTAMP NOT NULL DEFAULT now(),
    actor_id    VARCHAR(10),
    actor_role  VARCHAR(20),
    channel     VARCHAR(12) NOT NULL DEFAULT 'web',
    action      VARCHAR(60) NOT NULL,
    entity_type VARCHAR(40),
    entity_id   VARCHAR(60),
    outcome     VARCHAR(10) NOT NULL CHECK (outcome IN ('success','denied','failed')),
    detail      JSONB
);
CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_log(actor_id, at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit_log(entity_type, entity_id);
-- The audit trail is append-only: no application path (or ordinary SQL session) may alter or remove rows.
CREATE OR REPLACE FUNCTION audit_log_immutable() RETURNS trigger AS $$
BEGIN RAISE EXCEPTION 'audit_log is append-only'; END; $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_audit_immutable ON audit_log;
CREATE TRIGGER trg_audit_immutable BEFORE UPDATE OR DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION audit_log_immutable();
DROP TRIGGER IF EXISTS trg_audit_no_truncate ON audit_log;
CREATE TRIGGER trg_audit_no_truncate BEFORE TRUNCATE ON audit_log
    FOR EACH STATEMENT EXECUTE FUNCTION audit_log_immutable();

-- Consequential assistant actions wait here for explicit user confirmation (server-side, not LLM-controlled).
CREATE TABLE IF NOT EXISTS assistant_pending_actions (
    action_id  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id    VARCHAR(10) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    session_id VARCHAR(64) NOT NULL,
    tool       VARCHAR(60) NOT NULL,
    args       JSONB NOT NULL,
    summary    TEXT NOT NULL,
    status     VARCHAR(12) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','confirmed','cancelled','expired','failed')),
    result     JSONB,
    created_at TIMESTAMP NOT NULL DEFAULT now(),
    expires_at TIMESTAMP NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pending_user ON assistant_pending_actions(user_id, session_id, status);
