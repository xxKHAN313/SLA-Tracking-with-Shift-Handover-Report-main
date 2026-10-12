"""
services/cleanup_service.py
------------------------------
Gap #11: Report lifecycle management.

Deletes old generated report files and their DB records after a configurable
retention period. Prevents ``generated_reports/`` from growing unbounded.
"""

import logging
import os
from datetime import datetime, timedelta, timezone

from extensions import db
from models import Report

logger = logging.getLogger(__name__)

DEFAULT_MAX_AGE_DAYS = 90


def cleanup_old_reports(max_age_days=None):
    """
    Delete Report DB rows and their associated files that are older than
    ``max_age_days``.

    Args:
        max_age_days: int — reports older than this are deleted.
                      Defaults to ``DEFAULT_MAX_AGE_DAYS`` (90).

    Returns:
        dict with ``deleted_count`` and ``errors`` (files that couldn't be
        removed from disk).
    """
    if max_age_days is None:
        max_age_days = DEFAULT_MAX_AGE_DAYS

    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)

    old_reports = Report.query.filter(Report.generated_at < cutoff).all()

    deleted_count = 0
    errors = []

    for report in old_reports:
        # Try to delete the file first
        if report.file_path and os.path.isfile(report.file_path):
            try:
                os.remove(report.file_path)
            except OSError as exc:
                logger.warning(
                    "Could not delete report file %s: %s", report.file_path, exc
                )
                errors.append(str(report.file_path))

        db.session.delete(report)
        deleted_count += 1

    if deleted_count:
        db.session.commit()

    logger.info(
        "Report cleanup: deleted %d report(s) older than %d days. Errors: %d",
        deleted_count, max_age_days, len(errors),
    )

    return {"deleted_count": deleted_count, "errors": errors}
