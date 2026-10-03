"""
Onboarding invitation email sender.

Priority:
  1. Gmail API (HTTPS, works from Railway/cloud, uses your existing Gmail account)
  2. SMTP fallback (may timeout on Railway)

Environment variables:
  GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, GMAIL_REDIRECT_URI -> for Gmail API OAuth
  SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS -> SMTP fallback
"""
import os
import socket
import logging
import smtplib
import time
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formataddr, make_msgid
from typing import Dict, Any

logger = logging.getLogger("AdOptima")


def _smtp_from_env() -> Dict[str, Any]:
    """Read SMTP settings from environment with sane defaults and validation."""
    smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com").strip()
    smtp_port_raw = os.getenv("SMTP_PORT", "587").strip()
    smtp_user = os.getenv("SMTP_USER", "").strip()
    smtp_pass = os.getenv("SMTP_PASS", "").strip()
    smtp_from = os.getenv("SMTP_FROM", smtp_user).strip()
    sender_name = os.getenv("SMTP_SENDER_NAME", "ChlearSakhaaOps AI").strip()

    try:
        smtp_port = int(smtp_port_raw)
    except ValueError:
        return {"error": f"Invalid SMTP_PORT value: {smtp_port_raw!r}"}

    if not smtp_host or not smtp_port:
        return {"error": "SMTP_HOST/SMTP_PORT not configured"}
    if not smtp_user or not smtp_pass:
        return {
            "error": "SMTP_USER and SMTP_PASS are required for SMTP fallback.",
        }

    return {
        "host": smtp_host,
        "port": smtp_port,
        "user": smtp_user,
        "pass": smtp_pass,
        "from": smtp_from or smtp_user,
        "sender_name": sender_name or "ChlearSakhaaOps AI",
    }


def _build_email_payloads(recipient_email: str, full_name: str, setup_link: str, sender_name: str) -> Dict[str, str]:
    subject = "Welcome to ChlearSakhaaOps AI - Set up your account"
    html_body = f"""
    <html>
    <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
        <p>Hi {full_name or 'there'},</p>
        <p>You have been invited to access <strong>ChlearSakhaaOps AI</strong>.</p>
        <p>Click the button below to set your password and activate your account:</p>
        <p>
            <a href="{setup_link}" style="display:inline-block;padding:12px 24px;background:#2563eb;color:#fff;text-decoration:none;border-radius:6px;">
                Set up my account
            </a>
        </p>
        <p>Or copy and paste this link into your browser:</p>
        <p><a href="{setup_link}">{setup_link}</a></p>
        <p>This link expires in 72 hours.</p>
        <p>If you did not expect this invitation, please ignore this email.</p>
        <br>
        <p>Best regards,<br>{sender_name}</p>
    </body>
    </html>
    """

    plain_body = f"""Hi {full_name or 'there'},

You have been invited to access ChlearSakhaaOps AI.

Click the link below to set your password and activate your account:
{setup_link}

This link expires in 72 hours.

Best regards,
{sender_name}
"""
    return {"subject": subject, "html": html_body, "text": plain_body}


