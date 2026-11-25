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

## 006 – 009 (rounds 2-3)
Take a dump first (`pg_dump "$DATABASE_URL" > before.sql`). To undo only the additions:
* **009** `DROP TABLE student_course_progress; ALTER TABLE degree_minors DROP COLUMN course_pool, DROP COLUMN requirements_stated; ALTER TABLE academic_programs DROP COLUMN review_notes;`
  `DROP INDEX uq_program_course, uq_program_slot;` then `ALTER TABLE program_courses DROP COLUMN track_id, DROP COLUMN coreq_text, DROP COLUMN lab_of, DROP COLUMN credit_derived, DROP COLUMN slot_code, DROP COLUMN prereq_status;`
  and re-create the two original unique indexes from 006. Imported official courses can be removed with `DELETE FROM courses WHERE origin='official' AND catalog_only AND course_code NOT IN (SELECT course_code FROM course_history)` after deleting their prerequisite/source rows. Demo progress rows: `DELETE FROM course_history WHERE term='Demo assumption'`.
* **008** `DROP INDEX uq_mess_expenses_one_active_per_month; ALTER TABLE mess_expenses DROP COLUMN status, DROP COLUMN supersedes_id, DROP COLUMN correction_reason;` (expenditure history is a financial record: export `mess_expenses` first).
* **006/007** added societies, reservation types, document-version columns, `academic_programs`, `program_courses`, `program_tracks`, the shared-cost billing mode and the 15,000 deposit records; they only add objects and rows, nothing of the original schema is altered. Remove the deposit rows with `DELETE FROM mess_deposit_transactions WHERE reference LIKE 'ADMISSION-%'` if ever required.
