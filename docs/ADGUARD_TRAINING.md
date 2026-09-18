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

**Customer (subscriber) sees** — tabbed workspace:
- **Dashboard tab**: quota + 80% upgrade alert, connect banner, KPIs (audited/verified/blocked/recovered ₹/avg score), Live Lead Feed with search + CSV
- **Reports tab**: Campaign Junk Report (date range: 24h/7/28/30/90d — per campaign: leads, verified, blocked, junk %, recovered ₹), Waste Analysis (named reasons bar chart from lead flags), CSV export with range
- **Connections tab**: Google/Meta cards — connected status, Last sync timestamp (Meta auto-polls 5 min), discovered accounts list, Connect/Disconnect buttons (disconnect keeps lead history)
- **Support tab**: raise tickets (subject/category/body), threaded conversation with the owner, resolve & close
- **Settings tab**: timezone, Protection Mode (Monitor = detect only / Protect = Money Shield auto-pause), alert emails (max 5) for weekly report + alerts

**Admin (app owner) sees:**
- All subscribers, plans/quotas, storage, connection health, archive, edit (name/plan/quota/expiry/password reset), delete (typed-email confirm)
- Global Lead Stream (per-subscriber on demand)
- Test Lead Cleanup (preview before delete)
- Create Subscriber (login + workspace + plan in one step; **invite email** or instant password modes)
- View Page (open the exact subscriber experience in one click)
- **Support Inbox**: all customer tickets, reply threads, open-count badge, close tickets

---

## Pages

| Page | URL | Who |
|---|---|---|
| Public landing | /adguard-landing | Everyone (motto, pipeline, comparison, ROI calculator, pricing) |
| Admin portal login | /adguard-admin-login | Admin/superadmin login exclusively |
| Admin dashboard | /adguard | Admin/superadmin cockpit & tenant management |
| Subscriber workspace | /adguard-workspace | Customers (admin can simulate with ?ws=<id>) |
| Hub home card | / (landing.html) | Card routes: guest → landing, admin → /adguard, customer → workspace |

---

## Core Definitions: Lead Quota vs. Recovered ₹ Spend

### 1. What Exactly is "Lead Quota"?
> **Lead Quota** is the **total number of incoming leads ingested, audited, and processed** through AdGuard's 14-point fraud detection pipeline per month — **not just the leads that get blocked**.

* **Why does it count all leads?**
  AdGuard runs comprehensive verification on **every single lead** that enters your account:
  1. Indian carrier phone network query & active SIM format check.
  2. 3,500+ disposable temporary email blacklist verification.
  3. City/State geo-match against ad targeting.
  4. Cross-lead timing & rapid generator pattern detection.
  5. Gemini AI buying intent & legitimacy scoring (Pro/Agency).
  6. Shared threat network cross-referencing (FraudGraph).
  7. Downstream CRM routing with verified status chips.
  Because computational intelligence and threat scanning are applied to both clean leads and fraudulent leads, the quota applies to the entire intake volume.

### 2. What is "Recovered ₹ Spend Audit Log"?
> The **Recovered ₹ Spend Audit Log** is the accountant-verifiable calculation of **exact ad budget saved by blocking fraudulent form-fills before they waste sales bandwidth and ad spend**.

* **Calculation Formula**:
  $$\text{Recovered ₹ Spend} = \text{Blocked Garbage Leads} \times \text{Campaign Cost-Per-Lead (CPL)}$$
* **Example**: If your campaign CPL is ₹350, and AdGuard intercepts 40 fake/bot leads in a week, your Recovered ₹ Spend is **₹14,000**.
* **Audit Trail**: Every blocked lead is stored in the database with timestamp, campaign ID, integrity score (< 70), specific failure reason chips (e.g., `disposable_email`, `bad_phone_carrier`), and the rupee value saved. This data is available in live dashboard KPIs, downloadable as CSV, and delivered via the **Weekly Monday Executive Report Email**.

---

## Standardized Cumulative Plan Progression

AdGuard follows a strict **cumulative / additive** feature progression across all marketing materials, dashboard cards, and sales docs. Every tier retains 100% of the previous tier's features, repeating them identically, and adds new capabilities:

### Tier 1: Trial (Free / 14 Days — 100 Leads)
*The 6 Foundational Features:*
1. ✓ **Google & Meta 1-Click Connectors**
2. ✓ **0–100 Lead Integrity Scoring**
3. ✓ **Real-Time Fraud & Bot Blocker**
4. ✓ **Direct CRM Delivery & CSV Export**
5. ✓ **Recovered ₹ Spend Audit Log**
6. ✓ **Standard Help Center & FAQs**

