"""
routes/dashboard_routes.py
-----------------------------
Main dashboard: aggregate SLA metrics + data for Chart.js visualizations.

Multi-tenancy (Gap #1): metrics can be filtered by client_id. If no client
filter is provided, all clients' data is shown (admin view).
"""

from collections import Counter, defaultdict
from datetime import datetime, timezone

from flask import Blueprint, render_template, jsonify, request
from flask_login import login_required

from extensions import db
from models import Ticket, Client
from models.ticket import (
    SLA_WITHIN, SLA_NEAR_BREACH, SLA_BREACHED,
    SLA_CLOSED_WITHIN, SLA_CLOSED_AFTER_BREACH, SLA_NO_RULE,
)
from routes.decorators import permission_required

dashboard_bp = Blueprint("dashboard", __name__)


def _build_dashboard_metrics(client_id=None, month=None):
    query = Ticket.query
    if client_id:
        query = query.filter_by(client_id=client_id)

    tickets = query.all()

    # --- Month filtering ---
    if month:
        tickets = [
            t for t in tickets
            if t.created_at_source and t.created_at_source.strftime("%Y-%m") == month
        ]

    total = len(tickets)
    open_tickets = sum(1 for t in tickets if t.is_open())
    closed_tickets = total - open_tickets

    status_counts = Counter(t.sla_status or SLA_NO_RULE for t in tickets)
    within_sla = status_counts.get(SLA_WITHIN, 0) + status_counts.get(SLA_CLOSED_WITHIN, 0)
    near_breach = status_counts.get(SLA_NEAR_BREACH, 0)
    breached = status_counts.get(SLA_BREACHED, 0) + status_counts.get(SLA_CLOSED_AFTER_BREACH, 0)
    no_rule = status_counts.get(SLA_NO_RULE, 0)

    compliance_pct = round((within_sla / total) * 100, 1) if total else 0.0

    # Average resolution time (minutes) for closed tickets that had a rule
    resolved = [t for t in tickets if t.closed_at_source and t.created_at_source]
    if resolved:
        total_minutes = sum(
            (t.closed_at_source - t.created_at_source).total_seconds() / 60 for t in resolved
        )
        avg_resolution_minutes = round(total_minutes / len(resolved), 1)
    else:
        avg_resolution_minutes = 0

    # --- Analyst-wise performance ---
    analyst_stats = defaultdict(lambda: {"total": 0, "breached": 0})
    for t in tickets:
        analyst = t.assigned_to or "Unassigned"
        analyst_stats[analyst]["total"] += 1
        if t.sla_status in (SLA_BREACHED, SLA_CLOSED_AFTER_BREACH):
            analyst_stats[analyst]["breached"] += 1

    # --- Severity / priority / criticality distribution ---
    taxonomy_counter = Counter()
    for t in tickets:
        label = t.severity or t.priority or t.criticality
        if label:
            taxonomy_counter[label] += 1

    # --- Monthly SLA trend (based on created_at_source month) ---
    monthly_trend = defaultdict(lambda: {"within": 0, "breached": 0})
    # Use ALL tickets (unfiltered by month) for the trend chart
    all_tickets = Ticket.query.all()
    if client_id:
        all_tickets = [t for t in all_tickets if t.client_id == client_id]
    for t in all_tickets:
        if not t.created_at_source:
            continue
        key = t.created_at_source.strftime("%Y-%m")
        if t.sla_status in (SLA_BREACHED, SLA_CLOSED_AFTER_BREACH):
            monthly_trend[key]["breached"] += 1
        elif t.sla_status in (SLA_WITHIN, SLA_CLOSED_WITHIN):
            monthly_trend[key]["within"] += 1

    sorted_months = sorted(monthly_trend.keys())

    # --- Top Issues: group by severity/category, count breaches, trends ---
    top_issues_counter = Counter()
    top_issues_breach = Counter()
    for t in tickets:
        label = t.severity or t.priority or t.criticality or "Unclassified"
        top_issues_counter[label] += 1
        if t.sla_status in (SLA_BREACHED, SLA_CLOSED_AFTER_BREACH):
            top_issues_breach[label] += 1

    top_issues = []
    for label, count in top_issues_counter.most_common(6):
        breach_count = top_issues_breach.get(label, 0)
        compliance = round(((count - breach_count) / count) * 100, 1) if count else 0
        top_issues.append({
            "label": label,
            "count": count,
            "breached": breach_count,
            "compliance": compliance,
        })

    # --- Quarterly received cases (for area chart) ---
    quarterly = defaultdict(int)
    for t in all_tickets:
        if not t.created_at_source:
            continue
        if client_id and t.client_id != client_id:
            continue
        m = t.created_at_source.month
        if m <= 3:
            q = "Q1"
        elif m <= 6:
            q = "Q2"
        elif m <= 9:
            q = "Q3"
        else:
            q = "Q4"
        y = t.created_at_source.strftime("%Y")
        quarterly[f"{y} {q}"] += 1

    sorted_quarters = sorted(quarterly.keys())

    # --- Client-wise ticket counts ---
    client_counts = Counter()
    for t in tickets:
        if t.client_id:
            client_obj = Client.query.get(t.client_id)
            if client_obj:
                client_counts[client_obj.name] += 1
        else:
            client_counts["Unassigned"] += 1

    # --- Available months for month tab selector ---
    available_months = sorted(set(
        t.created_at_source.strftime("%Y-%m")
        for t in all_tickets
        if t.created_at_source
    ), reverse=True)

    # --- Year-to-date SLA compliance ---
    now = datetime.now(timezone.utc)
    ytd_tickets = [
        t for t in all_tickets
        if t.created_at_source and t.created_at_source.year == now.year
    ]
    if client_id:
        ytd_tickets = [t for t in ytd_tickets if t.client_id == client_id]
    ytd_total = len(ytd_tickets)
    ytd_within = sum(
        1 for t in ytd_tickets
        if t.sla_status in (SLA_WITHIN, SLA_CLOSED_WITHIN)
    )
    ytd_compliance = round((ytd_within / ytd_total) * 100, 1) if ytd_total else 0.0

    # --- Regional stats (Pakistan map) ---
    pakistan_cities = {
        "Karachi": {"lat": 24.8607, "lng": 67.0011},
        "Lahore": {"lat": 31.5204, "lng": 74.3587},
        "Islamabad": {"lat": 33.6844, "lng": 73.0479},
        "Rawalpindi": {"lat": 33.5651, "lng": 73.0169},
        "Peshawar": {"lat": 34.0151, "lng": 71.5249},
        "Quetta": {"lat": 30.1798, "lng": 66.9750},
        "Faisalabad": {"lat": 31.4504, "lng": 73.1350},
        "Multan": {"lat": 30.1575, "lng": 71.5249},
    }

    # Count active/all clients per city
    all_clients = Client.query.all()
    city_client_count = defaultdict(int)
    for c in all_clients:
        c_city = c.city or "Islamabad"
        city_client_count[c_city] += 1

    region_data = defaultdict(lambda: {"total": 0})
    for t in tickets:
        city = "Islamabad"
        if t.client_id:
            c = Client.query.get(t.client_id)
            if c and c.city:
                city = c.city
        region_data[city]["total"] += 1

    # Include cities that have clients even if no tickets exist yet
    for city_name, c_cnt in city_client_count.items():
        if city_name not in region_data:
            region_data[city_name]["total"] = 0

    region_stats = {}
    for city_name, stats in region_data.items():
        total_c = stats["total"]
        client_cnt = city_client_count.get(city_name, 0)
        coords = pakistan_cities.get(city_name, pakistan_cities["Islamabad"])
        region_stats[city_name] = {
            "total": total_c,
            "client_count": client_cnt,
            "lat": coords["lat"],
            "lng": coords["lng"],
        }

    # --- Breach Root Cause Distribution ---
    breach_reason_counter = Counter()
    for t in tickets:
        if t.breach_reason:
            breach_reason_counter[t.breach_reason] += 1
        elif t.sla_status in (SLA_BREACHED, SLA_CLOSED_AFTER_BREACH):
            breach_reason_counter["Uncategorized"] += 1

    return {
        "total_tickets": total,
        "open_tickets": open_tickets,
        "closed_tickets": closed_tickets,
        "within_sla": within_sla,
        "near_breach": near_breach,
        "breached": breached,
        "no_matching_rule": no_rule,
        "sla_compliance_percent": compliance_pct,
        "ytd_compliance_percent": ytd_compliance,
        "avg_resolution_minutes": avg_resolution_minutes,
        "analyst_stats": dict(analyst_stats),
        "sla_status_distribution": {
            "Within SLA": within_sla,
            "Near Breach": near_breach,
            "Breached": breached,
            "No Matching Rule": no_rule,
        },
        "taxonomy_distribution": dict(taxonomy_counter),
        "breach_reason_distribution": dict(breach_reason_counter),
        "monthly_trend": {
            "labels": sorted_months,
            "within": [monthly_trend[m]["within"] for m in sorted_months],
            "breached": [monthly_trend[m]["breached"] for m in sorted_months],
        },
        "top_issues": top_issues,
        "quarterly_received": {
            "labels": sorted_quarters,
            "values": [quarterly[q] for q in sorted_quarters],
        },
        "client_counts": dict(client_counts),
        "available_months": available_months,
        "region_stats": region_stats,
    }


