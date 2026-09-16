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
    msg["X-Mailer"] = "AdOptimaMailer/1.0"
    msg["Precedence"] = "bulk"
    msg["Auto-Submitted"] = "auto-generated"
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
                <p style="margin:0 0 16px; color:#57534e;">Stop paying for garbage leads — every lead from your Google &amp; Meta ads gets scored for integrity before it reaches your team.</p>
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
                © 2026 AdGuard · Built by Chlear Digital
            </div>
        </div>
    </body>
    </html>
    """
    plain_body = f"""Hi {full_name or 'there'},

Your AdGuard workspace is ready.

Activate it (set your password):
{setup_link}

Stop paying for garbage leads — every lead from your Google & Meta ads gets scored for integrity before it reaches your team.

This link expires in 72 hours.

— AdGuard, built by Chlear Digital
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