/**
 * Crash Club â€” Meta Lead Ads â†’ EXISTING client sheets (LIVE, every 5 min)
 * =======================================================================
 * Workbook: crash.club - PPC Workbook 2026
 *
 * ROUTING (campaign â†’ tab):
 *   ...BLR_Hoarding_GoaOffer_20260627      â†’ "JUNE - SEPT 2026 - Goa Leads"
 *   ...BLR_Magnificent_Wedding_20260702    â†’ "JUNE - SEPT 2026 - MW Leads"
 *
 * GOA TAB (Aâ€“I):  A Lead Created | B Date&Time IST | C Source | D Occasion |
 *                 E Budget | F Purchase Timeline | G Email | H Full Name | I Phone
 * MW TAB (Aâ€“I):   A Lead Created | B Date&Time IST | C Source |
 *                 D Plans to purchase before March 2027? | E Purchase made on cc/CKC before? |
 *                 F Tentative Date of Event | G Full Name | H Email | I Phone
 *
 * Columns J onwards are NEVER touched (client's manual columns).
 * DEDUP: hidden sheet "CC_Sync_State".
 * SETUP: run setupCrashClub() ONCE. Menu "Crash Club" â†’ Sync Now / Backfill 60 Days.
 */

// âš ï¸ LONG-LIVE OAUTH TOKEN â€” expires 16 Nov 2026. (60-day token; swap with
// the never-expiring system-user token when the 2-admin approval clears.)
const META_TOKEN = "EAAehafTnUf4BSnfaX4Y0HkUOaP1AeDnHWFL1kyQLwpl4fuhYT1dEcOIL9OysRcJI9MfITDlyumwJrvqZBZCEuebZAZCHyLPZByeaSfZCckNAEpOGzdiOn3TkrvC3vCiSFquXGzrNPInJCwMCTl7qDtkexVDNOAR8ntJcLugENQTmKJD2XgZAICZAiS0PhYZBy";

const AD_ACCOUNT_ID = "act_577546498668650";
const API_VERSION   = "v18.0";
const STATE_SHEET   = "CC_Sync_State";
const PAGE_SIZE     = 100;

// (b) Only sync leads created from 17-09-2026 00:00 IST onward.
// (IST = UTC+5:30 â†’ 2026-09-16T18:30:00Z)
const FIXED_START = new Date("2026-09-16T18:30:00Z").getTime() / 1000;

// ---- campaign â†’ tab + per-campaign field keys (verified LIVE from Meta) ----
const ROUTES = [
  {
    campaign: "CHLEAR_crash.club_LeadGen_Forms_BLR_Hoarding_GoaOffer_20260627",
    tab: "JUNE - SEPT 2026 - Goa Leads",
    // column â†’ candidate Meta field names (first non-empty wins)
    fields: {
      D: ["what's_the_occasion?"],
      E: ["what's_your_jewellery_budget?"],
      F: ["when_are_you_planning_to_purchase?"],
      G: ["email", "email_address"],
      H: ["full_name", "name"],
      I: ["phone_number", "phone"]
    }
  },
  {
    campaign: "CHLEAR_crash.club_LeadGen_Forms_BLR_Magnificent_Wedding_20260702",
    tab: "JUNE - SEPT 2026 - MW Leads",
    fields: {
      D: ["do_you_plan_to_make_a_purchase_in_the_near_future_or_before_march_31st,_2027?"],
      E: ["have_you_made_a_purchase_from_c._krishniah_chetty_group_of_jewellers_or_crash.club_at_any_time_before_?"],
      F: ["tentative_wedding/special_moment/corporate_events_date._*"],
      G: ["full name", "full_name", "name"],
      H: ["email", "email_address"],
      I: ["phone_number", "phone"]
    }
  }
];