def _send_via_smtp(
    recipient_email: str, full_name: str, setup_link: str, timeout: int = 60
) -> Dict[str, Any]:
    cfg = _smtp_from_env()
    if cfg.get("error"):
        logger.error(f"SMTP fallback misconfiguration: {cfg['error']}")
        return {"sent": False, "error": cfg["error"], "provider": "smtp"}

    sender_email = cfg["from"]
    sender_name = cfg["sender_name"]
    payloads = _build_email_payloads(recipient_email, full_name, setup_link, sender_name)
    message_id = make_msgid(domain=sender_email.split("@")[-1] or "adoptima.ai")

    msg = MIMEMultipart("alternative")
    msg["From"] = formataddr((sender_name, sender_email))
    msg["To"] = recipient_email
    msg["Subject"] = payloads["subject"]
    msg["Message-ID"] = message_id
    msg["Date"] = datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S +0000")
    msg["Reply-To"] = sender_email
    msg["X-Mailer"] = "AdGuardMailer/1.0"
    # NOTE: no Precedence/Auto-Submitted headers here — Gmail silently
    # spam-filters or drops transactional mail carrying bulk markers.
    msg.attach(MIMEText(payloads["text"], "plain", _charset="utf-8"))
    msg.attach(MIMEText(payloads["html"], "html", _charset="utf-8"))

    try:
        # Force IPv4 — Railway (and many cloud hosts) lack IPv6 connectivity.
        addrs = socket.getaddrinfo(cfg["host"], cfg["port"], socket.AF_INET, socket.SOCK_STREAM)
        ipv4_addr = addrs[0][4]
        ip_str = ipv4_addr[0]

        logger.info(
            f"Connecting to SMTP {cfg['host']} ({ip_str}):{cfg['port']} as {cfg['user']} "
            f"to send onboarding email to {recipient_email} (msgid={message_id})"
        )
        start = time.time()
        server = smtplib.SMTP(ip_str, cfg["port"], timeout=timeout)
        server.ehlo(cfg["host"])
        server.starttls()
        server.ehlo(cfg["host"])
        server.login(cfg["user"], cfg["pass"])
        response = server.sendmail(sender_email, [recipient_email], msg.as_string())
        server.quit()
        elapsed = round(time.time() - start, 2)

        if response:
            logger.error(f"SMTP server rejected recipients for {recipient_email}: {response}")
            return {
                "sent": False,
                "error": f"SMTP server rejected recipients: {response}",
                "provider": "smtp",
            }

        logger.info(f"Onboarding email ACCEPTED by SMTP for {recipient_email} in {elapsed}s (msgid={message_id})")
        return {"sent": True, "error": None, "provider": "smtp"}
    except smtplib.SMTPAuthenticationError as e:
        err = f"SMTP authentication failed for {cfg['user']}: {e.smtp_error}"
        logger.exception(err)
        return {"sent": False, "error": err, "provider": "smtp"}
    except smtplib.SMTPRecipientsRefused as e:
        err = f"SMTP server refused recipient {recipient_email}: {e.recipients}"
        logger.exception(err)
        return {"sent": False, "error": err, "provider": "smtp"}
    except smtplib.SMTPException as e:
        err = f"SMTP error while sending to {recipient_email}: {e}"
        logger.exception(err)
        return {"sent": False, "error": err, "provider": "smtp"}
    except socket.gaierror as e:
        err = f"DNS resolution failed for {cfg['host']}: {e}"
        logger.exception(err)
        return {"sent": False, "error": err, "provider": "smtp"}
    except Exception as e:
        err = f"Unexpected error sending onboarding email to {recipient_email}: {e}"
        logger.exception(err)
        return {"sent": False, "error": err, "provider": "smtp"}


def send_onboarding_email(
    recipient_email: str,
    full_name: str,
    setup_link: str,
    refresh_token: str = None,
    timeout: int = 60,
) -> Dict[str, Any]:
    """
    Send a setup-link email to a newly created user.

    Uses Gmail API (HTTPS) if a refresh_token is provided.
    Falls back to SMTP otherwise.
    """
    sender_email = os.getenv("SMTP_FROM", os.getenv("SMTP_USER", "")).strip()
    sender_name = os.getenv("SMTP_SENDER_NAME", "ChlearSakhaaOps AI").strip()

    if refresh_token:
        from backend.services.gmail_api import send_email_via_gmail_api
        payloads = _build_email_payloads(recipient_email, full_name, setup_link, sender_name)
        return send_email_via_gmail_api(
            recipient_email=recipient_email,
            subject=payloads["subject"],
            plain_body=payloads["text"],
            html_body=payloads["html"],
            sender_email=sender_email,
            sender_name=sender_name,
            refresh_token=refresh_token,
        )

    logger.warning("No Gmail refresh token; falling back to SMTP (may fail on Railway)")
    return _send_via_smtp(recipient_email, full_name, setup_link, timeout=timeout)


