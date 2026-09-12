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
    <p>We use the information to provide optimisation recommendations, reports, dashboards, notifications, and to keep connected accounts synchronised.</p>
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
