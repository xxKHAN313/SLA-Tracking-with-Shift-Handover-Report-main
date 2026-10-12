"""
services/audit_service.py
--------------------------
Centralized administrative action logging helper.
"""

import logging
from flask import request
from flask_login import current_user
from extensions import db
from models.audit_log import AuditLog

logger = logging.getLogger(__name__)


def log_audit(action, target_type, target_id=None, details=None):
    """
    Record an administrative or system action in the AuditLog database table.
    Automatically captures current authenticated user and remote IP address if available.
    """
    try:
        username = "system"
        user_id = None
        if current_user and getattr(current_user, "is_authenticated", False):
            username = getattr(current_user, "username", "unknown")
            user_id = getattr(current_user, "id", None)

        ip_address = None
        try:
            if request:
                ip_address = request.headers.get("X-Forwarded-For", request.remote_addr)
                if ip_address and "," in ip_address:
                    ip_address = ip_address.split(",")[0].strip()
        except RuntimeError:
            # Out of request context
            pass

        entry = AuditLog(
            user_id=user_id,
            username=username,
            action=action,
            target_type=target_type,
            target_id=target_id,
            details=details,
            ip_address=ip_address,
        )
        db.session.add(entry)
        db.session.commit()
    except Exception as err:
        logger.warning("Failed to record audit log entry: %s", err)
