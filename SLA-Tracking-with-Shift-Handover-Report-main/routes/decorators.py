"""
routes/decorators.py
-----------------------
Lightweight RBAC helpers, as requested: a simple @role_required(...) decorator
and a @permission_required(...) decorator that reads from the centralized
ROLE_PERMISSIONS map in models/user.py.
"""

from functools import wraps
from flask import abort
from flask_login import current_user


def role_required(*allowed_roles):
    """Usage: @role_required("Admin") or @role_required("Admin", "Manager")"""
    def decorator(view_func):
        @wraps(view_func)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                abort(401)
            if current_user.role not in allowed_roles:
                abort(403)
            return view_func(*args, **kwargs)
        return wrapped
    return decorator


def permission_required(permission):
    """Usage: @permission_required("manage_sla_rules")"""
    def decorator(view_func):
        @wraps(view_func)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                abort(401)
            if not current_user.has_permission(permission):
                abort(403)
            return view_func(*args, **kwargs)
        return wrapped
    return decorator
