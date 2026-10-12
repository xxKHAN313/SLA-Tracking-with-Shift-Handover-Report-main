"""
routes/settings_routes.py
----------------------------
Admin settings page: IRIS base URL, sync interval, timezone, email toggle,
plus a "Test Connection" action and field-mapping management.

Multi-tenancy (Gap #1): includes client CRUD (create/edit/deactivate).
"""

from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app
from flask_login import login_required

from extensions import db
from models import Setting, FieldMapping, Client, Holiday, AuditLog
from routes.decorators import permission_required
from services.audit_service import log_audit

settings_bp = Blueprint("settings", __name__)

SETTINGS_KEYS = [
    ("iris_base_url", "DFIR-IRIS Base URL (overrides .env if set)"),
    ("sync_interval_minutes", "Sync Interval (minutes)"),
    ("default_timezone", "Default Timezone"),
    ("email_notifications_enabled", "Email Notifications Enabled (true/false)"),
]


@settings_bp.route("/settings", methods=["GET", "POST"])
@login_required
@permission_required("manage_iris_settings")
def settings_page():
    if request.method == "POST":
        for key, _label in SETTINGS_KEYS:
            value = request.form.get(key, "")
            Setting.set(key, value)
        
        # Save business hours settings
        bh_start = request.form.get("business_hours_start", "09:00").strip()
        bh_end = request.form.get("business_hours_end", "17:00").strip()
        bh_days_list = request.form.getlist("business_hours_days")
        bh_days = ",".join(bh_days_list) if bh_days_list else "0,1,2,3,4"
        
        Setting.set("business_hours_start", bh_start)
        Setting.set("business_hours_end", bh_end)
        Setting.set("business_hours_days", bh_days)
        
        log_audit("update_settings", "Setting", details="Updated application & business hours settings")
        flash("Settings saved. Note: sync interval changes take effect after restart.", "success")
        return redirect(url_for("settings.settings_page"))

    current_values = {key: Setting.get(key, "") for key, _label in SETTINGS_KEYS}
    
    # Retrieve current business hours settings
    bh_start = Setting.get("business_hours_start", "09:00")
    bh_end = Setting.get("business_hours_end", "17:00")
    bh_days_str = Setting.get("business_hours_days", "0,1,2,3,4")
    bh_days = [int(x) for x in bh_days_str.split(",") if x.strip() != ""]

    field_mappings = FieldMapping.query.filter_by(source_system="dfir_iris", client_id=None).all()
    clients = Client.query.order_by(Client.name).all()
    holidays = Holiday.query.order_by(Holiday.holiday_date.desc()).all()

    return render_template(
        "settings.html",
        settings_keys=SETTINGS_KEYS,
        current_values=current_values,
        field_mappings=field_mappings,
        iris_configured=bool(current_app.config.get("IRIS_API_KEY")),
        clients=clients,
        bh_start=bh_start,
        bh_end=bh_end,
        bh_days=bh_days,
        holidays=holidays,
    )


@settings_bp.route("/settings/test-iris-connection", methods=["POST"])
@login_required
@permission_required("manage_iris_settings")
def test_iris_connection():
    from services.iris_api_service import test_connection

    success, message = test_connection()
    flash(message, "success" if success else "danger")
    return redirect(url_for("settings.settings_page"))


@settings_bp.route("/settings/field-mappings/save", methods=["POST"])
@login_required
@permission_required("manage_iris_settings")
def save_field_mapping():
    local_field = request.form.get("local_field", "").strip()
    source_field = request.form.get("source_field", "").strip()

    if not local_field or not source_field:
        flash("Local field and source field are both required.", "danger")
        return redirect(url_for("settings.settings_page"))

    mapping = FieldMapping.query.filter_by(
        source_system="dfir_iris", client_id=None, local_field=local_field
    ).first()

    if mapping:
        mapping.source_field = source_field
    else:
        mapping = FieldMapping(
            source_system="dfir_iris", client_id=None,
            local_field=local_field, source_field=source_field,
        )
        db.session.add(mapping)

    db.session.commit()
    flash(f"Field mapping saved: {local_field} <- {source_field}", "success")
    return redirect(url_for("settings.settings_page"))


# --- Client CRUD (Gap #1) ---

@settings_bp.route("/settings/clients")
@login_required
@permission_required("manage_iris_settings")
def client_list():
    clients = Client.query.order_by(Client.name).all()
    return render_template("clients.html", clients=clients)


@settings_bp.route("/settings/clients/create", methods=["POST"])
@login_required
@permission_required("manage_iris_settings")
def client_create():
    flash("Manual client creation is disabled. Clients are automatically synced from source tickets. Use Activate/Deactivate to manage client status.", "warning")
    return redirect(url_for("settings.client_list"))


