"""
services/email_service.py
----------------------------
Basic SMTP email notifications. Structure exists for the MVP even though
actual sending can remain disabled (EMAIL_NOTIFICATIONS_ENABLED=False) until
an organization is ready to configure SMTP + recipient lists.

Notification types:
  - Near breach alert
  - Breach alert
  - Daily SLA summary
"""

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from models import Ticket, User
from models.ticket import SLA_BREACHED, SLA_NEAR_BREACH
from models.user import ROLE_ADMIN, ROLE_MANAGER

logger = logging.getLogger(__name__)


def _send_email(app, subject, body_html, recipients):
    if not recipients:
        logger.info("No recipients configured - skipping email '%s'", subject)
        return False

    cfg = app.config
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = cfg["SMTP_FROM_EMAIL"]
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(body_html, "html"))

    try:
        with smtplib.SMTP(cfg["SMTP_HOST"], cfg["SMTP_PORT"], timeout=20) as server:
            if cfg.get("SMTP_USE_TLS", True):
                server.starttls()
            if cfg.get("SMTP_USER"):
                server.login(cfg["SMTP_USER"], cfg["SMTP_PASSWORD"])
            server.sendmail(cfg["SMTP_FROM_EMAIL"], recipients, msg.as_string())
        return True
    except Exception:
        logger.exception("Failed to send email: %s", subject)
        return False


def _admin_and_manager_emails():
    users = User.query.filter(User.role.in_([ROLE_ADMIN, ROLE_MANAGER])).all()
    return [u.email for u in users if u.email]


def send_breach_alerts_if_needed(app):
    """Sends email alerts for tickets that have entered Breached or Near Breach status.
    
    - If a rule-specific escalation email is configured, sends an individual email to that contact.
    - Otherwise (or also as a fallback), groups them and sends a consolidated summary to Admins/Managers.
    - Sets database-backed flags to ensure each ticket/stage alert is sent exactly once.
    """
    from extensions import db
    
    # Query all open tickets in warning/breach status
    open_alerts = Ticket.query.filter(
        Ticket.sla_status.in_([SLA_BREACHED, SLA_NEAR_BREACH])
    ).all()
    
    if not open_alerts:
        return False

    global_notified_tickets = []
    any_email_sent = False

    for t in open_alerts:
        should_notify = False
        alert_type = t.sla_status # SLA_BREACHED or SLA_NEAR_BREACH
        
        if alert_type == SLA_BREACHED and not t.breach_notified:
            should_notify = True
        elif alert_type == SLA_NEAR_BREACH and not t.near_breach_notified:
            should_notify = True
            
        if not should_notify:
            continue

        # Determine if there is a specific escalation email
        escalation_email = None
        if t.sla_rule and t.sla_rule.escalation_email:
            escalation_email = t.sla_rule.escalation_email.strip()
            
        if escalation_email:
            # Send individual email directly to the escalation contact
            subject = f"SLA Escalation Alert: Ticket {t.external_id} is {alert_type}"
            body = f"""
            <h3>SLA Escalation Alert</h3>
            <p>The following ticket has reached <strong>{alert_type}</strong> status under SLA Rule: '{t.sla_rule.rule_name}'.</p>
            <table border="1" cellpadding="4" cellspacing="0">
              <tr><td><strong>External ID</strong></td><td>{t.external_id}</td></tr>
              <tr><td><strong>Title</strong></td><td>{t.title}</td></tr>
              <tr><td><strong>SLA Status</strong></td><td><strong style="color:red;">{alert_type}</strong></td></tr>
              <tr><td><strong>Assigned To</strong></td><td>{t.assigned_to or 'Unassigned'}</td></tr>
              <tr><td><strong>Resolution Deadline</strong></td><td>{t.resolution_deadline or 'N/A'}</td></tr>
            </table>
            """
            sent = _send_email(app, subject, body, [escalation_email])
            if sent:
                any_email_sent = True
                if alert_type == SLA_BREACHED:
                    t.breach_notified = True
                else:
                    t.near_breach_notified = True
        else:
            # Add to global notification list to notify admins/managers in a consolidated email
            global_notified_tickets.append(t)

    # Send consolidated email to Admins/Managers for any tickets without rule-specific escalation
    if global_notified_tickets:
        rows = "".join(
            f"<tr><td>{t.external_id}</td><td>{t.title}</td><td>{t.sla_status}</td>"
            f"<td>{t.assigned_to or ''}</td></tr>"
            for t in global_notified_tickets
        )
        body = f"""
        <h3>SLA Alert: Consolidated Summary</h3>
        <p>The following new SLA alerts occurred (no specific escalation email was configured):</p>
        <table border="1" cellpadding="4" cellspacing="0">
          <tr><th>External ID</th><th>Title</th><th>Status</th><th>Assigned To</th></tr>
          {rows}
        </table>
        """
        recipients = _admin_and_manager_emails()
        sent = _send_email(app, "SLA Alert: Breached / Near Breach Tickets (Consolidated)", body, recipients)
        if sent:
            any_email_sent = True
            for t in global_notified_tickets:
                if t.sla_status == SLA_BREACHED:
                    t.breach_notified = True
                else:
                    t.near_breach_notified = True

    if any_email_sent:
        db.session.commit()

    return any_email_sent