def _get_recent_activity(limit=10):
    """Retrieve recent system activity timeline from tickets, sync logs, and audit logs."""
    from models import SyncLog, AuditLog
    activities = []

    # 1. Recent Tickets
    recent_tickets = Ticket.query.order_by(Ticket.updated_at.desc()).limit(limit).all()
    for t in recent_tickets:
        if t.sla_status in (SLA_BREACHED, SLA_CLOSED_AFTER_BREACH):
            icon = "bi-exclamation-octagon-fill text-danger"
            desc = f"Ticket #{t.external_id} breached SLA target"
        elif t.sla_status in (SLA_WITHIN, SLA_CLOSED_WITHIN):
            icon = "bi-check-circle-fill text-success"
            desc = f"Ticket #{t.external_id} resolved within SLA"
        else:
            icon = "bi-ticket-detailed-fill text-primary"
            desc = f"Ticket #{t.external_id} updated ({t.status or 'Open'})"

        activities.append({
            "timestamp": t.updated_at or t.created_at,
            "title": desc,
            "category": "Ticket",
            "icon": icon,
            "link": f"/tickets/{t.id}"
        })

    # 2. Recent Sync Runs
    recent_syncs = SyncLog.query.order_by(SyncLog.sync_started_at.desc()).limit(5).all()
    for s in recent_syncs:
        icon = "bi-arrow-repeat text-info" if s.status == "SUCCESS" else "bi-x-circle-fill text-warning"
        desc = f"IRIS Sync {s.status.lower()}: {s.records_created} created, {s.records_updated} updated"
        activities.append({
            "timestamp": s.sync_started_at,
            "title": desc,
            "category": "Sync",
            "icon": icon,
            "link": "/tickets"
        })

    # 3. Recent Audit Logs
    recent_audits = AuditLog.query.order_by(AuditLog.created_at.desc()).limit(5).all()
    for a in recent_audits:
        activities.append({
            "timestamp": a.created_at,
            "title": f"{a.username} performed {a.action} on {a.target_type}",
            "category": "Audit",
            "icon": "bi-shield-lock-fill text-secondary",
            "link": "/settings/audit-logs"
        })

    # Sort descending by timestamp
    activities.sort(key=lambda x: x["timestamp"] or datetime.min, reverse=True)
    return activities[:limit]