def _build_adguard_payloads(recipient_email: str, full_name: str, setup_link: str, sender_name: str) -> Dict[str, str]:
    """AdGuard-branded subscriber invite."""
    subject = "You're invited to AdGuard — activate your workspace"
    html_body = f"""
    <html>
    <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #1c1917; background:#fafaf9; padding:24px;">
        <div style="max-width:560px;margin:0 auto;background:#ffffff;border:1px solid #e7e5e4;border-radius:12px;overflow:hidden;">
            <div style="background:#d97706;padding:20px 24px;">
                <span style="display:inline-block;width:32px;height:32px;background:#ffffff;color:#d97706;font-weight:700;border-radius:8px;text-align:center;line-height:32px;font-size:14px;">AG</span>
                <span style="color:#ffffff;font-size:18px;font-weight:700;margin-left:8px;font-family:Arial,sans-serif;">AdGuard</span>
            </div>
            <div style="padding:28px 24px;">
                <p style="margin:0 0 12px;">Hi {full_name or 'there'},</p>
                <p style="margin:0 0 8px;"><strong>Your AdGuard workspace is ready.</strong></p>
                <p style="margin:0 0 16px; color:#57534e;">Stop buying fake leads — every lead from your Google &amp; Meta ads gets scored for integrity before it reaches your team.</p>
                <p style="margin:0 0 20px;">
                    <a href="{setup_link}" style="display:inline-block;padding:12px 28px;background:#d97706;color:#ffffff;text-decoration:none;border-radius:8px;font-weight:bold;">
                        Activate My Workspace
                    </a>
                </p>
                <p style="margin:0 0 6px; font-size:13px;color:#57534e;">Or copy this link into your browser:</p>
                <p style="font-size:13px;"><a href="{setup_link}">{setup_link}</a></p>
                <p style="font-size:13px;color:#a8a29e;">This link expires in 72 hours. If you didn't expect this invitation, ignore this email.</p>
            </div>
            <div style="padding:14px 24px;background:#fafaf9;border-top:1px solid #e7e5e4;font-size:11px;color:#a8a29e;">
                © 2026 AdGuard · Built by CHLEAR
            </div>
        </div>
    </body>
    </html>
    """
    plain_body = f"""Hi {full_name or 'there'},

Your AdGuard workspace is ready.

Activate it (set your password):
{setup_link}

Stop buying fake leads — every lead from your Google & Meta ads gets scored for integrity before it reaches your team.

This link expires in 72 hours.

— AdGuard, built by CHLEAR
"""
    return {"subject": subject, "html": html_body, "text": plain_body}


def send_adguard_invite_email(
    recipient_email: str,
    full_name: str,
    setup_link: str,
    refresh_token: str = None,
    timeout: int = 60,
) -> Dict[str, Any]:
    """Send the AdGuard-branded subscriber invite (Gmail API if token, else SMTP)."""
    sender_email = os.getenv("SMTP_FROM", os.getenv("SMTP_USER", "")).strip()
    sender_name = os.getenv("SMTP_SENDER_NAME", "AdGuard").strip()
    payloads = _build_adguard_payloads(recipient_email, full_name, setup_link, sender_name)

    if refresh_token:
        from backend.services.gmail_api import send_email_via_gmail_api
        gmail_result = send_email_via_gmail_api(
            recipient_email=recipient_email,
            subject=payloads["subject"],
            plain_body=payloads["text"],
            html_body=payloads["html"],
            sender_email=sender_email,
            sender_name=sender_name,
            refresh_token=refresh_token,
        )
        if gmail_result.get("sent"):
            return gmail_result
        logger.warning(f"Gmail API send failed ({gmail_result.get('error')}); falling back to SMTP")

    logger.warning("Sending AdGuard invite via SMTP fallback")
    cfg = _smtp_from_env()
    if cfg.get("error"):
        if "required" in cfg["error"]:
            cfg["error"] = "Email not configured: set SMTP_HOST/SMTP_PORT/SMTP_USER/SMTP_PASS (Gmail App Password) in Railway variables, then redeploy."
        return {"sent": False, "error": cfg["error"], "provider": "smtp"}

    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    from email.utils import formataddr, make_msgid
    import smtplib
    import socket

    message_id = make_msgid(domain=(sender_email.split("@")[-1] or "adguard.app"))
    msg = MIMEMultipart("alternative")
    msg["From"] = formataddr((sender_name, sender_email))
    msg["To"] = recipient_email
    msg["Subject"] = payloads["subject"]
    msg["Message-ID"] = message_id
    msg["Date"] = datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S +0000")
    msg["Reply-To"] = sender_email
    msg["X-Mailer"] = "AdGuardMailer/1.0"
    msg["Precedence"] = "bulk"
    msg["Auto-Submitted"] = "auto-generated"
    msg.attach(MIMEText(payloads["text"], "plain", _charset="utf-8"))
    msg.attach(MIMEText(payloads["html"], "html", _charset="utf-8"))

    try:
        addrs = socket.getaddrinfo(cfg["host"], cfg["port"], socket.AF_INET, socket.SOCK_STREAM)
        server = smtplib.SMTP(addrs[0][4][0], cfg["port"], timeout=timeout)
        server.ehlo(cfg["host"])
        server.starttls()
        server.ehlo(cfg["host"])
        server.login(cfg["user"], cfg["pass"])
        server.sendmail(sender_email, [recipient_email], msg.as_string())
        server.quit()
        logger.info(f"AdGuard invite sent to {recipient_email} via SMTP")
        return {"sent": True, "provider": "smtp", "message_id": message_id}
    except Exception as e:
        logger.exception(f"AdGuard invite SMTP send failed for {recipient_email}: {e}")
        return {"sent": False, "error": str(e), "provider": "smtp"}


