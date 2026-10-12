"""
tests/test_sync_service.py
-----------------------------
Test cases for the sync service (Gap #12).

Tests cover:
  1. Upsert creates new ticket
  2. Upsert updates existing ticket
  3. Unknown client (no matching Client) → skipped
  4. Soft-delete for tickets missing from IRIS response
"""

from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

from models import Ticket, Client
from services.sync_service import sync_cases_from_iris, _resolve_client, _soft_delete_missing_tickets


class TestResolveClient:
    """Client resolution from IRIS case_customer_id."""

    def test_resolves_known_client(self, app, db, sample_client):
        with app.app_context():
            raw_case = {"case_customer_id": "TEST001"}
            client = _resolve_client(raw_case)
            assert client is not None
            assert client.name == "Test Client"

    def test_returns_none_for_unknown_customer(self, app, db, sample_client):
        with app.app_context():
            raw_case = {"case_customer_id": "UNKNOWN999"}
            client = _resolve_client(raw_case)
            assert client is None

    def test_returns_none_when_no_customer_id(self, app, db, sample_client):
        with app.app_context():
            raw_case = {"case_name": "Some case"}
            client = _resolve_client(raw_case)
            assert client is None


class TestUpsertCreatesNewTicket:
    """First sync with a new external_id creates a ticket."""

    @patch("services.sync_service.iris_api_service")
    def test_creates_ticket(self, mock_iris, app, db, sample_client):
        with app.app_context():
            mock_iris.fetch_case_by_id.return_value = None
            mock_iris.fetch_all_cases.return_value = [
                {
                    "case_id": "CASE-NEW-1",
                    "case_name": "New Test Case",
                    "case_customer_id": "TEST001",
                    "severity": "Critical",
                    "priority": None,
                    "criticality": None,
                    "status_name": "open",
                    "owner": "alice",
                    "created_at": "2026-07-01T10:00:00",
                    "closed_at": None,
                },
            ]

            # Need a matching SLA rule
            from models import SLARule
            rule = SLARule(
                client_id=sample_client.id, rule_name="Test Rule",
                field_name="severity", field_value="Critical", priority=10,
                resolution_sla_minutes=480, stop_status="closed",
            )
            db.session.add(rule)

            # Seed default field mappings
            from services.field_mapping_service import seed_default_iris_mappings
            seed_default_iris_mappings()
            db.session.commit()

            result = sync_cases_from_iris()
            assert result["created"] == 1
            assert result["updated"] == 0

            ticket = Ticket.query.filter_by(external_id="CASE-NEW-1").first()
            assert ticket is not None
            assert ticket.client_id == sample_client.id
            assert ticket.title == "New Test Case"


class TestUpsertUpdatesExistingTicket:
    """Second sync with same external_id updates, doesn't duplicate."""

    @patch("services.sync_service.iris_api_service")
    def test_updates_existing(self, mock_iris, app, db, sample_client):
        with app.app_context():
            # Pre-create a ticket
            t = Ticket(
                client_id=sample_client.id, external_id="CASE-EXIST-1",
                source_system="dfir_iris", title="Old Title",
                severity="Critical", status="open",
                created_at_source=datetime(2026, 7, 1, tzinfo=timezone.utc),
            )
            db.session.add(t)

            from models import SLARule
            rule = SLARule(
                client_id=sample_client.id, rule_name="Test Rule",
                field_name="severity", field_value="Critical", priority=10,
                resolution_sla_minutes=480, stop_status="closed",
            )
            db.session.add(rule)

            from services.field_mapping_service import seed_default_iris_mappings
            seed_default_iris_mappings()
            db.session.commit()

            mock_iris.fetch_case_by_id.return_value = None
            mock_iris.fetch_all_cases.return_value = [
                {
                    "case_id": "CASE-EXIST-1",
                    "case_name": "Updated Title",
                    "case_customer_id": "TEST001",
                    "severity": "Critical",
                    "priority": None,
                    "criticality": None,
                    "status_name": "open",
                    "owner": "bob",
                    "created_at": "2026-07-01T00:00:00",
                    "closed_at": None,
                },
            ]

            result = sync_cases_from_iris()
            assert result["created"] == 0
            assert result["updated"] == 1

            tickets = Ticket.query.filter_by(external_id="CASE-EXIST-1").all()
            assert len(tickets) == 1
            assert tickets[0].title == "Updated Title"
            assert tickets[0].assigned_to == "bob"


