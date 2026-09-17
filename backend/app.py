"""
AdOptima AI - Google Ads Optimization Assistant
FastAPI application entry point.
"""
import os
import logging
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import traceback

from backend.routes import config, campaigns, search_terms, negatives, optimizations, logs, chat, auth, reports, accounts, audits, notifications, oauth, crm, revenueops, dsu_report, dsi_report, mantri, voice, categories, activity_log, mis_mantri, salesforce_mantri
from backend.routes import crashclub
from backend.routes import adguard
from backend.routes import adguard_support

from backend.db.database import init_db
from backend.services.scheduler import start_scheduler, stop_scheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("AdOptima")

app = FastAPI(title="AdOptima AI - Google Ads Optimization")

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"UNHANDLED ERROR: {request.url}")
    logger.error(traceback.format_exc())
    print(f"UNHANDLED ERROR: {request.url}")
    print(traceback.format_exc())
    return JSONResponse(
        status_code=500,
        content={"detail": str(exc)}
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register API routes
app.include_router(config.router)
app.include_router(accounts.router)
app.include_router(categories.router)
app.include_router(campaigns.router)
app.include_router(search_terms.router)
app.include_router(negatives.router)
app.include_router(optimizations.router)
app.include_router(logs.router)
app.include_router(chat.router)
app.include_router(auth.router)
app.include_router(reports.router)
app.include_router(audits.router)
app.include_router(notifications.router)
app.include_router(oauth.router)
app.include_router(crm.router)
app.include_router(dsu_report.router)
app.include_router(dsi_report.router)
app.include_router(revenueops.router)
app.include_router(mantri.router)
app.include_router(mis_mantri.router)
app.include_router(salesforce_mantri.router)
app.include_router(voice.router)
app.include_router(activity_log.router)
app.include_router(crashclub.router)
app.include_router(adguard.router)
app.include_router(adguard_support.router)


# Initialize database tables only at import time; scheduler starts lazily on first request
init_db()
_scheduler_started = False


def ensure_scheduler():
    global _scheduler_started
    if not _scheduler_started:
        try:
            start_scheduler()
            _scheduler_started = True
        except Exception as e:
            logger.error(f"Failed to start scheduler: {e}")


@app.middleware("http")
async def lazy_start_scheduler(request, call_next):
    ensure_scheduler()
    return await call_next(request)


@app.get("/health")
def health_check():
    return {"status": "ok", "port": os.getenv("PORT", "8000")}


@app.get("/india_states.js")
def get_india_states_js():
    frontend_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend")
    js_path = os.path.join(frontend_dir, "india_states.js")
    if os.path.exists(js_path):
        return FileResponse(js_path, media_type="application/javascript")
    return JSONResponse(status_code=404, content={"detail": "india_states.js not found"})


@app.get("/favicon.ico")
def get_favicon():
    frontend_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend")
    favicon_path = os.path.join(frontend_dir, "favicon.ico")
    if os.path.exists(favicon_path):
        return FileResponse(favicon_path)
    # Return a 1x1 transparent GIF if no favicon exists to avoid 404 noise
    return HTMLResponse(content=b"GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;", media_type="image/gif")


@app.get("/", response_class=HTMLResponse)
def get_landing(request: Request):
    frontend_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend")
    html_path = os.path.join(frontend_dir, "landing.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>ChlearSakhaaOps AI landing page not found.</h1>")


@app.get("/adpulse", response_class=HTMLResponse)
def get_ui(request: Request):
    frontend_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend")
    html_path = os.path.join(frontend_dir, "index.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>AdPulse UI not found.</h1>")


@app.get("/insightdesk", response_class=HTMLResponse)
def get_mis_ui(request: Request):
    frontend_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend")
    html_path = os.path.join(frontend_dir, "mis.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>InsightDesk UI not found.</h1>")


@app.get("/revenueops", response_class=HTMLResponse)
def get_revenueops_ui(request: Request):
    frontend_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend")
    html_path = os.path.join(frontend_dir, "revenueops.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>RevenueOps UI not found.</h1>")


@app.get("/adguard", response_class=HTMLResponse)
def get_adguard_ui(request: Request):
    frontend_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend")
    html_path = os.path.join(frontend_dir, "adguard.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>AdGuard UI not found.</h1>")


@app.get("/adguard-landing", response_class=HTMLResponse)
@app.get("/adguard/landing", response_class=HTMLResponse)
def get_adguard_landing(request: Request):
    frontend_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend")
    html_path = os.path.join(frontend_dir, "adguard_landing.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>AdGuard Landing Page not found.</h1>")


@app.get("/adguard-admin-login", response_class=HTMLResponse)
def get_adguard_admin_login(request: Request):
    frontend_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend")
    html_path = os.path.join(frontend_dir, "adguard_admin_login.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>AdGuard Admin Login not found.</h1>")


@app.get("/adguard-workspace", response_class=HTMLResponse)
def get_adguard_workspace(request: Request):
    frontend_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend")
    html_path = os.path.join(frontend_dir, "adguard_workspace.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>AdGuard Workspace Page not found.</h1>")


@app.get("/integrations", response_class=HTMLResponse)
def get_integrations_ui(request: Request):
    frontend_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend")
    html_path = os.path.join(frontend_dir, "integrations.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>Integrations UI not found.</h1>")


@app.get("/onboard", response_class=HTMLResponse)
@app.get("/onboard.html", response_class=HTMLResponse)
def get_onboard_ui(request: Request):
    frontend_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend")
    html_path = os.path.join(frontend_dir, "onboard.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>Onboarding UI not found.</h1>")


@app.get("/terms", response_class=HTMLResponse)
def get_terms(request: Request):
    return HTMLResponse(content="""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Terms of Service - AdGuard</title>
    <style>body{font-family:Arial,sans-serif;max-width:800px;margin:2rem auto;padding:0 1rem;line-height:1.6;color:#333}</style>
</head>
<body>
    <h1>Terms of Service</h1>
    <p><strong>AdGuard</strong> (&ldquo;AdGuard&rdquo;, &ldquo;we&rdquo;, &ldquo;us&rdquo;) is a lead-quality protection service operated by ChlearSakhaaOps AI. By creating an account or connecting an advertising account, you agree to these terms.</p>
    <h2>1. The Service</h2>
    <p>AdGuard connects to your Google Ads and/or Meta Ads accounts (via your authorisation) to audit incoming leads, flag low-quality or fraudulent leads, exclude flagged audiences from future ad delivery, and produce reports on wasted ad spend.</p>
    <h2>2. Your Account</h2>
    <p>You are responsible for the accuracy of your registration details and for keeping your credentials secure. Accounts are provided per subscription plan with defined lead-volume limits; exceeding plan limits may result in throttling or upgrade prompts.</p>
    <h2>3. Platform Authorisations</h2>
    <p>You grant AdGuard permission to access only the advertising data needed to run the service (campaigns, lead forms, audience lists). You may revoke access at any time, either from AdGuard or directly in Google / Meta account settings. Revocation stops data processing for your workspace.</p>
    <h2>4. Acceptable Use</h2>
    <p>You may not use AdGuard to process data you are not legally permitted to process, to attempt unauthorised access to other workspaces, or to reverse-engineer or resell the service.</p>
    <h2>5. Automated Actions</h2>
    <p>Where you enable Money Shield automated protections (campaign pausing, exclusion syncing), AdGuard may take actions in your connected ad accounts on your behalf. You can disable automation at any time in Settings.</p>
    <h2>6. Billing &amp; Plans</h2>
    <p>Paid plans are billed per the pricing shown at sign-up. Trials convert only with your explicit confirmation. Refunds, where applicable, are handled case-by-case — contact us below.</p>
    <h2>7. Disclaimer</h2>
    <p>AdGuard provides lead-scoring based on heuristics and platform data. Scores are informational; we do not guarantee that every flagged lead is junk or that every passed lead is genuine, and we are not liable for advertising spend outcomes.</p>
    <h2>8. Limitation of Liability</h2>
    <p>To the maximum extent permitted by law, our aggregate liability is limited to the fees you paid us in the three (3) months preceding the claim.</p>
    <h2>9. Termination</h2>
    <p>You may stop using AdGuard and delete your account at any time. We may suspend accounts that violate these terms or applicable law.</p>
    <h2>10. Governing Law</h2>
    <p>These terms are governed by the laws of India, with courts in Bengaluru, Karnataka having exclusive jurisdiction.</p>
    <h2>11. Contact</h2>
    <p>Questions: <a href="mailto:shekharraju6@gmail.com">shekharraju6@gmail.com</a></p>
    <p style="margin-top:2rem;color:#666;font-size:0.9rem;">Last updated: September 2026</p>
</body>
</html>""")


@app.get("/privacy", response_class=HTMLResponse)
def get_privacy(request: Request):
    return HTMLResponse(content="""<!DOCTYPE html>
<html lang=\"en\">
<head>
    <meta charset=\"UTF-8\">
    <title>Privacy Policy - ChlearSakhaaOps AI</title>
    <style>body{font-family:Arial,sans-serif;max-width:800px;margin:2rem auto;padding:0 1rem;line-height:1.6;color:#333}</style>
</head>
<body>
    <h1>Privacy Policy</h1>
    <p><strong>ChlearSakhaaOps AI</strong> (&ldquo;we&rdquo;, &ldquo;us&rdquo;, or &ldquo;our&rdquo;) operates the ChlearSakhaaOps AI advertising-optimisation platform.</p>
    <h2>1. Information We Collect</h2>
    <p>We collect information you provide when registering an account, connecting advertising accounts (Google Ads, Meta Ads), and configuring integrations. This may include account IDs, OAuth tokens, campaign metrics, and CRM data.</p>
    <h2>2. How We Use Information</h2>
    <p>We use the information to provide optimisation recommendations, reports, dashboards, notifications, and to keep connected accounts synchronised. For AdGuard workspaces, this includes scoring incoming leads for quality, flagging suspected junk/fraudulent leads, and pushing flagged audiences to your connected ad platforms (Google Customer Match, Meta Custom Audiences) as exclusions — only within accounts you have authorised.</p>
    <h2>3. Data Sharing</h2>
    <p>We do not sell personal information. Data is shared only with the advertising platforms and CRM systems you authorise (Google, Meta, LeadSquared, Salesforce, HubSpot, Zoho, etc.).</p>
    <h2>4. Data Security</h2>
    <p>OAuth tokens and credentials are encrypted at rest. Access to the platform is protected by authentication and authorisation controls.</p>
    <h2>5. Your Rights</h2>
    <p>You may disconnect advertising accounts, delete your account, or contact us to request data deletion.</p>
    <h2>6. Changes</h2>
    <p>We may update this policy. Continued use after changes constitutes acceptance.</p>
    <h2>7. Contact</h2>
    <p>For privacy questions, contact <a href=\"mailto:shekharraju6@gmail.com\">shekharraju6@gmail.com</a>.</p>
    <p style=\"margin-top:2rem;color:#666;font-size:0.9rem;\">Last updated: June 2026</p>
</body>
</html>""")


if __name__ == "__main__":
    import uvicorn
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("app:app", host=host, port=port, reload=True)