def _build_heatmap_data(client_id=None):
    """
    Build 7x24 matrix for ticket activity & breach density heatmap.
    Days: 0=Mon, 1=Tue, 2=Wed, 3=Thu, 4=Fri, 5=Sat, 6=Sun
    Hours: 00 to 23
    """
    query = Ticket.query
    if client_id:
        query = query.filter_by(client_id=client_id)

    tickets = query.all()

    days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    matrix = {day_idx: {hour: {"count": 0, "breached": 0} for hour in range(24)} for day_idx in range(7)}

    for t in tickets:
        if not t.created_at_source:
            continue
        day_idx = t.created_at_source.weekday()
        hour = t.created_at_source.hour
        matrix[day_idx][hour]["count"] += 1
        if t.sla_status in (SLA_BREACHED, SLA_CLOSED_AFTER_BREACH):
            matrix[day_idx][hour]["breached"] += 1

    return {"days": days, "matrix": matrix}


@dashboard_bp.route("/dashboard")
@login_required
@permission_required("view_dashboard")
def index():
    client_id = request.args.get("client_id", type=int)
    month = request.args.get("month", type=str)
    clients = Client.query.filter_by(is_active=True).order_by(Client.name).all()
    metrics = _build_dashboard_metrics(client_id=client_id, month=month)
    recent_activity = _get_recent_activity(limit=10)
    heatmap_data = _build_heatmap_data(client_id=client_id)
    return render_template(
        "dashboard.html",
        metrics=metrics,
        clients=clients,
        selected_client_id=client_id,
        selected_month=month,
        recent_activity=recent_activity,
        heatmap_data=heatmap_data,
    )


