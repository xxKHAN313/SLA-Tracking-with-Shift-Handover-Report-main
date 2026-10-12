"""
services/report_generator.py
------------------------------
Generates PDF and Excel reports from ticket/SLA data.

Report types (see models/report.py for constants):
  - SLA Summary Report        : high-level compliance stats
  - Detailed Ticket SLA Report: full ticket-by-ticket breakdown
  - Breached Tickets Report   : only breached / closed-after-breach tickets
  - Analyst Performance Report: per-assignee SLA compliance stats

Uses pandas + openpyxl for Excel, and ReportLab for PDF.
"""

import os
import uuid
from datetime import datetime, timezone

import pandas as pd
from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer,
)

from extensions import db
from models import Ticket, Report
from models.ticket import SLA_BREACHED, SLA_CLOSED_AFTER_BREACH, SLA_NEAR_BREACH, SLA_NO_RULE


def _ensure_reports_folder(app):
    folder = app.config["REPORTS_FOLDER"]
    os.makedirs(folder, exist_ok=True)
    return folder


def _tickets_to_rows(tickets):
    rows = []
    for t in tickets:
        rows.append({
            "External ID": t.external_id,
            "Title": t.title,
            "Status": t.status,
            "Severity": t.severity,
            "Priority": t.priority,
            "Criticality": t.criticality,
            "Assigned To": t.assigned_to,
            "Created At": t.created_at_source.strftime("%Y-%m-%d %H:%M") if t.created_at_source else "",
            "SLA Rule Applied": t.sla_rule.rule_name if t.sla_rule_id and t.sla_rule else "N/A",
            "Resolution Deadline": t.resolution_deadline.strftime("%Y-%m-%d %H:%M") if t.resolution_deadline else "",
            "SLA Status": t.sla_status,
            "Breach Duration (min)": t.breach_duration_minutes or 0,
        })
    return rows


def _compute_summary_stats(tickets):
    total = len(tickets)
    breached = sum(1 for t in tickets if t.sla_status in (SLA_BREACHED, SLA_CLOSED_AFTER_BREACH))
    near_breach = sum(1 for t in tickets if t.sla_status == SLA_NEAR_BREACH)
    no_rule = sum(1 for t in tickets if t.sla_status == SLA_NO_RULE)
    within = total - breached - near_breach - no_rule
    compliance_pct = round((within / total) * 100, 1) if total else 0.0

    return {
        "total_tickets": total,
        "within_sla": within,
        "near_breach": near_breach,
        "breached": breached,
        "no_matching_rule": no_rule,
        "sla_compliance_percent": compliance_pct,
    }


def _analyst_performance_rows(tickets):
    by_analyst = {}
    for t in tickets:
        analyst = t.assigned_to or "Unassigned"
        stats = by_analyst.setdefault(analyst, {"total": 0, "breached": 0, "within": 0})
        stats["total"] += 1
        if t.sla_status in (SLA_BREACHED, SLA_CLOSED_AFTER_BREACH):
            stats["breached"] += 1
        elif t.sla_status not in (SLA_NO_RULE,):
            stats["within"] += 1

    rows = []
    for analyst, stats in sorted(by_analyst.items(), key=lambda kv: -kv[1]["total"]):
        compliance = round((stats["within"] / stats["total"]) * 100, 1) if stats["total"] else 0.0
        rows.append({
            "Analyst": analyst,
            "Total Tickets": stats["total"],
            "Within SLA": stats["within"],
            "Breached": stats["breached"],
            "Compliance %": compliance,
        })
    return rows


def _filename(report_type, ext):
    safe_type = report_type.lower().replace(" ", "_")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"{safe_type}_{stamp}_{uuid.uuid4().hex[:6]}.{ext}"


def _get_tickets_for_report(report_type, client_id=None):
    query = Ticket.query
    if client_id:
        query = query.filter(Ticket.client_id == client_id)
    if report_type == "Breached Tickets Report":
        return query.filter(
            Ticket.sla_status.in_([SLA_BREACHED, SLA_CLOSED_AFTER_BREACH])
        ).order_by(Ticket.breach_duration_minutes.desc()).all()
    return query.order_by(Ticket.created_at_source.desc()).all()


# ---------------------------------------------------------------------------
# Excel generation
# ---------------------------------------------------------------------------

