# AdGuard SaaS Launch — October 10
_The complete plan: engineering, security, costs, margins, agency model, marketing, management pitch._
Owner: Shekhar Raju · Prepared: Sep 29, 2026

---

## 1. WHERE WE ARE (honest status)

| Area | Status |
|---|---|
| SaaS core (plans, quotas, subscribers, admin CRUD) | ✅ Done |
| Google multi-identity connect | ✅ Working end-to-end |
| Lead capture + verdicts + scoping | ✅ Done |
| Money Shield (governor, exclusion sync, push button) | ✅ Built, needs live-fire |
| Support tickets, reports, settings, CSV exports | ✅ Done |
| Legal pages (/privacy, /terms) | ✅ Done |
| Invite emails + weekly reports | 🔶 Blocked on SMTP verify (diag endpoint ready) |
| Meta multi-identity connect | ❌ Blocked (app config, decision: revert to old app 1423583466382155) |
| Railway 502 stability | ❌ Blocked (config-side, code proven clean locally) |
| Security hardening | 🔶 Partial (see checklist) |
| Memory/perf under load | ⬜ Not measured |

Days left: **11** (Sep 29 → Oct 10).

---

## 2. LAUNCH CHECKLIST

### A. Platform stability (Days 1–2) — BLOCKS EVERYTHING
- [ ] Fix Railway 502: check Deploy Logs first red line; Settings → Networking target port = auto; no custom start command overriding Dockerfile; no manual PORT variable
- [ ] Confirm `ADOPTIMA_JWT_SECRET` is set in Railway (default `"change-me-in-production"` is INSECURE — anyone can forge admin tokens)
- [ ] Remove dead lowercase `google_client_id/secret/developer_token` vars (confusion risk)
- [ ] Set `ADOPTIMA_PUBLIC_BASE_URL=https://ai-ad-optimiser-production-dd12.up.railway.app` (invite links break without it)
- [ ] One full deploy → smoke test: login admin, login customer, create subscriber, CSV export

### B. Email (Day 2)
- [ ] Run `/admin/email-diag` → paste JSON → fix per result (App Password path)
- [ ] Verify: invite email arrives < 60s from Gmail send
- [ ] Weekly report email: force one run Monday or add a "send test report" admin action

### C. Meta connect (Day 2–3) — decision already made
- [ ] Railway: `META_APP_ID=1423583466382155` + old app secret in `ADGUARD_META_APP_SECRET`
- [ ] Vishnu adds Shekhar + Vishnu FB profiles as Testers: developers.facebook.com/apps/1423583466382155/roles/
- [ ] Redirect URI on old app: `/api/adguard/oauth/meta/callback`
- [ ] Test: both FB logins connect, leads poll 5-min
- [ ] New verified app 1402734158628515: park as "publish later" project (App Review submission when public launch demands it)

### D. Security (Days 3–5) — NON-NEGOTIABLE FOR SAAS
- [ ] **Encrypt OAuth tokens at rest**: google/meta credentials currently sit as plaintext JSON in DB. Add Fernet encryption with a `ADGUARD_ENCRYPTION_KEY` env var (one migration + read/write wrapper)
- [ ] **JWT secret set + rotate** (above)
- [ ] **Rate-limit login endpoint** (5 attempts/min/IP) — brute force guard, ~20 lines
- [ ] **Row-scope audit**: every `/api/adguard/*` endpoint — confirm non-admins cannot pass another workspace id and read others' leads (routes filter by user's workspace; verify `/shield/actions/{workspace_id}` and reports endpoints too)
- [ ] **Webhook signature check**: Meta webhook `X-Hub-Signature-256` verify against `ADGUARD_META_APP_SECRET` (webhook forge = fake leads injection)
- [ ] Supabase: enable daily backups (paid tier) or nightly export job to storage
- [ ] Admin actions audit-log already exists via log_activity — confirm sensitive routes use it
- [ ] Delete account path: admin delete exists; add self-serve "request deletion" mailto on settings page (privacy policy promises it)

### E. Performance / memory (Day 5)
- [ ] Load test: simulate 5,000 leads insert on Supabase → measure report/dashboard query times; add index `adguard_leads(received_at)`, `adguard_leads(adguard_account_id, verdict)` if slow
- [ ] Scheduler memory: 8 jobs at various intervals — confirm no duplicate scheduler threads (Railway restart test: watch logs for double "Adding job")
- [ ] Railway memory limit: set 1GB service; verify RSS < 400MB after 24h run
- [ ] Meta poller fan-out: N identities × per-account calls — cap concurrent calls (already sequential; fine to 10 workspaces)

