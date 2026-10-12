"""
tests/test_breach_tagging.py
-----------------------------
Tests for SLA breach root cause tagging on tickets.
"""

import pytest
from models import Ticket, User


class TestBreachTagging:
    """Tests updating breach reason and notes on tickets."""

    @pytest.fixture
    def manager_user(self, app, db):
        with app.app_context():
            u = User(username="manager_test", role="Manager", is_active_user=True)
            u.set_password("Manager123!")
            db.session.add(u)
            db.session.commit()
            db.session.refresh(u)
            return u

    def test_update_breach_reason_success(self, client, sample_client, manager_user):
        """Verify setting breach reason and notes updates ticket record."""
        # Create breached ticket
        with client.application.app_context():
            from extensions import db
            t = Ticket(
                client_id=sample_client.id,
                external_id="T-999",
                source_system="test",
                title="Breached Ticket",
                sla_status="Breached",
            )
            db.session.add(t)
            db.session.commit()
            ticket_id = t.id

        with client.session_transaction() as sess:
            sess["_user_id"] = str(manager_user.id)

        res = client.post(f"/tickets/{ticket_id}/update-breach-reason", data={
            "breach_reason": "Vendor Delay",
            "breach_notes": "Awaiting hardware replacement from vendor.",
        }, follow_redirects=True)

        assert res.status_code == 200

        with client.application.app_context():
            updated = Ticket.query.get(ticket_id)
            assert updated.breach_reason == "Vendor Delay"
            assert "hardware replacement" in updated.breach_notes
