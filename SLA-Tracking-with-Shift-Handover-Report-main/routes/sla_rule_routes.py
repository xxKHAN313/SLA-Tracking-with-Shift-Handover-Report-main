"""
routes/sla_rule_routes.py
----------------------------
Full CRUD for SLA rules. Admin-only (manage_sla_rules permission).

Multi-tenancy (Gap #1): rules are created/listed per client.
New fields: priority (Gap #2), business_hours_only (Gap #3), pause_status (Gap #4).
"""

from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user

from extensions import db
from models import SLARule, SLARuleCondition, Ticket, Client
from routes.decorators import permission_required

sla_rule_bp = Blueprint("sla_rules", __name__)


@sla_rule_bp.route("/sla-rules")
@login_required
@permission_required("manage_sla_rules")
def rule_list():
    client_id = request.args.get("client_id", type=int)
    query = SLARule.query
    if client_id:
        query = query.filter_by(client_id=client_id)
    rules = query.order_by(SLARule.client_id.asc(), SLARule.priority.asc(), SLARule.id.asc()).all()
    clients = Client.query.filter_by(is_active=True).order_by(Client.name).all()
    return render_template(
        "sla_rules.html",
        rules=rules,
        clients=clients,
        selected_client_id=client_id,
    )


def _form_to_rule_fields(form):
    def safe_parse_int(val, default=0):
        try:
            return int(val)
        except (ValueError, TypeError):
            return default

    resp_sla_raw = form.get("response_sla_minutes")
    resp_sla = safe_parse_int(resp_sla_raw, None) if resp_sla_raw and str(resp_sla_raw).strip() != "" else None

    res_sla = safe_parse_int(form.get("resolution_sla_minutes"), 0)
    thresh = safe_parse_int(form.get("warning_threshold_percent"), 80)
    prio = safe_parse_int(form.get("priority"), 0)

    return dict(
        client_id=int(form.get("client_id")) if form.get("client_id") else None,
        rule_name=form.get("rule_name", "").strip()[:150],
        priority=max(0, min(prio, 10000)),
        response_sla_minutes=resp_sla,
        resolution_sla_minutes=res_sla,
        warning_threshold_percent=max(1, min(thresh, 99)),
        business_hours_only=form.get("business_hours_only") == "on",
        applies_to_status=form.get("applies_to_status", "").strip() or None,
        stop_status=form.get("stop_status", "").strip() or None,
        pause_status=form.get("pause_status", "").strip() or None,
        is_active=form.get("is_active") == "on",
        description=form.get("description", "").strip() or None,
        escalation_email=form.get("escalation_email", "").strip() or None,
    )


def _clean_format_option(val):
    if not val:
        return ""
    s = str(val).strip()
    acronyms = {"ddos": "DDoS", "dos": "DoS", "ip": "IP", "url": "URL", "dns": "DNS", "sql": "SQL", "sla": "SLA", "id": "ID"}
    
    parts = s.split(":")
    formatted_parts = []
    for part in parts:
        words = part.replace("-", " ").replace("_", " ").split()
        formatted_words = []
        for w in words:
            w_lower = w.lower()
            if w_lower in acronyms:
                formatted_words.append(acronyms[w_lower])
            else:
                formatted_words.append(w.capitalize())
        formatted_parts.append(" ".join(formatted_words))
    
    return ": ".join(formatted_parts)


def _get_field_options(classifications):
    distinct = lambda col: [row[0] for row in db.session.query(col).distinct() if row[0]]
    raw_options = {
        "severity": distinct(Ticket.severity),
        "priority": distinct(Ticket.priority),
        "criticality": distinct(Ticket.criticality),
        "status": distinct(Ticket.status)
    }
    
    # Merge classifications from IRIS into severity list
    severities = list(raw_options["severity"])
    if classifications:
        for c in classifications:
            name_exp = c.get("name_expanded")
            name_raw = c.get("name")
            if name_exp:
                severities.append(name_exp)
            if name_raw:
                severities.append(name_raw)

    def process_and_deduplicate(values):
        if not values:
            return []

        # 1. Format every raw string into a clean, capitalized option
        formatted_list = [_clean_format_option(v) for v in values if v and str(v).strip()]

        # 2. Filter out short sub-aliases when a full classification exists
        def norm_part(s):
            return s.lower().replace("_", " ").replace("-", " ").replace(":", " ").strip()

        unique_map = {}
        for item in formatted_list:
            norm_key = norm_part(item)
            if norm_key not in unique_map:
                unique_map[norm_key] = item
            else:
                existing = unique_map[norm_key]
                if len(item) > len(existing):
                    unique_map[norm_key] = item

        items = list(unique_map.values())
        final_items = []

        for item in items:
            item_norm = norm_part(item)
            item_last = norm_part(item.split(":")[-1])
            is_sub = False

            for other in items:
                if item == other:
                    continue
                other_norm = norm_part(other)
                other_last = norm_part(other.split(":")[-1])

                if (len(other_norm) > len(item_norm)) and (item_norm == other_last or item_last == other_last or item_norm in other_norm):
                    is_sub = True
                    break

            if not is_sub:
                final_items.append(item)

        return sorted(list(set(final_items)), key=lambda s: s.lower())

    return {
        "severity": process_and_deduplicate(severities),
        "priority": process_and_deduplicate(raw_options["priority"]),
        "criticality": process_and_deduplicate(raw_options["criticality"]),
        "status": process_and_deduplicate(raw_options["status"]),
    }


