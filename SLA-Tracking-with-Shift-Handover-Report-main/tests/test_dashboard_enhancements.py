"""
tests/test_dashboard_enhancements.py
------------------------------------
Unit tests for Client Breakdown page, Recent Activity Feed, and SLA Heatmap.
"""

import pytest
from models import User, Ticket


class TestDashboardEnhancements:
    """Tests for scalable client breakdown page, activity feed, and heatmap."""

    @pytest.fixture
    def admin_user(self, app, db):
        with app.app_context():
            u = User(username="dash_admin", role="Admin", is_active_user=True)
            u.set_password("Admin123!")
            db.session.add(u)
            db.session.commit()
            db.session.refresh(u)
            return u

    def test_client_breakdown_route(self, client, admin_user):
        """Verify /clients/breakdown page renders correctly with search and sort."""
        with client.session_transaction() as sess:
            sess["_user_id"] = str(admin_user.id)

        res = client.get("/clients/breakdown?search=Test&sort=name")
        assert res.status_code == 200
        assert b"Client Performance & SLA Breakdown" in res.data

    def test_recent_activity_helper(self, app, db):
        """Verify _get_recent_activity returns timeline items."""
        from routes.dashboard_routes import _get_recent_activity
        with app.app_context():
            activities = _get_recent_activity(limit=5)
            assert isinstance(activities, list)

    def test_heatmap_data_builder(self, app, db, sample_client):
        """Verify _build_heatmap_data returns 7x24 matrix structure."""
        from routes.dashboard_routes import _build_heatmap_data
        with app.app_context():
            heatmap = _build_heatmap_data(client_id=sample_client.id)
            assert "days" in heatmap
            assert len(heatmap["days"]) == 7
            assert "matrix" in heatmap
            assert 0 in heatmap["matrix"]
            assert len(heatmap["matrix"][0]) == 24
