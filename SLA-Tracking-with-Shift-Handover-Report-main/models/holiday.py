"""
models/holiday.py
-------------------
Holiday calendar model for excluding public/company holidays
from SLA business hours calculations.
"""

from datetime import datetime, timezone
from extensions import db


class Holiday(db.Model):
    __tablename__ = "holidays"

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(
        db.Integer, db.ForeignKey("clients.id", ondelete="CASCADE"), nullable=True, index=True
    )
    name = db.Column(db.String(150), nullable=False)
    holiday_date = db.Column(db.Date, nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    client = db.relationship("Client", backref=db.backref("holidays", lazy="dynamic", cascade="all, delete-orphan"))

    def __repr__(self):
        scope = f"Client {self.client_id}" if self.client_id else "Global"
        return f"<Holiday '{self.name}' on {self.holiday_date} ({scope})>"
