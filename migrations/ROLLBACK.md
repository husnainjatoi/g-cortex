# Rollback / recovery notes

Migrations are forward-only and **additive** (no existing column, table or row of the original project is dropped or rewritten, apart from the
documented removal of exact-duplicate `mess_ledger` rows in 001 and the retired rebate *settings* in 003).

Before applying a migration to a database that holds real data, take a dump:

    pg_dump "$DATABASE_URL" > gcortex_before_migration.sql

To recover, restore that dump into an empty database (`psql -f gcortex_before_migration.sql`). To undo only the additions of a migration
without restoring, these statements reverse it (no data from the original application lives in these objects):

* **005** `ALTER TABLE knowledge_sources DROP COLUMN full_text;`
* **004** `DROP TABLE course_import_flags, course_sources, knowledge_runs, knowledge_chunks, knowledge_sources;`
  `ALTER TABLE courses DROP COLUMN catalog_only, DROP COLUMN department, DROP COLUMN lecture_hours, DROP COLUMN lab_hours;`
  (catalogue-only courses imported from GIKI must be deleted first: `DELETE FROM courses WHERE catalog_only;` after removing their prerequisite rows)
* **003** `DROP TABLE mess_clearances, mess_deposit_transactions, mess_deposit_accounts, mess_payments, mess_challans, mess_invoice_adjustments, mess_invoices, mess_rates;`
  Invoices are financial records and carry a delete-blocking trigger by design - export them before dropping. The legacy rebate data
  (`mess_rebates`, `mess_out_requests.est_rebate_pkr`, `rebated` ledger rows) was left untouched, so no rebate history is lost.

The retired settings (`mess_daily_rate`, `mess_min_days`, `mess_notice_days`) were configuration only and can be re-inserted into
`system_settings` if ever needed.
