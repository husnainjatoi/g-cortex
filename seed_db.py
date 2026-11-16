"""
Seed script for the G-Cortex database.
Creates all database tables across Course, Mess, and Requisition subagents
and populates them with initial university data.

Usage:
    python seed_db.py
"""
import os
import json
import psycopg2
from dotenv import load_dotenv

load_dotenv("app/agents/course_management_agent/.env")

DB_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost/gcortex_db")


def seed():
    url = DB_URL.replace("postgresql+psycopg2://", "postgresql://")
    conn = psycopg2.connect(url)
    conn.autocommit = True
    cur = conn.cursor()

    print("[*] Creating all G-Cortex tables...")

    # ── 1. Users & RBAC ───────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id    VARCHAR(10) PRIMARY KEY,
            full_name  VARCHAR(100) NOT NULL,
            role       VARCHAR(20) CHECK (role IN ('student', 'faculty', 'admin')),
            email      VARCHAR(100) UNIQUE NOT NULL
        );
    """)

    # ── 2. Course Management Tables ───────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS courses (
            course_code      VARCHAR(10) PRIMARY KEY,
            title            VARCHAR(100) NOT NULL,
            credits          INT NOT NULL,
            slot             VARCHAR(30),
            category         VARCHAR(30) DEFAULT 'core',
            domains          TEXT,
            policy_embedding BYTEA
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS course_prerequisites (
            course_code VARCHAR(10) REFERENCES courses(course_code),
            prereq_code VARCHAR(10) REFERENCES courses(course_code),
            PRIMARY KEY (course_code, prereq_code)
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS degree_minors (
            minor_name       VARCHAR(100) PRIMARY KEY,
            required_courses TEXT NOT NULL,
            required_credits INT NOT NULL,
            description      TEXT
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS university_policies (
            topic       VARCHAR(50) PRIMARY KEY,
            title       VARCHAR(100) NOT NULL,
            policy_text TEXT NOT NULL
        );
    """)

    # ── 3. Mess Management Tables ─────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS mess_ledger (
            entry_id  SERIAL PRIMARY KEY,
            user_id   VARCHAR(10) REFERENCES users(user_id),
            meal_date DATE NOT NULL,
            meal_type VARCHAR(20) CHECK (meal_type IN ('breakfast', 'lunch', 'dinner')),
            status    VARCHAR(20) CHECK (status IN ('consumed', 'rebated', 'no-show'))
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS gate_passes (
            user_id     VARCHAR(10) PRIMARY KEY REFERENCES users(user_id),
            approved    BOOLEAN DEFAULT false,
            leave_start DATE,
            leave_end   DATE,
            destination VARCHAR(100)
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS mess_rebates (
            rebate_id         VARCHAR(30) PRIMARY KEY,
            user_id           VARCHAR(10) REFERENCES users(user_id),
            start_date        DATE NOT NULL,
            end_date          DATE NOT NULL,
            days_count        INT NOT NULL,
            rebate_amount_pkr NUMERIC(10, 2) NOT NULL,
            status            VARCHAR(20) DEFAULT 'approved',
            gate_pass_synced  BOOLEAN DEFAULT true,
            applied_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS daily_menu (
            meal_period VARCHAR(20) PRIMARY KEY CHECK (meal_period IN ('breakfast', 'lunch', 'dinner')),
            items       TEXT NOT NULL,
            calories    INT NOT NULL,
            protein_g   INT NOT NULL,
            carbs_g     INT NOT NULL,
            fat_g       INT NOT NULL,
            allergens   TEXT NOT NULL
        );
    """)

    # ── 4. Requisition Management Tables ──────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS venues (
            venue_id          VARCHAR(20) PRIMARY KEY,
            name              VARCHAR(100) NOT NULL,
            capacity          INT,
            requires_approval BOOLEAN DEFAULT false,
            category          VARCHAR(50) DEFAULT 'general',
            facilities        TEXT,
            description       TEXT
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS bookings (
            booking_id     SERIAL PRIMARY KEY,
            booking_code   VARCHAR(30) UNIQUE,
            venue_id       VARCHAR(20) REFERENCES venues(venue_id),
            user_id        VARCHAR(10) REFERENCES users(user_id),
            booking_time   TIMESTAMP NOT NULL,
            booking_date   DATE,
            start_time     VARCHAR(10),
            duration_hours NUMERIC(4, 2) DEFAULT 1.0,
            status         VARCHAR(20) CHECK (status IN ('pending', 'approved', 'auto-released', 'checked_in', 'rejected')),
            checkin_token  VARCHAR(20),
            checkin_status VARCHAR(20) DEFAULT 'pending_checkin',
            source         VARCHAR(50),
            created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS campus_societies (
            society_name VARCHAR(100) PRIMARY KEY,
            advisor_id   VARCHAR(10) REFERENCES users(user_id),
            advisor_name VARCHAR(100) NOT NULL,
            category     VARCHAR(50)
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS society_requisitions (
            requisition_id     VARCHAR(30) PRIMARY KEY,
            society_name       VARCHAR(100) REFERENCES campus_societies(society_name),
            venue_id           VARCHAR(20) REFERENCES venues(venue_id),
            applicant_id       VARCHAR(10) REFERENCES users(user_id),
            event_title        VARCHAR(150) NOT NULL,
            booking_date       DATE NOT NULL,
            start_time         VARCHAR(10) NOT NULL,
            duration_hours     NUMERIC(4, 2) DEFAULT 2.0,
            expected_attendees INT,
            justification      TEXT,
            current_tier       INT DEFAULT 1,
            status             VARCHAR(30) DEFAULT 'pending_advisor_approval',
            approval_chain     TEXT,
            created_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS lab_equipment (
            barcode       VARCHAR(30) PRIMARY KEY,
            name          VARCHAR(150) NOT NULL,
            lab           VARCHAR(100) NOT NULL,
            category      VARCHAR(50),
            status        VARCHAR(30) DEFAULT 'available',
            custodian_id  VARCHAR(10) REFERENCES users(user_id),
            checkout_date DATE,
            due_date      DATE,
            condition     VARCHAR(50) DEFAULT 'good',
            purpose       TEXT
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS no_show_records (
            user_id         VARCHAR(10) PRIMARY KEY REFERENCES users(user_id),
            strikes         INT DEFAULT 0,
            suspended       BOOLEAN DEFAULT false,
            suspended_until DATE,
            strike_history  TEXT
        );
    """)

    # ── Ensure columns exist on pre-existing tables ────────────
    cur.execute("""
        ALTER TABLE courses ADD COLUMN IF NOT EXISTS slot VARCHAR(30);
        ALTER TABLE courses ADD COLUMN IF NOT EXISTS category VARCHAR(30) DEFAULT 'core';
        ALTER TABLE courses ADD COLUMN IF NOT EXISTS domains TEXT;

        ALTER TABLE venues ADD COLUMN IF NOT EXISTS category VARCHAR(50) DEFAULT 'general';
        ALTER TABLE venues ADD COLUMN IF NOT EXISTS facilities TEXT;
        ALTER TABLE venues ADD COLUMN IF NOT EXISTS description TEXT;

        ALTER TABLE bookings ADD COLUMN IF NOT EXISTS booking_code VARCHAR(30);
        ALTER TABLE bookings ADD COLUMN IF NOT EXISTS booking_date DATE;
        ALTER TABLE bookings ADD COLUMN IF NOT EXISTS start_time VARCHAR(10);
        ALTER TABLE bookings ADD COLUMN IF NOT EXISTS duration_hours NUMERIC(4, 2) DEFAULT 1.0;
        ALTER TABLE bookings ADD COLUMN IF NOT EXISTS checkin_token VARCHAR(20);
        ALTER TABLE bookings ADD COLUMN IF NOT EXISTS checkin_status VARCHAR(20) DEFAULT 'pending_checkin';
        ALTER TABLE bookings ADD COLUMN IF NOT EXISTS source VARCHAR(50);
        ALTER TABLE bookings ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;
        CREATE UNIQUE INDEX IF NOT EXISTS idx_bookings_code ON bookings (booking_code);
    """)

    print("[*] Seeding data across all domains...")

    # ── Seed Users ────────────────────────────────────────────
    cur.execute("""
        INSERT INTO users (user_id, full_name, role, email) VALUES
            ('2024405', 'Muhammad Husnain Jatoi', 'student', 'u2024405@giki.edu.pk'),
            ('2024668', 'Zahra Aliabbas',         'student', 'u2024668@giki.edu.pk'),
            ('2024662', 'Warda Ammad',            'student', 'u2024662@giki.edu.pk'),
            ('FAC001',  'Farah Saeed',            'faculty', 'farah.saeed@giki.edu.pk'),
            ('FAC002',  'Tariq Mahmood',          'faculty', 'tariq.mahmood@giki.edu.pk'),
            ('FAC003',  'Dr. Masroor',            'faculty', 'dr.masroor@giki.edu.pk'),
            ('ADMIN01', 'Directorate of Student Affairs', 'admin', 'dsa@giki.edu.pk')
        ON CONFLICT DO NOTHING;
    """)

    # ── Seed Courses ──────────────────────────────────────────
    cur.execute("""
        INSERT INTO courses (course_code, title, credits, slot, category, domains) VALUES
            ('CS101', 'Introduction to Computing',           3, 'MWF 09:00-10:00', 'core',      'computing, fundamentals, programming'),
            ('CS102', 'Programming Fundamentals',            3, 'TTh 10:00-11:30', 'core',      'programming, c++, problem-solving'),
            ('CS201', 'Data Structures & Algorithms',        4, 'MWF 11:00-12:00', 'core',      'algorithms, data structures, optimization'),
            ('CS232', 'Database Management Systems',         4, 'TTh 14:00-15:30', 'core',      'databases, sql, backend, data modeling'),
            ('CS271', 'Operating Systems',                   3, 'MWF 14:00-15:00', 'core',      'systems, concurrency, linux, memory management'),
            ('CS325', 'Software Engineering',                3, 'MWF 10:00-11:00', 'core',      'software design, agile, architecture, devops'),
            ('CS330', 'Artificial Intelligence',             3, 'TTh 11:30-13:00', 'elective',  'artificial intelligence, search, heuristics, ai agents'),
            ('CS412', 'Machine Learning',                    3, 'MWF 15:00-16:00', 'elective',  'machine learning, deep learning, neural networks, data science'),
            ('EE213', 'Microprocessor Interfacing',          4, 'TTh 09:00-10:30', 'core',      'hardware, embedded, assembly, microcontrollers'),
            ('MT101', 'Calculus I',                          3, 'MWF 08:00-09:00', 'math',      'mathematics, calculus, analysis'),
            ('MT201', 'Linear Algebra',                      3, 'TTh 15:30-17:00', 'math',      'mathematics, linear algebra, matrices, vectors'),
            ('HU101', 'English Composition & Comprehension', 3, 'TTh 08:30-10:00', 'humanities','communication, writing, humanities')
        ON CONFLICT (course_code) DO UPDATE
            SET slot = EXCLUDED.slot, category = EXCLUDED.category, domains = EXCLUDED.domains;
    """)

    # ── Seed Prerequisites ────────────────────────────────────
    cur.execute("""
        INSERT INTO course_prerequisites (course_code, prereq_code) VALUES
            ('CS201', 'CS102'),
            ('CS232', 'CS101'),
            ('CS271', 'CS201'),
            ('CS325', 'CS232'),
            ('CS325', 'CS201'),
            ('CS330', 'CS201'),
            ('CS330', 'MT201'),
            ('CS412', 'CS330'),
            ('CS412', 'MT201')
        ON CONFLICT DO NOTHING;
    """)

    # ── Seed Degree Minors ────────────────────────────────────
    cur.execute("""
        INSERT INTO degree_minors (minor_name, required_courses, required_credits, description) VALUES
            ('Artificial Intelligence', 'CS330,CS412,MT201', 9, 'Specialized minor in intelligent systems, machine learning algorithms, and mathematical modeling.'),
            ('Software Systems',        'CS232,CS271,CS325', 10, 'Focuses on scalable database systems, operating system design, and large-scale software engineering.'),
            ('Embedded & Hardware Systems', 'EE213,CS102,CS271', 10, 'Cross-disciplinary minor covering microprocessor interfacing, embedded firmware, and systems programming.')
        ON CONFLICT (minor_name) DO NOTHING;
    """)

    # ── Seed University Policies ──────────────────────────────
    policies = [
        ("credit_limit", "Credit Hour Limits", "Students can register for a minimum of 12 and a maximum of 21 credit hours per semester. Overload requests (up to 24 credits) require a CGPA of 3.5+ and the Dean's written approval."),
        ("prerequisites", "Prerequisite Regulations", "A student must pass all prerequisite courses with a minimum grade of D before enrolling in the dependent course. Concurrent enrollment in a course and its prerequisite is NOT permitted."),
        ("drop_policy", "Course Drop Policy", "Courses can be dropped without academic penalty (W grade) until the 8th week of the semester. After week 8, a dropped course receives a WF (Withdraw Fail) grade which counts as an F in CGPA calculation."),
        ("repeat_policy", "Course Repeat Rules", "Students may repeat a failed course. Both attempts appear on the transcript, but only the higher grade is counted in the CGPA calculation. A course can be repeated a maximum of two times."),
        ("probation", "Academic Probation", "A student whose semester GPA falls below 2.0 is placed on academic probation. Two consecutive semesters on probation may result in dismissal from the program."),
        ("attendance", "Mandatory Attendance", "A minimum of 80% attendance is mandatory for all courses. Students falling below 75% attendance are debarred from the final examination and automatically receive an F grade."),
        ("mess_rebate", "Mess Rebate Regulations", "Students must apply for mess rebate at least 24 hours before the meal. Rebates are only applicable for leaves of 2 or more consecutive days with verified exit gate-pass."),
        ("venue_booking", "Venue Reservation Rules", "Campus venues can be booked through the requisition system. Bookings for auditoriums and labs require faculty advisor approval. Sports facilities are available on a first-come-first-served basis with a maximum of 2 hours per day per student."),
        ("library", "Library Borrowing Rules", "Library books can be borrowed for 14 days with one renewal allowed. Overdue books incur a fine of Rs. 10 per day. Reference books cannot be taken outside the library."),
        ("hostel", "Hostel Residency Policy", "All undergraduate students are required to reside in university hostels. Room changes are permitted only at the start of each semester with hostel admin approval."),
        ("grading_scale", "Grading Scale & GPA", "GIKI uses a 4.0 GPA scale: A (4.0), A- (3.67), B+ (3.33), B (3.0), B- (2.67), C+ (2.33), C (2.0), C- (1.67), D+ (1.33), D (1.0), F (0.0). A minimum CGPA of 2.0 is required for graduation."),
        ("electives", "Elective Requirements", "Students must complete a minimum of 4 free electives and 2 university electives to fulfil degree requirements. Elective selection opens two weeks before the start of each semester on a first-come-first-served basis.")
    ]
    for p in policies:
        cur.execute("""
            INSERT INTO university_policies (topic, title, policy_text)
            VALUES (%s, %s, %s)
            ON CONFLICT (topic) DO UPDATE SET policy_text = EXCLUDED.policy_text;
        """, p)

    # ── Seed Mess System (Gate Passes, Daily Menu, Rebates) ────
    cur.execute("""
        INSERT INTO gate_passes (user_id, approved, leave_start, leave_end, destination) VALUES
            ('2024668', true,  CURRENT_DATE,     CURRENT_DATE + 4, 'Islamabad'),
            ('2024405', false, NULL,             NULL,             NULL),
            ('2024662', true,  CURRENT_DATE + 2, CURRENT_DATE + 6, 'Peshawar')
        ON CONFLICT (user_id) DO UPDATE SET approved = EXCLUDED.approved;
    """)

    cur.execute("""
        INSERT INTO daily_menu (meal_period, items, calories, protein_g, carbs_g, fat_g, allergens) VALUES
            ('breakfast', 'Paratha, Omelette, Chana Masala, Chai',  650, 22, 68, 31, 'eggs, gluten, dairy'),
            ('lunch',     'Chicken Biryani, Mint Raita, Fresh Salad',820, 38, 95, 28, 'dairy'),
            ('dinner',    'Beef Karahi, Daal Mash, Tandoori Roti',   780, 42, 74, 34, 'gluten')
        ON CONFLICT (meal_period) DO UPDATE
            SET items = EXCLUDED.items, calories = EXCLUDED.calories,
                protein_g = EXCLUDED.protein_g, carbs_g = EXCLUDED.carbs_g,
                fat_g = EXCLUDED.fat_g, allergens = EXCLUDED.allergens;
    """)

    cur.execute("""
        INSERT INTO mess_ledger (user_id, meal_date, meal_type, status) VALUES
            ('2024405', CURRENT_DATE,                      'lunch',     'consumed'),
            ('2024668', CURRENT_DATE + INTERVAL '1 day',   'dinner',    'rebated'),
            ('2024662', CURRENT_DATE,                      'breakfast', 'consumed')
        ON CONFLICT DO NOTHING;
    """)

    cur.execute("""
        INSERT INTO mess_rebates (rebate_id, user_id, start_date, end_date, days_count, rebate_amount_pkr, status, gate_pass_synced) VALUES
            ('REB-101', '2024668', CURRENT_DATE, CURRENT_DATE + 4, 4, 1800.00, 'approved', true)
        ON CONFLICT (rebate_id) DO NOTHING;
    """)

    # ── Seed Venues ───────────────────────────────────────────
    cur.execute("""
        INSERT INTO venues (venue_id, name, capacity, requires_approval, category, facilities, description) VALUES
            ('AHA_AUDI',      'AHA Auditorium',                    300, true,  'auditorium',      'stage, dolby_sound, dual_projectors, tiered_seating', 'Main campus auditorium suited for high-profile symposiums, ceremonies, and guest lectures.'),
            ('SEM_HALL_1',    'Seminar Hall 1',                    120, true,  'seminar_hall',    'projector, podium_mic, acoustic_wall, air_conditioning', 'Acoustic amphitheater hall ideal for technical talks and medium-scale seminars.'),
            ('LAB_MEDS',      'Digital Systems Lab',                40, true,  'lab',             'workstations, fpga_boards, oscilloscopes, lan', 'Hardware and digital systems lab equipped with oscilloscopes and FPGA testbenches.'),
            ('LAB_SE',        'Software Engineering Lab',           45, true,  'lab',             'high_spec_workstations, dual_boot_linux, projector, lan', 'Software engineering lab suited for hackathons and programming competitions.'),
            ('CONF_RM_A',     'Conference Room A',                  25, true,  'conference_room', 'boardroom_table, video_conferencing, presentation_display', 'Executive boardroom for society cabinet meetings and administrative reviews.'),
            ('OAT_MAIN',      'Open Air Theater (OAT)',            500, true,  'outdoor',         'amphitheater_seating, large_stage, power_distribution', 'Open amphitheater designed for annual cultural festivals and student orientations.'),
            ('SPORTS_C1',     'Sports Complex - Badminton Court 1',  4, false, 'sports',          'wooden_parquet_flooring, tournament_net, high_bay_leds', 'Indoor wooden parquet badminton court with standard tournament netting.'),
            ('SPORTS_C2',     'Sports Complex - Badminton Court 2',  4, false, 'sports',          'wooden_parquet_flooring, tournament_net, high_bay_leds', 'Secondary indoor badminton court available for singles and doubles practice.'),
            ('SPORTS_SQ1',    'Sports Complex - Squash Court 1',     2, false, 'sports',          'glass_back_wall, hardwood_court, spectator_gallery', 'Glass-backed championship squash court with observation gallery.'),
            ('SPORTS_SQ2',    'Sports Complex - Squash Court 2',     2, false, 'sports',          'glass_back_wall, hardwood_court, spectator_gallery', 'Regulation squash court for training and casual student matches.'),
            ('SPORTS_TC1',    'Sports Complex - Tennis Court 1',     4, false, 'sports',          'synthetic_hardcourt, floodlights, fencing', 'Outdoor floodlit synthetic hardcourt for singles and doubles tennis.'),
            ('SPORTS_TT1',    'Student Center - Table Tennis Arena', 4, false, 'sports',          'ittf_standard_tables, anti_skid_flooring', 'Table tennis arena situated inside student center with ITTF-certified tables.'),
            ('SPORTS_FUTSAL', 'Main Sports Grounds - Futsal Turf',  14, false, 'sports',          'synthetic_astroturf, perimeter_netting, floodlights', 'All-weather floodlit synthetic astroturf ground for futsal.'),
            ('SPORTS_GYM',    'Sports Complex - Fitness Gymnasium', 30, false, 'sports',          'free_weights, power_racks, cardio_treadmills, air_conditioning', 'Central strength and conditioning gym equipped with Olympic weights.')
        ON CONFLICT (venue_id) DO UPDATE
            SET category = EXCLUDED.category, facilities = EXCLUDED.facilities, description = EXCLUDED.description;
    """)

    # ── Seed Societies ────────────────────────────────────────
    cur.execute("""
        INSERT INTO campus_societies (society_name, advisor_id, advisor_name, category) VALUES
            ('ACM GIKI Chapter',                    'FAC001', 'Farah Saeed',    'technical'),
            ('IEEE Student Branch',                 'FAC001', 'Farah Saeed',    'technical'),
            ('Netronix Networking Society',          'FAC001', 'Farah Saeed',    'technical'),
            ('GIKI Sports Club',                    'FAC002', 'Tariq Mahmood',  'sports'),
            ('Literary and Performing Arts Society','FAC003', 'Dr. Masroor',    'cultural'),
            ('Women in Engineering (WIE)',          'FAC001', 'Farah Saeed',    'technical')
        ON CONFLICT (society_name) DO NOTHING;
    """)

    # ── Seed Lab Equipment ────────────────────────────────────
    cur.execute("""
        INSERT INTO lab_equipment (barcode, name, lab, category, status, custodian_id, checkout_date, due_date, condition, purpose) VALUES
            ('BAR-OSC-201', 'Tektronix TBS1102B Digital Storage Oscilloscope (100MHz)', 'Digital Systems Lab',          'Instrumentation',   'available',  NULL,      NULL,         NULL,         'good', NULL),
            ('BAR-FPGA-302', 'Digilent Basys 3 Artix-7 FPGA Development Board',         'Digital Systems Lab',          'Development Board', 'available',  NULL,      NULL,         NULL,         'good', NULL),
            ('BAR-RPI-105', 'Raspberry Pi 4 Model B (4GB) IoT Embedded Kit',           'IoT & Robotics Lab',           'Embedded Systems',   'in_custody', '2024405', CURRENT_DATE, CURRENT_DATE+7, 'good', 'CS325 Semester Project Integration'),
            ('BAR-LA-401',  'Saleae Logic 8 USB Logic Analyzer',                       'Microprocessor Interfacing Lab','Instrumentation',   'available',  NULL,      NULL,         NULL,         'good', NULL),
            ('BAR-FG-102',  'Agilent 33220A 20MHz Function / Arbitrary Waveform Generator','Circuits Lab',             'Signal Generation', 'available',  NULL,      NULL,         NULL,         'good', NULL),
            ('BAR-SDR-503', 'HackRF One 1MHz-6GHz Software Defined Radio',             'Communications Lab',           'RF & Wireless',     'available',  NULL,      NULL,         NULL,         'good', NULL),
            ('BAR-STM-204', 'STM32 Nucleo-F446RE ARM Cortex-M4 Board',                 'Embedded Systems Lab',         'Microcontroller',   'available',  NULL,      NULL,         NULL,         'good', NULL),
            ('BAR-DMM-112', 'Fluke 87V Industrial Digital Multimeter',                 'Electronics Lab',              'Measurement',       'available',  NULL,      NULL,         NULL,         'good', NULL)
        ON CONFLICT (barcode) DO NOTHING;
    """)

    # ── Seed Bookings ─────────────────────────────────────────
    cur.execute("""
        INSERT INTO bookings (booking_code, venue_id, user_id, booking_time, booking_date, start_time, duration_hours, status, checkin_token, checkin_status, source) VALUES
            ('BK-SPT-101', 'SPORTS_C1', '2024405', CURRENT_TIMESTAMP + INTERVAL '2 hours', CURRENT_DATE, '14:00', 1.0, 'approved', 'CHK-4821', 'checked_in',     'sports_reservation'),
            ('BK-AUD-102', 'AHA_AUDI',  '2024668', CURRENT_TIMESTAMP + INTERVAL '1 day',   CURRENT_DATE + 1, '10:00', 3.0, 'pending',  'CHK-9104', 'pending_checkin', 'society_requisition')
        ON CONFLICT (booking_code) DO NOTHING;
    """)

    # ── Seed No-Show Records ──────────────────────────────────
    cur.execute("""
        INSERT INTO no_show_records (user_id, strikes, suspended, suspended_until, strike_history) VALUES
            ('2024405', 0, false, NULL, '[]'),
            ('2024668', 1, false, NULL, '[{"booking_id": "BK-SPT-042", "reason": "No-show past 15-minute grace window"}]'),
            ('2024662', 0, false, NULL, '[]')
        ON CONFLICT (user_id) DO NOTHING;
    """)

    cur.close()
    conn.close()
    print("[✓] All tables created and database seeded successfully!")


if __name__ == "__main__":
    seed()