/* ============ SETUP â€” run ONCE ============ */
function setupCrashClub() {
  ensureStateSheet_();
  ScriptApp.getProjectTriggers().forEach(t => {
    if (t.getHandlerFunction() === "syncCrashClubLeads") ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger("syncCrashClubLeads").timeBased().everyMinutes(5).create();
  SpreadsheetApp.getUi().createMenu("Crash Club")
    .addItem("Sync Now", "syncCrashClubLeads")
    .addItem("Enable 5-min Auto", "setupCrashClub")
    .addItem("Backfill 60 Days", "backfill60Days")
    .addSeparator()
    .addItem("Reset Sync State (re-pull leads)", "resetState")
    .addToUi();
  Logger.log("SETUP COMPLETE â€” auto-sync every 5 minutes is LIVE.");
  const msg = syncCrashClubLeads();
  try { SpreadsheetApp.getActiveSpreadsheet().toast(String(msg), "Crash Club", 8); } catch (e) {}
}

/* ============ MAIN SYNC ============ */
function syncCrashClubLeads() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const state = ensureStateSheet_();
  const known = getKnownIds_(state);
  const rollingSince = Math.floor(Date.now() / 1000) - 3 * 86400; // 3-day rolling window
  const since = Math.max(rollingSince, FIXED_START);              // never before 17-09-2026 IST

  const result = fetchLeads_(since);
  if (result.error) {
    Logger.log("Meta API error: " + result.error);
    return "Meta API error: " + result.error;
  }

  // (b) Meta ignores nested .since() â€” enforce the start date client-side
  // NOTE: lead.created_time is a STRING like "2026-09-03T15:16:01+0000" â†’ parse to unix seconds
  const inWindow = result.leads.filter(x => { const d = parseMetaTime_(x.lead.created_time); return d ? d.getTime()/1000 >= FIXED_START : false; });

  // bucket new leads per tab (oldest first), collect their ids
  const byTab = {};
  let alreadyIn = 0, unmatched = 0;
  const appendedItems = [];

  for (const item of inWindow) {
    if (known[item.lead.id]) { alreadyIn++; continue; }
    const route = ROUTES.find(r => r.campaign === item.campaign);
    if (!route) { unmatched++; continue; }
    if (!byTab[route.tab]) byTab[route.tab] = [];
    byTab[route.tab].push(item);
  }

  let appended = 0;
  for (const tab in byTab) {
    const sheet = ss.getSheetByName(tab);
    if (!sheet) { Logger.log("SHEET NOT FOUND: " + tab); continue; }
    const items = byTab[tab].sort((a, b) => (parseMetaTime_(a.lead.created_time)||0) - (parseMetaTime_(b.lead.created_time)||0));
    const route = ROUTES.find(r => r.tab === tab);
    // (a) anti-duplicate: skip leads whose phone already exists in the tab
    const seenPhones = getTabPhones_(sheet);
    const rows = [];
    const written = [];
    for (const x of items) {
      const phone = normPhone_(pickPhone_(x.lead, route));
      if (phone && seenPhones[phone]) continue;
      rows.push(buildRow_(x.lead, route));
      written.push(x);
      if (phone) seenPhones[phone] = true;
    }
    if (!rows.length) continue;
    sheet.getRange(sheet.getLastRow() + 1, 1, rows.length, rows[0].length).setValues(rows);
    appended += rows.length;
    written.forEach(x => appendedItems.push([x.lead.id, tab, new Date()]));
  }

  if (appendedItems.length) {
    state.getRange(state.getLastRow() + 1, 1, appendedItems.length, 3).setValues(appendedItems);
  }

  const msg = "Sync done. Appended: " + appended +
    " | already synced: " + alreadyIn +
    " | other campaigns: " + unmatched;
  Logger.log(msg);
  return msg;
}

/* ============ BACKFILL (history) ============ */
function backfill60Days() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const state = ensureStateSheet_();
  const known = getKnownIds_(state);
  // (b) backfill also honours the 17-09-2026 IST start â€” no history before it
  const since = FIXED_START;

  const result = fetchLeads_(since);
  if (result.error) { Logger.log("Meta API error: " + result.error); return; }

  // (b) enforce start date client-side (Meta ignores nested .since())
  const inWindow = result.leads.filter(x => { const d = parseMetaTime_(x.lead.created_time); return d ? d.getTime()/1000 >= FIXED_START : false; });

  const byTab = {};
  for (const item of inWindow) {
    if (known[item.lead.id]) continue;
    const route = ROUTES.find(r => r.campaign === item.campaign);
    if (!route) continue;
    if (!byTab[route.tab]) byTab[route.tab] = [];
    byTab[route.tab].push(item);
  }

  const saved = [];
  let appended = 0;
  for (const tab in byTab) {
    const sheet = ss.getSheetByName(tab);
    if (!sheet) { Logger.log("SHEET NOT FOUND: " + tab); continue; }
    const items = byTab[tab].sort((a, b) => (parseMetaTime_(a.lead.created_time)||0) - (parseMetaTime_(b.lead.created_time)||0));
    const route = ROUTES.find(r => r.tab === tab);
    const rows = items.map(x => buildRow_(x.lead, route));
    sheet.getRange(sheet.getLastRow() + 1, 1, rows.length, rows[0].length).setValues(rows);
    appended += rows.length;
    items.forEach(x => saved.push([x.lead.id, tab, new Date()]));
  }
  if (saved.length) state.getRange(state.getLastRow() + 1, 1, saved.length, 3).setValues(saved);
  Logger.log("Backfill complete. Appended: " + appended);
}

