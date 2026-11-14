"""
Seed script for the G-Cortex database.
Run this once to create the database tables and populate them with dummy data.

Usage:
    python seed_db.py
"""
import os
import psycopg2
from dotenv import load_dotenv

load_dotenv("app/agents/course_management_agent/.env")

DB_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost/gcortex_db")


def seed():
    conn = psycopg2.connect(DB_URL)
    conn.autocommit = True
    cur = conn.cursor()

    print("[*] Creating tables...")

    # ── Users ─────────────────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id    VARCHAR(10) PRIMARY KEY,
            full_name  VARCHAR(100) NOT NULL,
            role       VARCHAR(20) CHECK (role IN ('student', 'faculty', 'admin')),
            email      VARCHAR(100) UNIQUE NOT NULL
        );
    """)

    # ── Courses ───────────────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS courses (
            course_code      VARCHAR(10) PRIMARY KEY,
            title            VARCHAR(100) NOT NULL,
            credits          INT NOT NULL,
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

    # ── Mess Ledger ───────────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS mess_ledger (
            entry_id  SERIAL PRIMARY KEY,
            user_id   VARCHAR(10) REFERENCES users(user_id),
            meal_date DATE NOT NULL,
            meal_type VARCHAR(20) CHECK (meal_type IN ('breakfast', 'lunch', 'dinner')),
            status    VARCHAR(20) CHECK (status IN ('consumed', 'rebated', 'no-show'))
        );
    """)

    # ── Venues & Bookings ────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS venues (
            venue_id          VARCHAR(20) PRIMARY KEY,
            name              VARCHAR(100) NOT NULL,
            capacity          INT,
            requires_approval BOOLEAN DEFAULT false
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS bookings (
            booking_id   SERIAL PRIMARY KEY,
            venue_id     VARCHAR(20) REFERENCES venues(venue_id),
            user_id      VARCHAR(10) REFERENCES users(user_id),
            booking_time TIMESTAMP NOT NULL,
            status       VARCHAR(20) CHECK (status IN ('pending', 'approved', 'auto-released'))
        );
    """)

    print("[*] Seeding dummy data...")

    # ── Seed Users ────────────────────────────────────────────
    cur.execute("""
        INSERT INTO users (user_id, full_name, role, email) VALUES
            ('2024405', 'Muhammad Husnain Jatoi', 'student', 'u2024405@giki.edu.pk'),
            ('2024668', 'Zahra Aliabbas',         'student', 'u2024668@giki.edu.pk'),
            ('2024662', 'Warda Ammad',            'student', 'u2024662@giki.edu.pk'),
            ('FAC001',  'Farah Saeed',            'faculty', 'farah.saeed@giki.edu.pk')
        ON CONFLICT DO NOTHING;
    """)

    # ── Seed Courses (richer catalog for demo) ────────────────
    cur.execute("""
        INSERT INTO courses (course_code, title, credits) VALUES
            ('CS101', 'Introduction to Computing',           3),
            ('CS102', 'Programming Fundamentals',            3),
            ('CS201', 'Data Structures & Algorithms',        4),
            ('CS232', 'Database Management Systems',         4),
            ('CS271', 'Operating Systems',                   3),
            ('CS325', 'Software Engineering',                3),
            ('CS330', 'Artificial Intelligence',             3),
            ('CS412', 'Machine Learning',                    3),
            ('EE213', 'Microprocessor Interfacing',          4),
            ('MT101', 'Calculus I',                          3),
            ('MT201', 'Linear Algebra',                      3),
            ('HU101', 'English Composition & Comprehension', 3)
        ON CONFLICT DO NOTHING;
    """)

    # ── Seed Prerequisites (builds a realistic DAG) ──────────
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

    # ── Seed Mess Ledger ──────────────────────────────────────
    cur.execute("""
        INSERT INTO mess_ledger (user_id, meal_date, meal_type, status) VALUES
            ('2024405', CURRENT_DATE,                      'lunch',     'consumed'),
            ('2024668', CURRENT_DATE + INTERVAL '1 day',   'dinner',    'rebated'),
            ('2024662', CURRENT_DATE,                      'breakfast', 'consumed')
        ON CONFLICT DO NOTHING;
    """)

    # ── Seed Venues ───────────────────────────────────────────
    cur.execute("""
        INSERT INTO venues (venue_id, name, capacity, requires_approval) VALUES
            ('AHA_AUDI',   'AHA Auditorium',                    300, true),
            ('SPORTS_C1',  'Sports Complex - Badminton Court 1', 4,  false),
            ('LAB_MEDS',   'Digital Systems Lab',                40, true)
        ON CONFLICT DO NOTHING;
    """)

    # ── Seed Bookings ─────────────────────────────────────────
    cur.execute("""
        INSERT INTO bookings (venue_id, user_id, booking_time, status) VALUES
            ('SPORTS_C1', '2024405', CURRENT_TIMESTAMP + INTERVAL '2 hours', 'approved'),
            ('AHA_AUDI',  '2024668', CURRENT_TIMESTAMP + INTERVAL '1 day',   'pending')
        ON CONFLICT DO NOTHING;
    """)

    cur.close()
    conn.close()
    print("[✓] Database seeded successfully!")


if __name__ == "__main__":
    seed()
