"""Application-wide API access policy.

Exceptions match the registered route template and HTTP method exactly.
Machine callbacks still apply their own provider signature or scoped token checks.
"""

from fastapi import Depends, Request

from backend.routes.auth import get_current_user, require_admin_or_superadmin, require_user

PUBLIC_ROUTES = {
    ("GET", "/health"): "Railway liveness",
    ("GET", "/health/db"): "Minimal public status. The handler reserves diagnostics for admins.",
    ("POST", "/api/auth/login"): "Password exchange",
    ("GET", "/api/auth/onboarding-required"): "Landing page bootstrap discovery",
    ("POST", "/api/auth/onboard"): "First-user setup. The handler rejects this once a user exists.",
    ("POST", "/api/auth/signup-subscriber"): "Subscriber self-registration",
    ("GET", "/api/auth/onboard/{token}"): "Invitation or password-reset link validation",
    ("POST", "/api/auth/onboard/{token}"): "Invitation or password-reset completion",
    ("GET", "/api/oauth/google/callback"): "Google Ads OAuth redirect",
    ("GET", "/api/oauth/meta/callback"): "Meta Ads OAuth redirect",
    ("GET", "/api/adguard/oauth/callback"): "AdGuard Google Ads OAuth redirect",
    ("GET", "/api/adguard/oauth/meta/callback"): "AdGuard Meta OAuth redirect",
    ("GET", "/api/auth/gmail/callback"): "Gmail OAuth redirect",
    ("GET", "/api/crashclub/webhook"): "Meta subscription verification",
    ("POST", "/api/crashclub/webhook"): "Crash.club Meta lead delivery",
    ("GET", "/api/adguard/meta/webhook"): "Meta subscription verification",
    ("POST", "/api/adguard/meta/webhook"): "AdGuard Meta lead delivery",
    ("POST", "/api/adguard/webhook"): "Google Ads lead forms and bridge integrations",
    ("GET", "/api/v1/tag/adguard.js"): "Public AdGuard browser script",
    ("POST", "/api/v1/tag/session"): "Site-tag session",
    ("POST", "/api/v1/tag/verdict"): "Site-tag lead verdict",
    ("POST", "/api/v1/tag/otp"): "Site-tag OTP",
    ("POST", "/api/v1/webhooks/crm"): "CRM stage callback using a workspace secret",
    ("POST", "/api/v1/webhooks/botcaller"): "BotCaller callback using a workspace secret",
    ("POST", "/api/mini-robos/heartbeat"): "Mini Robos reporter key",
    ("POST", "/api/tlg/{account_id}/centres/{centre_id}/sheet-counts"): "Apps Script using a centre-scoped reporting token",
}

ADMIN_ROUTES = {
    "/api/config",
    "/api/config/meta-status",
    "/api/logs",
    "/api/audit-settings",
    "/api/audit-settings/{key}",
    "/api/campaign/{campaign_id}/budget",
    "/api/keyword/bid",
    "/api/seed-demo-accounts",
    "/api/revenueops/seed-demo",
    "/api/mantri/config",
}
ADMIN_PREFIXES = (
    "/api/auth/users",
    "/api/notification-settings",
)
ADMIN_WRITE_PREFIXES = ("/api/accounts", "/api/account-groups")
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _within(path, prefixes):
    return any(path == prefix or path.startswith(prefix + "/") for prefix in prefixes)


def authorize_api(request: Request, user=Depends(get_current_user)):
    # Route templates allow scoped callbacks without wildcard URL exceptions.
    route = request.scope.get("route")
    path = getattr(route, "path", request.url.path)
    if path != "/api" and not path.startswith("/api/"):
        return
    method = request.method
    if (method, path) in PUBLIC_ROUTES:
        return
    if (method, path) == ("GET", "/api/mini-robos/agents/{slug}/config"):
        return  # The private config handler requires a key scoped to the requested agent.
    require_user(user)
    if (path in ADMIN_ROUTES or _within(path, ADMIN_PREFIXES)
            or (method in WRITE_METHODS and _within(path, ADMIN_WRITE_PREFIXES))):
        require_admin_or_superadmin(user)
