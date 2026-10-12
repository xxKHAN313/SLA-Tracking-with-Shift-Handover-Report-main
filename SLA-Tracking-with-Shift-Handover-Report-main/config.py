"""
config.py
---------
Central application configuration, loaded from environment variables (.env).
Nothing sensitive is hardcoded here - only defaults for local development.
"""

import os
from dotenv import load_dotenv

basedir = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(basedir, ".env"))


def _bool(value, default=False):
    if value is None:
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on")


IS_VERCEL = bool(os.environ.get("VERCEL") or os.environ.get("VERCEL_ENV"))

# Fix legacy Postgres URL scheme (e.g. from Render / Heroku) for SQLAlchemy 2.x
_raw_db_url = os.environ.get("DATABASE_URL")
if _raw_db_url and _raw_db_url.startswith("postgres://"):
    _raw_db_url = _raw_db_url.replace("postgres://", "postgresql://", 1)


class Config:
    # --- Flask core ---
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key-change-me")
    TEMPLATES_AUTO_RELOAD = True
    SEND_FILE_MAX_AGE_DEFAULT = 0

    # --- Database ---
    is_vercel = _bool(os.environ.get("VERCEL"))
    # Note: /tmp path is used exclusively in the Vercel serverless environment (is_vercel == True) for ephemeral execution.
    default_db_path = "/tmp/sla_tracker.db" if is_vercel else os.path.join(basedir, "instance", "sla_tracker.db")

    SQLALCHEMY_DATABASE_URI = _raw_db_url or (
        "sqlite:///" + default_db_path
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # --- DFIR-IRIS ---
    IRIS_BASE_URL = os.environ.get("IRIS_BASE_URL", "")
    IRIS_API_KEY = os.environ.get("IRIS_API_KEY", "")
    IRIS_VERIFY_SSL = _bool(os.environ.get("IRIS_VERIFY_SSL"), True)
    IRIS_TIMEOUT_SECONDS = int(os.environ.get("IRIS_TIMEOUT_SECONDS", 30))

    # --- Scheduler ---
    SYNC_INTERVAL_MINUTES = int(os.environ.get("SYNC_INTERVAL_MINUTES", 15))
    SCHEDULER_ENABLED = _bool(os.environ.get("SCHEDULER_ENABLED"), True)
    DAILY_REPORT_ENABLED = _bool(os.environ.get("DAILY_REPORT_ENABLED"), False)
    DAILY_REPORT_HOUR = int(os.environ.get("DAILY_REPORT_HOUR", 7))

    # --- Timezone ---
    DEFAULT_TIMEZONE = os.environ.get("DEFAULT_TIMEZONE", "UTC")

    # --- Email ---
    EMAIL_NOTIFICATIONS_ENABLED = _bool(os.environ.get("EMAIL_NOTIFICATIONS_ENABLED"), False)
    SMTP_HOST = os.environ.get("SMTP_HOST", "")
    SMTP_PORT = int(os.environ.get("SMTP_PORT", 587))
    SMTP_USER = os.environ.get("SMTP_USER", "")
    SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
    SMTP_FROM_EMAIL = os.environ.get("SMTP_FROM_EMAIL", "")
    SMTP_USE_TLS = _bool(os.environ.get("SMTP_USE_TLS"), True)

    # --- Reports ---
    # Note: /tmp path is used exclusively in the Vercel serverless environment (is_vercel == True) for ephemeral execution.
    default_reports_folder = "/tmp/generated_reports" if is_vercel else os.path.join(basedir, "generated_reports")
    REPORTS_FOLDER = os.environ.get("REPORTS_FOLDER") or default_reports_folder


class DevelopmentConfig(Config):
    DEBUG = True


class ProductionConfig(Config):
    DEBUG = False


class TestingConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    WTF_CSRF_ENABLED = False


config_by_name = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
}
