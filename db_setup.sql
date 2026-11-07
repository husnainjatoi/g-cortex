-- Enable vector extension for the intelligence layer
CREATE EXTENSION IF NOT EXISTS vector;

-- ==========================================
-- 1. CENTRALIZED RBAC & USERS
-- ==========================================
CREATE TABLE users (
    user_id VARCHAR(10) PRIMARY KEY,
    full_name VARCHAR(100) NOT NULL,
    role VARCHAR(20) CHECK (role IN ('student', 'faculty', 'admin')),
    email VARCHAR(100) UNIQUE NOT NULL
);

INSERT INTO users (user_id, full_name, role, email) VALUES
('2024405', 'Muhammad Husnain Jatoi', 'student', 'u2024405@giki.edu.pk'),
('2024668', 'Zahra Aliabbas', 'student', 'u2024668@giki.edu.pk'),
('2024662', 'Warda Ammad', 'student', 'u2024662@giki.edu.pk'),
('FAC001', 'Farah Saeed', 'faculty', 'farah.saeed@giki.edu.pk');

-- ==========================================
-- 2. COURSE MANAGEMENT SUBAGENT
-- ==========================================
CREATE TABLE courses (
    course_code VARCHAR(10) PRIMARY KEY,
    title VARCHAR(100) NOT NULL,
    credits INT NOT NULL,
    policy_embedding vector(1536) -- For "Ask GIKI" RAG policy engine
);

CREATE TABLE course_prerequisites (
    course_code VARCHAR(10) REFERENCES courses(course_code),
    prereq_code VARCHAR(10) REFERENCES courses(course_code),
    PRIMARY KEY (course_code, prereq_code)
);

-- Dummy Data: DAG Setup
INSERT INTO courses (course_code, title, credits) VALUES
('CS101', 'Introduction to Computing', 3),
('CS232', 'Database Management Systems', 4),
('CS325', 'Software Engineering', 3),
('EE213', 'Microprocessor Interfacing', 4);

INSERT INTO course_prerequisites (course_code, prereq_code) VALUES
('CS232', 'CS101'),
('CS325', 'CS232');

-- ==========================================
-- 3. MESS MANAGEMENT SUBAGENT
-- ==========================================
CREATE TABLE mess_ledger (
    entry_id SERIAL PRIMARY KEY,
    user_id VARCHAR(10) REFERENCES users(user_id),
    meal_date DATE NOT NULL,
    meal_type VARCHAR(20) CHECK (meal_type IN ('breakfast', 'lunch', 'dinner')),
    status VARCHAR(20) CHECK (status IN ('consumed', 'rebated', 'no-show'))
);

-- Dummy Data: Rebate Ledgering
INSERT INTO mess_ledger (user_id, meal_date, meal_type, status) VALUES
('2024405', CURRENT_DATE, 'lunch', 'consumed'),
('2024668', CURRENT_DATE + INTERVAL '1 day', 'dinner', 'rebated'),
('2024662', CURRENT_DATE, 'breakfast', 'consumed');

-- ==========================================
-- 4. REQUISITION MANAGEMENT SUBAGENT
-- ==========================================
CREATE TABLE venues (
    venue_id VARCHAR(20) PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    capacity INT,
    requires_approval BOOLEAN DEFAULT false
);

CREATE TABLE bookings (
    booking_id SERIAL PRIMARY KEY,
    venue_id VARCHAR(20) REFERENCES venues(venue_id),
    user_id VARCHAR(10) REFERENCES users(user_id),
    booking_time TIMESTAMP NOT NULL,
    status VARCHAR(20) CHECK (status IN ('pending', 'approved', 'auto-released'))
);

-- Dummy Data: Zero-trust no-show auto-release rules & venues
INSERT INTO venues (venue_id, name, capacity, requires_approval) VALUES
('AHA_AUDI', 'AHA Auditorium', 300, true),
('SPORTS_C1', 'Sports Complex - Badminton Court 1', 4, false),
('LAB_MEDS', 'Digital Systems Lab', 40, true);

INSERT INTO bookings (venue_id, user_id, booking_time, status) VALUES
('SPORTS_C1', '2024405', CURRENT_TIMESTAMP + INTERVAL '2 hours', 'approved'),
('AHA_AUDI', '2024668', CURRENT_TIMESTAMP + INTERVAL '1 day', 'pending');