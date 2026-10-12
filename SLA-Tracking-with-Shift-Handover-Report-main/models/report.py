"""
models/report.py
-----------------
Tracks generated report files (PDF/Excel) so users can browse and
re-download past reports from the Reports page.
"""

from datetime import datetime, timezone
from extensions import db

REPORT_TYPE_SLA_SUMMARY = "SLA Summary Report"
REPORT_TYPE_DETAILED_TICKET = "Detailed Ticket SLA Report"
REPORT_TYPE_BREACHED = "Breached Tickets Report"
REPORT_TYPE_ANALYST_PERFORMANCE = "Analyst Performance Report"

REPORT_TYPES = (
    REPORT_TYPE_SLA_SUMMARY,
    REPORT_TYPE_DETAILED_TICKET,
    REPORT_TYPE_BREACHED,
    REPORT_TYPE_ANALYST_PERFORMANCE,
)

FORMAT_PDF = "pdf"
FORMAT_EXCEL = "xlsx"


class Report(db.Model):
    __tablename__ = "reports"

    id = db.Column(db.Integer, primary_key=True)
    report_name = db.Column(db.String(255), nullable=False)
    report_type = db.Column(db.String(100), nullable=False)
    file_format = db.Column(db.String(10), nullable=False, default=FORMAT_PDF)
    file_path = db.Column(db.String(500), nullable=False)

    generated_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    generated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return f"<Report {self.report_name} ({self.file_format})>"
