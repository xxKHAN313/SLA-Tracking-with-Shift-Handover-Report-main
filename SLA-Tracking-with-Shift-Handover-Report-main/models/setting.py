"""
models/setting.py
------------------
Simple key/value settings table for values that admins may want to change
at runtime without redeploying (e.g. sync interval, IRIS base URL override).

Sensitive values like the actual IRIS API key should stay in environment
variables (.env) - this table is for non-secret, admin-editable settings.
Where a setting also has an env var fallback, the DB value takes precedence
if present, otherwise config.py's env-based default is used.
"""

from datetime import datetime, timezone
from extensions import db


class Setting(db.Model):
    __tablename__ = "settings"

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(150), unique=True, nullable=False, index=True)
    value = db.Column(db.String(1000), nullable=True)
    description = db.Column(db.String(500), nullable=True)
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    @staticmethod
    def get(key, default=None):
        row = Setting.query.filter_by(key=key).first()
        return row.value if row else default

    @staticmethod
    def set(key, value, description=None):
        row = Setting.query.filter_by(key=key).first()
        if row:
            row.value = value
            if description:
                row.description = description
        else:
            row = Setting(key=key, value=value, description=description)
            db.session.add(row)
        db.session.commit()
        return row

    def __repr__(self):
        return f"<Setting {self.key}={self.value}>"
