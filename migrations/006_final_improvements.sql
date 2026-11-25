-- Round 3: identity (DOB), societies + reservation types, shared-cost mess billing/expenses, 15,000 PKR deposit init,
-- document versioning for official sources, academic programmes / semester-wise curricula / official minors. Additive only.

-- ── Identity ────────────────────────────────────────────────
ALTER TABLE users ADD COLUMN IF NOT EXISTS date_of_birth DATE;

-- ── Societies (official list is imported by app.services.societies) ──
CREATE TABLE IF NOT EXISTS societies (
    society_id     SERIAL PRIMARY KEY,
    name           VARCHAR(200) NOT NULL,
    name_key       VARCHAR(200) NOT NULL UNIQUE,         -- normalised name: prevents duplicates when the importer re-runs
    short_name     VARCHAR(40),
    description    TEXT,
    category       VARCHAR(50),
    logo_url       TEXT,
    source_url     TEXT,
    status         VARCHAR(10) NOT NULL DEFAULT 'active' CHECK (status IN ('active','inactive')),
    origin         VARCHAR(10) NOT NULL DEFAULT 'official' CHECK (origin IN ('official','manual','legacy')),
    advisor_id     VARCHAR(10) REFERENCES users(user_id),
    manually_edited BOOLEAN NOT NULL DEFAULT FALSE,        -- admin edits are never overwritten by a later import
    retrieved_at   TIMESTAMP,
    last_checked_at TIMESTAMP,
    created_at     TIMESTAMP NOT NULL DEFAULT now()
);
-- Pre-existing team-seeded societies are kept (history/requisitions reference them) but marked as legacy, not official.
INSERT INTO societies (name, name_key, category, origin, advisor_id)
  SELECT society_name, lower(regexp_replace(society_name, '[^a-zA-Z0-9]+', '', 'g')), category, 'legacy', advisor_id FROM campus_societies
ON CONFLICT (name_key) DO NOTHING;

CREATE TABLE IF NOT EXISTS society_memberships (
    society_id  INT NOT NULL REFERENCES societies(society_id) ON DELETE CASCADE,
    user_id     VARCHAR(10) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    member_role VARCHAR(20) NOT NULL DEFAULT 'member' CHECK (member_role IN ('member','office_bearer')),
    PRIMARY KEY (society_id, user_id)
);
INSERT INTO society_memberships (society_id, user_id, member_role)
  SELECT s.society_id, m.user_id, m.member_role FROM society_members m JOIN societies s ON s.name_key = lower(regexp_replace(m.society_name, '[^a-zA-Z0-9]+', '', 'g'))
ON CONFLICT DO NOTHING;

ALTER TABLE society_requisitions ALTER COLUMN society_name DROP NOT NULL;
ALTER TABLE society_requisitions DROP CONSTRAINT IF EXISTS society_requisitions_society_name_fkey;
ALTER TABLE society_requisitions ADD COLUMN IF NOT EXISTS reservation_type VARCHAR(12) NOT NULL DEFAULT 'society' CHECK (reservation_type IN ('individual','society'));
ALTER TABLE society_requisitions ADD COLUMN IF NOT EXISTS society_id INT REFERENCES societies(society_id);
ALTER TABLE society_requisitions ADD COLUMN IF NOT EXISTS authority_verified BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE society_requisitions ADD COLUMN IF NOT EXISTS description TEXT;
UPDATE society_requisitions r SET society_id = s.society_id FROM societies s WHERE r.society_id IS NULL AND r.society_name IS NOT NULL AND s.name_key = lower(regexp_replace(r.society_name, '[^a-zA-Z0-9]+', '', 'g'));
CREATE INDEX IF NOT EXISTS idx_requisitions_society ON society_requisitions(society_id);