def generate_excel_report(app, report_type, generated_by_user_id=None, client_id=None):
    folder = _ensure_reports_folder(app)
    tickets = _get_tickets_for_report(report_type, client_id)
    summary = _compute_summary_stats(tickets)

    client_name = "All Clients"
    if client_id:
        from models import Client
        c = Client.query.get(client_id)
        if c:
            client_name = c.name

    filename = _filename(report_type, "xlsx")
    filepath = os.path.join(folder, filename)

    with pd.ExcelWriter(filepath, engine="openpyxl") as writer:
        summary_df = pd.DataFrame([{
            "Report": report_type,
            "Target Client": client_name,
            "Generated At": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            **summary,
        }])
        summary_df.to_excel(writer, sheet_name="Summary", index=False)

        if report_type == "Analyst Performance Report":
            perf_df = pd.DataFrame(_analyst_performance_rows(tickets))
            perf_df.to_excel(writer, sheet_name="Analyst Performance", index=False)
        else:
            detail_df = pd.DataFrame(_tickets_to_rows(tickets))
            sheet_name = "Breached Tickets" if report_type == "Breached Tickets Report" else "Tickets"
            detail_df.to_excel(writer, sheet_name=sheet_name, index=False)

    report = Report(
        report_name=filename,
        report_type=report_type,
        file_format="xlsx",
        file_path=filepath,
        generated_by=generated_by_user_id,
    )
    db.session.add(report)
    db.session.commit()
    return report


# ---------------------------------------------------------------------------
# PDF generation with Executive Layout, Dynamic Page Numbers & Branding
# ---------------------------------------------------------------------------

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

class NumberedCanvas(canvas.Canvas):
    """
    Two-pass canvas that records total page count and draws professional
    headers, footers, page numbers ('Page X of Y'), and image logos on Portrait A4.
    """
    logo_path = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_decorations(num_pages)
            super().showPage()
        super().save()

    def draw_page_decorations(self, page_count):
        self.saveState()

        # Top Header Accent Bar (A4 Portrait Width: 595.27 pt, Height: 841.89 pt)
        self.setFillColor(colors.HexColor("#1a2980"))
        self.rect(0, 810, 595, 32, fill=True, stroke=False)
        self.setFillColor(colors.white)
        self.setFont("Helvetica-Bold", 10)
        self.drawString(36, 821, "AUTOMATED SLA TRACKER — EXECUTIVE REPORT")

        # Top Header Logo
        if self.logo_path and os.path.exists(self.logo_path):
            try:
                self.drawImage(self.logo_path, 550, 814, width=22, height=22, mask='auto', preserveAspectRatio=True)
            except Exception:
                pass

        # Bottom Footer Divider Line
        self.setStrokeColor(colors.HexColor("#e8ecf1"))
        self.setLineWidth(0.8)
        self.line(36, 40, 559, 40)

        # Footer Logo & Text
        if self.logo_path and os.path.exists(self.logo_path):
            try:
                self.drawImage(self.logo_path, 36, 18, width=16, height=16, mask='auto', preserveAspectRatio=True)
                self.setFont("Helvetica", 8)
                self.setFillColor(colors.HexColor("#5a6a7e"))
                self.drawString(56, 22, "CONFIDENTIAL — AUTOMATED INCIDENT SLA ENGINE")
            except Exception:
                self.setFont("Helvetica", 8)
                self.setFillColor(colors.HexColor("#5a6a7e"))
                self.drawString(36, 22, "CONFIDENTIAL — AUTOMATED INCIDENT SLA ENGINE")
        else:
            self.setFont("Helvetica", 8)
            self.setFillColor(colors.HexColor("#5a6a7e"))
            self.drawString(36, 22, "CONFIDENTIAL — AUTOMATED INCIDENT SLA ENGINE")

        page_str = f"Page {self._pageNumber} of {page_count}"
        self.drawRightString(559, 22, page_str)
        self.restoreState()