@dashboard_bp.route("/clients/breakdown")
@login_required
@permission_required("view_dashboard")
def client_breakdown():
    search = request.args.get("search", "").strip()
    sort_by = request.args.get("sort", "name").strip()
    page = request.args.get("page", 1, type=int)
    page = max(1, min(page, 1000))

    query = Client.query.filter_by(is_active=True)
    if search:
        query = query.filter(Client.name.ilike(f"%{search}%"))

    all_clients = query.all()

    client_list = []
    for c in all_clients:
        c_tickets = Ticket.query.filter_by(client_id=c.id).all()
        c_total = len(c_tickets)
        c_open = sum(1 for t in c_tickets if t.is_open())
        c_closed = c_total - c_open
        c_within = sum(1 for t in c_tickets if t.sla_status in (SLA_WITHIN, SLA_CLOSED_WITHIN))
        c_breached = sum(1 for t in c_tickets if t.sla_status in (SLA_BREACHED, SLA_CLOSED_AFTER_BREACH))
        c_compliance = round((c_within / c_total) * 100, 1) if c_total else 0.0

        client_list.append({
            "client": c,
            "total": c_total,
            "open": c_open,
            "closed": c_closed,
            "within": c_within,
            "breached": c_breached,
            "compliance": c_compliance,
        })

    # Sorting
    if sort_by == "compliance":
        client_list.sort(key=lambda x: x["compliance"], reverse=True)
    elif sort_by == "breached":
        client_list.sort(key=lambda x: x["breached"], reverse=True)
    elif sort_by == "total":
        client_list.sort(key=lambda x: x["total"], reverse=True)
    else:
        client_list.sort(key=lambda x: x["client"].name.lower())

    per_page = 20
    total_items = len(client_list)
    total_pages = max(1, (total_items + per_page - 1) // per_page)
    start_idx = (page - 1) * per_page
    paginated_items = client_list[start_idx:start_idx + per_page]

    return render_template(
        "client_breakdown.html",
        clients=paginated_items,
        page=page,
        total_pages=total_pages,
        total_clients=total_items,
        current_search=search,
        current_sort=sort_by,
    )


@dashboard_bp.route("/dashboard/metrics.json")
@login_required
@permission_required("view_dashboard")
def metrics_json():
    """Used by the dashboard template to refresh Chart.js data via fetch()."""
    client_id = request.args.get("client_id", type=int)
    month = request.args.get("month", type=str)
    return jsonify(_build_dashboard_metrics(client_id=client_id, month=month))
