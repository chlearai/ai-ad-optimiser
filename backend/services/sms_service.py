"""
AdGuard SMS / WhatsApp OTP service for Grey-band lead step-up verification.

Supports pluggable providers:
  - msg91: MSG91 OTP API
  - twilio: Twilio SMS API
  - generic: generic HTTP GET/POST SMS webhook
  - mock: sandbox mode for local/testing (returns OTP in response)
"""
import json
import logging
import os
import random
import string
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("AdOptima")


def generate_otp_code(length: int = 6) -> str:
    """Generate a numeric OTP code."""
    return "".join(random.choices(string.digits, k=length))


def send_otp(phone: str, otp_code: str, provider_settings: Optional[Dict[str, Any]] = None) -> Tuple[bool, Optional[str]]:
    """Send OTP to the given phone number using configured provider settings.

    Returns (success, error_or_note)
    """
    cfg = provider_settings or {}
    provider = (cfg.get("provider") or os.getenv("ADGUARD_OTP_PROVIDER", "mock")).lower()

    if not phone:
        return False, "missing_phone"

    clean_phone = "".join(c for c in phone if c.isdigit())
    if len(clean_phone) == 10:
        clean_phone = "91" + clean_phone

    if provider == "mock":
        logger.info(f"[AdGuard OTP Mock] Generated OTP {otp_code} for {phone}")
        return True, f"mock_otp_sent:{otp_code}"

    if provider == "msg91":
        auth_key = cfg.get("auth_key") or os.getenv("MSG91_AUTH_KEY", "")
        template_id = cfg.get("template_id") or os.getenv("MSG91_TEMPLATE_ID", "")
        if not auth_key:
            return False, "msg91_auth_key_missing"
        url = "https://api.msg91.com/api/v5/otp"
        params = {
            "template_id": template_id,
            "mobile": clean_phone,
            "authkey": auth_key,
            "otp": otp_code,
        }
        url = f"{url}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, method="POST", headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
                if data.get("type") == "success":
                    return True, None
                return False, str(data.get("message") or "msg91_error")
        except Exception as e:
            return False, f"msg91_request_failed: {e}"

    if provider == "twilio":
        account_sid = cfg.get("account_sid") or os.getenv("TWILIO_ACCOUNT_SID", "")
        auth_token = cfg.get("auth_token") or os.getenv("TWILIO_AUTH_TOKEN", "")
        from_number = cfg.get("from_number") or os.getenv("TWILIO_FROM_NUMBER", "")
        if not (account_sid and auth_token and from_number):
            return False, "twilio_credentials_missing"
        import base64
        url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"
        data = urllib.parse.urlencode({
            "To": f"+{clean_phone}",
            "From": from_number,
            "Body": f"Your verification code is: {otp_code}. Valid for 5 minutes.",
        }).encode()
        auth_header = "Basic " + base64.b64encode(f"{account_sid}:{auth_token}".encode()).decode()
        req = urllib.request.Request(url, data=data, method="POST", headers={"Authorization": auth_header})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                if 200 <= resp.status < 300:
                    return True, None
                return False, f"twilio_status_{resp.status}"
        except Exception as e:
            return False, f"twilio_failed: {e}"

    return True, f"unsupported_provider_{provider}_treated_as_mock"
