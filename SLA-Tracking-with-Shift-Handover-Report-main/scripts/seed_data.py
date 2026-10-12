"""
seed_data.py
------------
One-shot script to initialize the database with:
  - Tables (db.create_all())
  - Default users (Admin/Manager/Viewer) with hashed passwords
  - Default DFIR-IRIS field mappings
  - Live customers fetched from IRIS (auto-creates Client records)
  - Initial case sync from IRIS

Run:
    python scripts/seed_data.py
"""

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app
from extensions import db
from models import User
from services.field_mapping_service import seed_default_iris_mappings

app = create_app()


def seed_users():
    users = [
        ("admin", "admin@example.com", "Admin123!", "Admin"),
        ("manager", "manager@example.com", "Manager123!", "Manager"),
        ("viewer", "viewer@example.com", "Viewer123!", "Viewer"),
    ]
    for username, email, password, role in users:
        if User.query.filter_by(username=username).first():
            continue
        user = User(username=username, email=email, role=role)
        user.set_password(password)
        db.session.add(user)
    db.session.commit()
    print("Seeded users: admin/Admin123! , manager/Manager123! , viewer/Viewer123!")


def sync_iris_customers():
    """Fetch live customers from IRIS and create Client records."""
    from services.sync_service import sync_customers_from_iris

    try:
        summary = sync_customers_from_iris()
        print(f"IRIS customer sync: {summary['created']} created, {summary['updated']} updated "
              f"(fetched {summary['fetched']} from IRIS).")
    except Exception as exc:
        print(f"Warning: Could not sync customers from IRIS: {exc}")
        print("You can sync customers later from the Settings page.")


def run_initial_sync():
    """Run an initial case sync from IRIS."""
    from services.sync_service import sync_cases_from_iris

    try:
        summary = sync_cases_from_iris()
        print(f"Initial IRIS sync: {summary['fetched']} fetched, {summary['created']} created, "
              f"{summary['updated']} updated, {summary['skipped']} skipped.")
    except Exception as exc:
        print(f"Warning: Could not sync cases from IRIS: {exc}")
        print("You can sync cases later from the Tickets page.")


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        seed_users()
        mappings_created = seed_default_iris_mappings()
        if mappings_created:
            print(f"Seeded {mappings_created} default IRIS field mapping(s).")
        else:
            print("Field mappings already exist — skipping.")

        print("\nFetching live data from IRIS...")
        sync_iris_customers()
        run_initial_sync()

        print("\nDatabase initialized successfully.")
