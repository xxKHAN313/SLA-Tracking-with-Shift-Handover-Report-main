"""
tests/test_vapt_dynamic.py
----------------------------
Dynamic VAPT test suite that exercises the live application endpoints
against OWASP Top 10 categories using Flask's test client.

Categories Covered:
  A01 - Broken Access Control
  A02 - Cryptographic Failures (session cookies)
  A03 - Injection (SQLi, XSS)
  A05 - Security Misconfiguration (headers, debug, error pages)
  A07 - Identification & Authentication Failures
  A10 - Server-Side Request Forgery (SSRF) prevention
"""

import pytest
from models import User
from extensions import db as _db


@pytest.fixture
def authenticated_client(app, db, client):
    """Create a test user and return a logged-in Flask test client."""
    with app.app_context():
        user = User(username="vapt_admin", email="vapt@test.com", role="Admin")
        user.set_password("VaptTest123!")
        _db.session.add(user)
        _db.session.commit()

    # Login via POST
    client.post("/login", data={
        "username": "vapt_admin",
        "password": "VaptTest123!",
    }, follow_redirects=True)
    return client


# =========================================================================
# A01:2021 – BROKEN ACCESS CONTROL
# =========================================================================

class TestA01BrokenAccessControl:
    """Verify authentication enforcement and authorization on all routes."""

    def test_unauthenticated_dashboard_redirects_to_login(self, client):
        """Unauthenticated requests to /dashboard must redirect to login."""
        res = client.get("/dashboard", follow_redirects=False)
        assert res.status_code == 302
        assert "/login" in res.headers.get("Location", "")

    def test_unauthenticated_tickets_redirects_to_login(self, client):
        """Unauthenticated requests to /tickets must redirect to login."""
        res = client.get("/tickets", follow_redirects=False)
        assert res.status_code == 302
        assert "/login" in res.headers.get("Location", "")

    def test_unauthenticated_settings_redirects_to_login(self, client):
        """Unauthenticated requests to /settings must redirect to login."""
        res = client.get("/settings", follow_redirects=False)
        assert res.status_code == 302
        assert "/login" in res.headers.get("Location", "")

    def test_unauthenticated_reports_redirects_to_login(self, client):
        """Unauthenticated requests to /reports must redirect to login."""
        res = client.get("/reports", follow_redirects=False)
        assert res.status_code == 302
        assert "/login" in res.headers.get("Location", "")

    def test_unauthenticated_sla_rules_redirects_to_login(self, client):
        """Unauthenticated requests to /sla-rules must redirect to login."""
        res = client.get("/sla-rules", follow_redirects=False)
        assert res.status_code == 302
        assert "/login" in res.headers.get("Location", "")

    def test_open_redirect_blocked_on_login(self, app, db, client):
        """Login with ?next=https://evil.com must NOT redirect externally."""
        with app.app_context():
            user = User(username="redirect_test", email="redir@test.com", role="Admin")
            user.set_password("Test123!")
            _db.session.add(user)
            _db.session.commit()

        res = client.post("/login?next=https://evil.com", data={
            "username": "redirect_test",
            "password": "Test123!",
        }, follow_redirects=False)
        assert res.status_code == 302
        location = res.headers.get("Location", "")
        assert "evil.com" not in location
        assert "/dashboard" in location or location.startswith("/")

    def test_open_redirect_blocked_javascript_scheme(self, app, db, client):
        """Login with ?next=javascript:alert(1) must NOT redirect."""
        with app.app_context():
            user = User.query.filter_by(username="redirect_test").first()
            if not user:
                user = User(username="redirect_test2", email="redir2@test.com", role="Admin")
                user.set_password("Test123!")
                _db.session.add(user)
                _db.session.commit()

        res = client.post("/login?next=javascript:alert(1)", data={
            "username": "redirect_test2" if User.query.filter_by(username="redirect_test").first() is None else "redirect_test",
            "password": "Test123!",
        }, follow_redirects=False)
        assert res.status_code == 302
        location = res.headers.get("Location", "")
        assert "javascript:" not in location

    def test_idor_nonexistent_ticket_returns_404(self, authenticated_client):
        """Accessing a non-existent ticket ID returns 404, not data leak."""
        res = authenticated_client.get("/tickets/99999")
        assert res.status_code == 404

    def test_idor_nonexistent_report_download_returns_404(self, authenticated_client):
        """Downloading a non-existent report returns 404."""
        res = authenticated_client.get("/reports/download/99999")
        assert res.status_code == 404


# =========================================================================
# A02:2021 – CRYPTOGRAPHIC FAILURES
# =========================================================================

class TestA02CryptographicFailures:
    """Verify session cookie security attributes."""

    def test_session_cookie_httponly(self, app):
        assert app.config.get("SESSION_COOKIE_HTTPONLY") is True

    def test_session_cookie_samesite(self, app):
        assert app.config.get("SESSION_COOKIE_SAMESITE") in ("Lax", "Strict")


# =========================================================================
# A03:2021 – INJECTION
# =========================================================================

