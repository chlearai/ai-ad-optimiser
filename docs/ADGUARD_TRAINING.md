# AdGuard — Training & Brand Document

> Source of truth for AdGuard's motto, functions, and product structure.
> Use this for training material, marketing copy, and AI-assisted development.
> Last updated: 2026-09-12

---

## Motto (preserve verbatim)

**"Stop paying for garbage leads."**

Every rupee of ad spend should buy a real customer — not a bot, duplicate, or junk form-fill.

---

## What AdGuard Does — The 6 Functions

### 1. Capture — how leads enter AdGuard
- The intake door. Every lead form submission from your ads lands here automatically.
- Click **Connect Google Ads** → sign in with Google → approve. AdGuard reads which ad accounts you own. No API keys, no developer setup.
- Same for **Connect Meta Ads** → Facebook Pages + ad accounts link in one click.
- Lead forms (Google lead form assets, Meta instant forms) fire data to AdGuard's webhook URL — a private mailbox address.
- Speed: seconds (Google) / ~5 minutes (Meta poller; instant once webhook is live).
- Captures: name, email, phone, city + which campaign/ad the lead came from — every lead traceable to the exact ad that bought it.
- Why it matters: nothing manual. No CSV downloads, no copy-paste. Every lead captured, stamped, traceable the moment it exists.

### 2. Score — every lead gets an Integrity Score 0–100
- Disposable email check, phone validity, geo match, AI legitimacy (Gemini).
- Threshold 70 = verified. Below = flagged with visible reason chips.

### 3. Filter — verified pass, garbage blocked
- Duplicate, bot, fake leads flagged and blocked from reaching the CRM.
- Reasons shown as chips: duplicate_recent_lead, disposable email, bad phone, geo mismatch, low AI score.

### 4. Deliver — verified leads reach the subscriber's CRM
- Verified leads push to the subscriber's chosen CRM (LeadSquared, Zoho, Salesforce, HubSpot, Custom Webhook — subscriber's own choice; never hardcoded to one vendor).
- CRM preference is per-subscriber. Until connected, verified leads are held safely in AdGuard (exportable via CSV anytime).

### 5. Prove — recovered spend evidence
- Flagged leads × CPL = money saved, shown per campaign.
- This evidence powers the recovered-spend pitch and (later) profit-guarantee pricing.

### 6. Protect — the FraudGraph network moat
- Every scam fingerprint (email pattern, phone block, fill-timing, message syntax) feeds a shared FraudGraph.
- A bot blocked for one customer is pre-blocked for all customers. Each customer makes all customers smarter.
- Competitors protect one account; AdGuard protects the network (Visa/Cloudflare playbook).

---

## Product Roles & Views

**Customer (subscriber) sees:**
- Live lead feed (own leads only), scores, verified vs flagged
- CSV export
- Their quota with 80% upgrade alert
- Connectors: Google Ads, Meta Ads (+ LinkedIn, Microsoft/Bing, TikTok, X, Pinterest, Snapchat, Amazon — coming soon)
- CRM Delivery selector (their own CRM)

**Admin (app owner) sees:**
- All subscribers, plans/quotas, storage, connection health
- Global Lead Stream (per-subscriber on demand, not always-on)
- Test Lead Cleanup (preview before delete)
- Create Subscriber (login + workspace + plan in one step)
- View Page (open the exact subscriber experience in one click)

---

## Pages

| Page | URL | Who |
|---|---|---|
| Public landing | /adguard-landing | Everyone (motto, pipeline, ROI calculator, pricing) |
| Admin dashboard | /adguard | Admin/superadmin login |
| Subscriber workspace | /adguard-workspace | Customers (admin can simulate with ?ws=<id>) |
| Hub home card | / (landing.html) | Card routes: guest → landing, admin → /adguard, customer → workspace |

---

## Pricing Plans

| Plan | Price (INR/mo) | Lead quota |
|---|---|---|
| Trial | Free, 14 days | 100 |
| Starter | ₹4,999 | 1,000 |
| Pro | ₹14,999 | 5,000 |
| Agency | ₹39,999 | Unlimited (−1) |

Payments integration: later. Admin login = same login page, separate role.

---

## Copy Snippets (approved, reuse verbatim)

- Hero: "Stop Paying for Garbage Leads."
- Pipeline step 1: "1-Click Capture — Connect Google Ads & Meta Lead Forms via OAuth."
- Pipeline step 2: "AI & Heuristic Score — 0–100 score evaluating disposable domains, carrier validity, geo-match, and Gemini AI."
- Pipeline step 3: "CRM Delivery & Proof — Only verified leads (≥ 70) push to your CRM; blocked leads count toward recovered ad spend proof."

---

## Tech Reference (for AI sessions)

- Repo: `C:\Users\Shekhar Raju\Downloads\Clients\Shekhar_AI_Agents\AI_The_Optimiser`
- Remotes: `origin` (shekharraju6-droid) + `chlearai` — Railway watches **chlearai**; push BOTH
- Production: https://ai-ad-optimiser-production-dd12.up.railway.app (health: /health)
- Key files: backend/routes/adguard.py · backend/services/adguard.py (process_incoming_lead) · backend/services/adguard_meta.py · backend/services/scheduler.py (5-min Meta poller) · frontend/adguard.html · frontend/adguard_workspace.html · frontend/adguard_landing.html
- Plans auto-set quota: trial 100 / starter 1000 / pro 5000 / agency −1 (unlimited)
- Tracker: C:\Users\Shekhar Raju\Desktop\LANDMARK_TRACKER.csv

## Known Open Items
1. CRM credential capture + per-CRM delivery engine (preference stored; delivery not built)
2. Multi-account-per-login (agency use case: DSU + crash club under one login)
3. Sep 14 recovered-spend pitch doc
4. Meta webhook live delivery — blocked by Meta business verification (row 44); 5-min poller is working path
5. New ad platform connectors (LinkedIn/Bing/TikTok/X/Pinterest/Snapchat/Amazon) — placeholders only