import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app
from extensions import db
from models import Client, SLARule, Ticket

app = create_app()
with app.app_context():
    # Names of clients created by seed_data.py
    seed_client_names = ["Acme Corp", "Globex Industries", "Initech"]
    
    # Find these clients
    clients = Client.query.filter(Client.name.in_(seed_client_names)).all()
    client_ids = [c.id for c in clients]
    
    if not clients:
        print("No seed clients found. Database might already be clean.")
    else:
        # Delete tickets belonging to seed clients
        tickets_deleted = Ticket.query.filter(Ticket.client_id.in_(client_ids)).delete(synchronize_session=False)
        print(f"Deleted {tickets_deleted} seeded ticket(s).")
        
        # Delete SLA rules belonging to seed clients
        rules_deleted = SLARule.query.filter(SLARule.client_id.in_(client_ids)).delete(synchronize_session=False)
        print(f"Deleted {rules_deleted} seeded SLA rule(s).")
        
        # Delete seed clients themselves
        for c in clients:
            db.session.delete(c)
            print(f"Deleted seed client '{c.name}'.")
            
        db.session.commit()
        print("Cleanup complete!")
