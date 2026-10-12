"""
services/sync_service.py
--------------------------
Orchestrates a full sync cycle:
  1. Fetch customers from DFIR-IRIS and create/update local Client records
  2. Fetch raw cases from DFIR-IRIS (iris_api_service)
  3. Resolve each case's client via customer_id → Client.iris_customer_id
  4. Normalize each one into the generic ticket shape (field_mapping_service)
  5. Upsert into the local Ticket table, storing raw_payload_json
  6. Recalculate SLA status for affected tickets (sla_calculator)
  7. Soft-delete tickets that disappeared from IRIS (Gap #8)
  8. Detect and handle reopened tickets (Gap #10)
  9. Record the run in SyncLog
"""

import json
import logging
from datetime import datetime, timezone

from flask import current_app
from extensions import db
from models import Ticket, SyncLog, Client
from services import iris_api_service
from services.field_mapping_service import get_active_field_mappings, normalize_ticket
from services.sla_calculator import apply_sla_to_ticket

logger = logging.getLogger(__name__)


def _extract_city_from_raw_customer(raw_customer):
    """Deeply inspect raw IRIS customer dict for city/location in top-level fields or custom_attributes."""
    for key in ["city", "customer_city", "location", "customer_location", "region", "customer_region"]:
        if raw_customer.get(key):
            return str(raw_customer[key]).strip()

    custom_attrs = raw_customer.get("custom_attributes")
    if isinstance(custom_attrs, dict):
        for cat_name, cat_val in custom_attrs.items():
            if isinstance(cat_val, dict):
                for field_name, field_val in cat_val.items():
                    if field_name.lower() in ["city", "location", "region", "city hub", "city_hub"]:
                        if isinstance(field_val, dict):
                            val = field_val.get("value")
                            if val:
                                return str(val).strip()
                        elif isinstance(field_val, str):
                            return field_val.strip()
    return None


def sync_customers_from_iris():
    """Fetch all customers from DFIR-IRIS and create/update local Client records.

    This ensures the local Client table always reflects what's configured in IRIS,
    so that case sync can resolve client_id without manual setup.

    Returns a summary dict: {fetched, created, updated}
    """
    try:
        raw_customers = iris_api_service.fetch_customers()
    except Exception as exc:
        logger.warning("Could not fetch customers from IRIS: %s", exc)
        return {"fetched": 0, "created": 0, "updated": 0}

    created = 0
    updated = 0
    default_tz = current_app.config.get("DEFAULT_TIMEZONE", "Asia/Karachi")

    for raw_customer in raw_customers:
        # DFIR-IRIS customer fields: customer_id, customer_name, customer_description, etc.
        customer_id = raw_customer.get("customer_id")
        customer_name = raw_customer.get("customer_name", "").strip()

        if not customer_id and not customer_name:
            continue

        iris_id_str = str(customer_id).strip() if customer_id else None

        # Try to find existing client by iris_customer_id first, then by name
        client = None
        if iris_id_str:
            client = Client.query.filter_by(iris_customer_id=iris_id_str).first()
        if client is None and customer_name:
            client = Client.query.filter_by(name=customer_name).first()

        # Extract location/city from IRIS customer object
        customer_city = _extract_city_from_raw_customer(raw_customer)

        if client is None:
            # Create new client from IRIS customer
            client = Client(
                name=customer_name or f"IRIS Customer {customer_id}",
                iris_customer_id=iris_id_str,
                city=customer_city or "Islamabad",
                timezone=default_tz,
                is_active=True,
            )
            db.session.add(client)
            created += 1
            logger.info(
                "Auto-created client '%s' (iris_customer_id=%s, city=%s) from IRIS.",
                client.name, iris_id_str, client.city,
            )
        else:
            # Update existing client's iris_customer_id or city if provided
            if iris_id_str and client.iris_customer_id != iris_id_str:
                client.iris_customer_id = iris_id_str
                updated += 1
            if customer_city and client.city != customer_city:
                client.city = customer_city
                updated += 1

    db.session.commit()

    summary = {"fetched": len(raw_customers), "created": created, "updated": updated}
    logger.info("Customer sync complete: %s", summary)
    return summary