def _ag_brand_header(title: str = "AdGuard") -> str:
    return (
        '<div style="background:#d97706;padding:20px 24px;display:flex;align-items:center;">'
        '<span style="display:inline-block;width:32px;height:32px;background:#ffffff;color:#d97706;'
        'font-weight:700;border-radius:8px;text-align:center;line-height:32px;font-size:14px;">AG</span>'
        f'<span style="color:#ffffff;font-size:18px;font-weight:700;margin-left:10px;">{title}</span></div>'
    )


def _ag_brand_footer() -> str:
    return (
        '<div style="padding:14px 24px;background:#fafaf9;border-top:1px solid #e7e5e4;font-size:11px;color:#a8a29e;">'
        '&copy; 2026 AdGuard &middot; Built by CHLEAR &middot; '
        '<a href="mailto:shekhar.chlear@gmail.com" style="color:#d97706;">shekhar.chlear@gmail.com</a>'
        ' &middot; Customer Care: 80509 97977</div>'
    )


def send_adguard_verify_email(
    recipient_email: str,
    full_name: str,
    verify_link: str,
    timeout: int = 45,
) -> Dict[str, Any]:
    """Email #1: Verify your account & set a password (60-min link)."""
    sender_email = os.getenv("SMTP_FROM", os.getenv("SMTP_USER", "noreply@chlear.in")).strip() or "noreply@chlear.in"
    sender_name = os.getenv("SMTP_SENDER_NAME", "AdGuard Notification").strip() or "AdGuard Notification"
    subject = "[Quick Task] Verify your AdGuard Account & Set a Password"

    html_body = f"""
    <html><body style="font-family:Arial,sans-serif;line-height:1.6;color:#1c1917;background:#fafaf9;padding:24px;">
    <div style="max-width:560px;margin:0 auto;background:#ffffff;border:1px solid #e7e5e4;border-radius:12px;overflow:hidden;">
        {_ag_brand_header("AdGuard Notification")}
        <div style="padding:28px 24px;">
            <p style="margin:0 0 12px;">Hi {full_name or 'there'},</p>
            <p style="margin:0 0 12px;">Welcome to <strong>AdGuard</strong> &mdash; the lead-integrity platform that scores every lead from your
            Google &amp; Meta ads before it reaches your team, so you stop paying for garbage leads.</p>
            <p style="margin:0 0 12px;">One quick step before you start: <strong>verify your account and set a password</strong>. The link below expires in <strong>60 minutes</strong>.</p>
            <p style="margin:0 0 20px;">
                <a href="{verify_link}" style="display:inline-block;padding:12px 28px;background:#d97706;color:#ffffff;text-decoration:none;border-radius:8px;font-weight:bold;">
                    Verify &amp; Set Password
                </a>
            </p>
            <p style="margin:0 0 16px;font-size:13px;color:#57534e;">Once that's done, your trial is ready. Connect your Google &amp; Meta ad accounts and watch every lead get scored in real time.</p>
            <p style="margin:0 0 12px;font-size:13px;color:#57534e;">We'll send you a couple of short emails over the next few days with quick wins to get value faster.</p>
            <p style="margin:0 0 6px;font-size:13px;color:#57534e;">Or paste this link in your browser:<br><a href="{verify_link}" style="font-size:12px;word-break:break-all;">{verify_link}</a></p>
            <p style="font-size:13px;color:#a8a29e;">Need a hand? Write to <a href="mailto:shekhar.chlear@gmail.com" style="color:#d97706;">shekhar.chlear@gmail.com</a> or call us at <b>80509 97977</b>.</p>
            <p style="margin-top:24px;">Regards,<br><b>Team AdGuard</b></p>
        </div>
        {_ag_brand_footer()}
    </div></body></html>"""

    plain_body = f"""Hi {full_name or 'there'},

Welcome to AdGuard - the lead-integrity platform that scores every lead from your Google & Meta ads before it reaches your team.

One quick step before you start: verify your account and set a password. The link below expires in 60 minutes.

Verify & Set Password: {verify_link}

Once that's done, your trial is ready. Connect your ad accounts and watch every lead get scored in real time.

Need a hand? shekhar.chlear@gmail.com or call 80509 97977.

Regards,
Team AdGuard
"""
    return _send_raw_email(recipient_email, subject, plain_body, html_body, sender_email, sender_name, timeout)


