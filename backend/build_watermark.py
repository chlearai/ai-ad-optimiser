"""
Build/deploy helper: serves as the A1 IP-protection step.

WHAT IT DOES
1. Injects an invisible per-build fingerprint (watermark) into every frontend page —
   court-usable evidence that a copied page originated from this build.
2. Optionally minifies served JS/CSS (strip comments + collapse whitespace)
   into frontend/min/ which app.py never serves directly (manual swap if wanted;
   minification OFF by default to avoid breaking inline scripts — enable when
   frontend is bundled into static assets).

Usage:
    python backend/build_watermark.py            # inject watermark into pages
    python backend/build_watermark.py --minify   # also write minified copies to frontend/min/
"""
import base64
import glob
import hashlib
import os
import re
import sys

FRONTEND_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend")
BUILD_ID_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".build_id")


def build_id() -> str:
    if os.path.exists(BUILD_ID_FILE):
        with open(BUILD_ID_FILE) as f:
            return f.read().strip()
    bid = base64.b32encode(hashlib.sha256(os.urandom(16)).digest()).decode()[:16].lower()
    with open(BUILD_ID_FILE, "w") as f:
        f.write(bid)
    return bid


WATERMARK_TEMPLATE = '<span data-b="{bid}" data-c="CHL-MSPL-BLR" style="position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);opacity:0.01;" aria-hidden="true"></span>'


def inject_watermark():
    bid = build_id()
    tag = WATERMARK_TEMPLATE.format(bid=bid)
    pages = glob.glob(os.path.join(FRONTEND_DIR, "adguard_*.html")) + [os.path.join(FRONTEND_DIR, "verify.html")]
    changed = 0
    for path in pages:
        with open(path, "r", encoding="utf-8") as f:
            html = f.read()
        if f'data-b="{bid}"' in html:
            continue
        # replace any older watermark, then inject before </body>
        html = re.sub(r'<span data-b="[^"]*" data-c="CHL-MSPL-BLR"[^>]*></span>', "", html)
        html = html.replace("</body>", tag + "\n</body>")
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
        changed += 1
    print(f"watermark {bid} injected into {changed} pages")
    return bid


def minify_copy():
    out_dir = os.path.join(FRONTEND_DIR, "min")
    os.makedirs(out_dir, exist_ok=True)
    for path in glob.glob(os.path.join(FRONTEND_DIR, "*.html")):
        name = os.path.basename(path)
        with open(path, "r", encoding="utf-8") as f:
            html = f.read()
        html = re.sub(r"/\*[\s\S]*?\*/", "", html)          # CSS comments
        html = re.sub(r"^\s*//[^\n]*$", "", html, flags=re.M)  # line comments (safe: full-line only)
        html = re.sub(r"\n\s+", "\n", html)                  # leading indent
        with open(os.path.join(out_dir, name), "w", encoding="utf-8") as f:
            f.write(html)
        print("minified copy:", name)


if __name__ == "__main__":
    inject_watermark()
    if "--minify" in sys.argv:
        minify_copy()