class TestUnknownClientSkipped:
    """Ticket with case_customer_id not matching any Client → skipped."""

    @patch("services.sync_service.iris_api_service")
    def test_skips_unknown_client(self, mock_iris, app, db, sample_client):
        with app.app_context():
            mock_iris.fetch_case_by_id.return_value = None
            mock_iris.fetch_all_cases.return_value = [
                {
                    "case_id": "CASE-SKIP-1",
                    "case_name": "Unknown Client Case",
                    "case_customer_id": "UNKNOWN_CLIENT",
                    "severity": "High",
                    "status_name": "open",
                    "owner": "alice",
                    "created_at": "2026-07-01T10:00:00",
                    "closed_at": None,
                },
            ]

            from services.field_mapping_service import seed_default_iris_mappings
            seed_default_iris_mappings()
            db.session.commit()

            result = sync_cases_from_iris()
            assert result["skipped"] == 1
            assert result["created"] == 0


class TestSoftDeleteMissingTickets:
    """Ticket in DB but not in IRIS response → marked 'deleted_in_source'."""

    def test_soft_delete(self, app, db, sample_client):
        with app.app_context():
            # Pre-create two tickets
            t1 = Ticket(
                client_id=sample_client.id, external_id="CASE-A",
                source_system="dfir_iris", title="Will remain", status="open",
                created_at_source=datetime(2026, 7, 1, tzinfo=timezone.utc),
            )
            t2 = Ticket(
                client_id=sample_client.id, external_id="CASE-B",
                source_system="dfir_iris", title="Will be deleted", status="open",
                created_at_source=datetime(2026, 7, 1, tzinfo=timezone.utc),
            )
            db.session.add_all([t1, t2])
            db.session.commit()

            # Simulate sync where only CASE-A was seen
            deleted = _soft_delete_missing_tickets(
                sample_client.id, "dfir_iris", {"CASE-A"}
            )
            db.session.commit()

            assert deleted == 1
            t2_updated = Ticket.query.filter_by(external_id="CASE-B").first()
            assert t2_updated.status == "deleted_in_source"
            assert not t2_updated.is_open()

            # CASE-A should be untouched
            t1_updated = Ticket.query.filter_by(external_id="CASE-A").first()
            assert t1_updated.status == "open"
            assert t1_updated.is_open()


class TestCustomAttributesParsing:
    """Verify that priority and criticality are correctly extracted from nested custom attributes."""

    def test_custom_attributes_parsed_successfully(self):
        from services.field_mapping_service import normalize_ticket
        source_ticket = {
            "case_id": "CASE-1",
            "case_name": "Test Case",
            "custom_attributes": {
                "SLA_Priority_Tracker": {
                    "Priority": {
                        "value": "High"
                    },
                    "Criticality": {
                        "value": "Tier-1"
                    }
                }
            }
        }
        normalized = normalize_ticket(source_ticket, {})
        assert normalized["priority"] == "High"
        assert normalized["criticality"] == "Tier-1"

    def test_custom_attributes_missing_defaults_to_na(self):
        from services.field_mapping_service import normalize_ticket
        source_ticket = {
            "case_id": "CASE-2",
            "case_name": "Test Case",
            # custom_attributes is entirely missing
        }
        normalized = normalize_ticket(source_ticket, {})
        assert normalized["priority"] == "N/A"
        assert normalized["criticality"] == "N/A"

    def test_custom_attributes_null_defaults_to_na(self):
        from services.field_mapping_service import normalize_ticket
        source_ticket = {
            "case_id": "CASE-3",
            "case_name": "Test Case",
            "custom_attributes": {
                "SLA_Priority_Tracker": {
                    "Priority": None,
                    "Criticality": {"value": None}
                }
            }
        }
        normalized = normalize_ticket(source_ticket, {})
        assert normalized["priority"] == "N/A"
        assert normalized["criticality"] == "N/A"

    def test_custom_attributes_invalid_type_defaults_to_na(self):
        from services.field_mapping_service import normalize_ticket
        source_ticket = {
            "case_id": "CASE-4",
            "case_name": "Test Case",
            "custom_attributes": "invalid-string-here"
        }
        normalized = normalize_ticket(source_ticket, {})
        assert normalized["priority"] == "N/A"
        assert normalized["criticality"] == "N/A"

