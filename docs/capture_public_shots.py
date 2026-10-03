"""Re-capture: AdGuard landing (correct URL) + signup modal + admin cockpit."""
import os
import sys
import time
from playwright.sync_api import sync_playwright

BASE = "https://ai-ad-optimiser-production-dd12.up.railway.app"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "screenshots")
os.makedirs(OUT, exist_ok=True)

ADMIN_EMAIL = "admin@adoptima.ai"
ADMIN_PASSWORD = os.environ.get("ADMIN_PW", "")


def shot(page, name, full=False):
    page.screenshot(path=os.path.join(OUT, name), full_page=full)
    print("saved", name)


with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    ctx = browser.new_context(viewport={"width": 1600, "height": 900})
    page = ctx.new_page()

    # AdGuard landing hero (correct route)
    page.goto(BASE + "/adguard-landing")
    page.wait_for_load_state("networkidle")
    time.sleep(2)
    shot(page, "01_landing_hero.png")

    # 2. How-it-works
    page.evaluate("document.getElementById('how-it-works')?.scrollIntoView()")
    time.sleep(1)
    shot(page, "02_how_it_works.png")

    # 3. Live stream cards
    page.evaluate("document.querySelectorAll('.stream-card')[0]?.scrollIntoView({block:'center'})")
    time.sleep(1)
    shot(page, "03_live_stream.png")

    # 4. Comparison table
    page.evaluate("document.querySelector('.col-adguard')?.scrollIntoView({block:'center'})")
    time.sleep(1)
    shot(page, "04_comparison_table.png")

    # 5. Pricing
    page.evaluate("document.getElementById('pricing')?.scrollIntoView()")
    time.sleep(1)
    shot(page, "05_pricing.png")

    # 6. FAQ
    page.evaluate("document.getElementById('faq')?.scrollIntoView()")
    time.sleep(1)
    shot(page, "06_faq.png")

    # 7. Signup modal (nav button label may be 'SIGN UP' inside nav)
    try:
        page.evaluate("[...document.querySelectorAll('button')].find(b => /sign up/i.test(b.innerText))?.click()")
        time.sleep(1.5)
        shot(page, "07_signup_modal_tos.png")
    except Exception as e:
        print("signup modal skip:", e)

    # Academy + Setup guide (public lock check skipped; capture gated content via login storage)
    page.goto(BASE + "/adguard/academy")
    time.sleep(1.5)
    shot(page, "15_academy_locked.png")

    browser.close()
print("done")