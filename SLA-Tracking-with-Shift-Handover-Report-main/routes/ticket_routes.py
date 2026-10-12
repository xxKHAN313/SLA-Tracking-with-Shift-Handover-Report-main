"""
routes/ticket_routes.py
--------------------------
Ticket list, ticket detail, manual sync trigger, manual SLA recalculation.

Multi-tenancy (Gap #1): ticket list can be filtered by client_id.
"""

from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user

from extensions import db
from models import Ticket, Client
from routes.decorators import permission_required

ticket_bp = Blueprint("tickets", __name__)


def safe_int(value, default=None, min_val=None, max_val=None):
    if value is None or str(value).strip() == "":
        return default
    try:
        parsed = int(value)
        if min_val is not None and parsed < min_val:
            return default
        if max_val is not None and parsed > max_val:
            return default
        return parsed
    except (ValueError, TypeError):
        return default


@ticket_bp.route("/tickets")
@login_required
@permission_required("view_tickets")
def ticket_list():
    query = Ticket.query

    # --- Client filter (Gap #1) ---
    client_id_val = request.args.get("client_id", "").strip()
    client_id = safe_int(client_id_val, min_val=1)
    if client_id:
        query = query.filter(Ticket.client_id == client_id)

    search = request.args.get("search", "").strip()
    status = request.args.get("status", "").strip()
    sla_status = request.args.get("sla_status", "").strip()
    assigned_to = request.args.get("assigned_to", "").strip()
    severity = request.args.get("severity", "").strip()
    priority = request.args.get("priority", "").strip()
    criticality = request.args.get("criticality", "").strip()
    date_from = request.args.get("date_from", "").strip()
    date_to = request.args.get("date_to", "").strip()

    if search:
        like = f"%{search}%"
        query = query.filter(db.or_(Ticket.title.ilike(like), Ticket.external_id.ilike(like)))
    if status:
        query = query.filter(Ticket.status == status)
    if sla_status:
        query = query.filter(Ticket.sla_status == sla_status)
    if assigned_to:
        query = query.filter(Ticket.assigned_to == assigned_to)
    if severity:
        query = query.filter(Ticket.severity == severity)
    if priority:
        query = query.filter(Ticket.priority == priority)
    if criticality:
        query = query.filter(Ticket.criticality == criticality)
    if date_from:
        query = query.filter(Ticket.created_at_source >= date_from)
    if date_to:
        query = query.filter(Ticket.created_at_source <= date_to)

    page = request.args.get("page", 1, type=int)
    pagination = query.order_by(Ticket.created_at_source.desc()).paginate(
        page=page, per_page=25, error_out=False
    )

    # Distinct values for filter dropdowns
    distinct = lambda col: [row[0] for row in db.session.query(col).distinct() if row[0]]

    filter_options = {
        "statuses": distinct(Ticket.status),
        "sla_statuses": distinct(Ticket.sla_status),
        "assignees": distinct(Ticket.assigned_to),
        "severities": distinct(Ticket.severity),
        "priorities": distinct(Ticket.priority),
        "criticalities": distinct(Ticket.criticality),
    }

    clients = Client.query.filter_by(is_active=True).order_by(Client.name).all()

    return render_template(
        "tickets.html",
        pagination=pagination,
        tickets=pagination.items,
        filter_options=filter_options,
        current_filters=request.args,
        clients=clients,
        selected_client_id=client_id,
    )


@ticket_bp.route("/tickets/<int:ticket_id>")
@login_required
@permission_required("view_tickets")
def ticket_detail(ticket_id):
    ticket = Ticket.query.get_or_404(ticket_id)
    return render_template("ticket_detail.html", ticket=ticket)


@ticket_bp.route("/tickets/sync", methods=["POST"])
@login_required
@permission_required("manage_iris_settings")
def trigger_sync():
    from services.sync_service import sync_cases_from_iris

    try:
        result = sync_cases_from_iris()
        flash(
            f"Sync complete: {result['fetched']} fetched, "
            f"{result['created']} created, {result['updated']} updated, "
            f"{result['skipped']} skipped, {result['soft_deleted']} soft-deleted.",
            "success",
        )
    except Exception as exc:  # noqa: BLE001
        flash(f"Sync failed: {exc}", "danger")

    return redirect(url_for("tickets.ticket_list"))


@ticket_bp.route("/tickets/recalculate-sla", methods=["POST"])
@login_required
@permission_required("view_tickets")
def trigger_recalculate():
    from services.sla_calculator import recalculate_all_open_tickets

    result = recalculate_all_open_tickets()
    flash(f"Recalculated SLA for {result['recalculated_count']} ticket(s).", "success")
    return redirect(url_for("tickets.ticket_list"))


@ticket_bp.route("/tickets/<int:ticket_id>/send-mail", methods=["POST"])
@login_required
@permission_required("view_tickets")
def send_mail_directly(ticket_id):
    ticket = Ticket.query.get_or_404(ticket_id)
    from services.email_service import send_ticket_email_manually
    from flask import current_app

    success, message = send_ticket_email_manually(current_app, ticket)
    if success:
        flash(message, "success")
    else:
        flash(message, "danger")

    return redirect(url_for("tickets.ticket_detail", ticket_id=ticket.id))


VALID_BREACH_REASONS = [
    "Vendor Delay",
    "Customer Unresponsive",
    "Third-Party Outage",
    "Staff Shortage",
    "Technical Complexity",
    "Other",
]


@ticket_bp.route("/tickets/<int:ticket_id>/update-breach-reason", methods=["POST"])
@login_required
@permission_required("view_tickets")
def update_breach_reason(ticket_id):
    ticket = Ticket.query.get_or_404(ticket_id)
    reason = request.form.get("breach_reason", "").strip()
    custom_reason = request.form.get("custom_breach_reason", "").strip()
    notes = request.form.get("breach_notes", "").strip()

    # If custom reason is typed, prioritize it if "Custom" or "Other" or blank is selected
    if custom_reason and (reason in ("Custom", "Other", "") or not reason):
        final_reason = custom_reason[:100]
    elif reason:
        final_reason = reason[:100]
    else:
        final_reason = None

    ticket.breach_reason = final_reason
    ticket.breach_notes = notes or None
    db.session.commit()

    from services.audit_service import log_audit
    log_audit("update_breach_reason", "Ticket", target_id=ticket.id, details=f"Set breach reason: '{final_reason}'")

    flash("SLA breach analysis updated.", "success")
    return redirect(url_for("tickets.ticket_detail", ticket_id=ticket.id))
