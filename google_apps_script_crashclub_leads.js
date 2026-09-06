/**
 * Crash Club — Meta Lead Ads -> Google Sheet (Meta-only, no server)
 * ================================================================
 * The sheet polls Meta directly and appends INDIVIDUAL lead details
 * (name, phone, email, custom questions) as they drop in Meta.
 * Runs every 5 minutes via a time-driven trigger.
 *
 * SETUP (one time):
 * 1. Open the target Google Sheet -> Extensions -> Apps Script.
 * 2. Replace all code with this file's contents.
 * 3. Run saveToken() once (token is already filled in below).
 * 4. Run createSheet() once.
 * 5. Run setupTrigger() once.
 * 6. Authorize when Google asks (it only needs access to THIS sheet).
 * 7. IMPORTANT: the Meta system user must have access to the Crash Club
 *    ad account (Meta Business Settings -> Accounts -> Ad accounts ->
 *    Crash Club account -> People/System users -> add ChlearsakhaaopsBot
 *    with Standard Access incl. leads_retrieval).
 */

const AD_ACCOUNT_ID = "act_577546498668650";
const API_VERSION = "v18.0";
const SHEET_NAME = "Crash Club Leads";
const LOOKBACK_HOURS = 48;   // re-scan window; dedup makes repeats harmless
const PAGE_SIZE = 100;

const HEADERS = [
  "Lead ID",
  "Lead Created (UTC)",
  "Name",
  "Phone",
  "Email",
  "City",
  "Campaign",
  "Ad Set",
  "Ad",
  "Custom Questions",
  "Synced At (UTC)"
];

/** Run once: store the Meta system-user token (never expires). */
function saveToken() {
  const token = "EAAPE279Orc0BSD1tpIHGSX11CZCWeEDjJu6942VX9lkSPOBLSDZAmdZAHlBhSdaqA5imZBR1iw5o6DUPoSNaCJeiXfs1AyPxzZBISzXIhlHKNrZBt0ZAtSTXkfx4UTaMKHx7Dvf34nBwaZAYKR8XVCPxBDmdQrv9EkSnvA0UysmIqPl3tUw14nTp0qzsDqfJkm8nmAZDZD";
  PropertiesService.getScriptProperties().setProperty("META_TOKEN", token);
  Logger.log("Token saved.");
}

/** Run once: create the leads tab with headers. */
function createSheet() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let sheet = ss.getSheetByName(SHEET_NAME);
  if (!sheet) {
    sheet = ss.insertSheet(SHEET_NAME);
    sheet.appendRow(HEADERS);
    sheet.getRange(1, 1, 1, HEADERS.length).setFontWeight("bold").setBackground("#fef2c7");
    sheet.setFrozenRows(1);
    Logger.log("Sheet created: " + SHEET_NAME);
  } else {
    Logger.log("Sheet already exists: " + SHEET_NAME);
  }
}

/** Run once: schedule polling every 5 minutes. */
function setupTrigger() {
  ScriptApp.getProjectTriggers().forEach(t => {
    if (t.getHandlerFunction() === "pollCrashClubLeads") {
      ScriptApp.deleteTrigger(t);
    }
  });
  ScriptApp.newTrigger("pollCrashClubLeads")
    .timeBased()
    .everyMinutes(5)
    .create();
  Logger.log("Trigger set: pollCrashClubLeads every 5 minutes.");
}

