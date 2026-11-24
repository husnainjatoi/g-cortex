-- Monthly mess billing + refundable security deposit. Purely additive: the legacy rebate tables
-- (mess_rebates, 'rebated' ledger rows, est_rebate_pkr) are kept read-only for audit and are no longer written.
ALTER TABLE mess_out_requests ALTER COLUMN est_rebate_pkr DROP NOT NULL;
ALTER TABLE mess_out_requests ALTER COLUMN est_rebate_pkr SET DEFAULT NULL;
COMMENT ON TABLE mess_rebates IS 'LEGACY (read-only): rebate model retired; retained for audit of historical financial records.';

DELETE FROM system_settings WHERE key IN ('mess_daily_rate','mess_min_days','mess_notice_days');
INSERT INTO system_settings (key, value, description) VALUES
 ('mess_billing_mode','per_meal','Billing basis: per_meal (rate per consumed meal) or per_day (one daily rate per day with any consumed meal)'),
 ('mess_deposit_amount','0','Refundable mess security deposit per student in PKR (0 = not configured yet)'),
 ('mess_invoice_due_days','10','Days after invoice generation by which payment is due')
ON CONFLICT DO NOTHING;

-- Configurable charges. No prices are seeded: administrators must configure them. Rates are versioned by effective date.
CREATE TABLE IF NOT EXISTS mess_rates (
    rate_id        SERIAL PRIMARY KEY,
    meal_type      VARCHAR(20) NOT NULL CHECK (meal_type IN ('breakfast','lunch','dinner','daily')),
    rate_pkr       NUMERIC(10,2) NOT NULL CHECK (rate_pkr >= 0),
    effective_from DATE NOT NULL,
    set_by         VARCHAR(10) REFERENCES users(user_id),
    created_at     TIMESTAMP NOT NULL DEFAULT now(),
    UNIQUE (meal_type, effective_from)
);

CREATE TABLE IF NOT EXISTS mess_invoices (
    invoice_id     SERIAL PRIMARY KEY,
    invoice_no     VARCHAR(30) NOT NULL UNIQUE,
    user_id        VARCHAR(10) NOT NULL REFERENCES users(user_id),
    billing_month  DATE NOT NULL CHECK (billing_month = date_trunc('month', billing_month)::date),
    days_count     INT NOT NULL,
    meals_breakfast INT NOT NULL DEFAULT 0,
    meals_lunch    INT NOT NULL DEFAULT 0,
    meals_dinner   INT NOT NULL DEFAULT 0,
    billing_mode   VARCHAR(10) NOT NULL,
    lines          JSONB NOT NULL,
    amount_pkr     NUMERIC(12,2) NOT NULL CHECK (amount_pkr >= 0),
    status         VARCHAR(10) NOT NULL DEFAULT 'unpaid' CHECK (status IN ('unpaid','partial','paid')),
    generated_at   TIMESTAMP NOT NULL DEFAULT now(),
    generated_by   VARCHAR(10),
    due_date       DATE NOT NULL,
    UNIQUE (user_id, billing_month)
);
CREATE INDEX IF NOT EXISTS idx_mess_invoices_user ON mess_invoices(user_id, billing_month DESC);

-- A finalized invoice's amount and consumption snapshot can never be edited; corrections are signed adjustment rows.
CREATE OR REPLACE FUNCTION mess_invoice_immutable() RETURNS trigger AS $$
BEGIN
  IF NEW.amount_pkr <> OLD.amount_pkr OR NEW.days_count <> OLD.days_count OR NEW.lines::text <> OLD.lines::text
     OR NEW.billing_month <> OLD.billing_month OR NEW.user_id <> OLD.user_id OR NEW.invoice_no <> OLD.invoice_no
     OR NEW.meals_breakfast <> OLD.meals_breakfast OR NEW.meals_lunch <> OLD.meals_lunch OR NEW.meals_dinner <> OLD.meals_dinner THEN
    RAISE EXCEPTION 'finalized mess invoices are immutable; use an authorized adjustment';
  END IF;
  RETURN NEW;
END; $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_mess_invoice_immutable ON mess_invoices;
CREATE TRIGGER trg_mess_invoice_immutable BEFORE UPDATE ON mess_invoices FOR EACH ROW EXECUTE FUNCTION mess_invoice_immutable();
CREATE OR REPLACE FUNCTION mess_invoice_no_delete() RETURNS trigger AS $$
BEGIN RAISE EXCEPTION 'mess invoices cannot be deleted'; END; $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_mess_invoice_no_delete ON mess_invoices;
CREATE TRIGGER trg_mess_invoice_no_delete BEFORE DELETE ON mess_invoices FOR EACH ROW EXECUTE FUNCTION mess_invoice_no_delete();

