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

### 🛡️ Layer 1 — BEFORE the spend (prevention)
*The money never leaves your account.*

| Mechanism | How it saves |
|---|---|
| **Platform exclusion sync** | FraudGraph fingerprints (emails, phones, device patterns) pushed to Google Customer Match suppression + Meta Custom Audience exclusion → blocked scammers never see your ad again |
| **Auto-pause Governor** | Campaign junk-rate > 40% over 24h (min 50 leads) → auto-paused + WhatsApp alert + one-click re-approve. Bleeding campaigns stop overnight, mid-burn |
| **Placement/keyword hygiene** | Placements and search terms driving junk auto-added to exclusion/negative lists → budget diverts to clean inventory |
| **Geo/device bid shields** | Junk concentration patterns (specific city + device + 3AM timing) → bid down or exclude before budget drains |

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
- AI intent reading: "checking price" vs "admission this month" → sales priority tiers

---

## Status honesty (internal only — not for slide)
- Layer 2 + Layer 3: **built and live in production today**
- Layer 1: **the build ask.** Uses existing FraudGraph fingerprint store + fuses two planned roadmap items (Autonomous Budget Governor + Meta CAPI feedback loop) pointed at spend. Build = exclusion-list sync APIs (Google Customer Match, Meta Custom Audiences) + pause rules engine.

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

---

## Live Evidence Sources (pull before pitching)
- /adguard admin KPIs: Leads Audited · Verified & Pushed · Blocked Garbage · Recovered Spend (₹)
- /leads/export CSV for campaign-level breakdown
- Landing page fraud visualizer for the demo moment

---

## Pricing (for the pitch appendix)
| Plan | Price (INR/mo) | Lead quota |
|---|---|---|
| Trial | Free, 14 days | 100 |
| Starter | ₹4,999 | 1,000 |
| Pro | ₹14,999 | 5,000 |
| Agency | ₹39,999 | Unlimited |

ROI line: at ₹350 CPL, Pro blocks 5,000 junk leads max → up to ₹17.5L protected spend/mo for ₹14,999.
Layer 1 upgrade line: "Every month of Layer 1 data makes the exclusion lists stronger — the shield compounds."