"""
services/field_mapping_service.py
----------------------------------
Translates raw source-system ticket dicts (e.g. from DFIR-IRIS) into our
generic local ticket dictionary, using the field_mappings table.

This is the layer that makes the rest of the system source-agnostic:
if IRIS ever changes a field name (or a second source system is added),
only rows in field_mappings change - normalize_ticket() itself never needs
to be touched.

Multi-tenancy: ``get_active_field_mappings()`` resolves client-specific
mapping overrides. If a client has a mapping for a field, it takes precedence
over the global (client_id=NULL) default.
"""

from datetime import datetime, timezone as _tz

from models import FieldMapping

# The canonical set of local fields the rest of the system understands.
# Any of these may end up as None if the source doesn't provide it or no
# mapping row exists for it - that's expected and fine.
LOCAL_TICKET_FIELDS = [
    "external_id", "title", "status", "severity", "priority", "criticality",
    "assigned_to", "created_at", "closed_at",
]


def _get_nested(source_dict, dotted_path):
    """Walk a dotted path like 'owner.username' through a nested dict."""
    value = source_dict
    for part in dotted_path.split("."):
        if isinstance(value, dict) and part in value:
            value = value[part]
        else:
            return None
    return value


def _parse_datetime(value):
    """Best-effort parser for timestamps coming from external systems.

    Gap #5 (Timezone handling): if the parsed datetime is naive (no tzinfo),
    it is explicitly tagged as UTC before returning. This guarantees that
    all timestamps stored in the DB are timezone-aware UTC.
    """
    if not value:
        return None
    if isinstance(value, datetime):
        # Force naive datetimes to UTC
        if value.tzinfo is None:
            return value.replace(tzinfo=_tz.utc)
        return value
    if isinstance(value, str):
        # Try a handful of common formats. DFIR-IRIS typically returns ISO8601.
        candidates = [
            "%Y-%m-%dT%H:%M:%S.%f%z",
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d",
            "%m/%d/%Y %H:%M:%S",
            "%m/%d/%Y",
        ]
        cleaned = value.replace("Z", "+0000")
        for fmt in candidates:
            try:
                dt = datetime.strptime(cleaned, fmt)
                # Gap #5: force naive results to UTC
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=_tz.utc)
                return dt
            except ValueError:
                continue
    return None


def get_active_field_mappings(source_system="dfir_iris", client_id=None):
    """Fetch active field mappings for a given source system, as a dict:
    { local_field: FieldMapping }

    Multi-tenancy (Gap #1): if ``client_id`` is provided, client-specific
    mappings take precedence over global defaults (client_id=NULL).
    """
    # Start with global defaults
    global_rows = FieldMapping.query.filter_by(
        source_system=source_system, client_id=None, is_active=True
    ).all()
    result = {row.local_field: row for row in global_rows}

    # Layer client-specific overrides on top
    if client_id is not None:
        client_rows = FieldMapping.query.filter_by(
            source_system=source_system, client_id=client_id, is_active=True
        ).all()
        for row in client_rows:
            result[row.local_field] = row

    return result


def normalize_ticket(source_ticket, field_mappings):
    """
    Convert a raw source-system ticket dict into our generic local ticket
    dictionary using the supplied field mappings.

    Args:
        source_ticket: dict - raw ticket/case/alert data from the source system
        field_mappings: dict[local_field -> FieldMapping] as returned by
                         get_active_field_mappings()

    Returns:
        dict with keys: external_id, title, severity, priority, criticality,
        status, assigned_to, created_at, closed_at (any may be None)
    """
    result = {field: None for field in LOCAL_TICKET_FIELDS}

    for local_field in LOCAL_TICKET_FIELDS:
        if local_field == "priority":
            custom_attrs = source_ticket.get("custom_attributes") if isinstance(source_ticket, dict) else None
            if not isinstance(custom_attrs, dict):
                custom_attrs = {}
            tracker = custom_attrs.get("SLA_Priority_Tracker")
            if not isinstance(tracker, dict):
                tracker = {}
            prio_obj = tracker.get("Priority")
            if not isinstance(prio_obj, dict):
                prio_obj = {}
            raw_value = prio_obj.get("value")
            if raw_value is None:
                raw_value = "N/A"
        elif local_field == "criticality":
            custom_attrs = source_ticket.get("custom_attributes") if isinstance(source_ticket, dict) else None
            if not isinstance(custom_attrs, dict):
                custom_attrs = {}
            tracker = custom_attrs.get("SLA_Priority_Tracker")
            if not isinstance(tracker, dict):
                tracker = {}
            crit_obj = tracker.get("Criticality")
            if not isinstance(crit_obj, dict):
                crit_obj = {}
            raw_value = crit_obj.get("value")
            if raw_value is None:
                raw_value = "N/A"
        else:
            mapping = field_mappings.get(local_field)
            if not mapping:
                continue

            # Prefer a nested source_path if provided, else a flat source_field
            if mapping.source_path:
                raw_value = _get_nested(source_ticket, mapping.source_path)
            else:
                raw_value = source_ticket.get(mapping.source_field)

        if local_field in ("created_at", "closed_at"):
            raw_value = _parse_datetime(raw_value)
        elif raw_value is not None:
            raw_value = str(raw_value)

        result[local_field] = raw_value

    return result


DEFAULT_IRIS_FIELD_MAPPINGS = [
    # (local_field, source_field, source_path)
    # source_path is used for nested JSON fields; set to None for flat fields.
    # These are based on actual DFIR-IRIS API response inspection.
    ("external_id", "case_id", None),
    ("title", "case_name", None),
    ("severity", "classification", None),        # classification name string (may be null)
    ("status", "state_name", None),              # "Open", "Closed", etc. (not status_name which returns "Unknown")
    ("assigned_to", "owner", None),              # flat string like "administrator" (not nested)
    ("created_at", "initial_date", None),        # full ISO timestamp "2026-07-03T07:27:13.787739"
    ("closed_at", "close_date", None),           # "2026-07-10" date string
]


def seed_default_iris_mappings():
    """Idempotently insert a sensible default set of IRIS field mappings
    as global defaults (client_id=NULL).
    Admins can edit/replace these later from the Settings/Field Mapping UI."""
    from extensions import db

    existing = {
        (m.source_system, m.client_id, m.local_field)
        for m in FieldMapping.query.all()
    }
    created = 0
    for local_field, source_field, source_path in DEFAULT_IRIS_FIELD_MAPPINGS:
        key = ("dfir_iris", None, local_field)
        if key in existing:
            continue
        db.session.add(FieldMapping(
            source_system="dfir_iris",
            client_id=None,  # global default
            local_field=local_field,
            source_field=source_field,
            source_path=source_path,
            is_active=True,
        ))
        created += 1
    if created:
        db.session.commit()
    return created
