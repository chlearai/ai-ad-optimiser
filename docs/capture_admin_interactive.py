"""Admin screenshots v4 — waits on wall-clock instead of input()."""
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
    print("WINDOW OPEN — password was auto-filled by the page button. Waiting 90s; log in now if needed...")
    time.sleep(90)  # window for you to click "Fill Admin Demo Credentials" + Sign In
    page.goto(BASE + "/adguard")
    time.sleep(7)
    shot(page, "16_admin_cockpit_dashboard.png")
    page.goto(BASE + "/adguard?view=subscribers")
    time.sleep(5)
    shot(page, "17_admin_subscribers.png")
    browser.close()
print("done")