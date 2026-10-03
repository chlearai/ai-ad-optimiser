"""Admin cockpit screenshots v2: explicit login."""
import os
import time
from playwright.sync_api import sync_playwright

BASE = "https://ai-ad-optimiser-production-dd12.up.railway.app"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "screenshots")


def shot(page, name, full=False):
    page.screenshot(path=os.path.join(OUT, name), full_page=full)
    print("saved", name)


with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    ctx = browser.new_context(viewport={"width": 1600, "height": 900})
    page = ctx.new_page()

    page.goto(BASE + "/adguard-admin-login")
    page.wait_for_load_state("networkidle")
    time.sleep(2)
    page.fill("#adminEmail", "admin@adoptima.ai")
    page.fill("#adminPassword", "Admin@123")
    page.click("#adminSubmitBtn")
    try:
        page.wait_for_url("**/adguard**", timeout=25000)
    except Exception as e:
        print("nav:", e)
    time.sleep(7)
    shot(page, "16_admin_cockpit_dashboard.png")
    page.evaluate("[...document.querySelectorAll('button, .tab-item, a, div')].filter(b => /subscriber/i.test(b.innerText || b.textContent || '').slice(0,40) && (b.innerText||'').length < 60)[0]?.click()")
    time.sleep(4)
    shot(page, "17_admin_subscribers.png")
    browser.close()
print("done")