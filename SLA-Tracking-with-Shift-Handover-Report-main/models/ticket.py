"""
models/ticket.py
-----------------
Local, generic ticket/incident model. This is the normalized representation
that every source system (currently DFIR-IRIS, potentially others later)
gets mapped into via FieldMapping + normalize_ticket().

severity / priority / criticality are ALL optional - an organization may use
only one of these fields (or a completely different one, matched via
SLARule.field_name) and the rest simply stay NULL.

Multi-tenancy: every ticket belongs to a Client via ``client_id``.
"""

from datetime import datetime, timezone
from extensions import db

# SLA status values used across the app - defined once, referenced everywhere
SLA_WITHIN = "Within SLA"
SLA_NEAR_BREACH = "Near Breach"
SLA_BREACHED = "Breached"
SLA_CLOSED_WITHIN = "Closed Within SLA"
SLA_CLOSED_AFTER_BREACH = "Closed After Breach"
SLA_NO_RULE = "No Matching Rule"

ALL_SLA_STATUSES = (
    SLA_WITHIN, SLA_NEAR_BREACH, SLA_BREACHED,
    SLA_CLOSED_WITHIN, SLA_CLOSED_AFTER_BREACH, SLA_NO_RULE,
)

SLA_BADGE_COLOR = {
    SLA_WITHIN: "success",          # green
    SLA_CLOSED_WITHIN: "success",   # green
    SLA_NEAR_BREACH: "warning",     # yellow
    SLA_BREACHED: "danger",         # red
    SLA_CLOSED_AFTER_BREACH: "danger",  # red
    SLA_NO_RULE: "secondary",       # gray
}


class Ticket(db.Model):
    __tablename__ = "tickets"

    id = db.Column(db.Integer, primary_key=True)

    # --- Multi-tenant scoping (Gap #1) ---
    client_id = db.Column(
        db.Integer, db.ForeignKey("clients.id"), nullable=True, index=True
    )

    # --- Source identity ---
    external_id = db.Column(db.String(100), nullable=False, index=True)
    source_system = db.Column(db.String(50), nullable=False, default="dfir_iris", index=True)

    # --- Normalized generic fields ---
    title = db.Column(db.String(500), nullable=True)
    status = db.Column(db.String(100), nullable=True, index=True)

    severity = db.Column(db.String(100), nullable=True, index=True)
    priority = db.Column(db.String(100), nullable=True, index=True)
    criticality = db.Column(db.String(100), nullable=True, index=True)

    assigned_to = db.Column(db.String(200), nullable=True, index=True)

    created_at_source = db.Column(db.DateTime, nullable=True)
    closed_at_source = db.Column(db.DateTime, nullable=True)

    # --- SLA computed fields ---
    response_deadline = db.Column(db.DateTime, nullable=True)
    resolution_deadline = db.Column(db.DateTime, nullable=True)

    response_sla_status = db.Column(db.String(50), nullable=True)
    resolution_sla_status = db.Column(db.String(50), nullable=True)
    sla_status = db.Column(db.String(50), nullable=True, default=SLA_NO_RULE, index=True)

    breach_duration_minutes = db.Column(db.Integer, nullable=True, default=0)

    sla_rule_id = db.Column(db.Integer, db.ForeignKey("sla_rules.id"), nullable=True)

    # --- Pause/resume tracking (Gap #4) ---
    # Set when the ticket enters a "pause" status; cleared when it leaves.
    paused_at = db.Column(db.DateTime, nullable=True)
    # Accumulated total pause time across all pause/unpause cycles.
    total_paused_minutes = db.Column(db.Integer, nullable=False, default=0)

    # --- Breach Root Cause Tagging ---
    breach_reason = db.Column(db.String(100), nullable=True, index=True)
    breach_notes = db.Column(db.Text, nullable=True)

    # --- Notification flags ---
    near_breach_notified = db.Column(db.Boolean, default=False, nullable=False)
    breach_notified = db.Column(db.Boolean, default=False, nullable=False)

    # --- Raw payload retained for reprocessing if mappings change later ---
    raw_payload_json = db.Column(db.Text, nullable=True)

    last_synced_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        db.UniqueConstraint(
            "client_id", "source_system", "external_id",
            name="uq_client_source_external_id",
        ),
    )

    def sla_percentage_used(self, now=None):
        """Returns the percentage of the resolution SLA window consumed (0-100+)."""
        if not self.resolution_deadline or not self.created_at_source:
            return None
        now = now or datetime.now(timezone.utc)
        total_window = (self.resolution_deadline - self.created_at_source).total_seconds()
        if total_window <= 0:
            return 100
        reference_time = self.closed_at_source or now
        elapsed = (reference_time - self.created_at_source).total_seconds()
        return round((elapsed / total_window) * 100, 1)

    def is_open(self):
        if not self.status:
            return True
        status_lower = self.status.strip().lower()
        if status_lower in ("deleted_in_source", "closed", "resolved", "cancelled", "deleted"):
            return False
        return self.closed_at_source is None

    def __repr__(self):
        return f"<Ticket {self.external_id} client={self.client_id} status={self.status} sla={self.sla_status}>"
