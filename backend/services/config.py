"""
Configuration management for AdOptima AI.
Loads config.json and merges with environment variables from .env
"""
import os
import json
import logging
from typing import Dict, Any

logger = logging.getLogger("AdOptima")

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONFIG_PATH = os.path.join(ROOT_DIR, "config.json")
ENV_PATH = os.path.join(ROOT_DIR, ".env")

DEFAULT_CONFIG: Dict[str, Any] = {
        "developer_token": "",
        "client_id": "",
        "client_secret": "",
        "refresh_token": "",
        "customer_id": "",
        "login_customer_id": "",
        "gemini_api_key": "",
        "safe_mode": True,
        # OAuth app credentials
        "google_client_id": "",
        "google_client_secret": "",
        "google_developer_token": "",
        "meta_app_id": "",
        "meta_app_secret": "",
        # Base URL for OAuth redirects; defaults to local dev server
        "redirect_base_url": "http://127.0.0.1:8000",
        # CRM / lead integrations
        "salesforce_url": "",
        "salesforce_client_id": "",
        "salesforce_client_secret": "",
        "salesforce_refresh_token": "",
        "leadsquared_access_key": "",
        "leadsquared_secret_key": "",
        "leadsquared_base_url": "",
        # Mantri reporting integration
        "mantri_meta_account_id": "",
        "mantri_salesforce_url": "",
        "mantri_salesforce_client_id": "",
        "mantri_salesforce_client_secret": "",
        "mantri_salesforce_refresh_token": "",
        # Global auto-audit interval in minutes (0 = disabled)
        "global_audit_interval_minutes": 60,
    }


def load_env_file() -> Dict[str, str]:
    env_data = {}
    if not os.path.exists(ENV_PATH):
        return env_data
    try:
        with open(ENV_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip()
                if val.startswith(("'", '"')) and val.endswith(("'", '"')) and len(val) > 1:
                    val = val[1:-1]
                env_data[key] = val
    except Exception as e:
        logger.error(f"Error reading .env file: {e}")
    return env_data


def load_config() -> Dict[str, Any]:
    config = DEFAULT_CONFIG.copy()

    # First load .env so it takes precedence over config.json defaults
    env = load_env_file()

    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                config.update(json.load(f))
        except Exception as e:
            logger.error(f"Error reading config.json: {e}")

    # Map environment variables to config keys
    env_mappings = [
        ("developer_token", "DEVELOPER_TOKEN"),
        ("client_id", "CLIENT_ID"),
        ("client_secret", "CLIENT_SECRET"),
        ("refresh_token", "REFRESH_TOKEN"),
        ("customer_id", "CUSTOMER_ID"),
        ("login_customer_id", "LOGIN_CUSTOMER_ID"),
        ("gemini_api_key", "GEMINI_API_KEY"),
        ("google_client_id", "GOOGLE_CLIENT_ID"),
        ("google_client_secret", "GOOGLE_CLIENT_SECRET"),
        ("google_developer_token", "GOOGLE_DEVELOPER_TOKEN"),
        ("meta_app_id", "META_APP_ID"),
        ("meta_app_secret", "META_APP_SECRET"),
        ("meta_system_user_token", "META_SYSTEM_USER_TOKEN"),
        ("redirect_base_url", "REDIRECT_BASE_URL"),
        ("salesforce_url", "SALESFORCE_URL"),
        ("salesforce_client_id", "SALESFORCE_CLIENT_ID"),
        ("salesforce_client_secret", "SALESFORCE_CLIENT_SECRET"),
        ("salesforce_refresh_token", "SALESFORCE_REFRESH_TOKEN"),
        ("leadsquared_access_key", "LEADSQUARED_ACCESS_KEY"),
        ("leadsquared_secret_key", "LEADSQUARED_SECRET_KEY"),
        ("leadsquared_base_url", "LEADSQUARED_BASE_URL"),
        ("mantri_meta_account_id", "MANTRI_META_ACCOUNT_ID"),
        ("mantri_salesforce_url", "MANTRI_SALESFORCE_URL"),
        ("mantri_salesforce_client_id", "MANTRI_SALESFORCE_CLIENT_ID"),
        ("mantri_salesforce_client_secret", "MANTRI_SALESFORCE_CLIENT_SECRET"),
        ("mantri_salesforce_refresh_token", "MANTRI_SALESFORCE_REFRESH_TOKEN"),
    ]
    # Precedence (highest first): real process env (Railway/dashboard vars)
    # > .env file > config.json. On Railway there is no .env file, so the
    # dashboard variables must win.
    for cfg_key, env_key in env_mappings:
        val = os.environ.get(env_key) or env.get(env_key)
        if val:
            config[cfg_key] = val

    safe_mode_val = os.environ.get("SAFE_MODE") or env.get("SAFE_MODE")
    if safe_mode_val:
        config["safe_mode"] = safe_mode_val.lower() in ("true", "1", "yes")

    return config


def save_config(config: Dict[str, Any]) -> None:
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4)
    except Exception as e:
        logger.error(f"Error saving config.json: {e}")


def get_config_for_client() -> Dict[str, Any]:
    """Return a masked version of config safe to send to frontend."""
    config = load_config()
    masked = config.copy()
    for key in ["client_secret", "refresh_token", "gemini_api_key", "developer_token",
                "google_client_secret", "google_developer_token", "meta_app_secret",
                "meta_system_user_token",
                "salesforce_client_secret", "salesforce_refresh_token", "leadsquared_secret_key",
                "mantri_salesforce_client_secret", "mantri_salesforce_refresh_token"]:
        val = masked.get(key, "")
        if val and len(val) > 4:
            masked[key] = "●●●●●●●●" + val[-4:]
        elif val:
            masked[key] = "●●●●●●●●"
    # Expose whether a system token is configured (not the value)
    masked["meta_system_user_token_configured"] = bool(masked.get("meta_system_user_token"))
    return masked
