# AdGuard — The 3-Layer Money Shield (Pitch Document)
**Target date: Sep 14 · Audience: management / paying customers**
**Motto: "Stop paying for garbage leads."**

---

## The Problem (hook)
Up to 30% of lead-form submissions are not humans. Bots, duplicates, disposable emails, fake phones, professional fillers.
Every one of them costs you money — you paid CPL for each.

**Form validation stops typos. AdGuard stops fraud — and proves the money you got back.**

**The billion-dollar question management should ask:** *"Does this save money BEFORE the ad burns it, or just clean up after?"*
AdGuard's answer: **both. Three layers. Spend prevented at source, filtered at capture, proven in rupees.**

---

## THE 3-LAYER MONEY SHIELD

### 🛡️ Layer 1 — BEFORE the spend (prevention) — LIVE TODAY ✅
*The money never leaves your account. Runs autonomously 24/7 in background every 5 minutes.*

| Mechanism | How it saves | Status |
|---|---|---|
| **Autonomous Circuit Breaker** | Background watchdog scans every 5 min. Campaign junk-rate > 40% over 24h (min 50 leads) → auto-paused mid-burn | **LIVE (every 5m)** ✅ |
| **Platform exclusion sync** | FraudGraph fingerprints (emails, phones) pushed to Google Customer Match + Meta Custom Audience exclusion | **LIVE (weekly + on-demand)** ✅ |
| **Selective Go-Live Controls** | Granular account & campaign selector; only selected active campaigns are monitored & billed | **LIVE** ✅ |
| **Placement/keyword hygiene** | Placements and search terms driving junk auto-added to exclusion/negative lists → budget diverts to clean inventory | Roadmap |
| **Geo/device bid shields** | Junk concentration patterns (specific city + device + 3AM timing) → bid down or exclude before budget drains | Roadmap |

**Network effect at spend level:** a scammer excluded by customer A is excluded from customer B's campaigns before their first click. Every customer's shield protects every other customer.

### 🛡️ Layer 2 — AT capture (filtering) — LIVE TODAY ✅
*Whatever slips through never pollutes your CRM.*

- Integrity Score 0–100: disposable email, phone validity, geo match, Gemini AI legitimacy
- Cross-lead intelligence: same phone 6 names, 40 forms/10 min, rahul123→125 generator patterns, 2.5-second fill timings
- Verified (≥70) → CRM. Flagged → blocked with reason chips.

### 🛡️ Layer 3 — AFTER capture (proof + redirection) — LIVE TODAY ✅
*The invoice that proves the shield works.*

- Flagged leads × CPL = recovered spend, per campaign, timestamped, accountant-verifiable
- Per-campaign junk rates: "YouTube: 62% junk. Search: 8%. Move the budget."
- **Weekly report email** every Monday: campaign table + recovered ₹, straight to the customer's inbox
- **Waste Analysis**: named reasons why leads were blocked (disposable email, bad phone, geo mismatch…) — customers understand what hit them
- AI intent reading: "checking price" vs "admission this month" → sales priority tiers

---

## Status honesty (internal only — not for slide)
- **Layer 1**: Autonomous Circuit Breaker Governor (5-min background daemon) + Google Customer Match & Meta Custom Audience exclusion sync are **built and live in production today**.
- **Layer 2 + Layer 3**: **built and live in production today**.
- Full 3-Layer Money Shield is operational end-to-end.

---

## The One-Line Sell
> **"You don't buy validation. You buy a shield that stops the money before it burns — and an invoice proving what it saved."**

## The Deck Flow (suggested)
1. Open with money: "Last month, X% of your lead-form spend bought bots." (live numbers from /adguard KPIs)
2. Show a real blocked lead vs verified lead (fraud visualizer on landing page)
3. The 3-layer shield diagram: prevent → filter → prove
4. Layer 1 money shot: "A scammer caught once never sees ANY of our customers' ads again. ₹ never leaves the account."
5. FraudGraph moat in one sentence: code can be copied; the network's blocklist cannot.
6. Close with the math: "The tool pays for itself if it blocks > ~350 junk leads/month at ₹350 CPL." (Starter = ₹4,999)

---

