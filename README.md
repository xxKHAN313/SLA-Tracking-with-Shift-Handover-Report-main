# SLA Operations Tracker

**Ticket monitoring and shift handover reporting**

A Flask application for monitoring ticket SLA deadlines, integrating DFIR-IRIS case data, and producing operational reports.

Python · Flask · SQLAlchemy · SQLite · APScheduler · ReportLab

> Academic prototype. Features below are grounded in the source; validation limits are recorded below.

## What it does

- Ticket dashboard with SLA states and breach information.
- Client-specific rules, business hours, and holiday handling.
- DFIR-IRIS synchronization and configurable field mappings.
- Report generation, scheduled jobs, and email service integration.
- Role-based views, audit logging, and a pytest suite.

## Getting started

Commands below use Windows PowerShell.

```powershell
git clone https://github.com/xxKHAN313/SLA-Tracking-with-Shift-Handover-Report-main.git
cd SLA-Tracking-with-Shift-Handover-Report-main/SLA-Tracking-with-Shift-Handover-Report-main
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
# Configure .env before initializing the application.
.\.venv\Scripts\python.exe -m flask --app app seed
.\.venv\Scripts\python.exe app.py
```

Set a unique `SECRET_KEY`. For a local demonstration, leave `DATABASE_URL` empty to use SQLite, set `SCHEDULER_ENABLED=false`, and disable email notifications. Configure IRIS only if you have an authorized instance; `flask seed` attempts an IRIS sync, so leave its URL and key empty for a disconnected demonstration.

The seed command creates demonstration accounts defined in `cli.py`. These are public defaults, suitable only for an isolated local demonstration. The development server opens at `http://127.0.0.1:5000`. Do not treat the bundled deployment configuration as evidence of production readiness.

## Code map

`app.py` — application factory; `models` — database entities; `routes` — web views; `services` — SLA calculation, integrations, and reporting; `templates` — interface; `tests` — pytest coverage.

Browse the [application source](SLA-Tracking-with-Shift-Handover-Report-main/).

## Validation and limitations

All tracked Python files passed syntax parsing. The test suite was not executed because this environment lacks pip and the project dependencies. Run `python -m pytest -q` in the application directory after installation.

## Credits and license

This repository contains academic work. Existing source credits and acknowledgements are retained; individual contributions should be confirmed before attributing the whole project to one person.

See the existing [LICENSE](SLA-Tracking-with-Shift-Handover-Report-main/LICENSE) for the project’s license terms.
