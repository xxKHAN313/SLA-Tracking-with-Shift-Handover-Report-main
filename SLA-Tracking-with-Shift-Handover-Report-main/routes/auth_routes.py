"""
routes/auth_routes.py
------------------------
Login / logout using Flask-Login with hashed passwords.

Gap #9: Login rate limiting — after 5 failed attempts for a username in
15 minutes, that username is locked out for 15 minutes. All lockouts are
logged.
"""

import logging
import time
from collections import defaultdict

from urllib.parse import urlparse, urljoin
from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_user, logout_user, login_required, current_user

from models import User

logger = logging.getLogger(__name__)

auth_bp = Blueprint("auth", __name__)

def is_safe_url(target):
    if not target:
        return False
    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return test_url.scheme in ('http', 'https') and ref_url.netloc == test_url.netloc


# --- Gap #9: Simple in-memory rate limiter ---
# Structure: { username: [(timestamp, ...), ...] }
_failed_attempts = defaultdict(list)
_LOCKOUT_WINDOW_SECONDS = 15 * 60   # 15 minutes
_MAX_FAILED_ATTEMPTS = 5
_LOCKOUT_DURATION_SECONDS = 15 * 60  # 15 minutes


def _clean_old_attempts(username):
    """Remove failed-attempt records older than the lockout window."""
    cutoff = time.time() - _LOCKOUT_WINDOW_SECONDS
    _failed_attempts[username] = [
        ts for ts in _failed_attempts[username] if ts > cutoff
    ]


def _is_locked_out(username):
    """Check if the username is currently locked out."""
    _clean_old_attempts(username)
    if len(_failed_attempts[username]) >= _MAX_FAILED_ATTEMPTS:
        # Check if the most recent attempt is within the lockout duration
        most_recent = max(_failed_attempts[username])
        if time.time() - most_recent < _LOCKOUT_DURATION_SECONDS:
            return True
    return False


def _record_failed_attempt(username):
    """Record a failed login attempt."""
    _failed_attempts[username].append(time.time())
    _clean_old_attempts(username)
    if len(_failed_attempts[username]) >= _MAX_FAILED_ATTEMPTS:
        logger.warning(
            "Login rate limit: username '%s' locked out after %d failed "
            "attempts in %d minutes.",
            username, _MAX_FAILED_ATTEMPTS,
            _LOCKOUT_WINDOW_SECONDS // 60,
        )


def _clear_attempts(username):
    """Clear failed attempts after a successful login."""
    _failed_attempts.pop(username, None)


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        # Gap #9: check rate limit before attempting authentication
        if _is_locked_out(username):
            flash(
                "Too many failed login attempts. Please try again in 15 minutes.",
                "danger",
            )
            return render_template("login.html")

        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password) and user.is_active:
            _clear_attempts(username)
            login_user(user)
            flash(f"Welcome back, {user.username}!", "success")
            next_page = request.args.get("next")
            if next_page and not is_safe_url(next_page):
                next_page = None
            return redirect(next_page or url_for("dashboard.index"))

        _record_failed_attempt(username)
        flash("Invalid username or password.", "danger")

    return render_template("login.html")


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))
