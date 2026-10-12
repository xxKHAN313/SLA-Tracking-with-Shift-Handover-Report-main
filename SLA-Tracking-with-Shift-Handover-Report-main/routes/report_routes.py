import os
from flask import (
    Blueprint, render_template, request, redirect, url_for, flash,
    send_file, current_app, abort,
)
from flask_login import login_required, current_user

from models import Report, Client
from models.report import REPORT_TYPES
from routes.decorators import permission_required

report_bp = Blueprint("reports", __name__)


def is_safe_path(base_directory: str, target_path: str) -> bool:
    """Ensure target_path resolves strictly within base_directory."""
    abs_base = os.path.abspath(base_directory)
    abs_target = os.path.abspath(target_path)
    return os.path.commonpath([abs_base]) == os.path.commonpath([abs_base, abs_target])


@report_bp.route("/reports")
@login_required
@permission_required("view_reports")
def report_list():
    reports = Report.query.order_by(Report.generated_at.desc()).all()
    clients = Client.query.filter_by(is_active=True).order_by(Client.name).all()
    selected_client_id = request.args.get("selected_client_id", type=int)
    selected_report_type = request.args.get("selected_report_type", type=str)
    selected_format = request.args.get("selected_format", type=str, default="pdf")
    new_report_id = request.args.get("new_report_id", type=int)
    return render_template(
        "reports.html",
        reports=reports,
        report_types=REPORT_TYPES,
        clients=clients,
        selected_client_id=selected_client_id,
        selected_report_type=selected_report_type,
        selected_format=selected_format,
        new_report_id=new_report_id,
    )


@report_bp.route("/reports/generate", methods=["POST"])
@login_required
@permission_required("generate_reports")
def generate():
    from services.report_generator import generate_report

    report_type = request.form.get("report_type")
    file_format = request.form.get("file_format", "pdf")
    client_id_val = request.form.get("client_id")
    client_id = int(client_id_val) if client_id_val and client_id_val.isdigit() else None

    if report_type not in REPORT_TYPES:
        flash("Unknown report type.", "danger")
        return redirect(url_for("reports.report_list"))

    try:
        report = generate_report(current_app, report_type, file_format, current_user.id, client_id=client_id)
        
        client_label = "All Clients"
        if client_id:
            c = Client.query.get(client_id)
            if c:
                client_label = f"Client '{c.name}'"
        
        flash(f"Report '{report_type}' ({file_format.upper()}) for {client_label} generated successfully!", "success")
        return redirect(url_for(
            "reports.report_list",
            selected_client_id=client_id,
            selected_report_type=report_type,
            selected_format=file_format,
            new_report_id=report.id,
        ))
    except Exception as exc:  # noqa: BLE001
        flash(f"Report generation failed: {exc}", "danger")
        return redirect(url_for("reports.report_list", selected_client_id=client_id))


@report_bp.route("/reports/download/<int:report_id>")
@login_required
@permission_required("view_reports")
def download(report_id):
    report = Report.query.get_or_404(report_id)
    reports_folder = current_app.config["REPORTS_FOLDER"]

    if not is_safe_path(reports_folder, report.file_path):
        current_app.logger.error(f"Unauthorized path access attempt: {report.file_path}")
        abort(403)

    if not os.path.exists(report.file_path):
        abort(404)

    return send_file(report.file_path, as_attachment=True, download_name=report.report_name)


@report_bp.route("/reports/delete/<int:report_id>", methods=["POST"])
@login_required
@permission_required("generate_reports")
def delete_report(report_id):
    report = Report.query.get_or_404(report_id)
    report_name = report.report_name

    if report.file_path and os.path.exists(report.file_path):
        try:
            os.remove(report.file_path)
        except Exception:
            pass

    db.session.delete(report)
    db.session.commit()

    from services.audit_service import log_audit
    log_audit("delete_report", "Report", target_id=report_id, details=f"Deleted report '{report_name}'")

    flash(f"Report '{report_name}' deleted.", "info")
    return redirect(url_for("reports.report_list"))

