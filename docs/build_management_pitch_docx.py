"""
Generates the management pitch word document:
  docs/AdGuard_Management_Proposal_Oct2026.docx

Run:  uv run python docs/build_management_pitch_docx.py
"""
import os
from docx import Document
from docx.shared import Pt, Inches, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml.ns import qn

HERE = os.path.dirname(os.path.abspath(__file__))
SHOTS = os.path.join(HERE, "screenshots")
OUT = os.path.join(HERE, "AdGuard_Management_Proposal_Oct2026.docx")

AG_ORANGE = RGBColor(0xD9, 0x77, 0x06)
DARK = RGBColor(0x1C, 0x19, 0x17)
GREEN = RGBColor(0x0A, 0x7D, 0x4F)
GREY = RGBColor(0x57, 0x53, 0x4E)


def set_cell_bg(cell, hexcolor):
    tcPr = cell._tc.get_or_add_tcPr()
    shd = tcPr.makeelement(qn("w:shd"), {qn("w:val"): "clear", qn("w:fill"): hexcolor})
    tcPr.append(shd)


def style_base(doc):
    st = doc.styles["Normal"]
    st.font.name = "Calibri"
    st.font.size = Pt(10.5)
    st.font.color.rgb = DARK
    for i, sz in [(1, 17), (2, 13.5), (3, 11.5)]:
        h = doc.styles[f"Heading {i}"]
        h.font.name = "Calibri"
        h.font.size = Pt(sz)
        h.font.color.rgb = AG_ORANGE
        h.font.bold = True


def para(doc, text, bold=False, size=10.5, color=None, align=None, space_after=6):
    p = doc.add_paragraph()
    r = p.add_run(text)
    r.bold = bold
    r.font.size = Pt(size)
    if color:
        r.font.color.rgb = color
    if align:
        p.alignment = align
    p.paragraph_format.space_after = Pt(space_after)
    return p


def bullets(doc, items, bold_prefix=None):
    for it in items:
        p = doc.add_paragraph(style="List Bullet")
        if bold_prefix and it.startswith(bold_prefix):
            r = p.add_run(bold_prefix)
            r.bold = True
            p.add_run(it[len(bold_prefix):])
        else:
            p.add_run(it)
        p.paragraph_format.space_after = Pt(2)


def add_table(doc, headers, rows, widths=None, header_bg="D97706"):
    t = doc.add_table(rows=1 + len(rows), cols=len(headers))
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    for j, h in enumerate(headers):
        c = t.rows[0].cells[j]
        c.text = ""
        r = c.paragraphs[0].add_run(h)
        r.bold = True
        r.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
        r.font.size = Pt(9.5)
        set_cell_bg(c, header_bg)
    for i, row in enumerate(rows):
        for j, val in enumerate(row):
            c = t.rows[i + 1].cells[j]
            c.text = ""
            r = c.paragraphs[0].add_run(str(val))
            r.font.size = Pt(9.5)
            if i % 2 == 1:
                set_cell_bg(c, "FAF5EF")
    if widths:
        for j, w in enumerate(widths):
            for row in t.rows:
                row.cells[j].width = Inches(w)
    doc.add_paragraph().paragraph_format.space_after = Pt(4)
    return t


def shot(doc, filename, caption, width=6.4):
    path = os.path.join(SHOTS, filename)
    if not os.path.exists(path):
        para(doc, f"[screenshot: {filename}]", color=GREY)
        return
    doc.add_picture(path, width=Inches(width))
    doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
    para(doc, caption, size=8.5, color=GREY, align=WD_ALIGN_PARAGRAPH.CENTER, space_after=10)


