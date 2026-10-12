"""
tests/conftest.py
-----------------
Pytest fixtures for the SLA tracker test suite.

Provides:
  - app: Flask app with testing config (in-memory SQLite)
  - db: clean database session per test
  - client: Flask test client
  - sample_client: a Client model instance
  - sample_clients: multiple Client instances for multi-tenant tests
  - sample_rules: SLA rules scoped to sample clients
"""

import pytest
from datetime import datetime, timedelta, timezone

from app import create_app
from extensions import db as _db
from models import Client, SLARule, Ticket, User


@pytest.fixture(scope="session")
def app():
    """Create a Flask app with test configuration."""
    app = create_app("testing")
    return app


@pytest.fixture(autouse=True)
def db(app):
    """Create clean tables for each test, then drop after."""
    from routes.auth_routes import _failed_attempts
    _failed_attempts.clear()
    with app.app_context():
        _db.create_all()
        yield _db
        _db.session.rollback()
        _db.drop_all()
        _failed_attempts.clear()


@pytest.fixture
def client(app):
    """Flask test client."""
    return app.test_client()


@pytest.fixture
def sample_client(app, db):
    """A single Client for basic tests."""
    with app.app_context():
        c = Client(name="Test Client", iris_customer_id="TEST001", timezone="UTC")
        db.session.add(c)
        db.session.commit()
        db.session.refresh(c)
        db.session.expunge(c)
        return c


@pytest.fixture
def sample_clients(app, db):
    """Two clients for multi-tenant tests."""
    with app.app_context():
        c1 = Client(name="Client Alpha", iris_customer_id="ALPHA01", timezone="UTC")
        c2 = Client(name="Client Beta", iris_customer_id="BETA02", timezone="America/New_York")
        db.session.add_all([c1, c2])
        db.session.commit()
        db.session.refresh(c1)
        db.session.refresh(c2)
        db.session.expunge(c1)
        db.session.expunge(c2)
        return c1, c2


@pytest.fixture
def sample_rules(app, db, sample_clients):
    """SLA rules scoped to the two sample clients."""
    c1, c2 = sample_clients
    with app.app_context():
        rules = [
            # Client Alpha: severity-based
            SLARule(
                client_id=c1.id, rule_name="Alpha Critical", field_name="severity",
                field_value="Critical", priority=10,
                response_sla_minutes=60, resolution_sla_minutes=480,
                warning_threshold_percent=80, stop_status="closed,resolved",
                pause_status="awaiting_client",
            ),
            SLARule(
                client_id=c1.id, rule_name="Alpha P1", field_name="priority",
                field_value="P1", priority=20,
                response_sla_minutes=30, resolution_sla_minutes=240,
                warning_threshold_percent=75, stop_status="closed",
            ),
            # Client Beta: same field_value "Critical" but different SLA minutes
            SLARule(
                client_id=c2.id, rule_name="Beta Critical", field_name="severity",
                field_value="Critical", priority=10,
                response_sla_minutes=30, resolution_sla_minutes=120,
                warning_threshold_percent=80, stop_status="closed",
                business_hours_only=True,
            ),
        ]
        db.session.add_all(rules)
        db.session.commit()
        for r in rules:
            db.session.refresh(r)
            db.session.expunge(r)
        return rules


@pytest.fixture
def now():
    """Current UTC time for consistent test assertions."""
    return datetime.now(timezone.utc)