def send_adguard_password_set_confirmation(
    recipient_email: str,
    full_name: str,
    timeout: int = 45,
) -> Dict[str, Any]:
    """Email #2: confirmation right after password set (security notice)."""
    sender_email = os.getenv("SMTP_FROM", os.getenv("SMTP_USER", "noreply@chlear.in")).strip() or "noreply@chlear.in"
    sender_name = os.getenv("SMTP_SENDER_NAME", "AdGuard Notification").strip() or "AdGuard Notification"
    now_ist = datetime.utcnow() + __import__("datetime").timedelta(hours=5, minutes=30)
    date_str = now_ist.strftime("%Y-%m-%d")
    time_str = now_ist.strftime("%H:%M:%S IST")
    subject = "Password Set Successful"

    html_body = f"""
    <html><body style="font-family:Arial,sans-serif;line-height:1.6;color:#1c1917;background:#fafaf9;padding:24px;">
    <div style="max-width:560px;margin:0 auto;background:#ffffff;border:1px solid #e7e5e4;border-radius:12px;overflow:hidden;">
        {_ag_brand_header("AdGuard Notification")}
        <div style="padding:28px 24px;">
            <p style="margin:0 0 12px;">Hi {full_name or 'there'},</p>
            <p style="margin:0 0 12px;">Your <strong>AdGuard</strong> account password was set successfully on <b>{date_str}</b> at <b>{time_str}</b>.</p>
            <p style="margin:0 0 12px;">You can now sign in anytime at your AdGuard workspace and connect your Google &amp; Meta ad accounts.</p>
            <p style="margin:0 0 12px;">
                <a href="#" onclick="return false;" style="display:inline-block;padding:11px 24px;background:#d97706;color:#ffffff;text-decoration:none;border-radius:8px;font-weight:600;">Go to My Workspace</a>
            </p>
            <p style="font-size:13px;color:#78716c;margin-top:20px;">If you didn't make this request, please reach out to
            <a href="mailto:shekhar.chlear@gmail.com" style="color:#d97706;">shekhar.chlear@gmail.com</a> or call <b>80509 97977</b> immediately.</p>
        </div>
        {_ag_brand_footer()}
    </div></body></html>"""

    plain_body = f"""Hi {full_name or 'there'},

Your AdGuard account password was set successfully on {date_str} at {time_str}.

If you didn't make this request, reach out to shekhar.chlear@gmail.com or call 80509 97977 immediately.

- Team AdGuard
"""
    return _send_raw_email(recipient_email, subject, plain_body, html_body, sender_email, sender_name, timeout)