### F. UI polish (Day 6–7)
- [ ] Landing page: competitor table + motto banner (approval pending)
- [ ] Workspace: first-run empty states (no leads yet → "Connect your first account" guide)
- [ ] Favicon/app icon in all pages
- [ ] Mobile pass: admin + workspace on phone (owner will demo on laptop; customers may open on mobile — tables must not explode)
- [ ] Error toasts: no raw English tracebacks shown to customers

### G. Ops for launch day (Day 8–9)
- [ ] Demo workspace with REAL CHLEAR numbers (CHLEAR junk %, ₹ recovered) — the money slide
- [ ] 2 fake-but-realistic subscriber workspaces for demos (don't touch prod data)
- [ ] Rollback plan: previous deploy keeps working; DB migrations are additive-only (they are)
- [ ] Support channel: WhatsApp Business number + support email in footer

### H. Buffer (Day 10–11)
- [ ] Fix whatever smoke tests surface. Nothing new. Freeze.

---

## 3. REAL RUNNING COSTS (in-house, monthly, INR)

### Fixed infra (baseline, ≤30 subscribers)
| Item | ₹/month | Notes |
|---|---|---|
| Railway (app + scheduler) | 2,000–3,500 | usage-based; ~512MB–1GB running 24/7 |
| Supabase | 0 → 2,100 | Free tier to ~500MB; Pro at growth |
| Domain (if bought) + SSL | ~100 | SSL free via Railway |
| Email (Gmail App Password) | 0 | ~500 sends/day limit — fine |
| **Total fixed** | **~₹3,000–5,000** | |

### Variable per subscriber
| Item | ₹/sub/month |
|---|---|
| Compute (5-min Meta polls, scans, API calls) | 50–150 |
| Supabase storage growth | <50 |
| Support time (30 min @ ₹300/hr) | 150 |
| Payment gateway (Razorpay ~2%) | 2% of price |
| **Total variable** | **~₹300–400 + 2%** |

Break-even on fixed costs: **1 Starter subscriber** covers the entire infrastructure.

---

## 4. UNIT ECONOMICS / PROFIT %

Prices are yours (trial ₹0/100 leads, starter ₹4,999, pro ₹14,999, agency ₹39,999).

### Gross margin per subscriber (infra + support only, no salaries)
| Plan | Price | Var. cost | Gross ₹ | Gross % |
|---|---|---|---|---|
| Trial | 0 | ~50 | −50 | cost = lead-gen, worth it |
| Starter (1,000 leads) | 4,999 | ~300 | ~4,700 | **~94%** |
| Pro (5,000 leads, 3 ws) | 14,999 | ~600 | ~14,400 | **~96%** |
| Agency (10 ws, unlimited) | 39,999 | ~2,500 | ~37,500 | **~94%** |

### Profit & loss at realistic 60-day post-launch state (founder-run + 1 support hire)
| Line | ₹/mo |
|---|---|
| 10 Starter + 4 Pro + 2 Agency | 49,990 + 59,996 + 79,998 = **1,89,984** |
| Infra (scales a bit) | −8,000 |
| Gateway 2% | −3,800 |
| Support/dev salary | −60,000 |
| **Operating profit** | **~₹1,18,000/mo → ~62% net** |

Break-even (with salary): **~₹65–70k MRR ≈ 6–8 subscribers.** Before that: founder-run, 1–2 subs = already cash-positive.

Costs you should NOT forget to tell management (honesty = credibility):
- Your own build time (sunk; product exists)
- Meta/Google do NOT charge for API use — zero platform fees
- App Review/publish for Meta public access — free, takes calendar time, not money

---

## 5. AGENCY MODEL — HOW WE ACCOMMODATE THEM

The architecture ALREADY supports this (agency plan = 10 workspaces, one login). The package:

**What an agency gets for ₹39,999/mo:**
1. **One login → 10 client workspaces.** Each client = isolated workspace: own Google/Meta connections, own leads, own shield, own reports. Clients never see each other.
2. **Per-client onboarding in minutes**: create client workspace → owner connects client's FB/Google login (OAuth — no passwords shared with agency; this is the SELLING point for clients)
3. **Cross-client command view**: admin sees all 10 in one table (junk %, spend saved, quota) — that's the existing admin Stream view
4. **Branded reports**: weekly report emails carry AdGuard branding; SMTP_SENDER_NAME per deployment → white-label light
5. **CRM delivery per client** (LSQ/Zoho/HubSpot/webhook) — already built

**Agency's own math (how we pitch to THEM):**
- Agency charges each client ₹5,000–10,000/mo for lead protection (our own pricing anchor)
- 10 clients × ₹5k = ₹50,000/mo revenue for agency at ₹39,999 cost → agency breaks even on 8 clients, profits thereafter; effectively we're their ₹5k/wholesale cost per client
- With our ₹ recovered proof (₹350/junk lead), one mid-size Meta client wastes ₹20–50k/mo — agency shows savings > fee in month 1

**Gaps to close before Day 10 (small):**
- [ ] "Add workspace" flow already exists on agency plan — verify quota=10 enforced in UI
- [ ] Bulk CSV export across workspaces (admin has it per workspace; add "all" later — NOT launch blocking)
- [ ] White-label sender name per workspace — post-launch

**Future agency upsells (post-Oct 10):** per-client sub-logins (read-only client seats), custom domain white-label, rev-share deals.

---

## 6. MARKETING / GTM CHECKLIST

### Assets (Days 3–7)
- [ ] Landing page final (live) — motto "Stop paying for garbage leads." + competitor table
- [ ] 90-sec demo video (screen record: connect → leads flow → junk flagged → ₹ saved) — Shekhar's phone + Loom
- [ ] 1-page PDF: the CHLEAR case (real numbers: N leads, X% junk, ₹Y recovered spend)
- [ ] WhatsApp forward pack: 3 images (motto card, shield diagram, pricing)

### Channels (Days 7–11)
- [ ] Warm list: every agency/ad-ops contact Shekhar+Vishnu have — WhatsApp each, personal, offering free 2-week trial
- [ ] LinkedIn: 3 posts (launch announcement, the ₹ waste story, the fraud-graph explainer)
- [ ] chlearai's own client base: present AdGuard in existing client reviews (we have access — highest conversion channel)
- [ ] Communities: performance-marketing WhatsApp/Telegram groups (India) — share the case PDF
- [ ] Partnerships: 2–3 agencies as resellers (agency plan) with onboarding help

### Launch day (Oct 10)
- [ ] LinkedIn post + WhatsApp broadcast + email to all trials
- [ ] "First 10 agencies get agency plan at ₹29,999/mo for 3 months" — urgency lever
- [ ] Monitor: subscriber signups, invite failures, Railway memory

### KPI board (weekly, from admin dashboard)
Signups · Trial→paid conversion · MRR · Leads audited · Junk % found · ₹ recovered (customer-visible proof = retention weapon)

---

## 7. MANAGEMENT PITCH (10 slides worth, in bullets)

1. **Problem:** Agencies/p advertisers pay for garbage leads. Meta/Google charge for them anyway. Nobody audits lead quality at capture.
2. **Product:** AdGuard — sits in the customer's own ad accounts (OAuth), scores every lead, blocks junk audiences BEFORE spend (L1), filters at capture (L2), proves waste in ₹ (L3).
3. **Proof:** Live on real accounts — CHLEAR: [junk %, ₹ recovered] from actual leads.
4. **Model:** SaaS subscription. Self-serve. No per-lead charge — flat plans (predictable for customer, pure margin for us).
5. **Economics:** 94–96% gross margin. Fixed infra ₹5k/mo. 1 subscriber = break-even on infra. With a support hire: break-even at 6–8 subs; 60-day scenario = ₹1.2L/mo profit at 62% net.
6. **Agency engine:** ₹39,999 plan = 10 client workspaces; agencies resell at ₹5–10k/client → their profit, our biggest ARPU.
7. **Moat:** fraud-graph exclusions network (every flagged lead makes every other customer's shield smarter), platform exclusion push (Google Customer Match / Meta Custom Audiences) — not a dashboard, an ACTUATOR.
8. **Risk & answer:** Meta review delay → we launch with Google-first flow + assisted Meta onboarding; data security → encrypted tokens, scoping, audit logs (this checklist).
9. **Ask from management:** green light + ₹25–40k/mo budget for 3 months (infra + 1 support + ₹10k marketing) to reach ₹1L MRR by Jan.
10. **The close:** "One mid-size Meta lead-gen client wastes ₹20–50k/month on junk. We charge ₹4,999 to stop it. The product sells itself with math."

---

## 8. WHO DOES WHAT
| Owner | Tasks |
|---|---|
| Agent (me) | All code: security hardening, indexes, rate limit, webhook verify, UI states, email-diag fixes |
| Shekhar | Railway fixes + deploys, Meta app tester setup, SMTP verify, demo workspace prep |
| Vishnu | Meta app roles access, CHLEAR real numbers for case study, client warm-list |
| Joint | Landing approval, management presentation date, launch-day broadcast |

**Rule for the next 11 days:** no new features. Security + stability + proof only. Every new idea → post-Oct-10 list.