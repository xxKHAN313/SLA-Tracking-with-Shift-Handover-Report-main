"""
tests/test_pdf_formatting.py
----------------------------
Test verifying executive PDF generation with NumberedCanvas page numbering.
"""

import os
from services.report_generator import generate_pdf_report


def test_generate_pdf_report_executive_formatting(app, db, sample_client):
    with app.app_context():
        report = generate_pdf_report(app, "SLA Summary Report", client_id=sample_client.id)
        assert report is not None
        assert report.file_format == "pdf"
        assert os.path.exists(report.file_path)
        assert os.path.getsize(report.file_path) > 1000