def generate_pdf_report(app, report_type, generated_by_user_id=None, client_id=None):
    folder = _ensure_reports_folder(app)
    tickets = _get_tickets_for_report(report_type, client_id)
    summary = _compute_summary_stats(tickets)

    client_name = "All Clients"
    if client_id:
        from models import Client
        c = Client.query.get(client_id)
        if c:
            client_name = c.name

    filename = _filename(report_type, "pdf")
    filepath = os.path.join(folder, filename)

    # Attach logo path to NumberedCanvas
    NumberedCanvas.logo_path = os.path.join(app.root_path, "static", "images", "image.png")

    # Standard Portrait A4 layout with clean margins
    doc = SimpleDocTemplate(
        filepath,
        pagesize=A4,
        leftMargin=1.0 * cm,
        rightMargin=1.0 * cm,
        topMargin=1.6 * cm,
        bottomMargin=1.6 * cm,
    )

    styles = getSampleStyleSheet()
    
    # Custom Typography Styles
    title_style = ParagraphStyle(
        "ExecReportTitle",
        parent=styles["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=18,
        leading=22,
        textColor=colors.HexColor("#1a2980"),
        spaceAfter=4,
    )

    meta_style = ParagraphStyle(
        "ExecReportMeta",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8.5,
        leading=11.5,
        textColor=colors.HexColor("#5a6a7e"),
        spaceAfter=12,
    )

    table_header_style = ParagraphStyle(
        "TableHeader",
        fontName="Helvetica-Bold",
        fontSize=8,
        leading=10,
        textColor=colors.white,
        alignment=0,
    )

    table_cell_style = ParagraphStyle(
        "TableCell",
        fontName="Helvetica",
        fontSize=7.5,
        leading=9.5,
        textColor=colors.HexColor("#2c3e50"),
        alignment=0,
    )

    table_cell_bold = ParagraphStyle(
        "TableCellBold",
        fontName="Helvetica-Bold",
        fontSize=7.5,
        leading=9.5,
        textColor=colors.HexColor("#1a2980"),
        alignment=0,
    )

    elements = []

    # Title & Metadata Section
    elements.append(Paragraph(report_type.upper(), title_style))
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    elements.append(Paragraph(f"<b>Target Client:</b> {client_name} &nbsp;|&nbsp; <b>Generated At:</b> {now_str} &nbsp;|&nbsp; <b>Total Scope:</b> {summary['total_tickets']} Ticket(s)", meta_style))

    # KPI Summary Cards Block (Portrait A4 total width = 538 pt)
    summary_data = [
        [
            Paragraph(f"<b>Total Incidents</b><br/><font size=11 color='#1a2980'><b>{summary['total_tickets']}</b></font>", styles["Normal"]),
            Paragraph(f"<b>Within SLA</b><br/><font size=11 color='#28a745'><b>{summary['within_sla']}</b></font>", styles["Normal"]),
            Paragraph(f"<b>Breached</b><br/><font size=11 color='#dc3545'><b>{summary['breached']}</b></font>", styles["Normal"]),
            Paragraph(f"<b>Compliance Rate</b><br/><font size=11 color='#1a2980'><b>{summary['sla_compliance_percent']}%</b></font>", styles["Normal"]),
        ]
    ]
    summary_table = Table(summary_data, colWidths=[134, 134, 134, 134], hAlign="LEFT")
    summary_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8f9fa")),
        ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor("#d5dde8")),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e8ecf1")),
        ("PADDING", (0, 0), (-1, -1), 5),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    elements.append(summary_table)
    elements.append(Spacer(1, 12))

    # Main Detailed Table Setup
    if report_type == "Analyst Performance Report":
        rows = _analyst_performance_rows(tickets)
        headers = ["Analyst Name", "Total Tickets", "Within SLA", "Breached", "Compliance %"]
        col_widths = [176, 90, 90, 90, 90]
        
        table_data = [[Paragraph(h, table_header_style) for h in headers]]
        for r in rows:
            comp_val = r.get("Compliance %", 0.0)
            comp_color = "#28a745" if comp_val >= 90 else ("#ffc107" if comp_val >= 70 else "#dc3545")
            table_data.append([
                Paragraph(str(r.get("Analyst", "")), table_cell_bold),
                Paragraph(str(r.get("Total Tickets", 0)), table_cell_style),
                Paragraph(str(r.get("Within SLA", 0)), table_cell_style),
                Paragraph(f"<font color='#dc3545'><b>{r.get('Breached', 0)}</b></font>", table_cell_style),
                Paragraph(f"<font color='{comp_color}'><b>{comp_val}%</b></font>", table_cell_style),
            ])
    else:
        rows = _tickets_to_rows(tickets)
        headers = ["Ticket ID", "Title", "Status", "SLA Rule Applied", "Resolution Deadline", "SLA Status", "Breach Min"]
        col_widths = [75, 145, 55, 95, 75, 60, 31]

        table_data = [[Paragraph(h, table_header_style) for h in headers]]
        for r in rows[:500]:
            status_val = r.get("SLA Status", "N/A")
            status_color = "#28a745" if "Within" in status_val else ("#dc3545" if "Breach" in status_val else "#6c757d")
            
            table_data.append([
                Paragraph(str(r.get("External ID", "")), table_cell_bold),
                Paragraph(str(r.get("Title", "")), table_cell_style),
                Paragraph(str(r.get("Status", "")), table_cell_style),
                Paragraph(str(r.get("SLA Rule Applied", "")), table_cell_style),
                Paragraph(str(r.get("Resolution Deadline", "")), table_cell_style),
                Paragraph(f"<font color='{status_color}'><b>{status_val}</b></font>", table_cell_style),
                Paragraph(str(r.get("Breach Duration (min)", 0)), table_cell_style),
            ])

    detail_table = Table(table_data, colWidths=col_widths, repeatRows=1, hAlign="LEFT")
    detail_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1a2980")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#e8ecf1")),
        ("PADDING", (0, 0), (-1, -1), 4),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8f9fa")]),
    ]))
    elements.append(detail_table)

    # Build document with NumberedCanvas for dynamic Page X of Y header/footer
    doc.build(elements, canvasmaker=NumberedCanvas)

    report = Report(
        report_name=filename,
        report_type=report_type,
        file_format="pdf",
        file_path=filepath,
        generated_by=generated_by_user_id,
    )
    db.session.add(report)
    db.session.commit()
    return report


def generate_report(app, report_type, file_format, generated_by_user_id=None, client_id=None):
    if file_format == "xlsx":
        return generate_excel_report(app, report_type, generated_by_user_id, client_id)
    return generate_pdf_report(app, report_type, generated_by_user_id, client_id)
