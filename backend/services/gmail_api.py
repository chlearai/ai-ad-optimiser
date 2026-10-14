"""
Gmail API email sender.

Uses OAuth2 refresh token to send emails via the Gmail REST API (HTTPS).
This bypasses SMTP port blocking / timeouts on cloud hosts like Railway.
"""
import os
import base64
import logging
import secrets
import json
from functools import partial
import httplib2
from google_auth_httplib2 import AuthorizedHttp
import urllib.parse
import urllib.request
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formataddr
from typing import Dict, Any, Optional

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from google.auth.transport.requests import Request

logger = logging.getLogger("AdOptima")

_TOKEN_URL = "https://oauth2.googleapis.com/token"
_AUTH_URL = "https://accounts.google.com/o/oauth2/auth"


def _get_client_config() -> Dict[str, Any]:
    """Keep explicit Gmail clients; otherwise reuse the application's saved Google client."""
    client_id = os.getenv("GMAIL_CLIENT_ID", "").strip()
    client_secret = os.getenv("GMAIL_CLIENT_SECRET", "").strip()
    if client_id or client_secret:
        return {"client_id": client_id, "client_secret": client_secret}
    from backend.services.config import load_config
    config = load_config()
    return {
        "client_id": (os.getenv("google_client_id") or config.get("google_client_id") or "").strip(),
        "client_secret": (os.getenv("google_client_secret") or config.get("google_client_secret") or "").strip(),
    }


def _get_oauth_config(redirect_uri: Optional[str] = None) -> Dict[str, Any]:
    cfg = _get_client_config()
    if not cfg["client_id"] or not cfg["client_secret"]:
        return {"error": "Google OAuth client ID and secret are not configured"}
    redirect_uri = redirect_uri or os.getenv("GMAIL_REDIRECT_URI", "").strip()
    if not redirect_uri:
        return {"error": "GMAIL_REDIRECT_URI must be set"}
    return {**cfg, "redirect_uri": redirect_uri}


def _scopes() -> list:
    return ["https://www.googleapis.com/auth/gmail.send"]


def build_authorization_url(redirect_uri: Optional[str] = None) -> Dict[str, Any]:
    """Build the Google OAuth URL for the admin to authorize Gmail sending.

    No PKCE so we don't need to track a code_verifier across requests.
    """
    cfg = _get_oauth_config(redirect_uri=redirect_uri)
    if cfg.get("error"):
        return cfg
    if redirect_uri:
        cfg["redirect_uri"] = redirect_uri
    state = secrets.token_urlsafe(24)
    params = {
        "response_type": "code",
        "client_id": cfg["client_id"],
        "redirect_uri": cfg["redirect_uri"],
        "scope": " ".join(_scopes()),
        "access_type": "offline",
        "include_granted_scopes": "true",
        "prompt": "consent",
        "state": state,
    }
    url = _AUTH_URL + "?" + urllib.parse.urlencode(params)
    return {"url": url, "state": state}


def exchange_code_for_token(code: str, redirect_uri: Optional[str] = None) -> Dict[str, Any]:
    """Exchange OAuth authorization code for refresh token via direct HTTP POST.

    No PKCE code_verifier required.
    """
    cfg = _get_oauth_config(redirect_uri=redirect_uri)
    if cfg.get("error"):
        return cfg
    if redirect_uri:
        cfg["redirect_uri"] = redirect_uri
    data = {
        "code": code,
        "client_id": cfg["client_id"],
        "client_secret": cfg["client_secret"],
        "redirect_uri": cfg["redirect_uri"],
        "grant_type": "authorization_code",
    }
    try:
        body = urllib.parse.urlencode(data).encode("utf-8")
        req = urllib.request.Request(
            _TOKEN_URL,
            data=body,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        return {
            "refresh_token": payload.get("refresh_token"),
            "token": payload.get("access_token"),
            "expiry": None,
        }
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        logger.error(f"Gmail token exchange HTTP {e.code}: {err_body}")
        return {"error": f"(HTTP {e.code}) {err_body}"}
    except Exception as e:
        logger.exception(f"Failed to exchange Gmail OAuth code: {e}")
        return {"error": str(e)}


def _get_credentials_from_refresh_token(refresh_token: str, timeout: int = 15) -> Optional[Credentials]:
    cfg = _get_client_config()
    client_id, client_secret = cfg["client_id"], cfg["client_secret"]
    if not client_id or not client_secret or not refresh_token:
        return None
    try:
        creds = Credentials(
            token=None,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=client_id,
            client_secret=client_secret,
            scopes=_scopes(),
        )
        creds.refresh(partial(Request(), timeout=timeout))
        return creds
    except Exception as e:
        logger.error(f"Failed to refresh Gmail credentials: {e}")
        return None


def send_email_via_gmail_api(
    recipient_email: str,
    subject: str,
    plain_body: str,
    html_body: str,
    sender_email: str,
    sender_name: str,
    refresh_token: str,
    timeout: int = 15,
) -> Dict[str, Any]:
    """Send an email using the Gmail API."""
    creds = _get_credentials_from_refresh_token(refresh_token, timeout=timeout)
    if not creds:
        return {"sent": False, "error": "Gmail credentials not available or refresh failed"}

    msg = MIMEMultipart("alternative")
    msg["From"] = formataddr((sender_name, sender_email))
    msg["To"] = recipient_email
    msg["Subject"] = subject
    msg.attach(MIMEText(plain_body, "plain", _charset="utf-8"))
    msg.attach(MIMEText(html_body, "html", _charset="utf-8"))

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")

    try:
        transport = AuthorizedHttp(creds, http=httplib2.Http(timeout=timeout))
        service = build("gmail", "v1", http=transport, cache_discovery=False)
        result = service.users().messages().send(userId="me", body={"raw": raw}).execute()
        message_id = result.get("id")
        logger.info(f"Gmail API sent email to {recipient_email}, message_id={message_id}")
        return {"sent": True, "error": None, "provider": "gmail_api", "message_id": message_id}
    except Exception as e:
        logger.exception(f"Gmail API send failed for {recipient_email}: {e}")
        return {"sent": False, "error": str(e), "provider": "gmail_api"}