def build():
    doc = Document()
    style_base(doc)
    for sec in doc.sections:
        sec.top_margin = Inches(0.7)
        sec.bottom_margin = Inches(0.7)
        sec.left_margin = Inches(0.85)
        sec.right_margin = Inches(0.85)

    # ---------- COVER ----------
    para(doc, "CONFIDENTIAL — FOR MANAGEMENT REVIEW", bold=True, size=9, color=GREY, align=WD_ALIGN_PARAGRAPH.RIGHT)
    para(doc, "", space_after=80)
    para(doc, "AdGuard", bold=True, size=40, color=AG_ORANGE, align=WD_ALIGN_PARAGRAPH.CENTER, space_after=4)
    para(doc, "Stop paying for garbage leads.", bold=True, size=16, color=DARK, align=WD_ALIGN_PARAGRAPH.CENTER, space_after=8)
    para(doc, "Market-Entry & Payment-Activation Proposal", size=14, color=GREY, align=WD_ALIGN_PARAGRAPH.CENTER, space_after=30)
    para(doc, "Prepared by: Shekhar Raju  ·  CHLEAR (CHL Marketing Solutions Pvt. Ltd.)", size=10, color=GREY, align=WD_ALIGN_PARAGRAPH.CENTER, space_after=2)
    para(doc, "October 2026  ·  Version 1.0  ·  Beta live with subscribers", size=10, color=GREY, align=WD_ALIGN_PARAGRAPH.CENTER, space_after=60)
    para(doc, "THE ASK: Approve Razorpay LIVE activation so paying customers can check out on October 10.",
         bold=True, size=12, color=GREEN, align=WD_ALIGN_PARAGRAPH.CENTER)
    doc.add_page_break()

    # ---------- 1. EXECUTIVE SUMMARY ----------
    doc.add_heading("1. Executive Summary", level=1)
    para(doc, "AdGuard is a lead-integrity SaaS for businesses advertising on Google and Meta. It connects to the "
              "customer's own ad accounts, audits every incoming lead in real time, blocks bots and junk before they "
              "reach the CRM or poison the ad platforms' learning, and proves the recovered money in rupees on a "
              "customer dashboard.")
    para(doc, "Where we are today:", bold=True, space_after=2)
    bullets(doc, [
        "Platform built and live in production (Railway + Supabase/Postgres).",
        "Self-serve signup live: customer signs up, verifies email, sets password, gets workspace in under 5 minutes.",
        "Trial + Starter + Pro + Enterprise plans provisioned automatically; plan rules enforced in-product.",
        "Beta subscribers onboarded and using workspaces; welcome/verify email journey working end-to-end.",
        "Legal base live: Terms of Service v2.0 (audit disclaimer, liability cap, IP protection) accepted at signup.",
        "20-CRM delivery catalog, Money Shield, Script Tag interceptor, admin cockpit all shipped.",
    ])
    para(doc, "What is missing:", bold=True, space_after=2)
    para(doc, "Payments. Plan selection currently works but the payment step is bypassed for beta — a customer "
              "clicking “Choose Starter” gets the workspace without paying. Razorpay LIVE activation is the single "
              "remaining blocker between the product and its first rupee of revenue.")
    add_table(doc,
              ["Launch date", "The blocker", "The ask"],
              [["October 10, 2026", "Razorpay LIVE not yet activated", "Approve Razorpay LIVE + KYC/bank activation now"]],
              widths=[2.0, 2.6, 2.6])
    doc.add_page_break()

    # ---------- 2. PRODUCT ----------
    doc.add_heading("2. What AdGuard Does (Product Walkthrough)", level=1)
    para(doc, "The journey: marketer connects Google/Meta via official OAuth → leads flow into AdGuard's audit "
              "pipeline → each lead is device-fingerprinted, deduplicated, geo-checked, email/phone validated and "
              "AI-scored in ~2 seconds → verified leads reach the CRM, junk leads are blocked with evidence → "
              "Money Shield auto-pauses campaigns that attract junk → weekly reports show recovered spend in ₹.")

    shot(doc, "01_landing_hero.png", "Fig 2.1 — Public landing page (ai-ad-optimiser-production-dd12.up.railway.app/adguard-landing)")
    shot(doc, "02_how_it_works.png", "Fig 2.2 — How it works: connect → screen → shield → CRM")
    shot(doc, "03_live_stream.png", "Fig 2.3 — Live lead stream demo on the landing page")
    shot(doc, "10_workspace_dashboard.png", "Fig 2.4 — Customer workspace: starter plan quota meter, KPIs, live feed, Money Shield")
    shot(doc, "11_workspace_connections.png", "Fig 2.5 — Connections tab: OAuth connectors + campaign screening")
    shot(doc, "12_settings_crm_cards.png", "Fig 2.6 — Settings: CRM delivery choice (None / Deliver to a CRM)")
    shot(doc, "13_crm_picker.png", "Fig 2.7 — 20-CRM picker with real logos: 4 direct integrations, 16 via universal webhook")
    shot(doc, "17_admin_subscribers.png", "Fig 2.8 — Admin cockpit: subscriber & plan management, call credits, embed tag")

    doc.add_heading("2.1 Feature maturity (honest status)", level=2)
    add_table(doc, ["Capability", "Status"],
              [["Lead audit pipeline (Google + Meta webhooks, AI scoring, dedupe, geo, validation)", "LIVE"],
               ["Self-serve signup + email journey (verify → password → welcome)", "LIVE"],
               ["Plans, monthly hard-stop quotas, trial 14-day expiry", "LIVE"],
               ["CRM delivery (20 CRMs incl. LeadSquared, Zoho, HubSpot direct)", "LIVE"],
               ["Money Shield monitor/protect + auto-pause governor", "LIVE"],
               ["Pre-Submit Interceptor script tag (fail-open, 2.0s)", "LIVE"],
               ["Admin cockpit (subscribers, plans, quotas, call credits, simulate)", "LIVE"],
               ["Prepaid call-verification credits (Pro 300 / Enterprise 500)", "METERED (Samvaad debit pending)"],
               ["AI call verification (Samvaad integration)", "NEXT (Q4)"],
               ["WhatsApp OTP step-up", "PARKED (clients run OTP in own forms)"],
               ["Bot Traps", "PARKED (Enterprise roadmap)"],
               ["Invalid-click evidence CSV writer", "SCOPED (approved, in build)"]],
              widths=[5.4, 1.8])
    doc.add_page_break()

    # ---------- 3. MARKET ----------
    doc.add_heading("3. The Problem & Market", level=1)
    para(doc, "Industry studies consistently estimate 15–30% of paid-search and paid-social leads are never real "
              "buyers — bots, competitors, form-spam, duplicates, and unqualified clicks. Indian lead-gen verticals "
              "(education, real estate, healthcare, BFSI, hyperlocal) run on form-fills, making them the most "
              "exposed. The advertiser pays the platform for every one of these, then pays a sales team to call them.")
    bullets(doc, [
        "Nobody audits lead quality at capture time — platforms optimise on volume, agencies optimise on CPL, and junk flows through the entire funnel.",
        "Worse: when Meta/Google “learn” from fake leads, their optimisation findsmore fake leads — the waste compounds every week.",
        "Existing click-fraud tools (ClickCease, Lunio, TrafficGuard) block clicks — but none of them audit the LEADS arriving via your own ad accounts, score them, and push exclusion audiences back to the platforms.",
    ])
    para(doc, "AdGuard's wedge: we sit inside the customer's own ad accounts, audit the leads, block the junk, and "
              "make the platforms buy better. One mid-size Meta lead-gen client wastes an estimated ₹20,000–50,000 "
              "per month on junk; AdGuard Starter costs ₹4,999. The product sells itself with arithmetic.")
    doc.add_page_break()

    # ---------- 4. PLANS ----------
    doc.add_heading("4. Plans & Pricing", level=1)
    add_table(doc, ["Plan", "Price/mo", "Lead audits", "AI calls", "Google+Meta", "Workspaces"],
              [["Shadow (Free trial)", "₹0 · 14 days", "300 total, hard stop", "—", "1 + 1", "1"],
               ["Starter", "₹4,999", "1,000/month", "—", "1 + 1", "1"],
               ["Pro", "₹14,999", "5,000/month", "300 included", "1 + 1", "1"],
               ["Enterprise", "₹39,999", "Unlimited", "500 included", "2 + 2", "10 brand workspaces"]],
              widths=[1.6, 1.2, 1.4, 1.2, 1.0, 1.6])
    shot(doc, "05_pricing.png", "Fig 4.1 — Public pricing table as customers see it")
    para(doc, "Policy design (already enforced in-product):", bold=True, space_after=2)
    bullets(doc, [
        "Lead quotas are hard stops with monthly reset — unused quota does not carry over (capacity provision, industry standard).",
        "AI call credits are a prepaid bucket that NEVER expires — ethical and defensible: credits carry across renewals; customers top up 100/₹799, 300/₹1,999, 500/₹2,999 packs.",
        "Trial clock starts at password activation (not signup) — no lost trial days to slow email verification.",
        "Connection limits (1 Google + 1 Meta) enforced at OAuth; upsell path to Enterprise is in-product.",
    ])
    shot(doc, "07_signup_modal_tos.png", "Fig 4.2 — Signup modal with mandatory ToS acceptance (legal cover built into the flow)")
    doc.add_page_break()

    # ---------- 5. UNIT ECONOMICS ----------
    doc.add_heading("5. Unit Economics (Full View)", level=1)
    para(doc, "Costs are real production numbers (Railway + Supabase + gateway fees). No item is hidden.", space_after=8)
    doc.add_heading("5.1 Gross margin per subscriber", level=2)
    add_table(doc, ["Plan", "Price", "Variable cost*", "Gross ₹", "Gross %"],
              [["Starter (1,000 leads)", "₹4,999", "~₹300", "~₹4,700", "~94%"],
               ["Pro (5,000 leads + 300 calls)", "₹14,999", "~₹600", "~₹14,400", "~96%"],
               ["Enterprise (10 ws + 500 calls)", "₹39,999", "~₹2,500", "~₹37,500", "~94%"]],
              widths=[2.4, 1.1, 1.4, 1.2, 1.0])
    para(doc, "*Variable = compute, storage growth, 30 min support. Gateway adds 2% of revenue (Razorpay). "
              "AI-call cost sits inside the plans' call bundles (Samvaad ~₹2–4/call; prepaid packs retail at "
              "₹6–8/call — 33–50% margin on top-ups).", size=9, color=GREY)
    doc.add_heading("5.2 Fixed infrastructure", level=2)
    add_table(doc, ["Item", "₹/month"],
              [["Railway (app + scheduler, 24/7)", "2,000–3,500"],
               ["Supabase Postgres (free tier → Pro at growth)", "0–2,100"],
               ["Domain + SSL + email", "~100"],
               ["Total fixed", "≈ ₹3,000–5,000"]],
              widths=[4.4, 1.8])
    doc.add_heading("5.3 P&L at the 60-day post-launch state", level=2)
    add_table(doc, ["Line", "₹/month"],
              [["Revenue: 10 Starter + 4 Pro + 2 Enterprise", "1,89,984"],
               ["Infrastructure (scaled)", "(8,000)"],
               ["Gateway 2%", "(3,800)"],
               ["Support/dev salary", "(60,000)"],
               ["Operating profit", "≈₹1,18,000 → ~62% net margin"]],
              widths=[4.4, 1.8])
    add_table(doc, ["Break-even milestones", "Value"],
              [["Fixed infra break-even", "1 Starter subscriber"],
               ["True break-even (with salary)", "6–8 subscribers (~₹65–70k MRR)"],
               ["Before that", "Founder-run: 1–2 subscribers = already cash-positive"]],
              widths=[3.4, 2.8])
    doc.add_page_break()

    # ---------- 6. RAZORPAY ----------
    doc.add_heading("6. Why Razorpay Now (The Core Ask)", level=1)
    para(doc, "Today the product has no payment path by design (beta). A customer choosing a paid plan gets the "
              "workspace, but the payment step is bypassed. This was correct for beta — beta feedback without "
              "purchase friction — but it means the funnel stops exactly where revenue starts.")
    doc.add_heading("6.1 What Razorpay LIVE unlocks", level=2)
    bullets(doc, [
        "Checkout: customer picks Starter/Pro/Enterprise → Razorpay (UPI, cards, netbanking, wallets) → subscription active instantly, no manual UPI transfers and no support tickets.",
        "Automatic provisioning: plan + quota + call credits applied the moment payment succeeds (webhook-driven).",
        "Prepaid call-pack purchases inside the workspace (Buy Calls → Razorpay checkout → instant credit).",
        "Subscriptions with auto-recharge links for quota top-ups later, recurring billing at renewal.",
    ])
    doc.add_heading("6.2 Gateway comparison", level=2)
    add_table(doc, ["Gateway", "Fees (Indian cards)", "Settlement", "Why/why not"],
              [["Razorpay", "~2% + GST", "T+2 days", "Best developer docs, subscriptions, payment pages; industry standard"],
               ["Cashfree", "~1.75–2%", "T+1", "Cheaper, fewer product features (links/subscriptions weaker)"],
               ["PayU", "~2% + GST", "T+2", "Comparable; docs and support weaker for startups"]],
              widths=[1.2, 1.4, 1.0, 3.6])
    para(doc, "Recommendation: Razorpay. Fees are standard, the subscription & payment-page APIs are the strongest, "
              "and the code integration is a one-day change on the pre-built branch.")
    doc.add_heading("6.3 Activation steps (owner actions)", level=2)
    add_table(doc, ["Step", "Owner", "Time"],
              [["KYC on Razorpay dashboard (PAN, GST, bank account of CHLEAR)", "Management + Shekhar", "Same day (docs ready)"],
               ["Business verification review by Razorpay", "Razorpay", "2–7 working days"],
               ["Bank settlement activation", "Razorpay", "With activation"],
               ["Flip code to LIVE keys (env var change + deploy)", "Shekhar/agent", "1 hour"]],
              widths=[3.8, 1.8, 1.4])
    para(doc, "Activation is calendar-time, not engineering-time. Every day waiting is revenue not collected.")
    doc.add_page_break()

    # ---------- 7. GTM ----------
    doc.add_heading("7. Launch Plan (October 10)", level=1)
    bullets(doc, [
        "Warm outreach: every agency/ad-ops contact across the team — personal WhatsApp, free 2-week trial.",
        "chlear.in's own client base: present AdGuard in existing client reviews (highest-conversion channel).",
        "LinkedIn launch sequence: the ₹ waste story, the fraud-graph explainer, the launch post.",
        "Performance-marketing communities (India): share the CHLEAR case numbers.",
        "Launch offer: first 10 subscribers get an early-adopter discount on annual commitment.",
        "15-min demo promise: every demo request triggers an instant alert with a callback playbook — response inside 15 minutes is the differentiator.",
        "Subscriber enablement already shipped: gated AdGuard Academy (8 lessons) + 10-step Setup Guide — reduces support load from day one.",
    ])
    add_table(doc, ["Weekly KPI board (from admin dashboard)", "Target by Nov 30"],
              [["Trial → paid conversion", "> 25%"],
               ["MRR", "₹1,00,000 by Jan (stated in ask)"],
               ["Leads audited (cumulative)", "50,000"],
               ["Junk rate found (proof of value)", "> 15%"],
               ["₹ recovered (customer-visible)", "₹15–20L cumulative"]],
              widths=[4.0, 2.4])
    doc.add_page_break()

    # ---------- 8. RISKS ----------
    doc.add_heading("8. Risks & Mitigations", level=1)
    add_table(doc, ["Risk", "Mitigation (in place / planned)"],
              [["Subscriber legal claim: “you didn't really audit”", "ToS v2.0 §2: audit = automated algorithmic scoring via official APIs, explicitly not human verification; no detection guarantees; accuracy claims are targets, not contracts. Acceptance recorded per user with version + timestamp."],
               ["Ad platform refund decisions", "ToS §2: refunds are at the platform's sole discretion; AdGuard only facilitates evidence + claim files."],
               ["Liability exposure", "ToS §12: liability capped at 3 months' fees; no indirect damages."],
               ["Free-trial farming / abuse", "Signup abuse guard live (phone/company pattern detection, admin flags); hard-stop quotas cap any abuse at 300 leads."],
               ["Code/IP theft (competitor clones the app)", "ToS §6 prohibits reverse-engineering/derivatives (damages + injunction). Per-build invisible watermarks embedded in every page; proprietary notices in footers; repos private; legal base to act."],
               ["Email deliverability (Gmail relay ceiling)", "Already observed: personal Gmail relay limits scale. Mitigation: domain mailbox (adguard domain + SPF/DKIM) queued for purchase."],
               ["Platform API changes (Google/Meta)", "Official APIs only; multi-identity fallbacks built; changes absorbed by webhook adapter layer."],
               ["Support load at growth", "Academy + setup guide self-serve content; weekly digest emails; ticket system with priority for paid plans."]],
              widths=[2.2, 4.8])

    # ---------- 9. THE ASK ----------
    doc.add_heading("9. Approvals Requested", level=1)
    add_table(doc, ["#", "Approval", "Detail"],
              [["1", "Razorpay LIVE activation", "KYC under CHLEAR entity; bank account for settlement; keys handed to engineering for the 1-hour flip"],
               ["2", "Pricing confirmation", "₹4,999 / ₹14,999 / ₹39,999 + call packs ₹799/₹1,999/₹2,999 (all ex-GST) as published"],
               ["3", "Budget for 3 months", "₹25–40k/month (infra + support + ₹10k marketing) to reach ₹1L MRR by January"],
               ["4", "Domain purchase", "adguard brand domain for email deliverability + white-label (queued)"]],
              widths=[0.4, 2.2, 4.4])
    para(doc, "", space_after=10)
    para(doc, "The close:", bold=True, space_after=2)
    para(doc, "“One mid-size Meta lead-gen client wastes ₹20,000–50,000 every month on junk leads. AdGuard charges "
              "₹4,999 to stop it — with proof in rupees the client can see. The math sells the product. The only "
              "thing between us and the market is a Razorpay activation.”", size=12, bold=True)
    para(doc, "", space_after=24)
    add_table(doc, ["Approved by (Management)", "Date", "Signature"],
              [["", "", ""], ["", "", ""], ["", "", ""]],
              widths=[3.0, 1.4, 2.6])

    doc.save(OUT)
    print("saved:", OUT)


if __name__ == "__main__":
    build()