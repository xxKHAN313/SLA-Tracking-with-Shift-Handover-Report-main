"""
services/business_hours.py
----------------------------
Business-hours-aware SLA deadline calculation (Gap #3).

Provides ``add_business_minutes(start_dt, minutes, client)`` which returns
a deadline datetime that only counts working hours.

Default schedule: Monday-Friday 09:00-17:00 in the client's timezone.
The schedule is intentionally kept simple for the MVP — a per-client
``BusinessCalendar`` model can be added later to support holidays, custom
hours, etc.
"""

from datetime import datetime, timedelta, timezone, time
from zoneinfo import ZoneInfo

# Default working schedule (can be overridden per-client in a future version)
DEFAULT_WORK_START = time(9, 0)   # 09:00
DEFAULT_WORK_END = time(17, 0)    # 17:00
DEFAULT_WORK_DAYS = {0, 1, 2, 3, 4}  # Mon-Fri (datetime.weekday() values)


def get_business_hours(client=None):
    """
    Resolve the business hours parameters (start, end, weekdays) for calculations.
    Checks the client-level override first.
    Falls back to global database Settings.
    Falls back to hardcoded defaults (09:00 - 17:00, Mon-Fri).
    """
    from models.setting import Setting

    start_str = "09:00"
    end_str = "17:00"
    days_str = "0,1,2,3,4"

    # 1. Fetch global database settings if configured
    try:
        global_start = Setting.get("business_hours_start")
        if global_start:
            start_str = global_start
        global_end = Setting.get("business_hours_end")
        if global_end:
            end_str = global_end
        global_days = Setting.get("business_hours_days")
        if global_days:
            days_str = global_days
    except Exception as err:
        import logging
        logging.getLogger(__name__).debug("Global business hours settings lookup fallback: %s", err)

    # 2. Fetch client-level overrides if configured
    if client:
        if hasattr(client, "business_hours_start") and client.business_hours_start:
            start_str = client.business_hours_start
        if hasattr(client, "business_hours_end") and client.business_hours_end:
            end_str = client.business_hours_end
        if hasattr(client, "business_hours_days") and client.business_hours_days is not None:
            days_str = client.business_hours_days

    # Parse start time
    try:
        sh, sm = map(int, start_str.split(":"))
        work_start = time(sh, sm)
    except Exception as err:
        import logging
        logging.getLogger(__name__).debug("Defaulting start time to 09:00 due to parse error: %s", err)
        work_start = time(9, 0)

    # Parse end time
    try:
        eh, em = map(int, end_str.split(":"))
        work_end = time(eh, em)
    except Exception as err:
        import logging
        logging.getLogger(__name__).debug("Defaulting end time to 17:00 due to parse error: %s", err)
        work_end = time(17, 0)

    # Parse working days
    try:
        work_days = {int(x.strip()) for x in days_str.split(",") if x.strip() != ""}
    except Exception as err:
        import logging
        logging.getLogger(__name__).debug("Defaulting working days to Mon-Fri due to parse error: %s", err)
        work_days = {0, 1, 2, 3, 4}

    return work_start, work_end, work_days


def _get_client_tz(client):
    """Resolve the client's timezone to a ZoneInfo object, defaulting to UTC."""
    tz_name = "UTC"
    if client and hasattr(client, "timezone") and client.timezone:
        tz_name = client.timezone
    try:
        return ZoneInfo(tz_name)
    except (KeyError, Exception):
        return ZoneInfo("UTC")


def get_client_holidays(client=None):
    """Fetch set of holiday dates for client + global holidays."""
    try:
        from models.holiday import Holiday
        from extensions import db
        client_id = getattr(client, "id", None) if client else None
        query = Holiday.query.filter(
            db.or_(Holiday.client_id == None, Holiday.client_id == client_id)  # noqa: E711
        )
        return {h.holiday_date for h in query.all()}
    except Exception as err:
        import logging
        logging.getLogger(__name__).debug("Holiday lookup skipped: %s", err)
        return set()


def add_business_minutes(start_dt, minutes, client=None):
    """
    Compute a deadline by adding ``minutes`` of business time to ``start_dt``.

    Only hours within the working window (customizable globally or per-client)
    are counted. Weekends, holidays, and outside-hours periods are skipped.

    Args:
        start_dt: timezone-aware datetime (UTC internally)
        minutes: int — the number of business minutes to add
        client: optional Client model instance (provides timezone & custom business hours)

    Returns:
        timezone-aware datetime (UTC) representing the deadline
    """
    if minutes <= 0:
        return start_dt

    client_tz = _get_client_tz(client)

    # Convert start time to the client's local timezone for calendar math
    local_dt = start_dt.astimezone(client_tz)

    # Fetch custom business hours configuration & holiday calendar
    work_start, work_end, work_days = get_business_hours(client)
    holidays = get_client_holidays(client)

    # If work_days is empty (e.g. client set no working days), return start_dt to avoid infinite loop
    if not work_days:
        return start_dt

    remaining = float(minutes)

    while remaining > 0:
        # If we're on a non-working day or holiday, skip to next working day start
        if local_dt.weekday() not in work_days or local_dt.date() in holidays:
            next_dt = local_dt + timedelta(days=1)
            while next_dt.weekday() not in work_days or next_dt.date() in holidays:
                next_dt += timedelta(days=1)
            local_dt = next_dt.replace(
                hour=work_start.hour,
                minute=work_start.minute,
                second=0, microsecond=0,
            )
            continue

        local_time = local_dt.time()

        # Before business hours — fast-forward to start of day
        if local_time < work_start:
            local_dt = local_dt.replace(
                hour=work_start.hour,
                minute=work_start.minute,
                second=0, microsecond=0,
            )
            continue

        # After business hours — skip to next working day
        if local_time >= work_end:
            local_dt = local_dt + timedelta(days=1)
            local_dt = local_dt.replace(
                hour=work_start.hour,
                minute=work_start.minute,
                second=0, microsecond=0,
            )
            # May have landed on a weekend/non-working day — loop will handle it
            continue

        # We're within working hours — consume as many minutes as possible today
        end_of_day = local_dt.replace(
            hour=work_end.hour,
            minute=work_end.minute,
            second=0, microsecond=0,
        )
        available_minutes = (end_of_day - local_dt).total_seconds() / 60

        if remaining <= available_minutes:
            local_dt = local_dt + timedelta(minutes=remaining)
            remaining = 0
        else:
            remaining -= available_minutes
            # Move to next working day
            local_dt = local_dt + timedelta(days=1)
            local_dt = local_dt.replace(
                hour=work_start.hour,
                minute=work_start.minute,
                second=0, microsecond=0,
            )

    # Convert back to UTC for storage
    return local_dt.astimezone(timezone.utc)