@sla_rule_bp.route("/sla-rules/create", methods=["GET", "POST"])
@login_required
@permission_required("manage_sla_rules")
def rule_create():
    clients = Client.query.filter_by(is_active=True).order_by(Client.name).all()
    from services.iris_api_service import fetch_classifications
    classifications = fetch_classifications()
    field_options = _get_field_options(classifications)

    if request.method == "POST":
        fields = _form_to_rule_fields(request.form)
        
        # Extract conditions from post parameters
        cond_field_names = request.form.getlist("cond_field_name[]")
        cond_field_values = request.form.getlist("cond_field_value[]")
        
        conditions_data = []
        for name, value in zip(cond_field_names, cond_field_values):
            name_clean = name.strip()
            value_clean = value.strip()
            if name_clean and value_clean:
                conditions_data.append((name_clean, value_clean))

        # Re-attach conditions as dummy objects for postback if page needs to render errors
        fields["conditions"] = [SLARuleCondition(field_name=fn, field_value=fv) for fn, fv in conditions_data]

        # Validations
        if not fields["rule_name"]:
            flash("Rule name is required.", "danger")
            return render_template("sla_rule_form.html", rule=fields, clients=clients, classifications=classifications, field_options=field_options)
        if not fields["client_id"]:
            flash("A client must be selected.", "danger")
            return render_template("sla_rule_form.html", rule=fields, clients=clients, classifications=classifications, field_options=field_options)
        if not conditions_data:
            flash("At least one matching condition is required.", "danger")
            return render_template("sla_rule_form.html", rule=fields, clients=clients, classifications=classifications, field_options=field_options)
        if fields["resolution_sla_minutes"] <= 0:
            flash("Resolution SLA minutes must be greater than 0.", "danger")
            return render_template("sla_rule_form.html", rule=fields, clients=clients, classifications=classifications, field_options=field_options)
        if fields["response_sla_minutes"] is not None and fields["response_sla_minutes"] < 0:
            flash("Response SLA minutes cannot be negative.", "danger")
            return render_template("sla_rule_form.html", rule=fields, clients=clients, classifications=classifications, field_options=field_options)
        if fields["response_sla_minutes"] is not None and fields["response_sla_minutes"] >= fields["resolution_sla_minutes"]:
            flash("Response SLA minutes must be less than Resolution SLA minutes.", "danger")
            return render_template("sla_rule_form.html", rule=fields, clients=clients, classifications=classifications, field_options=field_options)

        # Remove temporary conditions list to avoid super().__init__ trying to set it directly
        temp_conditions = fields.pop("conditions", None)

        rule = SLARule(**fields)
        rule.created_by = current_user.username if current_user.is_authenticated else None
        rule.updated_by = current_user.username if current_user.is_authenticated else None
        
        for fname, fval in conditions_data:
            rule.conditions.append(SLARuleCondition(field_name=fname, field_value=fval))

        db.session.add(rule)
        db.session.commit()

        from services.sla_calculator import recalculate_all_open_tickets
        recalculate_all_open_tickets()

        from services.audit_service import log_audit
        log_audit("create_sla_rule", "SLARule", target_id=rule.id, details=f"Created SLA rule '{rule.rule_name}'")

        flash(f"SLA rule '{rule.rule_name}' created.", "success")
        return redirect(url_for("sla_rules.rule_list"))

    return render_template("sla_rule_form.html", rule=None, clients=clients, classifications=classifications, field_options=field_options)


