"""
services/scheduler_service.py
--------------------------------
Configures APScheduler background jobs:
  - Periodic DFIR-IRIS sync + SLA recalculation (interval from config/Settings)
  - Optional daily SLA summary report + email notifications
  - Weekly report cleanup (Gap #11)
"""

import logging

from extensions import scheduler

logger = logging.getLogger(__name__)


def _run_sync_job(app):
    """Wrapper that pushes an app context, since APScheduler jobs run
    outside of a normal Flask request context."""
    with app.app_context():
        from services.sync_service import sync_cases_from_iris
        from services.sla_calculator import recalculate_all_open_tickets

        try:
            result = sync_cases_from_iris()
            logger.info("Scheduled IRIS sync complete: %s", result)
        except Exception:
            logger.exception("Scheduled IRIS sync failed")
            return

        try:
            recalc = recalculate_all_open_tickets()
            logger.info("Post-sync SLA recalculation complete: %s", recalc)
        except Exception:
            logger.exception("Post-sync SLA recalculation failed")

        if app.config.get("EMAIL_NOTIFICATIONS_ENABLED"):
            from services.email_service import send_breach_alerts_if_needed
            try:
                send_breach_alerts_if_needed(app)
            except Exception:
                logger.exception("Breach alert email dispatch failed")


def _run_daily_report_job(app):
    with app.app_context():
        from services.report_generator import generate_report

        try:
            generate_report(app, "SLA Summary Report", "pdf")
            logger.info("Daily SLA summary report generated")
        except Exception:
            logger.exception("Daily report generation failed")

        if app.config.get("EMAIL_NOTIFICATIONS_ENABLED"):
            from services.email_service import send_daily_summary_email
            try:
                send_daily_summary_email(app)
            except Exception:
                logger.exception("Daily summary email dispatch failed")


def _run_cleanup_job(app):
    """Gap #11: Weekly cleanup of old generated reports."""
    with app.app_context():
        from services.cleanup_service import cleanup_old_reports

        try:
            result = cleanup_old_reports()
            logger.info("Report cleanup complete: %s", result)
        except Exception:
            logger.exception("Report cleanup failed")


def _run_sla_alert_job(app):
    """Frequent 1-minute background task to recalculate SLA status for open
    tickets and immediately dispatch Near Breach and Breach email alerts."""
    with app.app_context():
        from services.sla_calculator import recalculate_all_open_tickets
        try:
            recalculate_all_open_tickets()
        except Exception:
            logger.exception("Frequent SLA recalculation failed")
            return

        if app.config.get("EMAIL_NOTIFICATIONS_ENABLED"):
            from services.email_service import send_breach_alerts_if_needed
            try:
                send_breach_alerts_if_needed(app)
            except Exception:
                logger.exception("Breach alert email dispatch failed")


def init_scheduler(app):
    """Registers and starts scheduled jobs. Called once from app.py at
    startup. Safe to call in debug mode - guarded against the reloader
    starting the scheduler twice."""
    if not app.config.get("SCHEDULER_ENABLED", True):
        logger.info("Scheduler disabled via config.")
        return

    if scheduler.running:
        return

    interval_minutes = app.config.get("SYNC_INTERVAL_MINUTES", 15)
    scheduler.add_job(
        func=_run_sync_job,
        args=[app],
        trigger="interval",
        minutes=interval_minutes,
        id="iris_sync_job",
        replace_existing=True,
        max_instances=1,
    )

    # Real-time SLA monitoring & email alert dispatch (every 1 minute)
    scheduler.add_job(
        func=_run_sla_alert_job,
        args=[app],
        trigger="interval",
        minutes=1,
        id="sla_alert_job",
        replace_existing=True,
        max_instances=1,
    )

    if app.config.get("DAILY_REPORT_ENABLED"):
        hour = app.config.get("DAILY_REPORT_HOUR", 7)
        scheduler.add_job(
            func=_run_daily_report_job,
            args=[app],
            trigger="cron",
            hour=hour,
            id="daily_report_job",
            replace_existing=True,
        )

    # Gap #11: Weekly report cleanup (every Sunday at 03:00)
    scheduler.add_job(
        func=_run_cleanup_job,
        args=[app],
        trigger="cron",
        day_of_week="sun",
        hour=3,
        id="report_cleanup_job",
        replace_existing=True,
    )

    scheduler.start()
    logger.info("Scheduler started - sync every %s minute(s), SLA alerts every 1 minute.", interval_minutes)
