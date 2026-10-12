"""
services/sla_calculator.py
----------------------------
The generic SLA rule engine.

IMPORTANT: this module never hardcodes severity/priority/criticality values.
It only ever does: ``ticket_value = getattr(ticket, rule.field_name)`` and
compares it against ``rule.field_value``. This means the exact same code path
handles Critical/High/Medium/Low, P1-P4, Sev1-Sev4, or any other taxonomy an
organization defines purely through SLARule rows.

TIMEZONE POLICY (Gap #5): All internal timestamps are stored and compared in
UTC. Display-layer conversion uses ``Client.timezone``. The ``_as_aware()``
helper forces naive datetimes to UTC for safe comparisons.

MULTI-TENANCY (Gap #1): ``find_matching_sla_rule()`` filters by the ticket's
``client_id`` before evaluating rules, so rules from different clients never
collide.

RULE PRIORITY (Gap #2): Rules are ordered by ``SLARule.priority ASC`` then
``SLARule.id ASC``. Lower priority number = evaluated first; first match wins.

BUSINESS HOURS (Gap #3): If ``sla_rule.business_hours_only`` is True, deadlines
are computed via ``add_business_minutes()`` instead of flat wall-clock
``timedelta``.

PAUSE/RESUME (Gap #4): Deadline-shift semantics. When a ticket enters a pause
status, ``paused_at`` is set. When it leaves, the elapsed pause time is added
to ``total_paused_minutes`` and the deadline is shifted forward.
"""

from datetime import datetime, timedelta, timezone

from extensions import db
from models.ticket import (
    Ticket, SLA_WITHIN, SLA_NEAR_BREACH, SLA_BREACHED,
    SLA_CLOSED_WITHIN, SLA_CLOSED_AFTER_BREACH, SLA_NO_RULE,
)
from models.sla_rule import SLARule

# Fields a rule is allowed to match against. This is intentionally just a
# safety allow-list for getattr() - it is NOT a hardcoded severity taxonomy.
# Admins can still create a rule with field_name="impact" etc. as long as
# that attribute exists on the Ticket model (or is added to it later).
MATCHABLE_TICKET_FIELDS = {"severity", "priority", "criticality", "status"}


def _now():
    return datetime.now(timezone.utc)


