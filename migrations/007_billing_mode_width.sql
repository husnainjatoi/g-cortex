-- 'shared_cost' (11 chars) does not fit the original VARCHAR(10) billing_mode column.
ALTER TABLE mess_invoices ALTER COLUMN billing_mode TYPE VARCHAR(20);
