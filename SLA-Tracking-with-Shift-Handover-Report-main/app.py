"""
app.py
------
Application factory + entry point for the Automated SLA Tracking and
Report Generation System.

Run with:
    python app.py
or:
    flask run
"""

import os
import logging
from datetime import timedelta

from flask import Flask, redirect, url_for

from config import config_by_name
from extensions import db, login_manager, migrate, csrf


def create_app(config_name=None):
    config_name = config_name or os.environ.get("FLASK_ENV", "development")

    app = Flask(__name__)
    app.config.from_object(config_by_name.get(config_name, config_by_name["development"]))

    # --- Gap #9: Session cookie hardening ---
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config.setdefault("PERMANENT_SESSION_LIFETIME", timedelta(hours=8))
    if not app.config.get("DEBUG", False):
        app.config.setdefault("SESSION_COOKIE_SECURE", True)

    @app.after_request
    def add_security_headers(response):
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://unpkg.com; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://unpkg.com; "
            "font-src 'self' https://cdn.jsdelivr.net data:; "
            "img-src 'self' data: https://*.tile.openstreetmap.org;"
        )
        return response

    # Ensure instance/ and generated_reports/ exist for SQLite + report output (safely handled on read-only serverless filesystems)
    try:
        os.makedirs(os.path.join(app.root_path, "instance"), exist_ok=True)
    except OSError as err:
        logging.getLogger(__name__).debug("Instance directory creation skipped: %s", err)

    try:
        os.makedirs(app.config["REPORTS_FOLDER"], exist_ok=True)
    except OSError as err:
        logging.getLogger(__name__).debug("Reports directory creation skipped: %s", err)

    # --- Extensions ---
    db.init_app(app)
    login_manager.init_app(app)
    migrate.init_app(app, db)
    csrf.init_app(app)  # Gap #9: CSRF protection

    # Dynamic migration check to add missing columns/tables to local SQLite DB if needed
    with app.app_context():
        db.create_all()
        try:
            db.session.execute(db.text("SELECT business_hours_start FROM clients LIMIT 1"))
        except Exception:
            db.session.rollback()
            try:
                db.session.execute(db.text("ALTER TABLE clients ADD COLUMN business_hours_start VARCHAR(10)"))
                db.session.execute(db.text("ALTER TABLE clients ADD COLUMN business_hours_end VARCHAR(10)"))
                db.session.execute(db.text("ALTER TABLE clients ADD COLUMN business_hours_days VARCHAR(50)"))
                db.session.commit()
                logging.getLogger(__name__).info("Migrated database: added business hours columns to clients table")
            except Exception as e:
                db.session.rollback()
                logging.getLogger(__name__).error(f"Failed to migrate database columns: {e}")

        try:
            db.session.execute(db.text("SELECT breach_reason FROM tickets LIMIT 1"))
        except Exception:
            db.session.rollback()
            try:
                db.session.execute(db.text("ALTER TABLE tickets ADD COLUMN breach_reason VARCHAR(100)"))
                db.session.execute(db.text("ALTER TABLE tickets ADD COLUMN breach_notes TEXT"))
                db.session.commit()
                logging.getLogger(__name__).info("Migrated database: added breach_reason and breach_notes columns to tickets table")
            except Exception as e:
                db.session.rollback()
                logging.getLogger(__name__).error(f"Failed to migrate tickets columns: {e}")

    # --- Models (import after db.init_app so metadata registers correctly) ---
    from models import User  # noqa: F401  (registers all models via models/__init__.py)

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    @app.context_processor
    def inject_notifications():
        from flask import url_for
        from flask_login import current_user
        from datetime import datetime, timezone
        if not current_user.is_authenticated:
            return {"notifications": [], "unread_notification_count": 0}

        try:
            from models import Ticket
            from models.ticket import SLA_BREACHED, SLA_CLOSED_AFTER_BREACH, SLA_NEAR_BREACH

            notifications = []
            now = datetime.now(timezone.utc)

            open_tickets = Ticket.query.filter(Ticket.status.notin_(["closed", "resolved", "cancelled", "deleted", "deleted_in_source"])).all()

            for t in open_tickets:
                notif_dt = t.updated_at or t.created_at_source or t.created_at or datetime.min

                if t.sla_status == SLA_NEAR_BREACH:
                    rem_mins = 0
                    if t.resolution_deadline:
                        dl = t.resolution_deadline.replace(tzinfo=timezone.utc) if t.resolution_deadline.tzinfo is None else t.resolution_deadline
                        rem_mins = max(0, int((dl - now).total_seconds() / 60))

                    client_name = t.client.name if t.client else "N/A"
                    notifications.append({
                        "id": f"near_{t.id}",
                        "type": "near_breach",
                        "title": f"Near Breach: {t.external_id}",
                        "message": f"'{t.title}' ({client_name}) has {rem_mins} mins remaining until SLA breach!",
                        "time_str": f"{rem_mins}m left",
                        "badge_color": "warning",
                        "icon": "bi-exclamation-triangle-fill",
                        "link": url_for("tickets.ticket_detail", ticket_id=t.id),
                        "created_at": notif_dt,
                        "ticket_id": t.id or 0,
                    })
                elif t.sla_status in (SLA_BREACHED, SLA_CLOSED_AFTER_BREACH):
                    breach_mins = t.breach_duration_minutes or 0
                    client_name = t.client.name if t.client else "N/A"
                    notifications.append({
                        "id": f"breach_{t.id}",
                        "type": "breach",
                        "title": f"SLA Breached: {t.external_id}",
                        "message": f"'{t.title}' ({client_name}) breached SLA by {breach_mins} mins!",
                        "time_str": f"{breach_mins}m breach",
                        "badge_color": "danger",
                        "icon": "bi-alarm-fill",
                        "link": url_for("tickets.ticket_detail", ticket_id=t.id),
                        "created_at": notif_dt,
                        "ticket_id": t.id or 0,
                    })

            def _sort_key(item):
                dt = item.get("created_at")
                tid = item.get("ticket_id", 0)
                if isinstance(dt, datetime):
                    if dt.tzinfo is not None:
                        ts = dt.timestamp()
                    else:
                        ts = dt.replace(tzinfo=timezone.utc).timestamp()
                else:
                    ts = 0.0
                return (ts, tid)

            # Sort newer alerts on top (newest timestamp and ticket ID first)
            notifications.sort(key=_sort_key, reverse=True)
            return {
                "notifications": notifications,
                "unread_notification_count": len(notifications),
            }
        except Exception:
            return {"notifications": [], "unread_notification_count": 0}

    # --- Blueprints ---
    from routes.auth_routes import auth_bp
    from routes.dashboard_routes import dashboard_bp
    from routes.ticket_routes import ticket_bp
    from routes.sla_rule_routes import sla_rule_bp
    from routes.report_routes import report_bp
    from routes.settings_routes import settings_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(ticket_bp)
    app.register_blueprint(sla_rule_bp)
    app.register_blueprint(report_bp)
    app.register_blueprint(settings_bp)

    # --- CLI Commands ---
    from cli import register_cli_commands
    register_cli_commands(app)

    @app.route("/")
    def root():
        return redirect(url_for("dashboard.index"))

    # --- Jinja helpers used across templates ---
    from models.ticket import SLA_BADGE_COLOR

    @app.context_processor
    def inject_helpers():
        from services.iris_api_service import fetch_classifications
        
        def severity_display(val):
            if not val:
                return "-"
            try:
                classifications = fetch_classifications()
                for c in classifications:
                    if c.get("name") == val:
                        return c.get("name_expanded") or val
            except Exception as err:
                logging.getLogger(__name__).warning("Severity display classification lookup skipped: %s", err)
            return val

        return dict(
            sla_badge_color=SLA_BADGE_COLOR,
            severity_display=severity_display
        )

    # --- Logging ---
    logging.basicConfig(level=logging.INFO)

    # --- Scheduler (skip during CLI commands like `flask db migrate` or reloader parent process) ---
    if not app.config.get("TESTING") and os.environ.get("RUN_SCHEDULER", "1") == "1":
        if os.environ.get("WERKZEUG_RUN_MAIN") == "true" or not app.debug:
            with app.app_context():
                from services.scheduler_service import init_scheduler
                try:
                    init_scheduler(app)
                except Exception:
                    logging.getLogger(__name__).exception("Failed to start scheduler")

    return app


app = create_app()


if __name__ == "__main__":
    debug_mode = app.config.get("DEBUG", True)
    app.run(host="0.0.0.0", port=5000, debug=debug_mode, use_reloader=True)
