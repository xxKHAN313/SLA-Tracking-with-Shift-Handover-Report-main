"""
models/client.py
-----------------
Multi-tenant client/organization model.

This is the anchor for multi-tenancy: SLA rules, field mappings, and tickets
are all scoped to a client via ``client_id``. IRIS tickets are mapped to
clients by matching ``case_customer_id`` in the IRIS payload to
``Client.iris_customer_id``.

Each client can also carry its own timezone (for business-hours SLA
calculations), city/region (for regional SLA mapping), and display name.
"""

from datetime import datetime, timezone
from extensions import db


class Client(db.Model):
    __tablename__ = "clients"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), unique=True, nullable=False)

    # Maps to IRIS ``case_customer_id`` so the sync service can route
    # incoming tickets to the right client automatically.
    iris_customer_id = db.Column(db.String(100), nullable=True, index=True)

    # Per-client city/region for Pakistan map visualizations
    city = db.Column(db.String(100), nullable=True, default="Islamabad")

    # Per-client timezone for business-hours calculations (Gap #5).
    # Stored as an IANA tz name (e.g. "Asia/Karachi", "America/New_York").
    timezone = db.Column(db.String(50), nullable=False, default="Asia/Karachi")

    # Custom business hours parameters (None defaults to global settings)
    business_hours_start = db.Column(db.String(10), nullable=True)
    business_hours_end = db.Column(db.String(10), nullable=True)
    business_hours_days = db.Column(db.String(50), nullable=True)

    is_active = db.Column(db.Boolean, default=True, nullable=False)

    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # --- Relationships ---
    sla_rules = db.relationship("SLARule", backref="client", lazy="dynamic")
    tickets = db.relationship("Ticket", backref="client", lazy="dynamic")
    field_mappings = db.relationship("FieldMapping", backref="client", lazy="dynamic")

    def __repr__(self):
        return f"<Client {self.name} (iris={self.iris_customer_id}, city={self.city})>"