def send_daily_summary_email(app):
    total = Ticket.query.count()
    breached = Ticket.query.filter_by(sla_status=SLA_BREACHED).count()
    near_breach = Ticket.query.filter_by(sla_status=SLA_NEAR_BREACH).count()

    body = f"""
    <h3>Daily SLA Summary</h3>
    <ul>
      <li>Total tickets: {total}</li>
      <li>Breached: {breached}</li>
      <li>Near breach: {near_breach}</li>
    </ul>
    """
    return _send_email(app, "Daily SLA Summary", body, _admin_and_manager_emails())


def send_ticket_email_manually(app, ticket):
    """Manually sends an SLA alert email for a specific ticket directly.
    Bypasses the breach_notified / near_breach_notified database checks.
    """
    alert_type = ticket.sla_status or "Status Info"
    escalation_email = None
    if ticket.sla_rule and ticket.sla_rule.escalation_email:
        escalation_email = ticket.sla_rule.escalation_email.strip()
        
    recipients = [escalation_email] if escalation_email else _admin_and_manager_emails()
    
    if not recipients:
        return False, "No recipients found. Please configure an escalation email on the SLA rule or ensure Admin/Manager users have email addresses."
        
    subject = f"Manual SLA Alert: Ticket {ticket.external_id} is {alert_type}"
    body = f"""
    <h3>Manual SLA Alert (Direct Trigger)</h3>
    <p>This email alert was manually dispatched for the following ticket:</p>
    <table border="1" cellpadding="4" cellspacing="0" style="border-collapse: collapse; border: 1px solid #ddd;">
      <tr style="background-color: #f2f2f2;"><th>Field</th><th>Details</th></tr>
      <tr><td><strong>External ID</strong></td><td>{ticket.external_id}</td></tr>
      <tr><td><strong>Title</strong></td><td>{ticket.title}</td></tr>
      <tr><td><strong>SLA Status</strong></td><td><strong style="color:red;">{alert_type}</strong></td></tr>
      <tr><td><strong>Assigned To</strong></td><td>{ticket.assigned_to or 'Unassigned'}</td></tr>
      <tr><td><strong>Response Deadline</strong></td><td>{ticket.response_deadline or 'N/A'}</td></tr>
      <tr><td><strong>Resolution Deadline</strong></td><td>{ticket.resolution_deadline or 'N/A'}</td></tr>
    </table>
    """
    
    success = _send_email(app, subject, body, recipients)
    if success:
        return True, f"Manual email successfully sent to: {', '.join(recipients)}"
    else:
        return False, "Failed to send email. Please check SMTP server settings and logs."