def _resolve_client(raw_case):
    """Map a raw IRIS case to a Client using customer ID fields.

    Checks multiple possible field names from the IRIS response and matches
    against both Client.iris_customer_id (numeric ID) and Client.name.

    Returns None if no matching client is found.
    """
    # DFIR-IRIS uses various field names for the customer reference
    customer_id = (
        raw_case.get("case_customer") or
        raw_case.get("client_name") or
        raw_case.get("customer_name") or
        raw_case.get("case_customer_id") or
        raw_case.get("customer_id")
    )
    if customer_id is None:
        return None

    # Handle nested customer object (some IRIS versions return an object)
    if isinstance(customer_id, dict):
        nested_id = customer_id.get("customer_id")
        nested_name = customer_id.get("customer_name")
        if nested_id:
            client = Client.query.filter_by(
                iris_customer_id=str(nested_id).strip(), is_active=True
            ).first()
            if client:
                return client
        if nested_name:
            client = Client.query.filter_by(
                name=nested_name.strip(), is_active=True
            ).first()
            if client:
                return client
        return None

    customer_id_str = str(customer_id).strip()

    # Match by iris_customer_id (the numeric IRIS customer ID)
    client = Client.query.filter_by(
        iris_customer_id=customer_id_str, is_active=True
    ).first()
    if client:
        return client

    # Fallback: match by client name (in case the field contains a name string)
    client = Client.query.filter_by(
        name=customer_id_str, is_active=True
    ).first()
    return client


def _apply_normalized_to_ticket(ticket, normalized):
    """Apply normalized field values to a Ticket model instance.

    Gap #10 (Reopened tickets): if the ticket previously had a
    ``closed_at_source`` timestamp but the new sync data no longer has one,
    the ticket has been reopened in IRIS. We clear the closed timestamp and
    force SLA re-evaluation.
    """
    # Gap #10: detect reopen
    was_closed = ticket.closed_at_source is not None
    new_closed = normalized.get("closed_at")

    ticket.title = normalized.get("title")
    ticket.status = normalized.get("status")
    ticket.severity = normalized.get("severity")
    ticket.priority = normalized.get("priority")
    ticket.criticality = normalized.get("criticality")
    ticket.assigned_to = normalized.get("assigned_to")
    if normalized.get("created_at"):
        ticket.created_at_source = normalized["created_at"]
    ticket.closed_at_source = new_closed
    ticket.last_synced_at = datetime.now(timezone.utc)

    # Gap #10: if reopened, clear SLA status to force re-evaluation
    if was_closed and new_closed is None:
        logger.info(
            "Ticket %s (client=%s) was reopened — clearing closed_at, "
            "forcing SLA re-evaluation.",
            ticket.external_id, ticket.client_id,
        )
        ticket.sla_status = None
        # Optionally reset pause tracking since the ticket is "new" again
        ticket.paused_at = None
        # Reset notification flags for the reopened ticket
        ticket.near_breach_notified = False
        ticket.breach_notified = False


def _soft_delete_missing_tickets(client_id, source_system, seen_external_ids):
    """Gap #8: Mark tickets that were in the DB but not in the latest IRIS
    sync response. These have presumably been deleted in IRIS.

    We use a soft-delete: set ``status = 'deleted_in_source'`` rather than
    removing the row, so historical SLA data is preserved.
    """
    if not seen_external_ids:
        return 0

    missing_tickets = Ticket.query.filter(
        Ticket.client_id == client_id,
        Ticket.source_system == source_system,
        Ticket.external_id.notin_(seen_external_ids),
        Ticket.status != "deleted_in_source",
    ).all()

    for ticket in missing_tickets:
        ticket.status = "deleted_in_source"
        apply_sla_to_ticket(ticket, commit=False)
        logger.info(
            "Ticket %s (client=%s) not found in IRIS response — marking as "
            "deleted_in_source.",
            ticket.external_id, client_id,
        )

    return len(missing_tickets)