CREATE TABLE IF NOT EXISTS mess_invoice_adjustments (
    adjustment_id SERIAL PRIMARY KEY,
    invoice_id    INT NOT NULL REFERENCES mess_invoices(invoice_id),
    amount_pkr    NUMERIC(12,2) NOT NULL CHECK (amount_pkr <> 0),
    reason        VARCHAR(255) NOT NULL,
    adjusted_by   VARCHAR(10) NOT NULL REFERENCES users(user_id),
    at            TIMESTAMP NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS mess_challans (
    challan_no  VARCHAR(40) PRIMARY KEY,
    invoice_id  INT NOT NULL REFERENCES mess_invoices(invoice_id),
    user_id     VARCHAR(10) NOT NULL REFERENCES users(user_id),
    amount_pkr  NUMERIC(12,2) NOT NULL,
    issued_at   TIMESTAMP NOT NULL DEFAULT now(),
    due_date    DATE NOT NULL
);

CREATE TABLE IF NOT EXISTS mess_payments (
    payment_id   SERIAL PRIMARY KEY,
    invoice_id   INT NOT NULL REFERENCES mess_invoices(invoice_id),
    user_id      VARCHAR(10) NOT NULL REFERENCES users(user_id),
    amount_pkr   NUMERIC(12,2) NOT NULL CHECK (amount_pkr > 0),
    method       VARCHAR(20) NOT NULL DEFAULT 'bank_challan' CHECK (method IN ('bank_challan','cash_office','deposit_settlement')),
    reference    VARCHAR(80) NOT NULL,
    challan_no   VARCHAR(40) REFERENCES mess_challans(challan_no),
    note         VARCHAR(255),
    status       VARCHAR(10) NOT NULL DEFAULT 'submitted' CHECK (status IN ('submitted','verified','rejected')),
    submitted_at TIMESTAMP NOT NULL DEFAULT now(),
    verified_by  VARCHAR(10) REFERENCES users(user_id),
    verified_at  TIMESTAMP,
    verify_note  VARCHAR(255),
    channel      VARCHAR(12) NOT NULL DEFAULT 'web'
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_mess_payment_ref ON mess_payments(reference) WHERE status <> 'rejected';

CREATE TABLE IF NOT EXISTS mess_deposit_accounts (
    user_id             VARCHAR(10) PRIMARY KEY REFERENCES users(user_id),
    required_amount_pkr NUMERIC(12,2) NOT NULL CHECK (required_amount_pkr > 0),
    created_at          TIMESTAMP NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS mess_deposit_transactions (
    txn_id        SERIAL PRIMARY KEY,
    user_id       VARCHAR(10) NOT NULL REFERENCES users(user_id),
    kind          VARCHAR(20) NOT NULL CHECK (kind IN ('deposit_payment','settlement_deduction','refund')),
    amount_pkr    NUMERIC(12,2) NOT NULL CHECK (amount_pkr > 0),
    reference     VARCHAR(80),
    status        VARCHAR(10) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','verified','rejected')),
    note          VARCHAR(255),
    clearance_id  INT,
    recorded_by   VARCHAR(10) REFERENCES users(user_id),
    recorded_at   TIMESTAMP NOT NULL DEFAULT now(),
    verified_by   VARCHAR(10) REFERENCES users(user_id),
    verified_at   TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_deposit_txn_user ON mess_deposit_transactions(user_id, recorded_at);
CREATE UNIQUE INDEX IF NOT EXISTS uq_deposit_ref ON mess_deposit_transactions(reference) WHERE kind='deposit_payment' AND status <> 'rejected' AND reference IS NOT NULL;

CREATE TABLE IF NOT EXISTS mess_clearances (
    clearance_id        SERIAL PRIMARY KEY,
    user_id             VARCHAR(10) NOT NULL REFERENCES users(user_id),
    status              VARCHAR(10) NOT NULL DEFAULT 'requested' CHECK (status IN ('requested','approved','settled','returned','rejected')),
    requested_at        TIMESTAMP NOT NULL DEFAULT now(),
    requested_by        VARCHAR(10) NOT NULL REFERENCES users(user_id),
    deposit_balance_pkr NUMERIC(12,2),
    outstanding_pkr     NUMERIC(12,2),
    deductions_pkr      NUMERIC(12,2),
    refundable_pkr      NUMERIC(12,2),
    additional_due_pkr  NUMERIC(12,2),
    reviewed_by         VARCHAR(10) REFERENCES users(user_id),
    reviewed_at         TIMESTAMP,
    settled_by          VARCHAR(10) REFERENCES users(user_id),
    settled_at          TIMESTAMP,
    return_reference    VARCHAR(80),
    returned_at         TIMESTAMP,
    note                VARCHAR(255)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_open_clearance ON mess_clearances(user_id) WHERE status IN ('requested','approved','settled');
