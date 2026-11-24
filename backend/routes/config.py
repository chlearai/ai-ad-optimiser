from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict
from backend.services.config import CONFIG_SECRET_FIELDS, get_config_for_client, load_config, save_config

router = APIRouter(prefix="/api", tags=["config"])


class ConfigModel(BaseModel):
    developer_token: str = ""
    client_id: str = ""
    client_secret: str = ""
    refresh_token: str = ""
    customer_id: str = ""
    login_customer_id: str = ""
    gemini_api_key: str = ""
    safe_mode: bool = True
    # OAuth app credentials
    google_client_id: str = ""
    google_client_secret: str = ""
    google_developer_token: str = ""
    meta_app_id: str = ""
    meta_app_secret: str = ""
    redirect_base_url: str = ""
    # CRM integrations
    salesforce_url: str = ""
    salesforce_client_id: str = ""
    salesforce_client_secret: str = ""
    salesforce_refresh_token: str = ""
    leadsquared_access_key: str = ""
    leadsquared_secret_key: str = ""
    leadsquared_base_url: str = ""

    model_config = ConfigDict(extra="allow")


@router.get("/config")
def get_config():
    return get_config_for_client()


@router.get("/config/meta-status")
def get_meta_status():
    """Return whether the global Meta system user token is configured and shared app credentials."""
    cfg = load_config()
    return {
        "system_user_token_configured": bool(cfg.get("meta_system_user_token")),
        "meta_app_id": cfg.get("meta_app_id", ""),
        "meta_app_secret_configured": bool(cfg.get("meta_app_secret")),
    }


@router.post("/config")
def update_config(new_config: ConfigModel):
    updates = new_config.model_dump(exclude_unset=True)
    for field in CONFIG_SECRET_FIELDS:
        value = updates.get(field)
        if not value or "●●●●" in str(value):
            updates.pop(field, None)
    if updates:
        save_config(updates)
    return {"status": "success", "message": "Configuration updated successfully."}
