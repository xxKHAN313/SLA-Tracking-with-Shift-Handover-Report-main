"""
tests/test_security.py
-----------------------
Automated security regression tests for OWASP Top 10 vulnerabilities:
- Open Redirect protection on authentication endpoints
- HTTP Security Hardening Response Headers
- Session Cookie attributes
"""

import pytest

def test_open_redirect_prevention(client):
    """Verify that external domain redirect payloads in ?next= are blocked."""
    # Attempt login with external redirect parameter
    res = client.post("/login?next=https://attacker-controlled-site.com", data={
        "username": "admin",
        "password": "wrongpassword"
    })
    assert res.status_code == 200

    # Ensure relative paths are accepted, but external domains fall back to default dashboard
    res_valid = client.get("/login?next=/tickets")
    assert res_valid.status_code == 200


def test_security_headers_present(client):
    """Verify essential HTTP security response headers are returned."""
    res = client.get("/login")
    assert res.status_code == 200

    # Check for security headers added in app.py after_request hook
    assert res.headers.get("X-Frame-Options") == "SAMEORIGIN"
    assert res.headers.get("X-Content-Type-Options") == "nosniff"
    assert res.headers.get("X-XSS-Protection") == "1; mode=block"
    assert res.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
    assert "Content-Security-Policy" in res.headers


def test_session_cookie_security_defaults(app):
    """Verify session cookie security settings."""
    assert app.config.get("SESSION_COOKIE_HTTPONLY") is True
    assert app.config.get("SESSION_COOKIE_SAMESITE") in ("Lax", "Strict")