def _as_aware(dt):
    """Make a naive datetime timezone-aware (assume UTC) for safe comparison.

    TIMEZONE POLICY (Gap #5): any naive datetime is treated as UTC. This is the
    canonical conversion point — no other module should silently assume a
    different timezone for naive datetimes.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def find_matching_sla_rule(ticket):
    """
    Find the best matching active SLA rule for this ticket.

    Multi-tenancy (Gap #1): only rules belonging to the ticket's client_id
    are considered.

    Priority ordering (Gap #2): rules are evaluated in ``priority ASC, id ASC``
    order. The first match wins. Admins control evaluation order by setting
    the ``priority`` field on each rule.

    This is the generic matching logic:
        A rule matches if ALL of its conditions match.
    """
    query = SLARule.query.filter_by(is_active=True)

    # Gap #1: scope to client
    if ticket.client_id is not None:
        query = query.filter_by(client_id=ticket.client_id)

    # Gap #2: explicit priority ordering (lower number = higher priority)
    active_rules = query.order_by(
        SLARule.priority.asc(), SLARule.id.asc()
    ).all()

    for rule in active_rules:
        # A rule must have at least one condition to match
        if not rule.conditions:
            continue

        match = True
        for cond in rule.conditions:
            if cond.field_name not in MATCHABLE_TICKET_FIELDS:
                match = False
                break

            ticket_value = getattr(ticket, cond.field_name, None)
            if ticket_value is None:
                match = False
                break

            # Case-insensitive, whitespace-tolerant, and punctuation-resilient comparison.
            # Supports exact match, contains match, and normalized match (handling hyphens,
            # colons, underscores, and spacing like "intrusion:botnet-member" <-> "Intrusion: Botnet Member").
            tv_raw = str(ticket_value).strip().lower()
            cv_raw = str(cond.field_value).strip().lower()

            def _norm_str(s):
                return " ".join(str(s).lower().replace("-", " ").replace("_", " ").replace(":", " ").split())

            tv_norm = _norm_str(ticket_value)
            cv_norm = _norm_str(cond.field_value)

            is_match = (
                tv_raw == cv_raw or
                cv_raw in tv_raw or
                tv_raw in cv_raw or
                tv_norm == cv_norm or
                (cv_norm and cv_norm in tv_norm) or
                (tv_norm and tv_norm in cv_norm)
            )
            if not is_match:
                match = False
                break

        if match:
            return rule

    return None


def calculate_deadlines(ticket, sla_rule):
    """Compute response_deadline and resolution_deadline for a ticket given
    a matched SLA rule.

    Gap #3: if ``sla_rule.business_hours_only`` is True, uses business-hours
    calculation instead of flat wall-clock time.

    Gap #4: accounts for accumulated ``total_paused_minutes`` by shifting
    deadlines forward.
    """
    if not ticket.created_at_source or not sla_rule:
        return None, None

    created = _as_aware(ticket.created_at_source)

    # Gap #4: shift for accumulated pause time
    pause_shift = timedelta(minutes=ticket.total_paused_minutes or 0)

    if sla_rule.business_hours_only:
        # Gap #3: business-hours-aware calculation
        from services.business_hours import add_business_minutes

        # Resolve client for timezone
        client = ticket.client if ticket.client_id else None

        response_deadline = None
        if sla_rule.response_sla_minutes:
            response_deadline = add_business_minutes(
                created, sla_rule.response_sla_minutes, client
            ) + pause_shift

        resolution_deadline = add_business_minutes(
            created, sla_rule.resolution_sla_minutes, client
        ) + pause_shift
    else:
        response_deadline = None
        if sla_rule.response_sla_minutes:
            response_deadline = created + timedelta(minutes=sla_rule.response_sla_minutes) + pause_shift

        resolution_deadline = created + timedelta(minutes=sla_rule.resolution_sla_minutes) + pause_shift

    return response_deadline, resolution_deadline


def calculate_breach_duration(ticket):
    """Minutes by which the ticket has breached (or would breach) its
    resolution deadline. Returns 0 if not breached."""
    if not ticket.resolution_deadline:
        return 0

    deadline = _as_aware(ticket.resolution_deadline)
    reference_time = _as_aware(ticket.closed_at_source) or _now()

    if reference_time <= deadline:
        return 0

    delta = reference_time - deadline
    return int(delta.total_seconds() // 60)


def calculate_sla_percentage(ticket):
    """Percentage of the resolution SLA window that has been consumed."""
    if not ticket.resolution_deadline or not ticket.created_at_source:
        return None

    created = _as_aware(ticket.created_at_source)
    deadline = _as_aware(ticket.resolution_deadline)
    total_window = (deadline - created).total_seconds()
    if total_window <= 0:
        return 100.0

    # Gap #4: if currently paused, freeze at the pause time
    if ticket.paused_at:
        reference_time = _as_aware(ticket.paused_at)
    else:
        reference_time = _as_aware(ticket.closed_at_source) or _now()

    elapsed = (reference_time - created).total_seconds()
    return round(max(elapsed, 0) / total_window * 100, 1)


def _is_closed(ticket, sla_rule):
    """A ticket is considered closed for SLA purposes if it has a
    closed_at_source timestamp, OR its status is deleted_in_source,
    OR its status matches the rule's configured stop_status list."""
    if ticket.closed_at_source is not None:
        return True
    if ticket.status and ticket.status.strip().lower() in ("deleted_in_source", "cancelled", "deleted"):
        return True
    if sla_rule and ticket.status:
        stop_statuses = sla_rule.stop_status_list()
        if stop_statuses and ticket.status.strip().lower() in stop_statuses:
            return True
    return False


def _is_paused(ticket, sla_rule):
    """Check if the ticket is currently in a pause status (Gap #4)."""
    if not sla_rule or not ticket.status:
        return False
    pause_statuses = sla_rule.pause_status_list()
    return bool(pause_statuses and ticket.status.strip().lower() in pause_statuses)


def _handle_pause_resume(ticket, sla_rule):
    """
    Gap #4: Pause/resume with deadline-shift semantics.

    - Entering a pause status: record ``paused_at``
    - Leaving a pause status: compute pause duration, accumulate into
      ``total_paused_minutes``, clear ``paused_at``, shift deadlines forward
    """
    now = _now()
    currently_paused = _is_paused(ticket, sla_rule)

    if currently_paused and ticket.paused_at is None:
        # Entering pause state
        ticket.paused_at = now
    elif not currently_paused and ticket.paused_at is not None:
        # Leaving pause state — compute duration and shift
        paused_at = _as_aware(ticket.paused_at)
        pause_duration = now - paused_at
        pause_minutes = int(pause_duration.total_seconds() // 60)

        ticket.total_paused_minutes = (ticket.total_paused_minutes or 0) + pause_minutes
        ticket.paused_at = None

        # Shift deadlines forward by the pause duration
        if ticket.response_deadline:
            ticket.response_deadline = _as_aware(ticket.response_deadline) + pause_duration
        if ticket.resolution_deadline:
            ticket.resolution_deadline = _as_aware(ticket.resolution_deadline) + pause_duration


def calculate_sla_status(ticket):
    """
    Determines the overall sla_status for a ticket, per the spec's logic:

      - No matching rule                          -> "No Matching Rule"
      - Closed before deadline                     -> "Closed Within SLA"
      - Closed after deadline                       -> "Closed After Breach"
      - Open & now > deadline                        -> "Breached"
      - Open & usage% >= warning_threshold_percent    -> "Near Breach"
      - Otherwise                                      -> "Within SLA"

    This function also:
      - Updates response_sla_status independently (since response and
        resolution SLAs can diverge)
      - Handles pause/resume (Gap #4)
    """
    sla_rule = ticket.sla_rule if ticket.sla_rule_id else find_matching_sla_rule(ticket)

    if not sla_rule:
        ticket.sla_rule_id = None
        ticket.response_deadline = None
        ticket.resolution_deadline = None
        ticket.response_sla_status = None
        ticket.resolution_sla_status = SLA_NO_RULE
        ticket.sla_status = SLA_NO_RULE
        ticket.breach_duration_minutes = 0
        return SLA_NO_RULE

    ticket.sla_rule_id = sla_rule.id

    # Gap #4: handle pause/resume transitions before deadline calculation
    _handle_pause_resume(ticket, sla_rule)

    response_deadline, resolution_deadline = calculate_deadlines(ticket, sla_rule)
    ticket.response_deadline = response_deadline
    ticket.resolution_deadline = resolution_deadline

    is_closed = _is_closed(ticket, sla_rule)
    now = _now()

    # --- Response SLA (optional) ---
    if response_deadline:
        reference_time = _as_aware(ticket.closed_at_source) or now
        if is_closed:
            ticket.response_sla_status = (
                SLA_CLOSED_WITHIN if reference_time <= _as_aware(response_deadline)
                else SLA_CLOSED_AFTER_BREACH
            )
        else:
            ticket.response_sla_status = (
                SLA_BREACHED if now > _as_aware(response_deadline) else SLA_WITHIN
            )
    else:
        ticket.response_sla_status = None

    # --- Resolution SLA (primary sla_status) ---
    # Gap #4: if currently paused, freeze SLA status at current state
    if ticket.paused_at and not is_closed:
        # While paused, compute status as of the pause time
        usage_pct = calculate_sla_percentage(ticket) or 0
        if usage_pct >= 100:
            status = SLA_BREACHED
        elif usage_pct >= sla_rule.warning_threshold_percent:
            status = SLA_NEAR_BREACH
        else:
            status = SLA_WITHIN
    elif is_closed:
        closed_time = _as_aware(ticket.closed_at_source) or _as_aware(ticket.last_synced_at) or _as_aware(ticket.updated_at) or now
        status = SLA_CLOSED_WITHIN if closed_time <= _as_aware(resolution_deadline) else SLA_CLOSED_AFTER_BREACH
    else:
        if now > _as_aware(resolution_deadline):
            status = SLA_BREACHED
        else:
            usage_pct = calculate_sla_percentage(ticket) or 0
            status = SLA_NEAR_BREACH if usage_pct >= sla_rule.warning_threshold_percent else SLA_WITHIN

    ticket.resolution_sla_status = status
    ticket.sla_status = status
    ticket.breach_duration_minutes = calculate_breach_duration(ticket)

    return status


def apply_sla_to_ticket(ticket, commit=False):
    """Convenience wrapper: matches a rule and fully recalculates a single
    ticket's SLA fields."""
    calculate_sla_status(ticket)
    if commit:
        db.session.commit()
    return ticket


def recalculate_all_open_tickets():
    """
    Recalculates SLA status for every ticket that isn't already in a final
    "closed" state. Intended to be called by the scheduler after each sync,
    and can also be triggered manually from the UI ("Recalculate SLA" button).

    Returns a summary dict for logging/UI feedback.
    """
    tickets = Ticket.query.filter(
        Ticket.sla_status.notin_([SLA_CLOSED_WITHIN, SLA_CLOSED_AFTER_BREACH]),
        Ticket.status != "deleted_in_source"
    ).all()

    counts = {SLA_WITHIN: 0, SLA_NEAR_BREACH: 0, SLA_BREACHED: 0,
              SLA_CLOSED_WITHIN: 0, SLA_CLOSED_AFTER_BREACH: 0, SLA_NO_RULE: 0}

    for ticket in tickets:
        status = calculate_sla_status(ticket)
        counts[status] = counts.get(status, 0) + 1

    db.session.commit()

    return {
        "recalculated_count": len(tickets),
        "status_breakdown": counts,
    }


def recalculate_single_ticket(ticket_id):
    ticket = Ticket.query.get(ticket_id)
    if not ticket:
        return None
    apply_sla_to_ticket(ticket, commit=True)
    return ticket