def send_adguard_welcome_aboard(
    recipient_email: str,
    full_name: str,
    is_trial: bool = True,
    timeout: int = 45,
) -> Dict[str, Any]:
    """Email #3: 'Welcome Aboard! Your Toolkit to Get Started' — first steps + resources."""
    sender_email = os.getenv("SMTP_FROM", os.getenv("SMTP_USER", "noreply@chlear.in")).strip() or "noreply@chlear.in"
    sender_name = os.getenv("SMTP_SENDER_NAME", "Team AdGuard").strip() or "Team AdGuard"
    subject = "Welcome Aboard! Your Toolkit to Get Started"

    trial_note = (
        "Your trial includes <b>300 free leads</b> to score - enough to see exactly how much junk you've been paying for."
        if is_trial
        else "Your subscription is active - every lead from your connected accounts is scored automatically."
    )

    html_body = f"""
    <html><body style="font-family:Arial,sans-serif;line-height:1.6;color:#1c1917;background:#fafaf9;padding:24px;">
    <div style="max-width:600px;margin:0 auto;background:#ffffff;border:1px solid #e7e5e4;border-radius:12px;overflow:hidden;">
        {_ag_brand_header("Team AdGuard")}
        <div style="padding:28px 24px;">
            <h2 style="font-size:20px;margin:0 0 6px;">Hello. Your shield is ready.</h2>
            <p style="margin:0 0 16px;font-size:14px;color:#57534e;">Welcome to AdGuard! {trial_note}</p>
            <p style="margin:0 0 8px;"><b>Your first steps, all free:</b></p>
            <p style="margin:0 0 6px;">&#9989; <b><a href="#" style="color:#d97706;text-decoration:none;">Connect Your Ad Accounts</a>:</b> One-click Google &amp; Meta OAuth - no passwords shared with anyone.</p>
            <p style="margin:0 0 6px;">&#9989; <b><a href="#" style="color:#d97706;text-decoration:none;">Turn On Money Shield</a>:</b> Auto-flag junk patterns and stop repeat fraud before it costs you.</p>
            <p style="margin:0 0 16px;">&#9989; <b><a href="#" style="color:#d97706;text-decoration:none;">Set CRM Delivery</a>:</b> Route only verified leads to LeadSquared, Zoho, HubSpot, or a webhook.</p>

            <p style="margin:0 0 8px;"><b>Resources for your journey:</b></p>
            <p style="margin:0 0 6px;">&#127891; <b><a href="#" style="color:#d97706;text-decoration:none;">AdGuard Academy</a>:</b> Bite-sized walkthroughs to train your team.</p>
            <p style="margin:0 0 6px;">&#128413; <b><a href="#" style="color:#d97706;text-decoration:none;">Setup Guide</a>:</b> Connect accounts + first lead in under 10 minutes.</p>
            <p style="margin:0 0 6px;">&#63; <b><a href="#" style="color:#d97706;text-decoration:none;">Help Center</a>:</b> Solutions to common questions.</p>
            <p style="margin:0 0 16px;">&#128200; <b><a href="#" style="color:#d97706;text-decoration:none;">Pricing</a>:</b> Upgrade when your trial runs out.</p>

            <p style="margin:0 0 12px;">Need a hand? Reach out at <a href="mailto:shekhar.chlear@gmail.com" style="color:#d97706;">shekhar.chlear@gmail.com</a>, or call us at <b>80509 97977</b>.</p>
            <p style="font-size:13px;color:#78716c;">Stop paying for garbage leads. - Team AdGuard</p>
        </div>
        {_ag_brand_footer()}
    </div></body></html>"""

    plain_body = f"""Hello,

Welcome to AdGuard! {trial_note if is_trial else ''}

Your first steps:
1. Connect Your Ad Accounts (Google + Meta OAuth)
2. Turn On Money Shield (auto-flag junk patterns)
3. Set CRM Delivery (LeadSquared / Zoho / HubSpot / webhook)

Resources:
- AdGuard Academy: walkthroughs to train your team
- Setup Guide: connect + first lead in under 10 minutes
- Help Center: solutions to common questions
- Pricing: upgrade when your trial runs out

Need a hand? shekhar.chlear@gmail.com or call 80509 97977.

Stop paying for garbage leads.
- Team AdGuard
"""
    return _send_raw_email(recipient_email, subject, plain_body, html_body, sender_email, sender_name, timeout)


def _send_raw_email(
    recipient_email: str,
    subject: str,
    plain_body: str,
    html_body: str,
    sender_email: str,
    sender_name: str,
    timeout: int = 45,
) -> Dict[str, Any]:
    """SMTP-only raw email used by the signup journey (Gmail API path uses gmail_api when token exists)."""
    cfg = _smtp_from_env()
    if cfg.get("error"):
        return {"sent": False, "error": cfg["error"], "provider": "smtp"}

    message_id = make_msgid(domain=(sender_email.split("@")[-1] or "chlear.in"))
    msg = MIMEMultipart("alternative")
    msg["From"] = formataddr((sender_name, sender_email))
    msg["To"] = recipient_email
    msg["Subject"] = subject
    msg["Message-ID"] = message_id
    msg["Date"] = datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S +0000")
    msg["Reply-To"] = sender_email
    msg["X-Mailer"] = "AdGuardMailer/1.0"
    msg["Precedence"] = "bulk"
    msg["Auto-Submitted"] = "auto-generated"
    msg.attach(MIMEText(plain_body, "plain", _charset="utf-8"))
    msg.attach(MIMEText(html_body, "html", _charset="utf-8"))

    try:
        addrs = socket.getaddrinfo(cfg["host"], cfg["port"], socket.AF_INET, socket.SOCK_STREAM)
        server = smtplib.SMTP(addrs[0][4][0], cfg["port"], timeout=timeout)
        server.ehlo(cfg["host"])
        server.starttls()
        server.ehlo(cfg["host"])
        server.login(cfg["user"], cfg["pass"])
        server.sendmail(sender_email, [recipient_email], msg.as_string())
        server.quit()
        logger.info(f"AdGuard email '{subject}' sent to {recipient_email} via SMTP")
        return {"sent": True, "provider": "smtp", "message_id": message_id}
    except Exception as e:
        logger.exception(f"AdGuard email '{subject}' failed for {recipient_email}: {e}")
        return {"sent": False, "error": str(e), "provider": "smtp"}


