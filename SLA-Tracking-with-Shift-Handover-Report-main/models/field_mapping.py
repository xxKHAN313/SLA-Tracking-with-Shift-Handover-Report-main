"""
models/field_mapping.py
------------------------
Maps DFIR-IRIS (or any future source system) field names onto our generic
local ticket model field names. This is what lets the rest of the system
stay source-agnostic: if IRIS renames a field, or a second source system is
added later, only the mapping rows change - no code changes needed.

Multi-tenancy: mappings can be global (client_id=NULL → default for all
clients) or client-specific (client_id set → override for that client).
When resolving mappings, client-specific ones take precedence over globals.

Example rows:
    local_field="ticket_id",    source_field="case_id"
    local_field="title",        source_field="case_name"
    local_field="severity",     source_field="severity"
    local_field="status",       source_field="status_name"
    local_field="assigned_to",  source_field="owner"
"""

from datetime import datetime, timezone
from extensions import db


class FieldMapping(db.Model):
    __tablename__ = "field_mappings"

    id = db.Column(db.Integer, primary_key=True)

    # NULL = global default mapping; set = client-specific override (Gap #1).
    client_id = db.Column(
        db.Integer, db.ForeignKey("clients.id"), nullable=True, index=True
    )

    source_system = db.Column(db.String(50), nullable=False, default="dfir_iris", index=True)
    local_field = db.Column(db.String(100), nullable=False)
    source_field = db.Column(db.String(200), nullable=False)
    # Optional dotted path if the value is nested in the source JSON,
    # e.g. "owner.username" - normalize_ticket() knows how to walk this.
    source_path = db.Column(db.String(300), nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        db.UniqueConstraint(
            "client_id", "source_system", "local_field",
            name="uq_client_source_local_field",
        ),
    )

    def __repr__(self):
        return f"<FieldMapping {self.local_field} <- {self.source_field} (client={self.client_id})>"
