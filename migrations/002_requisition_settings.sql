INSERT INTO system_settings (key, value, description) VALUES
 ('requisition_notice_days','2','Minimum days of notice for a society event requisition'),
 ('requisition_max_hours','8','Maximum event duration in hours'),
 ('requisition_max_pending','3','Maximum open requisitions per student'),
 ('sports_checkin_early_minutes','10','How many minutes before start a check-in PIN is accepted')
ON CONFLICT DO NOTHING;
-- Society memberships (who may file requisitions for which society) - seeded from existing data only:
-- applicants of existing requisitions are treated as office bearers of the society they applied for.
INSERT INTO society_members (society_name, user_id, member_role)
  SELECT DISTINCT society_name, applicant_id, 'office_bearer' FROM society_requisitions
  WHERE society_name IN (SELECT society_name FROM campus_societies)
ON CONFLICT DO NOTHING;
