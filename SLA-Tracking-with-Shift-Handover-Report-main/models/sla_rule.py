"""
models/sla_rule.py
-------------------
Generic, database-driven SLA rule definitions, scoped per client.

CRITICAL DESIGN RULE: no severity/priority/criticality values are hardcoded
anywhere in code. A rule simply says "when ticket.<field_name> == <field_value>,
these are the SLA minutes to use". This lets each organization define its own
taxonomy (Critical/High/Medium/Low, P1-P4, Sev1-Sev4, or anything else)
purely through data, without touching Python code.

Multi-tenancy: every rule belongs to exactly one Client via ``client_id``.
Two clients can each have a rule for field_value="Critical" with different
SLA minutes — they will never collide because the matching engine filters
by client first.

Rule priority: when multiple rules could match a ticket (e.g. it has both
severity=Critical AND priority=P1), the rule with the lowest ``priority``
number is evaluated first (first-match-wins).

Example rows:
    client_id=1, rule_name="Critical Severity SLA", field_name="severity",
    field_value="Critical", response_sla_minutes=60, resolution_sla_minutes=480,
    priority=10

    client_id=2, rule_name="P1 Priority SLA", field_name="priority",
    field_value="P1", response_sla_minutes=30, resolution_sla_minutes=240,
    priority=10
"""

from datetime import datetime, timezone
from extensions import db


class SLARuleCondition(db.Model):
    __tablename__ = "sla_rule_conditions"

    id = db.Column(db.Integer, primary_key=True)
    sla_rule_id = db.Column(
        db.Integer,
        db.ForeignKey("sla_rules.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    field_name = db.Column(db.String(100), nullable=False)
    field_value = db.Column(db.String(150), nullable=False)

    def __repr__(self):
        return f"<SLARuleCondition {self.field_name}={self.field_value}>"


class SLARule(db.Model):
    __tablename__ = "sla_rules"

    id = db.Column(db.Integer, primary_key=True)

    # --- Multi-tenant scoping (Gap #1) ---
    client_id = db.Column(
        db.Integer, db.ForeignKey("clients.id"), nullable=False, index=True
    )

    rule_name = db.Column(db.String(150), nullable=False)

    description = db.Column(db.Text, nullable=True)
    escalation_email = db.Column(db.String(255), nullable=True)

    created_by = db.Column(db.String(100), nullable=True)
    updated_by = db.Column(db.String(100), nullable=True)

    # --- Explicit evaluation order (Gap #2) ---
    # Lower number = evaluated first. First match wins.
    priority = db.Column(db.Integer, nullable=False, default=0)

    # Conditions relationship
    conditions = db.relationship(
        "SLARuleCondition",
        backref="sla_rule",
        cascade="all, delete-orphan",
        lazy="joined"
    )

    response_sla_minutes = db.Column(db.Integer, nullable=True)  # optional
    resolution_sla_minutes = db.Column(db.Integer, nullable=False)

    warning_threshold_percent = db.Column(db.Integer, nullable=False, default=80)

    # --- Business-hours flag (Gap #3) ---
    # When True, deadlines are computed using only working hours
    # (Mon-Fri 09:00-17:00 in the client's timezone by default).
    business_hours_only = db.Column(db.Boolean, nullable=False, default=False)

    # Comma-separated list of ticket statuses this rule actively tracks against
    # (e.g. "open,in_progress"). Empty/NULL = applies regardless of status.
    applies_to_status = db.Column(db.String(300), nullable=True)

    # Comma-separated list of statuses considered "closed/stopped" for SLA
    # clock purposes (e.g. "closed,resolved,cancelled").
    stop_status = db.Column(db.String(300), nullable=True)

    # --- Pause statuses (Gap #4) ---
    # Comma-separated list of statuses that pause the SLA clock
    # (e.g. "awaiting_client,on_hold"). While the ticket is in one of these
    # statuses, the deadline shifts forward by the paused duration.
    pause_status = db.Column(db.String(300), nullable=True)

    is_active = db.Column(db.Boolean, default=True, nullable=False, index=True)

    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    tickets = db.relationship("Ticket", backref="sla_rule", lazy="dynamic")

    def __init__(self, **kwargs):
        # Support old-style initialization for backward compatibility
        field_name = kwargs.pop("field_name", None)
        field_value = kwargs.pop("field_value", None)
        super().__init__(**kwargs)
        if field_name and field_value:
            self.conditions.append(SLARuleCondition(field_name=field_name, field_value=field_value))

    @property
    def field_name(self):
        return self.conditions[0].field_name if self.conditions else None

    @property
    def field_value(self):
        return self.conditions[0].field_value if self.conditions else None

    def stop_status_list(self):
        if not self.stop_status:
            return []
        return [s.strip().lower() for s in self.stop_status.split(",") if s.strip()]

    def applies_to_status_list(self):
        if not self.applies_to_status:
            return []
        return [s.strip().lower() for s in self.applies_to_status.split(",") if s.strip()]

    def pause_status_list(self):
        """Statuses that pause the SLA clock (Gap #4)."""
        if not self.pause_status:
            return []
        return [s.strip().lower() for s in self.pause_status.split(",") if s.strip()]

    def __repr__(self):
        return f"<SLARule {self.rule_name}: {self.field_name}={self.field_value} (client={self.client_id})>"
