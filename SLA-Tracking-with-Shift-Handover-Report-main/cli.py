import click
from extensions import db
from models import User, Client, SLARule, Ticket, FieldMapping
from services.field_mapping_service import seed_default_iris_mappings

def register_cli_commands(app):
    @app.cli.command("seed")
    def seed_command():
        """Initialize the database and seed default users, mappings, and IRIS data."""
        db.create_all()
        
        # Seed default users
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
        click.echo("Seeded users: admin/Admin123! , manager/Manager123! , viewer/Viewer123!")

        # Seed field mappings
        mappings_created = seed_default_iris_mappings()
        if mappings_created:
            click.echo(f"Seeded {mappings_created} default IRIS field mapping(s).")
        else:
            click.echo("Field mappings already exist — skipping.")

        # Sync customers from IRIS
        click.echo("\nFetching live data from IRIS...")
        from services.sync_service import sync_customers_from_iris
        try:
            summary = sync_customers_from_iris()
            click.echo(f"IRIS customer sync: {summary['created']} created, {summary['updated']} updated "
                       f"(fetched {summary['fetched']} from IRIS).")
        except Exception as exc:
            click.echo(f"Warning: Could not sync customers from IRIS: {exc}")
            click.echo("You can sync customers later from the Settings page.")

        # Sync cases from IRIS
        from services.sync_service import sync_cases_from_iris
        try:
            summary = sync_cases_from_iris()
            click.echo(f"Initial IRIS sync: {summary['fetched']} fetched, {summary['created']} created, "
                       f"{summary['updated']} updated, {summary['skipped']} skipped.")
        except Exception as exc:
            click.echo(f"Warning: Could not sync cases from IRIS: {exc}")
            click.echo("You can sync cases later from the Tickets page.")

        click.echo("\nDatabase initialized successfully.")

    @app.cli.command("clear-seed")
    def clear_seed_command():
        """Clear all seeded clients, rules, and tickets from the database."""
        seed_client_names = ["Acme Corp", "Globex Industries", "Initech"]
        clients = Client.query.filter(Client.name.in_(seed_client_names)).all()
        client_ids = [c.id for c in clients]
        
        if not clients:
            click.echo("No seed clients found. Database might already be clean.")
        else:
            tickets_deleted = Ticket.query.filter(Ticket.client_id.in_(client_ids)).delete(synchronize_session=False)
            click.echo(f"Deleted {tickets_deleted} seeded ticket(s).")
            
            rules_deleted = SLARule.query.filter(SLARule.client_id.in_(client_ids)).delete(synchronize_session=False)
            click.echo(f"Deleted {rules_deleted} seeded SLA rule(s).")
            
            for c in clients:
                db.session.delete(c)
                click.echo(f"Deleted seed client '{c.name}'.")
                
            db.session.commit()
            click.echo("Cleanup complete!")

    @app.cli.command("fix-mappings")
    def fix_mappings_command():
        """Update global field mappings to match the keys returned by DFIR-IRIS."""
        mappings = {
            "created_at": "initial_date",
            "closed_at": "case_close_date",
            "status": "state_name",
            "severity": "classification", 
            "assigned_to": "owner"
        }
        
        for local_field, source_field in mappings.items():
            mapping = FieldMapping.query.filter_by(source_system="dfir_iris", local_field=local_field).first()
            if mapping:
                mapping.source_field = source_field
                click.echo(f"Updated mapping: {local_field} <- {source_field}")
            else:
                mapping = FieldMapping(source_system="dfir_iris", local_field=local_field, source_field=source_field)
                db.session.add(mapping)
                click.echo(f"Created mapping: {local_field} <- {source_field}")
                
        db.session.commit()
        click.echo("Done! Mappings updated successfully. Please run a sync in the UI now.")
