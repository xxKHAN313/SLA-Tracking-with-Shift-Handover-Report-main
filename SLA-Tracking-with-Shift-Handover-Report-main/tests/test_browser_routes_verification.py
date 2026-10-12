"""
tests/test_browser_routes_verification.py
-----------------------------------------
Integration tests verifying all browser routes render with HTTP 200 without errors.
"""

import pytest
from models import User


class TestBrowserRoutesVerification:
    """Verifies all main application routes return HTTP 200/302 without 500/404 errors."""

    def test_all_routes_render_cleanly(self, client, app, db):
        """Test all 5 pages: dashboard, tickets, clients breakdown, holidays, audit logs."""
        with app.app_context():
            u = User.query.filter_by(username="admin").first()
            if not u:
                u = User(username="admin", role="Admin", is_active_user=True)
                u.set_password("Admin123!")
                db.session.add(u)
                db.session.commit()
            uid = str(u.id)

        with client.session_transaction() as sess:
            sess["_user_id"] = uid

        routes_to_test = [
            "/dashboard",
            "/tickets",
            "/clients/breakdown",
            "/settings/holidays",
            "/settings/audit-logs",
            "/settings",
            "/sla-rules",
            "/reports",
        ]

        for route in routes_to_test:
            res = client.get(route, follow_redirects=True)
            assert res.status_code == 200, f"Route {route} failed with status {res.status_code}"
