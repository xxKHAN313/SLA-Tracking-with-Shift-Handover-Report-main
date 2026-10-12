"""
tests/test_audit_logging.py
----------------------------
Tests for administrative audit logging service.
"""

import pytest
from models import AuditLog, User
from services.audit_service import log_audit


class TestAuditLogging:
    """Tests administrative audit log generation."""

    def test_log_audit_records_entry(self, app, db):
        """Verify log_audit creates audit record in database."""
        with app.app_context():
            log_audit("test_action", "Setting", target_id=1, details="Changed test setting")

            entry = AuditLog.query.filter_by(action="test_action").first()
            assert entry is not None
            assert entry.target_type == "Setting"
            assert entry.target_id == 1
            assert entry.details == "Changed test setting"