/** ================= MAIN POLLER ================= */
function pollCrashClubLeads() {
  const token = PropertiesService.getScriptProperties().getProperty("META_TOKEN");
  if (!token) {
    Logger.log("ERROR: run saveToken() first.");
    return;
  }
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const sheet = ss.getSheetByName(SHEET_NAME);
  if (!sheet) {
    Logger.log("ERROR: run createSheet() first.");
    return;
  }

  const backfill = PropertiesService.getScriptProperties().getProperty("CC_BACKFILL") === "1";
  const lookbackDays = backfill ? 3650 : LOOKBACK_HOURS / 24;
  const sinceUnix = Math.floor(Date.now() / 1000) - lookbackDays * 86400;
  const known = getKnownLeadIds_(sheet);

  // One efficient call: ads -> nested leads (with campaign/adset names)
  let url = "https://graph.facebook.com/" + API_VERSION + "/" + AD_ACCOUNT_ID + "/ads"
    + "?fields=id,name,campaign{name},adset{name},"
    + "leads{id,created_time,field_data}"
    + "&limit=" + PAGE_SIZE
    + "&time_range={'since':'" + isoDate_(sinceUnix) + "'}"
    + "&access_token=" + encodeURIComponent(token);

  let appended = 0;
  let pages = 0;

  try {
    while (url && pages < 30) {
      const resp = UrlFetchApp.fetch(url, { muteHttpExceptions: true });
      const json = JSON.parse(resp.getContentText());
      if (json.error) {
        Logger.log("Meta API error: " + JSON.stringify(json.error));
        return;
      }
      for (const ad of (json.data || [])) {
        const campaign = (ad.campaign && ad.campaign.name) || "";
        const adset = (ad.adset && ad.adset.name) || "";
        const adName = ad.name || "";
        for (const lead of (ad.leads && ad.leads.data) || []) {
          if (known[lead.id]) continue;
          const row = buildRow_(lead, campaign, adset, adName);
          sheet.appendRow(row);
          known[lead.id] = true;
          appended++;
        }
      }
      url = (json.paging && json.paging.next) || null;
      pages++;
    }
    Logger.log("pollCrashClubLeads done. New leads appended: " + appended);
  } catch (e) {
    Logger.log("Execution error: " + e.message);
  }
}

/** Build one sheet row from a lead object. */
function buildRow_(lead, campaign, adset, adName) {
  const f = {};
  for (const item of lead.field_data || []) {
    f[item.name] = (item.values || []).join("; ");
  }

  const createdIso = lead.created_time
    ? new Date(lead.created_time * 1000).toISOString()
    : "";

  const knownKeys = {
    "full_name": 1, "name": 1, "Full Name": 1, "Name": 1,
    "phone_number": 1, "phone": 1, "Phone Number": 1, "phone_number_2": 1,
    "email": 1, "email_address": 1, "Email": 1,
    "city": 1, "City": 1, "location": 1, "current_city": 1
  };

  const custom = {};
  for (const k in f) {
    if (!knownKeys[k] && f[k]) custom[k] = f[k];
  }

  return [
    lead.id || "",
    createdIso,
    f["full_name"] || f["name"] || f["Full Name"] || f["Name"] || "",
    f["phone_number"] || f["phone"] || f["Phone Number"] || f["phone_number_2"] || "",
    f["email"] || f["email_address"] || f["Email"] || "",
    f["city"] || f["City"] || f["location"] || f["current_city"] || "",
    campaign,
    adset,
    adName,
    JSON.stringify(custom),
    new Date().toISOString()
  ];
}

/** Load existing Lead IDs from column A for dedup. */
function getKnownLeadIds_(sheet) {
  const map = {};
  const lastRow = sheet.getLastRow();
  if (lastRow < 2) return map;
  const ids = sheet.getRange(2, 1, lastRow - 1, 1).getValues();
  for (const [id] of ids) {
    if (id) map[String(id)] = true;
  }
  return map;
}

function isoDate_(unix) {
  return new Date(unix * 1000).toISOString().slice(0, 10);
}

/** Run once (optional): backfill ALL leads regardless of age. */
function backfillAll() {
  const props = PropertiesService.getScriptProperties();
  props.setProperty("CC_BACKFILL", "1");
  pollCrashClubLeads();
  props.deleteProperty("CC_BACKFILL");
  Logger.log("Backfill pass complete.");
}