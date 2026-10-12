"""
tests/test_input_resilience.py
--------------------------------
Input boundary and parameter resilience tests for application endpoints.
Uses pytest parameterization to test form/JSON field handling for edge cases,
oversized strings, malformed types, and special character combinations.
"""

import pytest
from models import User


# Edge-case input strings for field boundary testing
FIELD_EDGE_CASES = [
    "",                             # Empty string
    "   ",                          # Whitespace only
    "A" * 5000,                     # Oversized payload
    "<script>alert(1)</script>",    # HTML/JS tags
    "'; DROP TABLE tickets; --",    # SQL injection syntax
    "../../../../etc/passwd",       # Path traversal syntax
    "Line1\nLine2\r\nLine3",        # Control characters
    "Café 🚀 💥 𝄢 Null:\x00",      # Unicode + Null byte
]


class TestAuthInputResilience:
    """Tests parameter resilience on authentication endpoints."""

    @pytest.mark.parametrize("payload_val", FIELD_EDGE_CASES)
    def test_login_field_resilience(self, client, payload_val):
        """Verify login endpoint handles edge-case input values without crashing (500)."""
        response = client.post("/login", data={
            "username": payload_val,
            "password": "somepassword"
        }, follow_redirects=True)
        
        # Application should gracefully return 200 (re-render form with error) or 400/422, never 500
        assert response.status_code != 500

    @pytest.mark.parametrize("i, payload_val", list(enumerate(FIELD_EDGE_CASES)))
    def test_login_password_resilience(self, client, i, payload_val):
        """Verify login password field handles edge-case input values without crashing."""
        response = client.post("/login", data={
            "username": f"resilience_user_{i}",
            "password": payload_val
        }, follow_redirects=True)
        
        assert response.status_code != 500


class TestAPIInputResilience:
    """Tests input resilience on API endpoints."""

    @pytest.mark.parametrize("invalid_json", [
        {},                         # Missing fields
        {"title": None},            # Null title
        {"title": 12345},           # Invalid type
        {"title": "A" * 10000},     # Oversized title
        {"priority": -99999},       # Unexpected priority
    ])
    def test_ticket_api_invalid_payloads(self, client, invalid_json):
        """Verify ticket creation endpoint handles malformed payloads gracefully."""
        response = client.post(
            "/api/tickets",
            json=invalid_json,
            headers={"Content-Type": "application/json"}
        )
        
        # Endpoint should respond with client error (4xx) or success, never 500
        assert response.status_code != 500


class TestReportFilterResilience:
    """Tests parameter resilience on report filtering endpoints."""

    @pytest.mark.parametrize("date_val", [
        "invalid-date",
        "2026-02-31",
        "9999-99-99",
        "A" * 500,
        "1970-01-01T00:00:00Z",
    ])
    def test_report_date_filter_resilience(self, client, date_val):
        """Verify date filtering parameters handle invalid formats gracefully."""
        response = client.get(f"/reports?start_date={date_val}&end_date={date_val}")
        
        assert response.status_code != 500


class TestSLARuleBoundaryLimits:
    """Tests field boundary enforcement on SLA Rule creation and modification."""

    @pytest.fixture
    def admin_user(self, app, db):
        with app.app_context():
            u = User(username="admin_test", role="Admin", is_active_user=True)
            u.set_password("Admin123!")
            db.session.add(u)
            db.session.commit()
            db.session.refresh(u)
            return u

    def test_negative_or_zero_resolution_sla_rejected(self, client, sample_client, admin_user):
        """Verify resolution SLA minutes <= 0 is rejected."""
        with client.session_transaction() as sess:
            sess["_user_id"] = str(admin_user.id)

        res = client.post("/sla-rules/create", data={
            "client_id": sample_client.id,
            "rule_name": "Test Boundary Rule",
            "cond_field_name[]": ["severity"],
            "cond_field_value[]": ["Critical"],
            "resolution_sla_minutes": "-60",
            "priority": "10",
        }, follow_redirects=True)

        assert res.status_code == 200
        assert b"Resolution SLA minutes must be greater than 0" in res.data

    def test_response_greater_than_resolution_sla_rejected(self, client, sample_client, admin_user):
        """Verify response SLA minutes >= resolution SLA minutes is rejected."""
        with client.session_transaction() as sess:
            sess["_user_id"] = str(admin_user.id)

        res = client.post("/sla-rules/create", data={
            "client_id": sample_client.id,
            "rule_name": "Test Boundary Rule 2",
            "cond_field_name[]": ["severity"],
            "cond_field_value[]": ["Critical"],
            "response_sla_minutes": "300",
            "resolution_sla_minutes": "100",
            "priority": "10",
        }, follow_redirects=True)

        assert res.status_code == 200
        assert b"Response SLA minutes must be less than Resolution SLA minutes" in res.data

