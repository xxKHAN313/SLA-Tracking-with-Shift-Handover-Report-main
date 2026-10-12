# 🎓 Internship Report — Automated SLA Tracking & Report Generation System

**Intern Duration:** 4 Weeks  
**Project:** Automated SLA Tracking and Report Generation System  
**Tech Stack:** Python · Flask · SQLAlchemy · Bootstrap 5 · Chart.js · APScheduler · ReportLab · Pandas  
**Integration:** DFIR-IRIS (Incident Response Platform)

---

## 📋 Table of Contents

1. [Week 1 — Foundation & Core Architecture](#week-1--foundation--core-architecture)
2. [Week 2 — DFIR-IRIS Integration & SLA Engine](#week-2--dfir-iris-integration--sla-engine)
3. [Week 3 — Dashboard, Reports & Automation](#week-3--dashboard-reports--automation)
4. [Week 4 — Security Hardening, Testing & Polish](#week-4--security-hardening-testing--polish)
5. [Complete System Workflow](#-complete-system-workflow)
6. [Architecture Diagram](#-architecture-diagram)
7. [Technologies Learned](#-technologies-learned)
8. [Summary of Achievements](#-summary-of-achievements)

---

## Week 1 — Foundation & Core Architecture

### 📅 Focus: Project Setup, Database Design & Models

### What We Did

1. **Project Initialization & Environment Setup**
   - Created the Flask application factory pattern (`app.py`) with `create_app()` function
   - Set up `config.py` with environment-driven configuration (`DevelopmentConfig`, `ProductionConfig`, `TestingConfig`)
   - Created `extensions.py` to hold shared Flask extension instances (db, login_manager, migrate, csrf, scheduler) to avoid circular imports
   - Set up `.env` / `.env.example` for environment variables
   - Created `requirements.txt` with all dependencies

2. **Database Models Designed & Implemented (8 models)**

   | Model | File | Purpose |
   |-------|------|---------|
   | `Client` | `models/client.py` | Multi-tenant organization model with per-client timezone support |
   | `User` | `models/user.py` | User authentication with RBAC (Admin, Manager, Viewer roles) |
   | `Ticket` | `models/ticket.py` | Generic normalized ticket model with SLA computed fields |
   | `SLARule` | `models/sla_rule.py` | Database-driven SLA rule definitions (field_name/field_value matching) |
   | `FieldMapping` | `models/field_mapping.py` | Source-field → local-field translation layer |
   | `Report` | `models/report.py` | Tracks generated PDF/Excel report files |
   | `Setting` | `models/setting.py` | Key/value runtime settings table |
   | `SyncLog` | `models/sync_log.py` | Records each IRIS sync run for debugging |

3. **Multi-Tenancy Architecture (Gap #1)**
   - Every ticket, SLA rule, and field mapping is scoped to a `Client` via `client_id`
   - Clients carry their own timezone (IANA tz name) for business-hours calculations
   - IRIS tickets are routed to clients by matching `case_customer_id` → `Client.iris_customer_id`

4. **Authentication & RBAC System**
   - Flask-Login integration with hashed passwords (Werkzeug)
   - 3 roles: Admin (full access), Manager (dashboard + tickets + reports), Viewer (read-only)
   - Centralized permission matrix in `ROLE_PERMISSIONS` dict
   - `@role_required()` and `@permission_required()` decorators created in `routes/decorators.py`

5. **Database Seeder (`flask seed` / `scripts/seed_data.py`)**
   - Seeds 3 demo clients (Acme Corp, Globex Industries, Initech) with different timezones
   - Creates 3 demo users (admin, manager, viewer) with hashed passwords
   - Seeds SLA rules across 3 different taxonomies (severity-based, priority-based, criticality-based)
   - Creates 11 dummy tickets exercising every SLA status

### Challenges Faced

| Challenge | How We Solved It |
|-----------|-----------------|
| **Circular imports** between models, routes, and services | Created `extensions.py` as a shared module; imported models only inside `create_app()` after `db.init_app()` |
| **Designing a generic ticket model** that works with any taxonomy (severity, priority, criticality) | Made all taxonomy fields optional (nullable). The SLA engine uses `getattr(ticket, rule.field_name)` instead of hardcoded fields |
| **Multi-tenancy scoping** — ensuring data from different clients never collides | Added `client_id` foreign key on tickets, SLA rules, and field mappings; composite unique constraints prevent duplicates |
| **Understanding Flask-Login vs custom `is_active`** | Mapped `is_active_user` column to Flask-Login's expected `is_active` property via a `@property` decorator |

### Improvements Made
- Learned the **Application Factory Pattern** — why `create_app()` is better than a global `app` object (testability, multiple configs)
- Understood **database normalization** and why separating clients, tickets, and SLA rules into distinct tables with foreign keys is important
- Learned how to use **Flask-Migrate** for database schema migrations

---

## Week 2 — DFIR-IRIS Integration & SLA Engine

### 📅 Focus: API Integration, Field Mapping, SLA Calculator & Sync Pipeline

### What We Did

1. **DFIR-IRIS REST API Integration (`services/iris_api_service.py`)**
   - Built a thin wrapper around the IRIS API using `requests` library directly (no heavy SDK dependency)
   - Implemented functions: `test_connection()`, `fetch_cases()`, `fetch_alerts()`, `fetch_case_by_id()`, `fetch_all_cases()`, `fetch_all_alerts()`
   - **Pagination (Gap #6):** Uses IRIS response envelope fields (`total`, `last_page`, `current_page`) to loop until all pages are consumed, with fallback heuristic for older IRIS versions
   - **Retry with Exponential Backoff (Gap #7):** `_request_with_retry()` retries up to 3 times on transient failures (timeouts, 429, 5xx errors) with configurable backoff

2. **Field Mapping Service (`services/field_mapping_service.py`)**
   - Built the **translation layer** that makes the system source-agnostic
   - `normalize_ticket()` converts raw IRIS JSON into our generic ticket dict using configurable field mappings from the database
   - Supports **nested JSON paths** (e.g., `owner.username`) via `_get_nested()` helper
   - **Multi-tenant mapping resolution:** client-specific mappings override global defaults
   - Robust datetime parsing (`_parse_datetime()`) handles ISO8601, various formats, and forces naive datetimes to UTC (Gap #5)
   - Default IRIS field mappings can be seeded via `seed_default_iris_mappings()`

3. **Generic SLA Rule Engine (`services/sla_calculator.py`)** — 367 lines, the heart of the system
   - **Rule Matching (`find_matching_sla_rule()`):**
     - Filters by `client_id` (Gap #1: multi-tenancy)
     - Orders by `priority ASC, id ASC` (Gap #2: explicit evaluation order, first match wins)
     - Generic matching: `getattr(ticket, rule.field_name) == rule.field_value`
     - Case-insensitive, whitespace-tolerant comparison
   - **Deadline Calculation (`calculate_deadlines()`):**
     - Supports both wall-clock time and business-hours-only mode (Gap #3)
     - Accounts for accumulated `total_paused_minutes` by shifting deadlines forward (Gap #4)
   - **SLA Status Determination (`calculate_sla_status()`):**
     - No Matching Rule → "No Matching Rule"
     - Closed before deadline → "Closed Within SLA"
     - Closed after deadline → "Closed After Breach"
     - Open & now > deadline → "Breached"
     - Open & usage% ≥ warning_threshold → "Near Breach"
     - Otherwise → "Within SLA"
   - **Pause/Resume (Gap #4):** `_handle_pause_resume()` implements deadline-shift semantics — when a ticket enters a pause status, `paused_at` is set; when it leaves, elapsed time is accumulated and deadlines shift forward
   - `recalculate_all_open_tickets()` — batch recalculation for all non-closed tickets

4. **Business Hours Calculator (`services/business_hours.py`)**
   - `add_business_minutes()` computes deadlines counting only working hours (Mon-Fri 09:00-17:00)
   - Respects per-client timezone (converts to client's local timezone for calendar math, then back to UTC for storage)
   - Properly handles: weekends, before/after business hours, spanning multiple days

5. **Sync Service (`services/sync_service.py`)** — The orchestrator
   - Runs a full sync cycle: Fetch → Resolve Client → Normalize → Upsert → Calculate SLA → Soft-delete → Log
   - **Client resolution** via `case_customer_id` mapping
   - **Explicit upsert logic (Gap #8):** checks if ticket exists by composite key (`client_id`, `source_system`, `external_id`), then creates or updates
   - **Soft-delete (Gap #8):** tickets missing from the latest IRIS response are marked `status = 'deleted_in_source'` (preserves historical SLA data)
   - **Reopened ticket detection (Gap #10):** if a previously-closed ticket no longer has `closed_at`, clears SLA status to force re-evaluation
   - Records every sync run in `SyncLog` with counts (fetched, created, updated, skipped)

### Challenges Faced

| Challenge | How We Solved It |
|-----------|-----------------|
| **No hardcoded severity values** — how to match SLA rules generically | Used `getattr(ticket, rule.field_name)` for dynamic attribute access + case-insensitive string comparison |
| **IRIS API response format varies** between versions | Used `.get()` with fallbacks, optional pagination envelope, and graceful degradation |
| **Business hours spanning weekends** — the deadline calculation was incorrect initially | Built a step-by-step loop that skips non-working days and hours, consuming minutes only during work windows |
| **Naive vs aware datetimes** causing comparison errors | Created `_as_aware()` helper that forces all naive datetimes to UTC; established a **timezone policy** (Gap #5) |
| **Pause/resume tracking** — how to shift deadlines without losing history | Used `paused_at` timestamp + `total_paused_minutes` accumulator; shift deadlines forward on unpause |
| **Deleted tickets in IRIS** — should we hard-delete or preserve history? | Chose **soft-delete** (`status = 'deleted_in_source'`) to preserve historical SLA metrics |

### Improvements Made
- Deeply understood **REST API integration patterns** (retry, backoff, pagination, error handling)
- Learned the importance of **separation of concerns** — API fetching, field mapping, SLA logic, and sync orchestration are all separate services
- Understood **idempotent upsert patterns** — why composite unique constraints matter for sync reliability
- Learned about **timezone-aware programming** — storing everything in UTC, converting only for display

---

## Week 3 — Dashboard, Reports & Automation

### 📅 Focus: UI/UX, Visualizations, Report Generation, Scheduler & Email

### What We Did

1. **Dashboard with Chart.js Visualizations (`routes/dashboard_routes.py` + `templates/dashboard.html`)**
   - Built comprehensive metrics computation: total tickets, open/closed, SLA compliance %, average resolution time
   - **5 Chart.js visualizations:**
     - SLA Status Distribution (doughnut chart)
     - Severity/Priority/Criticality Breakdown (bar chart)
     - Breach Rate metrics
     - Monthly SLA Trend (line chart — within vs breached per month)
     - Analyst Performance (per-assignee stats)
   - Multi-tenant filtering: client_id query parameter filters all metrics to a specific client
   - JSON endpoint (`/dashboard/metrics.json`) for dynamic chart refresh via `fetch()`

2. **Ticket Management UI (`routes/ticket_routes.py` + `templates/tickets.html`, `ticket_detail.html`)**
   - Ticket list with search (by title, external ID), multi-field filters (status, severity, SLA status, client)
   - Color-coded SLA badges (green = Within SLA, yellow = Near Breach, red = Breached, gray = No Rule)
   - Ticket detail view showing all fields, matched SLA rule, deadline, breach duration
   - "Sync from IRIS" button for manual sync trigger
   - "Recalculate SLA" button for manual recalculation

3. **SLA Rules CRUD (`routes/sla_rule_routes.py` + `templates/sla_rules.html`, `sla_rule_form.html`)**
   - Admin-only rule management UI (create, edit, delete, activate/deactivate)
   - Form with all rule fields: name, field_name, field_value, response SLA, resolution SLA, warning threshold, business hours flag, stop/pause/applies-to statuses, evaluation priority

4. **Report Generation (`services/report_generator.py` + `routes/report_routes.py`)**
   - **4 report types:**
     1. SLA Summary Report — high-level compliance statistics
     2. Detailed Ticket SLA Report — full ticket-by-ticket breakdown
     3. Breached Tickets Report — only breached/closed-after-breach tickets
     4. Analyst Performance Report — per-assignee SLA compliance stats
   - **2 output formats:**
     - PDF (ReportLab) — styled tables with color headers, alternating row backgrounds
     - Excel (Pandas + OpenPyXL) — multi-sheet workbooks with Summary + Detail sheets
   - Reports are tracked in the `reports` table and downloadable from the Reports page

5. **Settings UI (`routes/settings_routes.py` + `templates/settings.html`)**
   - Admin-only settings page with:
     - IRIS connection configuration + "Test Connection" button
     - Field mapping management (view, add, edit, delete mappings)
     - Client management (view clients with their IRIS customer IDs and timezones)
     - Sync log viewer (history of all sync runs with status, counts)

6. **APScheduler Background Jobs (`services/scheduler_service.py`)**
   - **Periodic IRIS sync job:** runs every `SYNC_INTERVAL_MINUTES` (default 15 min), fetches → normalizes → upserts → recalculates SLA → sends breach alerts if enabled
   - **Optional daily report job:** cron-triggered at `DAILY_REPORT_HOUR`, generates SLA Summary PDF + optional email
   - **Weekly report cleanup job (Gap #11):** runs every Sunday at 03:00, deletes reports older than 90 days from disk and DB

7. **Email Notifications (`services/email_service.py`)**
   - SMTP-based notification system (disabled by default)
   - **Near-breach/breach alert:** consolidated email after each sync listing all breached + near-breach tickets
   - **Daily SLA summary email:** total tickets, breached count, near-breach count
   - Recipients: all Admin and Manager users with email addresses

8. **Report Cleanup Service (`services/cleanup_service.py`)**
   - 90-day retention policy for generated reports
   - Deletes both the file on disk and the DB record
   - Handles OS errors gracefully (logs warnings, continues with other files)

9. **Base Template & Styling (`templates/base.html` + `static/css/style.css`)**
   - Bootstrap 5 responsive layout with navigation sidebar
   - Consistent styling across all pages
   - Jinja2 `context_processor` injects `sla_badge_color` helper for template-wide SLA badge coloring

### Challenges Faced

| Challenge | How We Solved It |
|-----------|-----------------|
| **Chart.js data format** — getting the right shape for doughnut/bar/line charts | Created a dedicated `metrics_json()` endpoint that pre-computes all chart data server-side and returns clean JSON |
| **APScheduler running in Flask** — app context issues | Wrapped every job function with `with app.app_context():` since APScheduler jobs run outside Flask request context |
| **Scheduler starting twice** in debug mode (Werkzeug reloader) | Guarded `init_scheduler()` with `if scheduler.running: return` and used `use_reloader=False` |
| **PDF table layout** with many columns overflowing the page | Used `landscape(A4)` page size, reduced font size to 7.5pt, and capped rows at 500 per PDF for sane file sizes |
| **Report cleanup** — file deletion vs DB record deletion | Delete the file first (with error handling), then always delete the DB record to prevent orphan records |

### Improvements Made
- Learned **Chart.js integration** with Flask — how to pass server data to client-side JavaScript charts
- Understood the **challenges of background job scheduling** in web applications (app context, thread safety, duplicate execution)
- Learned **PDF generation with ReportLab** — document templates, table styles, paragraph styles
- Understood **report lifecycle management** — generation, storage, download, and cleanup

---

## Week 4 — Security Hardening, Testing & Polish

### 📅 Focus: Security, Testing, Error Handling & Documentation

### What We Did

1. **Security Hardening (Gap #9)**
   - **CSRF Protection:** Flask-WTF `CSRFProtect` initialized globally, protecting all POST forms
   - **Session Cookie Hardening:** `SESSION_COOKIE_HTTPONLY=True`, `SESSION_COOKIE_SAMESITE="Lax"`, `PERMANENT_SESSION_LIFETIME=8 hours`
   - **Login Rate Limiting:** in-memory lockout after 5 failed attempts in 15 minutes per username
     - `_is_locked_out()`, `_record_failed_attempt()`, `_clean_old_attempts()`, `_clear_attempts()` in `auth_routes.py`
     - All lockouts are logged with `logger.warning()`
   - **Password Hashing:** Werkzeug's `generate_password_hash` / `check_password_hash` (bcrypt-like)
   - **API Key handling:** IRIS API key stays in `.env` only (never in DB Settings table)

2. **Test Suite (`tests/`)**
   - **Test Configuration (`tests/conftest.py`):**
     - Flask app with `TestingConfig` (in-memory SQLite, CSRF disabled)
     - Auto-cleanup: `db.create_all()` before each test, `db.drop_all()` after
     - Fixtures: `sample_client`, `sample_clients` (multi-tenant), `sample_rules` (cross-client rules)
   - **SLA Calculator Tests (`tests/test_sla_calculator.py`):**
     - Tests rule matching (correct rule found by field_name/field_value)
     - Tests multi-tenant isolation (same field_value, different clients get different rules)
     - Tests priority ordering (lower priority number wins)
     - Tests deadline calculation (wall-clock and business-hours modes)
     - Tests SLA status transitions (Within → Near Breach → Breached → Closed)
     - Tests pause/resume semantics (deadline shifts forward on unpause)
     - Tests edge cases (no matching rule, no created_at, closed tickets)
   - **Sync Service Tests (`tests/test_sync_service.py`):**
     - Tests upsert logic (create new ticket, update existing)
     - Tests soft-delete behavior (tickets missing from IRIS marked as `deleted_in_source`)
     - Tests reopened ticket detection (closed_at cleared → SLA re-evaluated)
     - Tests client resolution from raw case data

3. **Error Handling & Robustness**
   - Sync service catches per-ticket errors without failing the entire sync batch
   - Scheduler wraps each job in try/except with `logger.exception()` — one failed job doesn't crash others
   - `_request_with_retry()` has configurable max retries, backoff multiplier, and retryable status codes
   - `cleanup_old_reports()` handles individual file deletion failures gracefully

4. **Documentation**
   - Comprehensive `README.md` (217 lines) covering:
     - Feature list, project structure, setup steps, demo logins
     - IRIS connection guide, SLA rules configuration guide
     - PostgreSQL migration instructions
     - Scheduler management, report generation guide
     - Future development notes (intern handoff section)
     - Known limitations
   - Inline docstrings on every module, class, and function explaining design decisions
   - "Gap #N" comments throughout code referencing specific design improvements

5. **Utility Scripts & CLI Commands (`cli.py`, `scripts/`)**
   - `cli.py` — custom Flask CLI command interface (`flask seed`, `flask clear-seed`, `flask fix-mappings`)
   - `scripts/seed_data.py` — standalone database seeder script
   - `scripts/clear_seed_data.py` — standalone script to clear seeded data
   - `scripts/fix_field_mappings.py` — standalone utility script to repair/update field mappings

### Challenges Faced

| Challenge | How We Solved It |
|-----------|-----------------|
| **CSRF tokens breaking AJAX requests** | Used Flask-WTF's CSRF token injection in forms; for AJAX, passed token in headers |
| **In-memory rate limiter resets on restart** | Documented as a known limitation; acceptable for MVP since a proper solution (Redis/DB-backed) is a production enhancement |
| **Testing with SQLAlchemy sessions** — objects detaching from session between fixtures | Used `db.session.refresh()` + `db.session.expunge()` in fixtures to detach clean objects |
| **Testing the sync service** requires mocking IRIS API calls | Used `unittest.mock.patch` to mock `iris_api_service` functions in tests |
| **Timezone-aware datetime comparisons** in tests | Consistently used `datetime.now(timezone.utc)` and the `now` fixture for reproducible test times |

### Improvements Made
- Learned **web application security fundamentals** — CSRF, session cookies, rate limiting, password hashing
- Understood **test-driven development** with pytest — fixtures, mocking, isolation
- Learned the importance of **documentation** for code handoff — every module has a docstring explaining "why" not just "what"
- Understood the difference between **MVP security** and production-grade security (in-memory vs Redis-backed rate limiting)

---

## 🔄 Complete System Workflow

### How the Entire Program Runs (End-to-End)

```
┌─────────────────────────────────────────────────────────────────────┐
│                        APPLICATION STARTUP                         │
│                                                                    │
│  1. python app.py                                                  │
│  2. create_app() → loads config from .env                          │
│  3. Initializes: db, login_manager, migrate, csrf                  │
│  4. Registers 6 Blueprints (auth, dashboard, ticket, sla_rule,     │
│     report, settings)                                              │
│  5. Starts APScheduler background jobs (if enabled)                │
│  6. Flask dev server runs on http://localhost:5000                  │
└─────────────────────────────────────────────────────────────────────┘
```

### Step-by-Step Data Flow

#### 1️⃣ User Authentication
```
User → /login (POST username/password)
  → auth_routes.py checks rate limit (_is_locked_out)
  → User.query.filter_by(username=...) fetches from DB
  → user.check_password() verifies bcrypt hash
  → login_user(user) creates session
  → Redirect to /dashboard
```

#### 2️⃣ IRIS Data Sync (Automatic or Manual)
```
Trigger: APScheduler interval job OR "Sync from IRIS" button click

┌──────────────────────────────────────────────────────────────────┐
│  sync_service.sync_cases_from_iris()                             │
│                                                                  │
│  Step 1: CREATE SYNC LOG                                         │
│    └─ SyncLog(status="running") → saved to DB                    │
│                                                                  │
│  Step 2: FETCH FROM IRIS                                         │
│    └─ iris_api_service.fetch_all_cases()                         │
│       ├─ Paginates through /manage/cases/list                    │
│       ├─ Uses _request_with_retry() (3 retries, exponential      │
│       │   backoff on 429/5xx/timeouts)                           │
│       └─ For each case, fetches full details via                 │
│          fetch_case_by_id() to get precise timestamps            │
│                                                                  │
│  Step 3: FOR EACH RAW CASE                                       │
│    ├─ RESOLVE CLIENT                                             │
│    │   └─ _resolve_client() matches case_customer_id             │
│    │      → Client.iris_customer_id                              │
│    │      (skip if no matching client found)                     │
│    │                                                             │
│    ├─ NORMALIZE TICKET                                           │
│    │   └─ field_mapping_service.normalize_ticket()               │
│    │      ├─ Loads active FieldMapping rows for client           │
│    │      │   (client-specific overrides global defaults)        │
│    │      ├─ For each local_field (external_id, title,           │
│    │      │   severity, priority, criticality, status,           │
│    │      │   assigned_to, created_at, closed_at):               │
│    │      │   └─ Extract value from raw JSON using               │
│    │      │      source_field or source_path (nested)            │
│    │      └─ Parse datetime fields → force to UTC                │
│    │                                                             │
│    ├─ UPSERT TICKET                                              │
│    │   └─ Query by (client_id, source_system, external_id)       │
│    │      ├─ EXISTS → update all fields                          │
│    │      └─ NOT EXISTS → create new Ticket                      │
│    │      + Store raw_payload_json for reprocessing              │
│    │      + Detect reopened tickets (Gap #10)                    │
│    │                                                             │
│    └─ CALCULATE SLA                                              │
│        └─ sla_calculator.apply_sla_to_ticket()                   │
│           (see SLA Engine flow below)                            │
│                                                                  │
│  Step 4: SOFT-DELETE MISSING TICKETS (Gap #8)                    │
│    └─ Tickets in DB but not in IRIS response                     │
│       → status = "deleted_in_source"                             │
│                                                                  │
│  Step 5: COMMIT & LOG                                            │
│    └─ sync_log.mark_success(fetched, created, updated)           │
└──────────────────────────────────────────────────────────────────┘
```

#### 3️⃣ SLA Rule Engine (The Core Algorithm)
```
sla_calculator.calculate_sla_status(ticket)

┌──────────────────────────────────────────────────────────────────┐
│  Step 1: FIND MATCHING RULE                                      │
│    └─ find_matching_sla_rule(ticket)                             │
│       ├─ Filter: is_active=True, client_id=ticket.client_id     │
│       ├─ Order: priority ASC, id ASC (first match wins)         │
│       └─ For each rule:                                          │
│          ticket_value = getattr(ticket, rule.field_name)         │
│          if str(ticket_value).lower() == str(rule.field_value)   │
│             .lower() → MATCH!                                    │
│                                                                  │
│  Step 2: HANDLE PAUSE/RESUME (Gap #4)                            │
│    ├─ Is ticket status in rule.pause_status_list()?              │
│    │   YES & paused_at is None → Set paused_at = now             │
│    │   NO & paused_at is set  → Calculate pause duration,        │
│    │                             add to total_paused_minutes,    │
│    │                             shift deadlines forward,        │
│    │                             clear paused_at                 │
│    └─                                                            │
│                                                                  │
│  Step 3: CALCULATE DEADLINES                                     │
│    ├─ IF rule.business_hours_only:                               │
│    │   └─ add_business_minutes(created_at, sla_minutes, client)  │
│    │      (skips weekends, non-working hours, per-client tz)     │
│    └─ ELSE:                                                      │
│        └─ created_at + timedelta(minutes=sla_minutes)            │
│    + Add pause_shift (total_paused_minutes) to both deadlines    │
│                                                                  │
│  Step 4: DETERMINE SLA STATUS                                    │
│    ├─ No rule found           → "No Matching Rule"               │
│    ├─ Closed before deadline  → "Closed Within SLA"      ✅      │
│    ├─ Closed after deadline   → "Closed After Breach"    🔴      │
│    ├─ Open & now > deadline   → "Breached"               🔴      │
│    ├─ Open & usage% ≥ warn%  → "Near Breach"            🟡      │
│    └─ Otherwise               → "Within SLA"             ✅      │
│                                                                  │
│  Step 5: UPDATE TICKET FIELDS                                    │
│    └─ Set: sla_rule_id, response_deadline,                       │
│       resolution_deadline, response_sla_status,                  │
│       resolution_sla_status, sla_status,                         │
│       breach_duration_minutes                                    │
└──────────────────────────────────────────────────────────────────┘
```

#### 4️⃣ Dashboard Rendering
```
User → /dashboard?client_id=1

  → dashboard_routes._build_dashboard_metrics(client_id)
    ├─ Query all tickets (filtered by client if specified)
    ├─ Count: total, open, closed, within_sla, near_breach, breached
    ├─ Calculate: SLA compliance %, avg resolution time
    ├─ Compute: analyst stats, taxonomy distribution, monthly trend
    └─ Return metrics dict

  → render_template("dashboard.html", metrics=...)
    └─ Chart.js renders 5 visualizations from metrics data
       + JSON endpoint (/dashboard/metrics.json) for dynamic refresh
```

#### 5️⃣ Report Generation
```
User → /reports (POST report_type="SLA Summary Report", format="pdf")

  → report_routes generates report
    └─ report_generator.generate_report(app, type, format, user_id)
       ├─ Query tickets (filtered by type - e.g., only breached)
       ├─ Compute summary stats
       │
       ├─ IF format == "xlsx":
       │   └─ pandas.ExcelWriter → Summary sheet + Detail sheet
       │
       └─ IF format == "pdf":
           └─ ReportLab SimpleDocTemplate
              ├─ Title + timestamp
              ├─ Summary stats table
              └─ Detail table (color-coded headers, alternating rows)
       
       → Save file to generated_reports/
       → Create Report DB record
       → Return report for download
```

#### 6️⃣ Background Scheduler Loop
```
APScheduler (runs continuously in background)

Every {SYNC_INTERVAL_MINUTES} minutes:
  ├─ sync_cases_from_iris()              ← Full sync cycle
  ├─ recalculate_all_open_tickets()       ← Update SLA for all open tickets
  └─ send_breach_alerts_if_needed()       ← Email if breaches detected

Daily at {DAILY_REPORT_HOUR}:00 (if enabled):
  ├─ generate_report("SLA Summary", "pdf") ← Auto-generate daily report
  └─ send_daily_summary_email()            ← Email summary to admins/managers

Every Sunday at 03:00:
  └─ cleanup_old_reports(max_age_days=90)  ← Delete old reports from disk + DB
```

---

## 🏗 Architecture Diagram

```
                    ┌──────────────────┐
                    │   DFIR-IRIS API   │
                    │  (External SOAR)  │
                    └────────┬─────────┘
                             │ REST API (fetch cases/alerts)
                             │ with retry + exponential backoff
                    ┌────────▼─────────┐
                    │ iris_api_service  │  ← API wrapper layer
                    └────────┬─────────┘
                             │ raw case dicts
                    ┌────────▼─────────┐
                    │ field_mapping_svc │  ← Translation layer
                    │ normalize_ticket()│     (source → generic)
                    └────────┬─────────┘
                             │ normalized ticket dict
                    ┌────────▼─────────┐
                    │  sync_service     │  ← Orchestration layer
                    │  (upsert + log)   │     (fetch → normalize →
                    └────────┬─────────┘      save → SLA calc)
                             │
               ┌─────────────┼──────────────┐
               │             │              │
    ┌──────────▼──┐  ┌───────▼──────┐  ┌───▼───────────┐
    │   Ticket    │  │  SLARule     │  │  SyncLog      │
    │   (model)   │  │  (model)    │  │  (model)      │
    └──────┬──────┘  └───────┬──────┘  └───────────────┘
           │                 │
    ┌──────▼─────────────────▼───────┐
    │      sla_calculator            │  ← SLA engine
    │   (match rule → deadlines →    │
    │    status determination)       │
    └──────┬─────────────────────────┘
           │
    ┌──────▼──────────┐    ┌───────────────────┐
    │   Dashboard     │    │  report_generator  │
    │   (Chart.js     │    │  (PDF / Excel)     │
    │    metrics)     │    └─────────┬──────────┘
    └─────────────────┘              │
                            ┌────────▼─────────┐
                            │  email_service    │
                            │  (SMTP alerts)    │
                            └──────────────────┘

    ┌─────────────────────────────────────────────┐
    │           scheduler_service                  │
    │  (APScheduler — ties everything together)    │
    │  • Periodic sync + SLA recalc                │
    │  • Daily report generation                   │
    │  • Weekly report cleanup                     │
    └─────────────────────────────────────────────┘
```

### Project File Structure & Responsibility

```
automated-sla-tracker/
│
├── app.py                          # App factory + entry point
├── config.py                       # Environment-driven config (Dev/Prod/Test)
├── extensions.py                   # Shared Flask extensions (avoids circular imports)
├── cli.py                          # Custom Flask CLI commands (flask seed, clear-seed, etc)
├── requirements.txt                # Python dependencies (16 packages)
├── scripts/                        # Utility standalone python scripts
│   ├── seed_data.py                #   Seeds database + demo data
│   ├── clear_seed_data.py          #   Clears database seeded data
│   └── fix_field_mappings.py       #   Fixes IRIS field mappings keys
│
├── models/                         # 8 SQLAlchemy models
│   ├── client.py                   #   Multi-tenant organization
│   ├── user.py                     #   Auth + RBAC (3 roles, permission matrix)
│   ├── ticket.py                   #   Generic normalized ticket (source-agnostic)
│   ├── sla_rule.py                 #   Database-driven SLA rules (field_name/value)
│   ├── field_mapping.py            #   Source→local field translation definitions
│   ├── report.py                   #   Generated report tracking
│   ├── setting.py                  #   Runtime key/value settings
│   └── sync_log.py                 #   Sync run history
│
├── services/                       # 9 business logic services
│   ├── iris_api_service.py         #   DFIR-IRIS REST API (retry+backoff+pagination)
│   ├── field_mapping_service.py    #   normalize_ticket() + mapping resolution
│   ├── sla_calculator.py           #   THE SLA ENGINE (367 lines, zero hardcoded values)
│   ├── sync_service.py             #   Fetch→Normalize→Upsert→SLA→SoftDelete→Log
│   ├── business_hours.py           #   Mon-Fri 09:00-17:00 deadline calculation
│   ├── report_generator.py         #   PDF (ReportLab) + Excel (Pandas) generation
│   ├── scheduler_service.py        #   APScheduler job registration
│   ├── email_service.py            #   SMTP breach alerts + daily summaries
│   └── cleanup_service.py          #   90-day report retention policy
│
├── routes/                         # 7 Flask Blueprints + decorators
│   ├── decorators.py               #   @role_required + @permission_required
│   ├── auth_routes.py              #   Login/logout + rate limiting
│   ├── dashboard_routes.py         #   Metrics + Chart.js data
│   ├── ticket_routes.py            #   Ticket list/detail + sync trigger
│   ├── sla_rule_routes.py          #   CRUD for SLA rules (Admin only)
│   ├── report_routes.py            #   Generate + download reports
│   └── settings_routes.py          #   IRIS config, mappings, clients, sync logs
│
├── templates/                      # 10 Jinja2 + Bootstrap 5 templates
│   ├── base.html                   #   Layout with navigation sidebar
│   ├── login.html, dashboard.html, tickets.html, ticket_detail.html,
│   │   sla_rules.html, sla_rule_form.html, reports.html,
│   │   settings.html, clients.html
│
├── static/css/style.css            # Custom CSS
├── tests/                          # pytest test suite
│   ├── conftest.py                 #   Fixtures (app, db, clients, rules)
│   ├── test_sla_calculator.py      #   SLA engine unit tests
│   └── test_sync_service.py        #   Sync pipeline integration tests
│
├── generated_reports/              # PDF/Excel output (auto-cleaned after 90 days)
└── instance/                       # SQLite database lives here
```

---

## 🧠 Technologies Learned

| Technology | What We Learned |
|-----------|----------------|
| **Flask** | Application factory, Blueprints, Jinja2 templating, context processors, request handling |
| **SQLAlchemy** | ORM modeling, relationships, unique constraints, composite keys, query building |
| **Flask-Login** | Session management, user_loader, `@login_required`, `is_active` property |
| **Flask-Migrate** | Database schema migrations with Alembic |
| **Flask-WTF** | CSRF protection for web forms |
| **APScheduler** | Background job scheduling (interval + cron triggers), app context management |
| **requests** | REST API integration, retry logic, exponential backoff |
| **Chart.js** | Client-side data visualization (doughnut, bar, line charts) |
| **ReportLab** | PDF generation with tables, styles, and layouts |
| **Pandas + OpenPyXL** | Excel report generation with multi-sheet workbooks |
| **Bootstrap 5** | Responsive UI design, cards, tables, badges, modals |
| **pytest** | Test fixtures, mocking with `unittest.mock`, test isolation |
| **python-dotenv** | Environment variable management for secrets |
| **Git** | Version control, commits, project management |

---

## 🏆 Summary of Achievements

### By The Numbers

| Metric | Count |
|--------|-------|
| Python source files | **30+** |
| Total lines of code | **~4,000+** |
| Database models | **8** |
| Service modules | **9** |
| Route Blueprints | **6** |
| HTML templates | **10** |
| Report types | **4** (PDF + Excel) |
| Test files | **3** (conftest + 2 test modules) |
| Identified & closed design gaps | **11** |

### Design Gaps Addressed

| Gap # | Description | Solution |
|-------|-------------|----------|
| Gap #1 | Multi-tenancy | `client_id` on tickets, rules, mappings; client-scoped queries |
| Gap #2 | SLA rule priority/ordering | `priority` column with ASC ordering, first-match-wins |
| Gap #3 | Business hours SLA | `add_business_minutes()` with Mon-Fri 09:00-17:00 + client timezone |
| Gap #4 | Pause/resume tracking | `paused_at` + `total_paused_minutes` with deadline-shift semantics |
| Gap #5 | Timezone handling | All-UTC storage, `_as_aware()` helper, `_parse_datetime()` forces UTC |
| Gap #6 | API pagination | Envelope-aware pagination loop with fallback heuristic |
| Gap #7 | API retry/backoff | `_request_with_retry()` — 3 retries, exponential backoff, retryable status codes |
| Gap #8 | Sync upsert + soft-delete | Composite key upsert + `status='deleted_in_source'` for missing tickets |
| Gap #9 | Security hardening | CSRF, session cookies, login rate limiting, password hashing |
| Gap #10 | Reopened ticket detection | Clear `closed_at` + force SLA re-evaluation on reopen |
| Gap #11 | Report cleanup | 90-day retention policy with weekly cron job |

### Key Design Principles

1. **Zero hardcoded severity/priority values** — SLA rules are purely data-driven (`field_name`/`field_value` matching)
2. **Source-agnostic architecture** — field mapping layer means switching from IRIS to another platform requires only mapping row changes
3. **Multi-tenant by design** — every data record is scoped to a client; cross-client contamination is impossible
4. **Separation of concerns** — API fetching, normalization, SLA logic, sync orchestration, and reporting are all isolated services
5. **Defensive coding** — graceful error handling at every layer; one bad ticket doesn't crash the entire sync

---

*This report was generated as part of the 4-week internship project. The system is fully functional with demo data and can be connected to a live DFIR-IRIS instance by configuring the `.env` file.*
