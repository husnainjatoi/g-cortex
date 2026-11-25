-- Monthly food expenditure is ONE active record per billing month. Corrections never overwrite: the old row is kept as 'superseded'.
ALTER TABLE mess_expenses ADD COLUMN IF NOT EXISTS status VARCHAR(12) NOT NULL DEFAULT 'active' CHECK (status IN ('active','superseded'));
ALTER TABLE mess_expenses ADD COLUMN IF NOT EXISTS supersedes_id INTEGER REFERENCES mess_expenses(expense_id);
ALTER TABLE mess_expenses ADD COLUMN IF NOT EXISTS correction_reason VARCHAR(300);
CREATE UNIQUE INDEX IF NOT EXISTS uq_mess_expenses_one_active_per_month ON mess_expenses(period) WHERE status='active';
