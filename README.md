# G-Cortex — Campus Operating System for GIKI

> **CS325 Software Engineering | Assignment #2 | Team Project**
> Muhammad Husnain Jatoi (2024405) · Zahra Aliabbas (2024668) · Warda Ammad (2024662)

---

## Vision

G-Cortex replaces GIKI's fragmented, paper-heavy administrative infrastructure with an autonomous, event-driven campus operating system. Three specialized AI-powered subagents handle academic planning, dining operations, and campus resource allocation — all backed by a unified PostgreSQL database with cross-subagent event-bus communication.

---

## Architecture

```
Client Layer         →   Web Chat Interface (/chat) + REST API (FastAPI)
Orchestration Layer  →   Central Intelligent Orchestrator + Session Manager + Event Bus
Intelligence Layer   →   4 ADK AI Agents (Orchestrator + 3 Subagents with Gemini LLM & DB Tools)
Data Layer           →   PostgreSQL (gcortex_db) + pgvector
```

### Central Orchestrator & Autonomous Subagents

| Agent / Interface | Path / Prefix | Capabilities |
|---|---|---|
| **Central Orchestrator** | `/api/orchestrator`<br>`/chat` (Web UI) | Natural language intent routing, multi-turn session memory, ambiguous query clarification, multi-agent coordination & synthesis, out-of-scope boundaries |
| **Course Management** | `/api/courses` | DAG prerequisite engine, enrollment eligibility, schedule conflict matrix, What-If degree simulator, elective recommender, Ask-GIKI policy engine |
| **Mess Management** | `/api/mess` | TOTP QR dining gatekeeping, gate-pass-synced rebate ledgering, allergen shield, predictive kitchen demand forecasting |
| **Requisition Management** | `/api/req` | Multi-tier society approval chains, fair-use sports reservations (2h/day cap), lab equipment barcode custody, natural language venue recommender, zero-trust no-show auto-release |

---

## Prerequisites

- Ubuntu 22.04+ (or WSL2)
- Python 3.12
- PostgreSQL 14+ with `pgvector` extension
- Google Cloud credentials (for ADK agent LLM access)

---

## Setup

### 1. Database

```bash
# Create database
sudo -u postgres psql -c "CREATE DATABASE gcortex_db;"

# Install pgvector extension
sudo -u postgres psql -d gcortex_db -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

### 2. Python Environment

```bash
cd /home/husnainjatoi/g-cortex
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Environment Variables

Create `.env` files inside each agent directory (`.env` is gitignored):

```bash
# app/agents/course_management_agent/.env
DATABASE_URL=postgresql://postgres:postgres@localhost/gcortex_db
GOOGLE_CLOUD_PROJECT=your-gcp-project-id
GOOGLE_CLOUD_LOCATION=us-central1

# Same structure for mess_management_agent/.env and requisition_management_agent/.env
```

### 4. Seed the Database

```bash
source .venv/bin/activate
python seed_db.py
```

### 5. Run the API Server & Chat Interface

```bash
source .venv/bin/activate
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

- **Interactive Web Chat Interface**: `http://localhost:8000/chat`
- **Swagger API Documentation**: `http://localhost:8000/docs`
- **ReDoc API Documentation**: `http://localhost:8000/redoc`

### 6. Run AI Agents (ADK)

Each subagent can be run independently via the Google ADK CLI:

```bash
# Course Management Agent
cd app/agents/course_management_agent
source ../../../.venv/bin/activate
adk run .

# Mess Management Agent
cd app/agents/mess_management_agent
adk run .

# Requisition Management Agent
cd app/agents/requisition_management_agent
adk run .
```

---

## Running Tests

```bash
source .venv/bin/activate

# 1. Run full subagent test suite (54 test cases)
python test_all.py

# 2. Run Central Orchestrator test suite (12 test cases)
python test_orchestrator.py
```

The test suites cover **66 automated test cases** across:
- Central Orchestrator routing, multi-turn context, disambiguation, and multi-agent synthesis
- Session isolation and lifecycle management
- Database connectivity and schema validation
- All 8 Course Management agent tools
- All 6 Mess Management agent tools
- All 12 Requisition Management agent tools
- Event Bus pub/sub, error isolation, and log recording
- RBAC security layer permission checks
- REST API router import and structure validation

---

## API Quick Reference

