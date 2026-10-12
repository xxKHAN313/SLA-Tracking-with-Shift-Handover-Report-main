"""
tests/test_breach_reason_chart.py
---------------------------------
Test verifying custom breach reason updates and dashboard metric inclusion.
"""

from datetime import datetime, timezone
from models import Ticket, Client
from routes.dashboard_routes import _build_dashboard_metrics


def test_custom_breach_reason_chart_data(app, db, sample_client):
    with app.app_context():
        # Create a ticket with custom breach reason
        t = Ticket(
            client_id=sample_client.id,
            external_id="CASE-BREACH-CHART-1",
            source_system="dfir_iris",
            title="Custom Breach Test",
            status="open",
            sla_status="Breached",
            breach_reason="Custom ISP Fiber Cut",
            created_at_source=datetime.now(timezone.utc),
        )
        db.session.add(t)
        db.session.commit()

        metrics = _build_dashboard_metrics()
        assert "breach_reason_distribution" in metrics
        assert "Custom ISP Fiber Cut" in metrics["breach_reason_distribution"]
        assert metrics["breach_reason_distribution"]["Custom ISP Fiber Cut"] >= 1