/* ============ FETCH ============ */
function fetchLeads_(since) {
  const fields = "id,name,campaign{name},leads.since(" + since + "){id,created_time,field_data,platform}";
  let url = "https://graph.facebook.com/" + API_VERSION + "/" + AD_ACCOUNT_ID + "/ads"
    + "?fields=" + encodeURIComponent(fields)
    + "&limit=" + PAGE_SIZE
    + "&access_token=" + encodeURIComponent(META_TOKEN);

  const leads = [];
  let pages = 0;
  while (url && pages < 100) {
    const resp = UrlFetchApp.fetch(url, { muteHttpExceptions: true });
    const json = JSON.parse(resp.getContentText());
    if (json.error) return { error: json.error.message, leads: [] };
    for (const ad of (json.data || [])) {
      const campaign = (ad.campaign && ad.campaign.name) || "";
      for (const lead of (ad.leads && ad.leads.data) || []) {
        leads.push({ lead: lead, campaign: campaign });
      }
    }
    url = (json.paging && json.paging.next) || null;
    pages++;
  }
  return { error: null, leads: leads };
}

/* ============ ROW BUILDER (Aâ€“I) ============ */
function buildRow_(lead, route) {
  const f = {};
  for (const item of lead.field_data || []) {
    f[item.name] = (item.values || []).join("; ");
  }
  const pick = (keys) => {
    for (const k of keys) { if (f[k] !== undefined && f[k] !== "") return f[k]; }
    return "";
  };

  const createdDate = parseMetaTime_(lead.created_time);
  // A: Lead Created — client CSV format (ISO with IST offset, e.g. 2026-09-16T09:26:49+05:30)
  const rawCreated = createdDate
    ? Utilities.formatDate(createdDate, "Asia/Kolkata", "yyyy-MM-dd'T'HH:mm:ss'+05:30'")
    : "";
  // B: Date & Time — client format (e.g. 16-Sep-2026 09:26:49 AM, IST)
  const istDateTime = createdDate
    ? Utilities.formatDate(createdDate, "Asia/Kolkata", "dd-MMM-yyyy hh:mm:ss a")
    : "";
  // C: platform (fb/ig lowercased by Meta â†’ normalise)
  const platRaw = String(lead.platform || "").toUpperCase();
  const platform = (platRaw === "IG") ? "IG" : (platRaw === "FB" ? "FB" : "Meta");

  const cols = route.fields;
  const D = pick(cols.D);
  const E = pick(cols.E);
  const F = pick(cols.F);
  const G = pick(cols.G);
  const H = pick(cols.H);
  const I = pick(cols.I);

  return [rawCreated, istDateTime, platform, D, E, F, G, H, I];
}

/* ============ RESET STATE (one-click re-pull) ============ */
// Clears the hidden CC_Sync_State ledger so leads get re-appended with
// current formatting. Run AFTER deleting any broken/duplicate rows.
// Phone-dedup still protects rows already in the tabs.
function resetState() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const state = ss.getSheetByName(STATE_SHEET);
  if (state) {
    const lastRow = state.getLastRow();
    if (lastRow > 1) state.deleteRows(2, lastRow - 1);
  }
  Logger.log("State cleared. Re-running sync...");
  const msg = syncCrashClubLeads();
  try { SpreadsheetApp.getActiveSpreadsheet().toast(String(msg), "Reset + Sync", 8); } catch (e) {}
  return msg;
}

/* ============ TIME PARSER ============ */
// Meta created_time is a STRING like "2026-09-16T19:39:44+0000" (never a unix number here).
// JS Date parses this fine (treats +0000 as UTC); return null on failure.
function parseMetaTime_(s) {
  if (!s) return null;
  const d = new Date(s);
  return isNaN(d.getTime()) ? null : d;
}

/* ============ STATE (dedup) ============ */
// (a) phone-based duplicate guard against manually pasted rows
function normPhone_(p) {
  const digits = String(p || "").replace(/\D/g, "");
  return digits.length > 10 ? digits.slice(-10) : digits;
}
function pickPhone_(lead, route) {
  for (const item of lead.field_data || []) {
    if (route.fields.I.indexOf(item.name) !== -1) {
      return (item.values || []).join("; ");
    }
  }
  return "";
}
function getTabPhones_(sheet) {
  const map = {};
  // phone lives in column I (9th column)
  const lastRow = sheet.getLastRow();
  if (lastRow < 2) return map;
  const vals = sheet.getRange(2, 9, lastRow - 1, 1).getValues();
  for (const [v] of vals) {
    const p = normPhone_(v);
    if (p) map[p] = true;
  }
  return map;
}
function ensureStateSheet_() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let s = ss.getSheetByName(STATE_SHEET);
  if (!s) {
    s = ss.insertSheet(STATE_SHEET);
    s.appendRow(["lead_id", "tab", "synced_at"]);
    s.hideSheet();
  }
  return s;
}
function getKnownIds_(state) {
  const map = {};
  const lastRow = state.getLastRow();
  if (lastRow < 2) return map;
  const ids = state.getRange(2, 1, lastRow - 1, 1).getValues();
  for (const [id] of ids) { if (id) map[String(id)] = true; }
  return map;
}