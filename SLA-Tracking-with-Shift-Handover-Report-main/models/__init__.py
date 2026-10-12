"""
models package
---------------
Import all models here so that Flask-Migrate / SQLAlchemy metadata
discovers them when `db` is initialized in app.py.
"""

from .client import Client
from .user import User
from .ticket import Ticket
from .sla_rule import SLARule, SLARuleCondition
from .field_mapping import FieldMapping
from .report import Report
from .setting import Setting
from .sync_log import SyncLog
from .holiday import Holiday
from .audit_log import AuditLog

__all__ = [
    "Client",
    "User",
    "Ticket",
    "SLARule",
    "SLARuleCondition",
    "FieldMapping",
    "Report",
    "Setting",
    "SyncLog",
    "Holiday",
    "AuditLog",
]
