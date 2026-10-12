# Security Policy

## Reporting Vulnerabilities

We take the security of the **Automated SLA Tracking and Report Generation System** seriously. If you discover a security vulnerability, we appreciate your efforts to responsibly disclose it to us.

### How to Report a Vulnerability

Please do **NOT** create a public GitHub issue for security vulnerabilities. Instead, send an email to:

📧 **ma4200417@gmail.com**

Please include the following details in your report:
- Type of issue (e.g., SQL Injection, XSS, CSRF, IDOR, Authentication Bypass)
- Step-by-step instructions or proof-of-concept (PoC) script to reproduce the issue
- Affected component or URL endpoint
- Impact assessment of the vulnerability

---

## Supported Versions

Only the latest release branch receives security updates and patches.

| Version | Supported |
| ------- | --------- |
| Main Branch (v1.x) | :white_check_mark: Supported |
| Legacy Branches | :x: Unsupported |

---

## Disclosure & SLA Timeline

- **Acknowledgment**: Within **24 hours** of receiving your report.
- **Triage & Validation**: Within **48 hours**.
- **Fix & Patch Release**: Critical vulnerabilities patched within **7 days**.
- **Public Disclosure**: Coordinated after the fix has been merged and deployed.

---

## Security Architecture & Built-in Controls

This project adheres to **OWASP Top 10 (2021)** security best practices:

1. **Broken Access Control (A01)**:
   - Centralized Role-Based Access Control (RBAC) enforced in `models/user.py` (`Admin`, `Manager`, `Viewer`).
   - Authorization decorators (`@permission_required`) on all protected endpoints.
   - Strict multi-tenant client isolation ensuring zero data cross-talk.
   - Raw JSON source payloads restricted to Admin users only.

2. **Cryptographic Failures (A02)**:
   - Passwords hashed using Werkzeug PBKDF2 with SHA-256 salts.
   - HTTPS enforcement and secure HTTP headers (`X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Content-Security-Policy`).

3. **Injection Resilience (A03)**:
   - All database queries parameterized using SQLAlchemy ORM (zero raw string concatenation).
   - Jinja2 auto-escaping active across all HTML templates to prevent Cross-Site Scripting (XSS).

4. **Authentication & Session Security (A07)**:
   - In-memory rate limiting lockout: Accounts locked out after 5 consecutive failed login attempts in 15 minutes.
   - CSRF tokens required on all state-modifying form actions (`POST`).