@settings_bp.route("/settings/clients/<int:client_id>/edit", methods=["POST"])
@login_required
@permission_required("manage_iris_settings")
def client_edit(client_id):
    client = Client.query.get_or_404(client_id)
    client.name = request.form.get("name", client.name).strip()
    client.iris_customer_id = request.form.get("iris_customer_id", "").strip() or None
    client.timezone = request.form.get("timezone", "Asia/Karachi").strip()
    client.city = request.form.get("city", client.city or "Islamabad").strip()
    
    client.business_hours_start = request.form.get("business_hours_start", "").strip() or None
    client.business_hours_end = request.form.get("business_hours_end", "").strip() or None
    days_list = request.form.getlist("business_hours_days")
    client.business_hours_days = ",".join(days_list) if days_list else None

    db.session.commit()
    flash(f"Client '{client.name}' updated.", "success")
    return redirect(url_for("settings.client_list"))



@settings_bp.route("/settings/clients/<int:client_id>/toggle", methods=["POST"])
@login_required
@permission_required("manage_iris_settings")
def client_toggle(client_id):
    client = Client.query.get_or_404(client_id)
    client.is_active = not client.is_active
    db.session.commit()
    flash(
        f"Client '{client.name}' is now {'active' if client.is_active else 'inactive'}.",
        "info",
    )
    return redirect(url_for("settings.client_list"))


@settings_bp.route("/settings/clients/<int:client_id>/delete", methods=["POST"])
@login_required
@permission_required("manage_iris_settings")
def client_delete(client_id):
    client = Client.query.get_or_404(client_id)
    c_name = client.name
    flash(f"Client deletion is disabled to preserve historical SLA tracking and ticket records for '{c_name}'. Please use Activate/Deactivate instead.", "warning")
    return redirect(url_for("settings.client_list"))


@settings_bp.route("/settings/send-test-email", methods=["POST"])
@login_required
@permission_required("manage_iris_settings")
def send_test_email():
    from services.email_service import _send_email
    from flask import current_app
    from models import User
    
    recipients = [u.email for u in User.query.all() if u.email]
    if not recipients:
        flash("No users with email addresses found to send test to.", "danger")
        return redirect(url_for("settings.settings_page"))
        
    subject = "SLA Tracker - Test Email Notification"
    body = """
    <h3>SLA Tracker Test Connection</h3>
    <p>This is a test email sent from the Automated SLA Tracker to verify SMTP configuration.</p>
    """
    success = _send_email(current_app, subject, body, recipients)
    if success:
        flash(f"Test email sent successfully to: {', '.join(recipients)}", "success")
    else:
        flash("Failed to send test email. Check SMTP settings and logs.", "danger")
        
    return redirect(url_for("settings.settings_page"))


# --- Holiday Calendar CRUD ---

@settings_bp.route("/settings/holidays", methods=["GET"])
@login_required
@permission_required("manage_iris_settings")
def holiday_list_page():
    return redirect(url_for("settings.settings_page"))


@settings_bp.route("/settings/holidays/create", methods=["POST"])
@login_required
@permission_required("manage_iris_settings")
def holiday_create():
    name = request.form.get("name", "").strip()
    date_str = request.form.get("holiday_date", "").strip()
    client_id_raw = request.form.get("client_id", "").strip()
    client_id = int(client_id_raw) if client_id_raw else None

    if not name or not date_str:
        flash("Holiday name and date are required.", "danger")
        return redirect(url_for("settings.settings_page"))

    try:
        h_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        flash("Invalid date format. Use YYYY-MM-DD.", "danger")
        return redirect(url_for("settings.settings_page"))

    holiday = Holiday(name=name, holiday_date=h_date, client_id=client_id)
    db.session.add(holiday)
    db.session.commit()

    log_audit("create_holiday", "Holiday", target_id=holiday.id, details=f"Created holiday '{name}' on {h_date}")
    flash(f"Holiday '{name}' added for {h_date}.", "success")
    return redirect(url_for("settings.settings_page"))


@settings_bp.route("/settings/holidays/<int:holiday_id>/delete", methods=["POST"])
@login_required
@permission_required("manage_iris_settings")
def holiday_delete(holiday_id):
    holiday = Holiday.query.get_or_404(holiday_id)
    h_name = holiday.name
    db.session.delete(holiday)
    db.session.commit()

    log_audit("delete_holiday", "Holiday", target_id=holiday_id, details=f"Deleted holiday '{h_name}'")
    flash(f"Holiday '{h_name}' deleted.", "info")
    return redirect(url_for("settings.settings_page"))


# --- Audit Logs View ---

@settings_bp.route("/settings/audit-logs")
@login_required
@permission_required("manage_iris_settings")
def audit_log_list():
    page = request.args.get("page", 1, type=int)
    page = max(1, min(page, 1000))
    pagination = AuditLog.query.order_by(AuditLog.created_at.desc()).paginate(
        page=page, per_page=50, error_out=False
    )
    return render_template("audit_logs.html", pagination=pagination, logs=pagination.items)
