import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app
from extensions import db
from models import FieldMapping

app = create_app()
with app.app_context():
    # Update global field mappings to match the keys returned by this IRIS instance
    mappings = {
        "created_at": "initial_date",
        "closed_at": "case_close_date",
        "status": "state_name",
        # Map local severity to IRIS classification so we can build rules on classification!
        "severity": "classification", 
        "assigned_to": "owner"
    }
    
    for local_field, source_field in mappings.items():
        mapping = FieldMapping.query.filter_by(source_system="dfir_iris", local_field=local_field).first()
        if mapping:
            mapping.source_field = source_field
            print(f"Updated mapping: {local_field} <- {source_field}")
        else:
            mapping = FieldMapping(source_system="dfir_iris", local_field=local_field, source_field=source_field)
            db.session.add(mapping)
            print(f"Created mapping: {local_field} <- {source_field}")
            
    db.session.commit()
    print("Done! Mappings updated successfully. Please run a sync in the UI now.")