-- ── Mess: shared-cost estimate and invoices ─────────────────
CREATE TABLE IF NOT EXISTS mess_expenses (
    expense_id  SERIAL PRIMARY KEY,
    period      DATE NOT NULL CHECK (period = date_trunc('month', period)::date),
    category    VARCHAR(60) NOT NULL DEFAULT 'food',
    description VARCHAR(200),
    amount_pkr  NUMERIC(14,2) NOT NULL CHECK (amount_pkr > 0),
    recorded_by VARCHAR(10) REFERENCES users(user_id),
    recorded_at TIMESTAMP NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_mess_expenses_period ON mess_expenses(period);
ALTER TABLE mess_invoices DROP CONSTRAINT IF EXISTS mess_invoices_billing_mode_check;
UPDATE system_settings SET value='shared_cost', description='Billing basis: shared_cost (total food expenditure / students using the mess), per_meal or per_day (administrator-configured rates)'
  WHERE key='mess_billing_mode' AND value='per_meal';

-- Security deposit: PKR 15,000, paid at admission. Every existing student gets exactly one account + one verified admission payment.
UPDATE system_settings SET value='15000', description='Refundable mess security deposit per student in PKR (paid at admission)' WHERE key='mess_deposit_amount' AND value='0';
INSERT INTO mess_deposit_accounts (user_id, required_amount_pkr)
  SELECT user_id, 15000 FROM users WHERE role='student' AND NOT EXISTS (SELECT 1 FROM mess_deposit_accounts a WHERE a.user_id = users.user_id)
ON CONFLICT DO NOTHING;
INSERT INTO mess_deposit_transactions (user_id, kind, amount_pkr, reference, status, note, recorded_at, verified_at)
  SELECT u.user_id, 'deposit_payment', 15000, 'ADMISSION-' || u.user_id, 'verified', 'Paid at admission (project assumption for existing accounts)', now(), now()
  FROM users u JOIN mess_deposit_accounts a ON a.user_id = u.user_id
  WHERE u.role='student' AND a.required_amount_pkr = 15000
    AND NOT EXISTS (SELECT 1 FROM mess_deposit_transactions t WHERE t.user_id = u.user_id AND t.kind='deposit_payment' AND t.status <> 'rejected')
    AND NOT EXISTS (SELECT 1 FROM mess_deposit_transactions t WHERE t.reference = 'ADMISSION-' || u.user_id);

-- ── Source versioning for official documents ────────────────
ALTER TABLE knowledge_sources ADD COLUMN IF NOT EXISTS series VARCHAR(40);            -- e.g. ug_prospectus, student_handbook
ALTER TABLE knowledge_sources ADD COLUMN IF NOT EXISTS version_label VARCHAR(40);
ALTER TABLE knowledge_sources ADD COLUMN IF NOT EXISTS published_at DATE;
ALTER TABLE knowledge_sources ADD COLUMN IF NOT EXISTS published_precision VARCHAR(8);  -- 'day' | 'month'
ALTER TABLE knowledge_sources ADD COLUMN IF NOT EXISTS landing_url TEXT;
ALTER TABLE knowledge_sources ADD COLUMN IF NOT EXISTS superseded_by INT REFERENCES knowledge_sources(source_id);
ALTER TABLE knowledge_sources DROP CONSTRAINT IF EXISTS knowledge_sources_status_check;
ALTER TABLE knowledge_sources ADD CONSTRAINT knowledge_sources_status_check CHECK (status IN ('active','failed','removed','superseded'));

ALTER TABLE courses ADD COLUMN IF NOT EXISTS origin VARCHAR(10) NOT NULL DEFAULT 'manual' CHECK (origin IN ('manual','official'));   -- 'official' rows may be refreshed from a newer official document
CREATE TABLE IF NOT EXISTS course_source_history (
    history_id   SERIAL PRIMARY KEY,
    course_code  VARCHAR(10) NOT NULL,
    source_id    INT REFERENCES knowledge_sources(source_id),
    source_url   TEXT NOT NULL,
    source_title TEXT,
    source_page  INT,
    official_title TEXT,
    official_credits INT,
    official_prereq_text TEXT,
    retrieved_at TIMESTAMP,
    superseded_at TIMESTAMP NOT NULL DEFAULT now(),
    superseded_by_url TEXT
);

-- ── Academic programmes, semester-wise curricula, official minors ──
CREATE TABLE IF NOT EXISTS academic_programs (
    program_id   SERIAL PRIMARY KEY,
    code         VARCHAR(12) NOT NULL UNIQUE,
    name         VARCHAR(160) NOT NULL,
    faculty      VARCHAR(160),
    level        VARCHAR(14) NOT NULL DEFAULT 'undergraduate',
    source_id    INT REFERENCES knowledge_sources(source_id),
    source_url   TEXT,
    source_page  INT,
    status       VARCHAR(10) NOT NULL DEFAULT 'active' CHECK (status IN ('active','inactive')),
    plan_status  VARCHAR(12) NOT NULL DEFAULT 'parsed' CHECK (plan_status IN ('parsed','needs_review','no_plan')),
    retrieved_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS program_courses (
    id           SERIAL PRIMARY KEY,
    program_id   INT NOT NULL REFERENCES academic_programs(program_id) ON DELETE CASCADE,
    semester     INT NOT NULL CHECK (semester BETWEEN 1 AND 10),
    course_code  VARCHAR(10) REFERENCES courses(course_code) ON UPDATE CASCADE,
    title        VARCHAR(160) NOT NULL,
    credit_hours INT NOT NULL,
    lecture_hours INT,
    lab_hours    INT,
    prereq_text  TEXT,
    kind         VARCHAR(10) NOT NULL DEFAULT 'course' CHECK (kind IN ('course','slot')),   -- slot = elective/language placeholder with no fixed code
    source_page  INT
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_program_course ON program_courses(program_id, semester, course_code) WHERE course_code IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_program_slot ON program_courses(program_id, semester, title) WHERE course_code IS NULL;
CREATE TABLE IF NOT EXISTS program_tracks (
    track_id   SERIAL PRIMARY KEY,
    program_id INT NOT NULL REFERENCES academic_programs(program_id) ON DELETE CASCADE,
    name       VARCHAR(160) NOT NULL,
    kind       VARCHAR(12) NOT NULL CHECK (kind IN ('track','thrust_area')),
    source_page INT,
    UNIQUE (program_id, name, kind)
);
ALTER TABLE degree_minors ADD COLUMN IF NOT EXISTS status VARCHAR(14) NOT NULL DEFAULT 'unverified' CHECK (status IN ('official','unverified','needs_review'));
ALTER TABLE degree_minors ADD COLUMN IF NOT EXISTS source_url TEXT;
ALTER TABLE degree_minors ADD COLUMN IF NOT EXISTS source_id INT REFERENCES knowledge_sources(source_id);
ALTER TABLE degree_minors ADD COLUMN IF NOT EXISTS source_page INT;
ALTER TABLE degree_minors ADD COLUMN IF NOT EXISTS offered_by VARCHAR(160);
ALTER TABLE student_profiles ADD COLUMN IF NOT EXISTS program_id INT REFERENCES academic_programs(program_id);
ALTER TABLE student_profiles ADD COLUMN IF NOT EXISTS is_demo BOOLEAN NOT NULL DEFAULT FALSE;
