"""Capture AdGuard product screenshots for the management pitch doc."""
import os
import sys
import time
from playwright.sync_api import sync_playwright

BASE = "https://ai-ad-optimiser-production-dd12.up.railway.app"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "screenshots")
os.makedirs(OUT, exist_ok=True)

EMAIL = sys.argv[1] if len(sys.argv) > 1 else ""
PASSWORD = sys.argv[2] if len(sys.argv) > 2 else ""


def shot(page, name, full=False):
    page.screenshot(path=os.path.join(OUT, name), full_page=full)
    print("saved", name)


with sync_playwright() as p:
    browser = p.chromium.launch(headless=False, args=["--start-maximized"])
    ctx = browser.new_context(viewport={"width": 1600, "height": 900})
    page = ctx.new_page()

    # 1. Landing hero
    page.goto(BASE + "/")
    page.wait_for_load_state("networkidle")
    time.sleep(2)
    shot(page, "01_landing_hero.png")

    # 2. How-it-works section
    page.evaluate("document.getElementById('how-it-works')?.scrollIntoView()")
    time.sleep(1)
    shot(page, "02_how_it_works.png")

    # 3. Live stream demo cards
    page.evaluate("document.querySelectorAll('.stream-card')[0]?.scrollIntoView({block:'center'})")
    time.sleep(1)
    shot(page, "03_live_stream.png")

    # 4. Comparison table
    page.evaluate("document.querySelector('.col-adguard')?.scrollIntoView({block:'center'})")
    time.sleep(1)
    shot(page, "04_comparison_table.png")

    # 5. Pricing section
    page.evaluate("document.getElementById('pricing')?.scrollIntoView()")
    time.sleep(1)
    shot(page, "05_pricing.png")

    # 6. FAQ + ToS links
    page.evaluate("document.getElementById('faq')?.scrollIntoView()")
    time.sleep(1)
    shot(page, "06_faq.png")

    # Signup modal (with ToS checkbox)
    try:
        page.click("text=SIGN UP")
        time.sleep(1.2)
        shot(page, "07_signup_modal_tos.png")
        page.keyboard.press("Escape")
    except Exception as e:
        print("signup modal skip:", e)

    # Terms page
    page.goto(BASE + "/terms")
    page.wait_for_load_state("domcontentloaded")
    shot(page, "08_terms_v2.png")

    # Login page
    page.goto(BASE + "/adguard/login")
    page.wait_for_load_state("domcontentloaded")
    shot(page, "09_customer_login.png")

    # Workspace (requires login)
    if EMAIL and PASSWORD:
        page.fill("#custEmail", EMAIL)
        page.fill("#custPassword", PASSWORD)
        page.click("#custSubmitBtn")
        try:
            page.wait_for_url("**/adguard-workspace**", timeout=20000)
        except Exception:
            pass
        time.sleep(5)
        shot(page, "10_workspace_dashboard.png")
        page.evaluate("window.scrollTo(0,0)")
        # Connections tab
        page.evaluate("document.querySelectorAll('#tabNav .tab-item')[2]?.click()")
        time.sleep(2)
        shot(page, "11_workspace_connections.png")
        # Settings + CRM picker
        page.evaluate("document.querySelectorAll('#tabNav .tab-item')[4]?.click()")
        time.sleep(2)
        page.evaluate("document.getElementById('crmOptCrm')?.scrollIntoView({block:'center'})")
        shot(page, "12_settings_crm_cards.png")
        page.evaluate("openCrmPicker()")
        time.sleep(1)
        shot(page, "13_crm_picker.png")
        page.evaluate("closeCrmPicker()")
        # Quota + calls meters (sidebar)
        page.evaluate("document.querySelectorAll('#tabNav .tab-item')[0]?.click()")
        time.sleep(1)
        shot(page, "14_sidebar_meters.png")

    browser.close()
print("done ->", OUT)