### Tier 2: Starter (₹4,999/mo — 1,000 Leads/mo)
*All 6 Trial features + 2 Additions (8 Cumulative Features):*
1. ✓ Google & Meta 1-Click Connectors
2. ✓ 0–100 Lead Integrity Scoring
3. ✓ Real-Time Fraud & Bot Blocker
4. ✓ Direct CRM Delivery & CSV Export
5. ✓ Recovered ₹ Spend Audit Log
6. ✓ **3,500+ Disposable Email Blacklist** *(Added)*
7. ✓ **Indian Carrier Phone Validation** *(Added)*
8. ✓ **Standard Email & Ticket Support (24h response)** *(Upgraded SLA)*

### Tier 3: Pro (₹14,999/mo — 5,000 Leads/mo) — *Most Popular*
*All 8 Starter features + 2 Additions (10 Cumulative Features):*
1. ✓ Google & Meta 1-Click Connectors
2. ✓ 0–100 Lead Integrity Scoring
3. ✓ Real-Time Fraud & Bot Blocker
4. ✓ Direct CRM Delivery & CSV Export
5. ✓ Recovered ₹ Spend Audit Log
6. ✓ 3,500+ Disposable Email Blacklist
7. ✓ Indian Carrier Phone Validation
8. ✓ **Gemini AI Deep Intent Scoring** *(Added)*
9. ✓ **FraudGraph Shared Threat Network** *(Added)*
10. ✓ **Priority Support (< 4h SLA response)** *(Upgraded SLA)*

### Tier 4: Agency (₹39,999/mo — Unlimited Leads)
*All 10 Pro features + 2 Additions (12 Cumulative Features):*
1. ✓ Google & Meta 1-Click Connectors
2. ✓ 0–100 Lead Integrity Scoring
3. ✓ Real-Time Fraud & Bot Blocker
4. ✓ Direct CRM Delivery & CSV Export
5. ✓ Recovered ₹ Spend Audit Log
6. ✓ 3,500+ Disposable Email Blacklist
7. ✓ Indian Carrier Phone Validation
8. ✓ Gemini AI Deep Intent Scoring
9. ✓ FraudGraph Shared Threat Network
10. ✓ **Unlimited Client Workspaces & Brands** *(Added)*
11. ✓ **Multi-tenant BM & Multi-CRM Routing** *(Added)*
12. ✓ **Dedicated Account Manager & VIP SLA** *(Upgraded SLA)*

---

## Email Support & Help Desk Architecture

AdGuard provides an integrated, multi-channel support infrastructure bridging direct email and web-based threaded ticketing:

### 1. Official Support Contact Email
- **Primary Support Desk**: **`support@adguard.ai`**
- **Dynamic Configuration**: Configurable in the Admin Portal without code redeployments via `POST /api/adguard/support/admin/config`.

### 2. Plan-Based Support SLAs
| Plan Tier | Guaranteed Support SLA | Delivery Channel |
|---|---|---|
| **Trial** | Documentation, Community & FAQs | Help Center |
| **Starter** | Standard Email & Ticket Support (**24h response**) | Web Ticket + Email |
| **Pro** | Priority Email Support (**< 4h SLA response**) | High-Priority Queue |
| **Agency** | Dedicated Account Manager & VIP SLA | Dedicated Contact + Escalations |

### 3. Subscriber Workspace Implementation (`/adguard-workspace`)
- **Top Help Desk Card in `#tab-support`**:
  - Displays official support email (`support@adguard.ai`) with clickable `mailto:` link prefilling Workspace ID and account context.
  - **"📋 Copy Email"** button with dynamic feedback (`✓ Copied!`).
  - **Dynamic Plan Support SLA Badge**: Automatically detects active subscription tier and displays SLA commitment.
- **Sidebar Support Widget**:
  - Located at bottom of the left navigation sidebar right above **Log Out**.
  - Shows quick email access, plan badge, and one-click shortcuts to tickets or email clients.

### 4. Admin Portal Integration (`/adguard`)
- **Master Controls Support Inbox**:
  - Overview of all tickets across all tenants with filter by `Open`, `Answered`, or `Closed` and live unread badge.
  - **Official Support Contact Bar**: Displays active email with **"✏️ Configure Email"** modal for live updates.
  - **Direct Email Client**: Button to open local email client pre-addressed to the customer with ticket subject headers.
- **Automated Two-Way Notification Dispatcher**:
  - When the admin replies in the portal, the backend sends an **AdGuard-branded HTML & text email notification** to the subscriber containing the reply message and a direct CTA link to view the ticket.
  - When a subscriber raises a ticket or replies, an alert notification is automatically dispatched to the admin support desk.

---

## Payment Gateway & Razorpay Sandbox Simulator