def send_adguard_demo_lead_alert(
    lead: dict,
    verify_link: str = "",
    timeout: int = 30,
) -> Dict[str, Any]:
    """Instant notification to the owner: someone just booked a 15-min demo. Call them NOW."""
    owner_email = os.getenv("ADGUARD_DEMO_ALERT_EMAIL", "shekhar.chlear@gmail.com").strip()
    sender_email = os.getenv("SMTP_FROM", os.getenv("SMTP_USER", "noreply@chlear.in")).strip() or "noreply@chlear.in"
    sender_name = "AdGuard Demo Alerts"
    subject = f"🔥 DEMO REQUEST: {lead.get('full_name', '?')} ({lead.get('company_name', '?')}) — call {lead.get('phone', '?')} now"

    channels_map = {"both": "Meta + Google", "meta": "Meta only", "google": "Google only", "other": "Other forms"}
    rows = [
        ("Name", lead.get("full_name", "-")),
        ("Company", lead.get("company_name", "-")),
        ("Email", lead.get("email", "-")),
        ("Phone (with dial code)", lead.get("phone", "-")),
        ("Country of Operation", lead.get("country", "-")),
        ("Industry", lead.get("industry", "-")),
        ("Primary Ad Channels", channels_map.get(lead.get("channels", ""), lead.get("channels", "-") or "-")),
        ("Monthly Ad Spend Band", lead.get("monthly_ad_spend_band", "-") or "-"),
        ("Currency at signup", lead.get("currency", "-") or "-"),
    ]
    html_rows = "".join(
        f'<tr><td style="padding:7px 12px;border:1px solid #e7e5e4;font-weight:600;white-space:nowrap;">{k}</td>'
        f'<td style="padding:7px 12px;border:1px solid #e7e5e4;">{v}</td></tr>'
        for k, v in rows
    )

    html_body = f"""
    <html><body style="font-family:Arial,sans-serif;line-height:1.6;color:#1c1917;background:#fafaf9;padding:24px;">
    <div style="max-width:600px;margin:0 auto;background:#ffffff;border:1px solid #e7e5e4;border-radius:12px;overflow:hidden;">
        {_ag_brand_header("🔥 Demo Lead — HOT")}
        <div style="padding:28px 24px;">
            <p style="margin:0 0 8px;font-size:15px;"><b>New demo request just came in.</b> The promise on the site: call within 15 minutes.</p>
            <table style="border-collapse:collapse;font-size:13px;margin:14px 0 18px;">{html_rows}</table>
            <p style="margin:0 0 8px;"><b>Your 15-min demo playbook:</b></p>
            <p style="margin:0 0 4px;">1. Call/WhatsApp <b>{lead.get('phone', '-')}</b> — introduce yourself</p>
            <p style="margin:0 0 4px;">2. Ask them to open the verify email (sent to {lead.get('email', '-')}) and set password during the call</p>
            <p style="margin:0 0 4px;">3. Screen-share their workspace: Connect Google/Meta → live lead stream → Money Shield → ₹ proof</p>
            <p style="margin:0 0 4px;">4. Close: pick a paid plan or extend shadow reporting</p>
            {f'<p style="font-size:12px;color:#78716c;margin-top:12px;">Their verify link (resend if expired): <a href="{verify_link}" style="word-break:break-all;">{verify_link}</a></p>' if verify_link else ''}
            <p style="margin:24px 0 0;font-size:12px;color:#78716c;border-top:1px solid #e7e5e4;padding-top:12px;">Workspace is already provisioned in the admin cockpit — open /adguard → Subscribers to view.</p>
        </div>
        {_ag_brand_footer()}
    </div></body></html>"""

    plain_lines = "\n".join(f"{k}: {v}" for k, v in rows)
    plain_body = f"""DEMO REQUEST — CALL {lead.get('phone', '-')} WITHIN 15 MINUTES

{plain_lines}

Playbook: call -> they verify during call -> screen-share workspace (connect accounts, live stream, shield, rupee proof) -> close plan.

Workspace provisioned. Verify link: {verify_link}
"""
    return _send_raw_email(owner_email, subject, plain_body, html_body, sender_email, sender_name, timeout)


