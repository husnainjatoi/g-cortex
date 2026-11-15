-- ==============================================================================
-- G-Cortex Campus Operating System: Database Setup
-- Complete relational schema supporting Course, Mess, and Requisition Subagents
-- ==============================================================================

CREATE EXTENSION IF NOT EXISTS vector;

-- 1. CENTRALIZED RBAC & USERS
CREATE TABLE IF NOT EXISTS users (
    user_id VARCHAR(10) PRIMARY KEY,
    full_name VARCHAR(100) NOT NULL,
    role VARCHAR(20) CHECK (role IN ('student', 'faculty', 'admin')),
    email VARCHAR(100) UNIQUE NOT NULL
);

-- 2. COURSE MANAGEMENT SUBAGENT
CREATE TABLE IF NOT EXISTS courses (
    course_code VARCHAR(10) PRIMARY KEY,
    title VARCHAR(100) NOT NULL,
    credits INT NOT NULL,
    slot VARCHAR(30),
    category VARCHAR(30) DEFAULT 'core',
    domains TEXT,
    policy_embedding vector(1536)
);

CREATE TABLE IF NOT EXISTS course_prerequisites (
    course_code VARCHAR(10) REFERENCES courses(course_code),
    prereq_code VARCHAR(10) REFERENCES courses(course_code),
    PRIMARY KEY (course_code, prereq_code)
);

CREATE TABLE IF NOT EXISTS degree_minors (
    minor_name VARCHAR(100) PRIMARY KEY,
    required_courses TEXT NOT NULL,
    required_credits INT NOT NULL,
    description TEXT
);

CREATE TABLE IF NOT EXISTS university_policies (
    topic VARCHAR(50) PRIMARY KEY,
    title VARCHAR(100) NOT NULL,
    policy_text TEXT NOT NULL
);

-- 3. MESS MANAGEMENT SUBAGENT
CREATE TABLE IF NOT EXISTS mess_ledger (
    entry_id SERIAL PRIMARY KEY,
    user_id VARCHAR(10) REFERENCES users(user_id),
    meal_date DATE NOT NULL,
    meal_type VARCHAR(20) CHECK (meal_type IN ('breakfast', 'lunch', 'dinner')),
    status VARCHAR(20) CHECK (status IN ('consumed', 'rebated', 'no-show'))
);

CREATE TABLE IF NOT EXISTS gate_passes (
    user_id VARCHAR(10) PRIMARY KEY REFERENCES users(user_id),
    approved BOOLEAN DEFAULT false,
    leave_start DATE,
    leave_end DATE,
    destination VARCHAR(100)
);

CREATE TABLE IF NOT EXISTS mess_rebates (
    rebate_id VARCHAR(30) PRIMARY KEY,
    user_id VARCHAR(10) REFERENCES users(user_id),
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    days_count INT NOT NULL,
    rebate_amount_pkr NUMERIC(10, 2) NOT NULL,
    status VARCHAR(20) DEFAULT 'approved',
    gate_pass_synced BOOLEAN DEFAULT true,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS daily_menu (
    meal_period VARCHAR(20) PRIMARY KEY CHECK (meal_period IN ('breakfast', 'lunch', 'dinner')),
    items TEXT NOT NULL,
    calories INT NOT NULL,
    protein_g INT NOT NULL,
    carbs_g INT NOT NULL,
    fat_g INT NOT NULL,
    allergens TEXT NOT NULL
);

-- 4. REQUISITION MANAGEMENT SUBAGENT
CREATE TABLE IF NOT EXISTS venues (
    venue_id VARCHAR(20) PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    capacity INT,
    requires_approval BOOLEAN DEFAULT false,
    category VARCHAR(50) DEFAULT 'general',
    facilities TEXT,
    description TEXT
);

CREATE TABLE IF NOT EXISTS bookings (
    booking_id SERIAL PRIMARY KEY,
    booking_code VARCHAR(30) UNIQUE,
    venue_id VARCHAR(20) REFERENCES venues(venue_id),
    user_id VARCHAR(10) REFERENCES users(user_id),
    booking_time TIMESTAMP NOT NULL,
    booking_date DATE,
    start_time VARCHAR(10),
    duration_hours NUMERIC(4, 2) DEFAULT 1.0,
    status VARCHAR(20) CHECK (status IN ('pending', 'approved', 'auto-released', 'checked_in', 'rejected')),
    checkin_token VARCHAR(20),
    checkin_status VARCHAR(20) DEFAULT 'pending_checkin',
    source VARCHAR(50),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS campus_societies (
    society_name VARCHAR(100) PRIMARY KEY,
    advisor_id VARCHAR(10) REFERENCES users(user_id),
    advisor_name VARCHAR(100) NOT NULL,
    category VARCHAR(50)
);

CREATE TABLE IF NOT EXISTS society_requisitions (
    requisition_id VARCHAR(30) PRIMARY KEY,
    society_name VARCHAR(100) REFERENCES campus_societies(society_name),
    venue_id VARCHAR(20) REFERENCES venues(venue_id),
    applicant_id VARCHAR(10) REFERENCES users(user_id),
    event_title VARCHAR(150) NOT NULL,
    booking_date DATE NOT NULL,
    start_time VARCHAR(10) NOT NULL,
    duration_hours NUMERIC(4, 2) DEFAULT 2.0,
    expected_attendees INT,
    justification TEXT,
    current_tier INT DEFAULT 1,
    status VARCHAR(30) DEFAULT 'pending_advisor_approval',
    approval_chain TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS lab_equipment (
    barcode VARCHAR(30) PRIMARY KEY,
    name VARCHAR(150) NOT NULL,
    lab VARCHAR(100) NOT NULL,
    category VARCHAR(50),
    status VARCHAR(30) DEFAULT 'available',
    custodian_id VARCHAR(10) REFERENCES users(user_id),
    checkout_date DATE,
    due_date DATE,
    condition VARCHAR(50) DEFAULT 'good',
    purpose TEXT
);

CREATE TABLE IF NOT EXISTS no_show_records (
    user_id VARCHAR(10) PRIMARY KEY REFERENCES users(user_id),
    strikes INT DEFAULT 0,
    suspended BOOLEAN DEFAULT false,
    suspended_until DATE,
    strike_history TEXT
);