- **Payment Engine**: Integrated with **Razorpay**.
- **Test / Sandbox Mode**:
  - Fully enabled for zero-risk demonstration and user onboarding testing.
  - Supports **Test Credit/Debit Cards** (`4111 •••• •••• 1111`, expiry `12/28`, CVV `123`).
  - Supports **UPI / Dynamic QR Code Simulation**: Renders high-contrast QR code for mobile apps (GPay, PhonePe, Paytm, BHIM) with 1-click test authorization.
- **Instant Autonomous Provisioning**:
  - Completing checkout instantly updates the database (`plan` tier and `lead_quota`).
  - Automatically records audit log entry.
  - Dismisses quota warning banners and updates the progress bar in real time.

---

## Authentication & Dedicated Admin Portal

- **Customer Registration & Sign In**: Located on `/adguard-landing`.
  - Free trial registration provisions workspace with 100 leads quota and enables Money Shield.
  - Selecting Starter, Pro, or Agency prompts registration and smoothly transitions directly into Razorpay checkout.
- **Dedicated Standalone Admin Portal (`/adguard-admin-login`)**:
  - Standalone login window strictly reserved for `admin` and `superadmin` credentials.
  - Subscriber credentials attempting login receive an immediate access denial with a redirect to the Customer Portal.
  - Direct access to Master Controls cockpit (`/adguard`).

---

## Selective Account & Campaign Screening (Go-Live Control)

To give subscribers total operational control and prevent test campaigns or non-commercial accounts from consuming lead quotas or skewing metrics:
- **Zero by Default / Clean Slate**:
  - Newly connected Google or Meta ad accounts are in **Standby** by default.
  - No dummy figures, no fabricated metrics: a workspace starts with clean zeros (`0` audited, `0` blocked, `₹0` recovered) until genuine leads enter active campaigns.
- **Granular Go-Live Selection**:
  - In the subscriber workspace (`/adguard-workspace`), users can select specific ad accounts or individual campaigns from the right-hand panel and click **"Go Live"** (or pause).
  - Only campaigns explicitly marked as **LIVE** are screened through the fraud detection pipeline.
- **Workspace Dashboard Filtering**:
  - Dashboard KPIs (Audited Leads, Blocked Garbage, Clean Leads Passed, and Recovered Ad Spend) calculate metrics strictly for the selected live accounts/campaigns.
  - Metrics focus purely on AdGuard core value: **Leads Intercepted, Scams Blocked, and Ad Spend Recovered**.

---

## 24/7 Autonomous Money Shield Watchdog (Zero Idle / Zero Manual Intervention)

Money Shield does not sit idle waiting for manual execution. It operates 100% autonomously in the background:
- **Autonomous Auto-Arming**:
  - The moment any campaign or account is marked **LIVE**, Money Shield is automatically armed (`ws.shield_enabled = True`).
- **5-Minute Continuous Background Watchdog**:
  - An automated background worker runs every **5 minutes** (`scheduler.py` interval job).
  - Inspects all live campaigns across all active workspaces in real time.
- **Circuit Breaker Auto-Pause**:
  - If any live campaign generates a junk lead rate exceeding the threshold (default: **> 40% junk** with a minimum of **50 leads in 24 hours**), Money Shield **automatically pauses the campaign mid-burn**.
  - Prevents ad budgets from draining overnight into bot farms.
  - Action is logged to the subscriber's permanent **Shield Action Log** and reported in the Monday Executive Email.
- **Visual Heartbeat & Pulsating Status**:
  - The subscriber workspace displays a live pulsating green status beacon: `🟢 AUTONOMOUS MONITOR ACTIVE`.
  - Details exact live campaigns guarded and the 5-minute background cycle, with compact diagnostic test scan (`⚡ Test Scan`) and list synchronization (`🚫 Sync Lists`) tools.

---

## Tech Reference & Deployment

- **Repository**: `C:\Users\Shekhar Raju\Downloads\Clients\Shekhar_AI_Agents\AI_The_Optimiser`
- **Git Remotes**:
  - `origin`: GitHub (`shekharraju6-droid/ai-ad-optimiser`)
  - `chlearai`: Railway production watcher (`chlearai/ai-ad-optimiser`)
  - *Rule*: Always push to both remotes!
- **Key Modules**:
  - `backend/routes/adguard_support.py`: Ticket management, SLA config, and inbox endpoints.
  - `backend/services/onboarding_email.py`: Branded email sender & support notification dispatchers.
  - `backend/routes/billing.py` & `backend/services/billing.py`: Razorpay order generation & sandbox verification.
  - `frontend/adguard_workspace.html`: Customer workspace, support banner, sidebar widget, upgrade modal.
  - `frontend/adguard.html`: Admin cockpit, support inbox, email configuration bar.
  - `frontend/adguard_landing.html`: Public landing page, cumulative pricing cards, competitor matrix.
  - `frontend/adguard_admin_login.html`: Dedicated admin portal login.