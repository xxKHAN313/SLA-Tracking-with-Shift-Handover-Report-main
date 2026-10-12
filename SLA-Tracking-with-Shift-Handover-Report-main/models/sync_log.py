"""
models/sync_log.py
-------------------
Records each DFIR-IRIS sync run for debugging and auditing.
"""

from datetime import datetime, timezone
from extensions import db

SYNC_STATUS_RUNNING = "running"
SYNC_STATUS_SUCCESS = "success"
SYNC_STATUS_FAILED = "failed"
SYNC_STATUS_PARTIAL = "partial"


class SyncLog(db.Model):
    __tablename__ = "sync_logs"

    id = db.Column(db.Integer, primary_key=True)
    sync_started_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    sync_finished_at = db.Column(db.DateTime, nullable=True)
    status = db.Column(db.String(20), nullable=False, default=SYNC_STATUS_RUNNING)

    records_fetched = db.Column(db.Integer, default=0)
    records_created = db.Column(db.Integer, default=0)
    records_updated = db.Column(db.Integer, default=0)

    error_message = db.Column(db.Text, nullable=True)

    def mark_success(self, fetched=0, created=0, updated=0):
        self.status = SYNC_STATUS_SUCCESS
        self.records_fetched = fetched
        self.records_created = created
        self.records_updated = updated
        self.sync_finished_at = datetime.now(timezone.utc)

    def mark_failed(self, error_message):
        self.status = SYNC_STATUS_FAILED
        self.error_message = str(error_message)[:4000]
        self.sync_finished_at = datetime.now(timezone.utc)

    def __repr__(self):
        return f"<SyncLog {self.id} {self.status} @ {self.sync_started_at}>"