def send_adguard_support_notification(
    recipient_email: str,
    subject: str,
    title: str,
    message_body: str,
    ticket_id: int,
    cta_link: str = "",
    cta_text: str = "View in Workspace",
    sender_name: str = "AdGuard Support",
    reply_to: str = "",
    timeout: int = 45,
) -> Dict[str, Any]:
    """Send an AdGuard-branded support notification email to subscriber or admin."""
    sender_email = os.getenv("SMTP_FROM", os.getenv("SMTP_USER", "")).strip() or "shekhar.chlear@gmail.com"
    reply_to_email = reply_to or os.getenv("ADGUARD_SUPPORT_EMAIL", "shekhar.chlear@gmail.com")

    html_body = f"""
    <html>
    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; line-height: 1.6; color: #1c1917; background:#fafaf9; padding:24px;">
        <div style="max-width:580px;margin:0 auto;background:#ffffff;border:1px solid #e7e5e4;border-radius:12px;overflow:hidden;box-shadow:0 4px 12px rgba(0,0,0,0.05);">
            <div style="background:#d97706;padding:20px 24px;display:flex;align-items:center;">
                <span style="display:inline-block;width:32px;height:32px;background:#ffffff;color:#d97706;font-weight:700;border-radius:8px;text-align:center;line-height:32px;font-size:14px;">AG</span>
                <span style="color:#ffffff;font-size:18px;font-weight:700;margin-left:10px;">AdGuard Support Desk</span>
            </div>
            <div style="padding:28px 24px;">
                <div style="font-size:12px;font-weight:700;text-transform:uppercase;color:#d97706;letter-spacing:0.05em;margin-bottom:6px;">
                    Ticket #{ticket_id}
                </div>
                <h2 style="font-size:18px;font-weight:700;color:#1c1917;margin:0 0 16px;">{title}</h2>
                <div style="background:#f5f5f4;border-left:4px solid #d97706;border-radius:6px;padding:14px 16px;margin-bottom:20px;font-size:14px;color:#292524;white-space:pre-wrap;">{message_body}</div>
                {f'<p style="margin:0 0 20px;"><a href="{cta_link}" style="display:inline-block;padding:11px 24px;background:#d97706;color:#ffffff;text-decoration:none;border-radius:8px;font-weight:600;font-size:13px;">{cta_text} &rarr;</a></p>' if cta_link else ''}
                <p style="font-size:12px;color:#78716c;margin-top:20px;border-top:1px solid #e7e5e4;padding-top:14px;">
                    You can reply directly to this ticket inside your <a href="{cta_link or '/adguard-workspace'}" style="color:#d97706;">AdGuard Workspace</a> or email our support desk at <a href="mailto:{reply_to_email}" style="color:#d97706;">{reply_to_email}</a>.
                </p>
            </div>
            <div style="padding:14px 24px;background:#fafaf9;border-top:1px solid #e7e5e4;font-size:11px;color:#a8a29e;">
                &copy; 2026 AdGuard &middot; Chlear Digital Support Desk
            </div>
        </div>
    </body>
    </html>
    """

    plain_body = f"""AdGuard Support Desk — Ticket #{ticket_id}
{title}

{message_body}

View in workspace: {cta_link}
Direct email support: {reply_to_email}

© 2026 AdGuard · Chlear Digital Support Desk
"""

    cfg = _smtp_from_env()
    if cfg.get("error"):
        logger.warning(f"SMTP not fully configured for support notification ({cfg.get('error')})")
        return {"sent": False, "error": cfg.get("error"), "provider": "none"}

    message_id = make_msgid(domain=(sender_email.split("@")[-1] or "adguard.ai"))
    msg = MIMEMultipart("alternative")
    msg["From"] = formataddr((sender_name, sender_email))
    msg["To"] = recipient_email
    msg["Subject"] = subject
    msg["Message-ID"] = message_id
    msg["Date"] = datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S +0000")
    msg["Reply-To"] = reply_to_email
    msg["X-Mailer"] = "AdGuardSupportMailer/1.0"
    msg.attach(MIMEText(plain_body, "plain", _charset="utf-8"))
    msg.attach(MIMEText(html_body, "html", _charset="utf-8"))

    try:
        addrs = socket.getaddrinfo(cfg["host"], cfg["port"], socket.AF_INET, socket.SOCK_STREAM)
        server = smtplib.SMTP(addrs[0][4][0], cfg["port"], timeout=timeout)
        server.ehlo(cfg["host"])
        server.starttls()
        server.ehlo(cfg["host"])
        server.login(cfg["user"], cfg["pass"])
        server.sendmail(sender_email, [recipient_email], msg.as_string())
        server.quit()
        logger.info(f"Support notification sent to {recipient_email} for Ticket #{ticket_id}")
        return {"sent": True, "provider": "smtp", "message_id": message_id}
    except Exception as e:
        logger.warning(f"Support notification email failed to send to {recipient_email}: {e}")
        return {"sent": False, "error": str(e), "provider": "smtp"}