def sync_cases_from_iris(source_system="dfir_iris"):
    """
    Runs one full sync cycle against DFIR-IRIS. Safe to call manually
    (from a route) or on a schedule (from scheduler_service).

    Now auto-syncs customers from IRIS first, then fetches cases.

    Gap #1: tickets are routed to clients via case_customer_id.
    Gap #8: explicit upsert logic + soft-delete for missing tickets.
    Gap #10: reopened ticket detection.

    Returns a summary dict: {fetched, created, updated, skipped, soft_deleted, sync_log_id, customers}
    """
    # Step 0: Sync customers from IRIS so client matching works
    customer_summary = sync_customers_from_iris()

    sync_log = SyncLog(sync_started_at=datetime.now(timezone.utc))
    db.session.add(sync_log)
    db.session.commit()

    try:
        raw_cases = iris_api_service.fetch_all_cases()

        created_count = 0
        updated_count = 0
        skipped_count = 0

        # Track which external_ids we saw per client for soft-delete (Gap #8)
        seen_by_client = {}  # { client_id: set(external_ids) }

        for raw_case in raw_cases:
            # Fetch full case details to get precise timestamp fields like initial_date
            try:
                case_id = raw_case.get("case_id")
                if case_id:
                    detailed = iris_api_service.fetch_case_by_id(case_id)
                    if detailed:
                        raw_case.update(detailed)
            except Exception as exc:
                logger.warning("Could not fetch detailed case %s: %s", raw_case.get("case_id"), exc)

            # Gap #1: resolve client
            client = _resolve_client(raw_case)
            if client is None:
                customer_id = (
                    raw_case.get("case_customer") or
                    raw_case.get("case_customer_id") or
                    raw_case.get("customer_id") or
                    raw_case.get("client_name")
                )
                logger.warning(
                    "Skipping case (customer_id=%s): no matching Client found.",
                    customer_id,
                )
                skipped_count += 1
                continue

            # Get client-aware field mappings
            field_mappings = get_active_field_mappings(
                source_system=source_system, client_id=client.id
            )
            normalized = normalize_ticket(raw_case, field_mappings)
            external_id = normalized.get("external_id")
            if not external_id:
                # Can't upsert without a stable external identifier - skip,
                # but don't fail the whole sync over one bad record.
                skipped_count += 1
                continue

            # Track for soft-delete
            seen_by_client.setdefault(client.id, set()).add(str(external_id))

            # Gap #8: explicit upsert — does it exist → update, else → insert
            ticket = Ticket.query.filter_by(
                client_id=client.id,
                source_system=source_system,
                external_id=str(external_id),
            ).first()

            if ticket is None:
                ticket = Ticket(
                    client_id=client.id,
                    source_system=source_system,
                    external_id=str(external_id),
                )
                db.session.add(ticket)
                created_count += 1
            else:
                updated_count += 1

            _apply_normalized_to_ticket(ticket, normalized)
            ticket.raw_payload_json = json.dumps(raw_case, default=str)

            # Flush so the ticket has an id / persisted created_at_source
            # available before SLA calc reads it back.
            db.session.flush()
            apply_sla_to_ticket(ticket, commit=False)

        # Gap #8: soft-delete tickets not seen in this sync
        soft_deleted_count = 0
        for client_id, seen_ids in seen_by_client.items():
            soft_deleted_count += _soft_delete_missing_tickets(
                client_id, source_system, seen_ids
            )

        db.session.commit()

        total_fetched = len(raw_cases)
        sync_log.mark_success(
            fetched=total_fetched, created=created_count, updated=updated_count
        )
        db.session.commit()

        summary = {
            "fetched": total_fetched,
            "created": created_count,
            "updated": updated_count,
            "skipped": skipped_count,
            "soft_deleted": soft_deleted_count,
            "sync_log_id": sync_log.id,
            "customers": customer_summary,
        }
        logger.info("Sync complete: %s", summary)
        return summary

    except Exception as exc:  # noqa: BLE001 - we want to log any failure and re-raise
        db.session.rollback()
        sync_log.mark_failed(exc)
        db.session.commit()
        raise

