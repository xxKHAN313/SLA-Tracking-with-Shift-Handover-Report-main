"""
tests/test_holidays.py
-----------------------
Unit tests for Holiday Calendar model and business-hours exclusion logic.
"""

import pytest
from datetime import datetime, date, timezone
from models import Client, Holiday, SLARule, Ticket
from services.business_hours import add_business_minutes


class TestHolidayCalendar:
    """Tests holiday creation and SLA deadline calculations with holidays."""

    def test_holiday_skips_in_business_hours_math(self, app, db, sample_client):
        """Verify that a holiday date is skipped when calculating SLA deadlines."""
        with app.app_context():
            # Monday July 6, 2026 at 09:00 UTC
            start_dt = datetime(2026, 7, 6, 9, 0, 0, tzinfo=timezone.utc)

            # Add a holiday on Tuesday July 7, 2026
            holiday = Holiday(name="Company Holiday", holiday_date=date(2026, 7, 7), client_id=sample_client.id)
            db.session.add(holiday)
            db.session.commit()

            # Add 8 business hours (480 mins) starting Monday 09:00.
            # Normal end would be Monday 17:00 (since 09:00 to 17:00 is 8 hours).
            # If we add 12 hours (720 mins = 8h Monday + 4h next business day):
            # Tuesday is a holiday, so it should skip Tuesday and land on Wednesday July 8 at 13:00 UTC.
            deadline = add_business_minutes(start_dt, 720, client=sample_client)

            assert deadline == datetime(2026, 7, 8, 13, 0, 0, tzinfo=timezone.utc)