@sla_rule_bp.route("/sla-rules/<int:rule_id>/edit", methods=["GET", "POST"])
@login_required
@permission_required("manage_sla_rules")
def rule_edit(rule_id):
    rule = SLARule.query.get_or_404(rule_id)
    clients = Client.query.filter_by(is_active=True).order_by(Client.name).all()
    from services.iris_api_service import fetch_classifications
    classifications = fetch_classifications()
    field_options = _get_field_options(classifications)

    if request.method == "POST":
        fields = _form_to_rule_fields(request.form)

        # Extract conditions from post parameters
        cond_field_names = request.form.getlist("cond_field_name[]")
        cond_field_values = request.form.getlist("cond_field_value[]")
        
        conditions_data = []
        for name, value in zip(cond_field_names, cond_field_values):
            name_clean = name.strip()
            value_clean = value.strip()
            if name_clean and value_clean:
                conditions_data.append((name_clean, value_clean))

        # Validations
        if not fields["rule_name"]:
            flash("Rule name is required.", "danger")
            return render_template("sla_rule_form.html", rule=rule, clients=clients, classifications=classifications, field_options=field_options)
        if not fields["client_id"]:
            flash("A client must be selected.", "danger")
            return render_template("sla_rule_form.html", rule=rule, clients=clients, classifications=classifications, field_options=field_options)
        if not conditions_data:
            flash("At least one matching condition is required.", "danger")
            return render_template("sla_rule_form.html", rule=rule, clients=clients, classifications=classifications, field_options=field_options)
        if fields["resolution_sla_minutes"] <= 0:
            flash("Resolution SLA minutes must be greater than 0.", "danger")
            return render_template("sla_rule_form.html", rule=rule, clients=clients, classifications=classifications, field_options=field_options)
        if fields["response_sla_minutes"] is not None and fields["response_sla_minutes"] < 0:
            flash("Response SLA minutes cannot be negative.", "danger")
            return render_template("sla_rule_form.html", rule=rule, clients=clients, classifications=classifications, field_options=field_options)
        if fields["response_sla_minutes"] is not None and fields["response_sla_minutes"] >= fields["resolution_sla_minutes"]:
            flash("Response SLA minutes must be less than Resolution SLA minutes.", "danger")
            return render_template("sla_rule_form.html", rule=rule, clients=clients, classifications=classifications, field_options=field_options)

        for key, value in fields.items():
            setattr(rule, key, value)
        
        rule.updated_by = current_user.username if current_user.is_authenticated else None
        
        # Sync conditions
        rule.conditions.clear()
        for fname, fval in conditions_data:
            rule.conditions.append(SLARuleCondition(field_name=fname, field_value=fval))

        db.session.commit()

        from services.sla_calculator import recalculate_all_open_tickets
        recalculate_all_open_tickets()

        flash(f"SLA rule '{rule.rule_name}' updated.", "success")
        return redirect(url_for("sla_rules.rule_list"))

    return render_template("sla_rule_form.html", rule=rule, clients=clients, classifications=classifications, field_options=field_options)


@sla_rule_bp.route("/sla-rules/<int:rule_id>/delete", methods=["POST"])
@login_required
@permission_required("manage_sla_rules")
def rule_delete(rule_id):
    rule = SLARule.query.get_or_404(rule_id)

    in_use = Ticket.query.filter_by(sla_rule_id=rule.id).count()
    if in_use:
        flash(
            f"Cannot delete '{rule.rule_name}': {in_use} ticket(s) reference it. "
            "Disable it instead.",
            "warning",
        )
        return redirect(url_for("sla_rules.rule_list"))

    db.session.delete(rule)
    db.session.commit()
    flash("SLA rule deleted.", "info")
    return redirect(url_for("sla_rules.rule_list"))


@sla_rule_bp.route("/sla-rules/<int:rule_id>/toggle", methods=["POST"])
@login_required
@permission_required("manage_sla_rules")
def rule_toggle(rule_id):
    rule = SLARule.query.get_or_404(rule_id)
    rule.is_active = not rule.is_active
    db.session.commit()

    from services.sla_calculator import recalculate_all_open_tickets
    recalculate_all_open_tickets()

    flash(f"SLA rule '{rule.rule_name}' is now {'active' if rule.is_active else 'inactive'}.", "info")
    return redirect(url_for("sla_rules.rule_list"))
