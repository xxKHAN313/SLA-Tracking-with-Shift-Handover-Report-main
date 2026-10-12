"""
models/user.py
---------------
User model with lightweight role-based access control (RBAC).

Roles: Admin, Manager, Viewer
- Admin   : full access (dashboard, tickets, SLA rules, IRIS settings, reports)
- Manager : dashboard, tickets, generate + view reports
- Viewer  : dashboard, tickets, view reports only
"""

from datetime import datetime, timezone
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

from extensions import db

ROLE_ADMIN = "Admin"
ROLE_MANAGER = "Manager"
ROLE_VIEWER = "Viewer"

VALID_ROLES = (ROLE_ADMIN, ROLE_MANAGER, ROLE_VIEWER)

# Permission matrix - lightweight, in-code. Keeping this centralized means
# routes and templates can share the same source of truth instead of
# scattering "if role == ..." checks everywhere.
ROLE_PERMISSIONS = {
    ROLE_ADMIN: {
        "view_dashboard", "view_tickets", "manage_sla_rules",
        "manage_iris_settings", "generate_reports", "view_reports",
    },
    ROLE_MANAGER: {
        "view_dashboard", "view_tickets", "generate_reports", "view_reports",
    },
    ROLE_VIEWER: {
        "view_dashboard", "view_tickets", "view_reports",
    },
}


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    email = db.Column(db.String(255), unique=True, nullable=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default=ROLE_VIEWER)
    is_active_user = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    reports_generated = db.relationship("Report", backref="generated_by_user", lazy="dynamic")

    def set_password(self, raw_password):
        self.password_hash = generate_password_hash(raw_password)

    def check_password(self, raw_password):
        return check_password_hash(self.password_hash, raw_password)

    def has_permission(self, permission):
        return permission in ROLE_PERMISSIONS.get(self.role, set())

    # Flask-Login expects `is_active` - map it to our own column so we don't
    # collide with UserMixin's default (always-True) property.
    @property
    def is_active(self):
        return self.is_active_user

    def __repr__(self):
        return f"<User {self.username} ({self.role})>"