class TestA03Injection:
    """Verify protection against SQL injection and XSS."""

    def test_sql_injection_ticket_search_no_crash(self, authenticated_client):
        """SQL injection payload in search must not crash the application."""
        res = authenticated_client.get("/tickets?search=' OR 1=1 --")
        assert res.status_code == 200
        # No database error in response body
        assert b"OperationalError" not in res.data
        assert b"ProgrammingError" not in res.data
        assert b"sqlalchemy" not in res.data.lower()

    def test_sql_injection_ticket_search_union(self, authenticated_client):
        """UNION-based SQL injection must not leak data."""
        res = authenticated_client.get("/tickets?search=' UNION SELECT 1,2,3--")
        assert res.status_code == 200
        assert b"OperationalError" not in res.data

    def test_xss_ticket_search_escaped(self, authenticated_client):
        """XSS payload in search must be HTML-escaped, not rendered."""
        payload = "<script>alert('XSS')</script>"
        res = authenticated_client.get(f"/tickets?search={payload}")
        assert res.status_code == 200
        # The raw script tag must NOT appear unescaped in response
        assert b"<script>alert('XSS')</script>" not in res.data

    def test_xss_login_username_escaped(self, client):
        """XSS payload in login username field must be escaped."""
        res = client.post("/login", data={
            "username": "<img src=x onerror=alert('XSS')>",
            "password": "test",
        }, follow_redirects=True)
        assert res.status_code == 200
        assert b"<img src=x onerror=" not in res.data

    def test_malformed_client_id_filter_no_crash(self, authenticated_client):
        """Non-numeric client_id must not cause ValueError/500 error."""
        res = authenticated_client.get("/tickets?client_id=abc")
        assert res.status_code == 200

    def test_negative_client_id_filter_no_crash(self, authenticated_client):
        """Negative client_id must be handled gracefully."""
        res = authenticated_client.get("/tickets?client_id=-1")
        assert res.status_code == 200


# =========================================================================
# A05:2021 – SECURITY MISCONFIGURATION
# =========================================================================

class TestA05SecurityMisconfiguration:
    """Verify HTTP security headers and error handling."""

    def test_x_frame_options_header(self, client):
        res = client.get("/login")
        assert res.headers.get("X-Frame-Options") == "SAMEORIGIN"

    def test_x_content_type_options_header(self, client):
        res = client.get("/login")
        assert res.headers.get("X-Content-Type-Options") == "nosniff"

    def test_x_xss_protection_header(self, client):
        res = client.get("/login")
        assert res.headers.get("X-XSS-Protection") == "1; mode=block"

    def test_referrer_policy_header(self, client):
        res = client.get("/login")
        assert res.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"

    def test_content_security_policy_header(self, client):
        res = client.get("/login")
        csp = res.headers.get("Content-Security-Policy")
        assert csp is not None
        assert "default-src" in csp

    def test_404_page_no_stack_trace(self, client):
        """404 error page must not expose a Python stack trace."""
        res = client.get("/nonexistent-page-xyz-random")
        assert res.status_code == 404
        assert b"Traceback" not in res.data
        assert b"File \"/\"" not in res.data

    def test_csrf_token_present_on_login(self, client):
        """Login form must contain a CSRF token hidden field."""
        res = client.get("/login")
        assert b"csrf_token" in res.data


# =========================================================================
# A07:2021 – IDENTIFICATION & AUTHENTICATION FAILURES
# =========================================================================

class TestA07AuthenticationFailures:
    """Verify login error messages and brute-force protections."""

    def test_generic_error_message_no_user_enumeration(self, client, app, db):
        """Invalid logins must show the same message whether user exists or not."""
        from models import User
        with app.app_context():
            admin = User(username="admin", role="Admin", is_active_user=True)
            admin.set_password("Admin123!")
            db.session.add(admin)
            db.session.commit()

        # Non-existent user
        res1 = client.post("/login", data={
            "username": "nonexistent_user_xyz_12345",
            "password": "wrong",
        }, follow_redirects=True)

        # Existing user with wrong password
        res2 = client.post("/login", data={
            "username": "admin",
            "password": "wrongpassword",
        }, follow_redirects=True)

        # Both must contain the same error message
        assert b"Invalid username or password" in res1.data
        assert b"Invalid username or password" in res2.data

    def test_login_rate_limiting_lockout(self, client):
        """After 5 failed attempts, the account should be locked out."""
        for i in range(6):
            res = client.post("/login", data={
                "username": "lockout_test_user",
                "password": "wrong",
            }, follow_redirects=True)

        # The 6th attempt should show a lockout message
        assert b"Too many failed login attempts" in res.data


# =========================================================================
# A10:2021 – SERVER-SIDE REQUEST FORGERY (SSRF)
# =========================================================================

class TestA10SSRF:
    """Verify IRIS URL validation rejects invalid schemes."""

    def test_ssrf_validation_rejects_file_scheme(self):
        from services.iris_api_service import is_valid_external_url
        assert is_valid_external_url("file:///etc/passwd") is False

    def test_ssrf_validation_rejects_empty(self):
        from services.iris_api_service import is_valid_external_url
        assert is_valid_external_url("") is False
        assert is_valid_external_url(None) is False

    def test_ssrf_validation_rejects_gopher(self):
        from services.iris_api_service import is_valid_external_url
        assert is_valid_external_url("gopher://localhost:7000") is False

    def test_ssrf_validation_accepts_https(self):
        from services.iris_api_service import is_valid_external_url
        assert is_valid_external_url("https://iris.company.com") is True

    def test_ssrf_validation_accepts_http(self):
        from services.iris_api_service import is_valid_external_url
        assert is_valid_external_url("http://iris.internal.local") is True