### Central Orchestrator (`/api/orchestrator` & `/chat`)

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/chat` | Interactive Web Chat Interface |
| `POST` | `/api/orchestrator/chat` | Send natural language prompt to Central Orchestrator |
| `POST` | `/api/orchestrator/session/new` | Create an isolated conversation session |
| `GET` | `/api/orchestrator/session/{id}/history` | Get message history for a session |
| `DELETE` | `/api/orchestrator/session/{id}` | Clear/delete conversation session |
| `GET` | `/api/orchestrator/sessions` | List active sessions |
| `GET` | `/api/orchestrator/status` | Central Orchestrator & subagent status |

### Course Management (`/api/courses`)

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/courses/` | List all courses |
| `GET` | `/api/courses/{code}` | Get course details |
| `GET` | `/api/courses/{code}/prerequisites` | Full prerequisite DAG chain |
| `POST` | `/api/courses/{code}/can-enroll` | Enrollment eligibility check |

### Mess Management (`/api/mess`)

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/mess/rebate` | Apply for mess rebate |
| `POST` | `/api/mess/qr-token` | Generate TOTP meal QR token |
| `POST` | `/api/mess/verify-entry` | Verify turnstile QR scan |
| `GET` | `/api/mess/menu/{meal_period}` | Menu macros + allergen shield |
| `GET` | `/api/mess/forecast/{meal_period}` | Kitchen demand forecast |
| `GET` | `/api/mess/profile/{reg_no}` | Student mess profile |

### Requisition Management (`/api/req`)

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/req/society-requisitions` | Submit society venue requisition |
| `GET` | `/api/req/society-requisitions/{id}` | Get requisition status + approval trail |
| `POST` | `/api/req/society-requisitions/{id}/review` | Approve/reject at current tier |
| `POST` | `/api/req/sports/reserve` | Reserve sports facility |
| `GET` | `/api/req/sports/availability` | Check sports facility availability |
| `POST` | `/api/req/venues/recommend` | Natural language venue recommendation |
| `POST` | `/api/req/equipment/{barcode}/checkout` | Lab equipment barcode checkout |
| `POST` | `/api/req/equipment/{barcode}/return` | Lab equipment barcode return |
| `GET` | `/api/req/equipment` | Query equipment inventory |
| `POST` | `/api/req/bookings/{id}/checkin` | Zero-trust physical check-in |
| `POST` | `/api/req/bookings/auto-release` | Auto-release no-show bookings |
| `GET` | `/api/req/profile/{reg_no}` | Student requisition profile |

---

## Database Schema

```
users                   → Centralized RBAC (student / faculty / admin)
courses                 → Course catalog with time slots and domain tags
course_prerequisites    → Prerequisite DAG (many-to-many)
degree_minors           → Minor tracks (AI, Software Systems, Embedded)
university_policies     → Ask-GIKI RAG knowledge base
mess_ledger             → Daily meal consumption and rebate log
gate_passes             → Campus security exit clearances
mess_rebates            → Financial rebate ledger (gate-pass-synced)
daily_menu              → Meal periods with macros and allergens
venues                  → Rooms, labs, sports, auditoriums
bookings                → Spatial reservations with zero-trust tokens
campus_societies        → Student societies and faculty advisors
society_requisitions    → Multi-tier digital approval chains
lab_equipment           → Barcode-tracked hardware custody
no_show_records         → Zero-trust penalty strike ledger
```

---

## Software Process Model

**Agile (Scrum)** — 6-phase cycle:
1. Product Backlog Creation
2. Sprint Planning (2-week sprints)
3. Sprint Execution (parallel subagent development)
4. CI/CD Testing
5. Sprint Review
6. Sprint Retrospective

---

## Project Scope

**In-Scope:** Course DAG engine, TOTP mess gatekeeping, rebate ledgering, fair-use sports reservations, lab barcode custody, multi-tier society requisitions, event bus, RBAC, unified API.

**Out-of-Scope:** Physical hardware/turnstile manufacturing, real-time bank clearing, alumni records, clinical EHR.

---

## Team

| Name | Reg. No. | Contribution |
|---|---|---|
| Muhammad Husnain Jatoi | 2024405 | Project vision, Course Management Subagent, Software Process Model |
| Zahra Aliabbas | 2024668 | System Analysis, Mess Management Subagent, Project Objectives |
| Warda Ammad | 2024662 | Requisition Subagent, App Logo, Cross-Subagent Integration Pipeline |
