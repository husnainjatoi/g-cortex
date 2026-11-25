-- Semester-wise curricula from the official prospectus: tracks, split lab rows, co-requisites, demo progress, minor course pools.
ALTER TABLE program_courses ADD COLUMN IF NOT EXISTS track_id INT REFERENCES program_tracks(track_id) ON DELETE CASCADE;
ALTER TABLE program_courses ADD COLUMN IF NOT EXISTS coreq_text TEXT;
ALTER TABLE program_courses ADD COLUMN IF NOT EXISTS lab_of VARCHAR(10);                  -- set on a lab row that belongs to a lecture course
ALTER TABLE program_courses ADD COLUMN IF NOT EXISTS credit_derived BOOLEAN NOT NULL DEFAULT FALSE;   -- lab credit derived from a combined lecture+lab credit figure (3 lab hours = 1 credit)
ALTER TABLE program_courses ADD COLUMN IF NOT EXISTS slot_code VARCHAR(20);                -- printed placeholder such as HM3XX or AI/CX3xx
ALTER TABLE program_courses ADD COLUMN IF NOT EXISTS prereq_status VARCHAR(10);            -- none | parsed | ambiguous | varies
DROP INDEX IF EXISTS uq_program_course;
DROP INDEX IF EXISTS uq_program_slot;
CREATE UNIQUE INDEX IF NOT EXISTS uq_program_course ON program_courses(program_id, COALESCE(track_id, 0), semester, course_code) WHERE course_code IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_program_slot ON program_courses(program_id, COALESCE(track_id, 0), semester, title) WHERE course_code IS NULL;
ALTER TABLE academic_programs ADD COLUMN IF NOT EXISTS review_notes JSONB NOT NULL DEFAULT '[]';
ALTER TABLE degree_minors ADD COLUMN IF NOT EXISTS course_pool TEXT;                       -- courses the official document lists for the minor (when no fixed requirement is stated)
ALTER TABLE degree_minors ADD COLUMN IF NOT EXISTS requirements_stated BOOLEAN NOT NULL DEFAULT TRUE;
-- Current-semester courses of designated demo students: an in-progress marker, NOT a registration (no offering/timetable is implied).
CREATE TABLE IF NOT EXISTS student_course_progress (
    user_id     VARCHAR(10) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    program_course_id INT NOT NULL REFERENCES program_courses(id) ON DELETE CASCADE,
    status      VARCHAR(12) NOT NULL DEFAULT 'in_progress' CHECK (status IN ('in_progress')),
    basis       VARCHAR(20) NOT NULL DEFAULT 'demo_assumption',
    created_at  TIMESTAMP NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, program_course_id)
);