## Objection Handling
| Objection | Answer |
|---|---|
| "Forms already validate emails/phones" | Table stakes. AdGuard works on the layer forms can't see: cross-lead history + network intelligence + spend prevention. |
| "So you filter my loss after I pay for it?" (the CFO kill-shot on filter-only tools) | Layer 1: excluded scammers' impressions are never served — spend prevented at source. Layers 2–3 protect CRM + prove savings. |
| "Why not build in-house?" | The value is the FraudGraph network data, not the code. In-house = one account's fingerprints. AdGuard = every customer's scammer pre-blocked for you from day one. |
| "What if it blocks real customers?" | Every block carries reason chips + CSV export + admin review. One-click unarchive. Shield is auditable. |
| "How are you different from ClickCease/Lunio?" | They block clicks (one layer, USD 250–420/mo). AdGuard does prevention + filtering + proof at Indian pricing, with a transparent 0–100 Integrity Score instead of a black box, delivers to ANY CRM (they deliver to none), and every customer's blocked scammer protects the whole network. |

---

## Live Evidence Sources (pull before pitching)
- `/adguard` admin KPIs: Leads Audited · Verified & Pushed · Blocked Garbage · Recovered Spend (₹)
- `/leads/export` CSV for campaign-level breakdown
- Landing page fraud visualizer for the demo moment

---

## Executive Definitions for Leadership & CFOs

### 1. What is "Lead Quota"?
> **Lead Quota** is the **total number of ad form-fill leads audited, verified, and scored** by AdGuard per month — **not just the leads that get blocked**.

* **Why it covers all leads**: Every lead undergoes our 14-point inspection engine (Indian carrier phone network check, 3,500+ disposable email blacklist check, geo-verification, Gemini AI buying intent, and FraudGraph threat scoring). Because intelligence is computed for every lead before routing to your CRM, the quota covers total lead ingestion volume.

### 2. What is "Recovered ₹ Spend Audit Log"?
> **Recovered ₹ Spend** is the accountant-verifiable rupee calculation of ad budget saved by intercepting fake leads:
> $$\text{Recovered ₹ Spend} = \text{Blocked Fake Leads} \times \text{Campaign CPL}$$

* Every blocked lead records an immutable audit log with timestamp, ad account ID, campaign ID, integrity score (< 70), specific failure reason chips, and the rupee savings.
* Sent every Monday directly to executives in the **Automated Executive Report Email**.

---

## Pricing & Cumulative Tier Progression (Pitch Appendix)

Every tier strictly repeats all prior tier capabilities and adds high-leverage features:

| Plan | Price (INR/mo) | Monthly Quota | Key Additions | Support SLA |
|---|---|---|---|---|
| **Trial** | **Free** (14 days) | 100 Leads | 1-Click Connectors, 0–100 Integrity Scoring, Real-Time Fraud & Bot Blocker, CRM Delivery, Recovered ₹ Spend Audit Log | Help Center & FAQs |
| **Starter** | **₹4,999** | 1,000 Leads/mo | All Trial + 3,500+ Disposable Email Blacklist, Indian Carrier Phone Validation | Standard Email (24h response) |
| **Pro** *(Popular)* | **₹14,999** | 5,000 Leads/mo | All Starter + Gemini AI Deep Intent Scoring, FraudGraph Threat Network | **Priority Support (< 4h SLA)** |
| **Agency** | **₹39,999** | **Unlimited** | All Pro + Unlimited Client Workspaces & Brands, Multi-tenant BM, Multi-CRM Routing | **Dedicated Account Manager & VIP SLA** |

---

## Multi-Channel Support & Enterprise SLA Guarantee

- **Official Support Desk**: **`support@adguard.ai`**
- **In-Dashboard Help Desk**: Integrated ticket desk inside the subscriber workspace (`/adguard-workspace`), displaying the user's active plan SLA badge, 1-click email launch, and 1-click email copy.
- **Admin Cockpit (`/adguard`)**: Centralized support inbox with live email reconfiguration, direct customer reply composer, and **automated two-way email notifications** whenever tickets are raised or answered.
- **Zero-Risk Testing Mode**: Razorpay Sandbox simulator integrated with Test Credit Cards (`4111 •••• 1111`) and simulated UPI QR code scans for frictionless onboarding walkthroughs.

---

## ROI Pitch Summary
- At ₹350 CPL, Pro blocks up to 5,000 junk leads max → **up to ₹17.5L in protected spend for ₹14,999**.
- Layer 1 upgrade line: *"Every month of Layer 1 data makes the exclusion lists stronger — the shield compounds."*