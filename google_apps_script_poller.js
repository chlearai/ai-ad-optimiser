/**
 * Meta Lead Poller for Google Sheets (Crash Club)
 * Runs every 15 minutes, pulls today's aggregate lead conversions from Meta,
 * and appends them to the "Meta Leads Aggregate" tab.
 *
 * Setup steps:
 * 1. Open the target Google Sheet.
 * 2. Click Extensions -> Apps Script.
 * 3. Replace all default code with the contents of this file.
 * 4. Run saveToken() once (replace the placeholder below with the real token).
 * 5. Run createSheet() once.
 * 6. Run setupTrigger() once.
 * 7. Authorize permissions when asked.
 */

const SHEET_URL = "https://docs.google.com/spreadsheets/d/11X4-LGdGnNXCvcRKsyUNRx8-6Sk8bhd4okHwzFcS-WU/edit";
const SHEET_NAME = "Meta Leads Aggregate";
const API_VERSION = "v18.0";
const AD_ACCOUNT_ID = "act_577546498668650";

/**
 * Store the Meta access token in Script Properties.
 * Run this function once after pasting the code.
 */
function saveToken() {
  // System User token for API_Google_Sheet (does not expire)
  const token = "EAAPE279Orc0BSD1tpIHGSX11CZCWeEDjJu6942VX9lkSPOBLSDZAmdZAHlBhSdaqA5imZBR1iw5o6DUPoSNaCJeiXfs1AyPxzZBISzXIhlHKNrZBt0ZAtSTXkfx4UTaMKHx7Dvf34nBwaZAYKR8XVCPxBDmdQrv9EkSnvA0UysmIqPl3tUw14nTp0qzsDqfJkm8nmAZDZD";
  PropertiesService.getScriptProperties().setProperty("META_ACCESS_TOKEN", token);
  Logger.log("Token saved.");
}

/**
 * Create the "Meta Leads Aggregate" sheet tab with headers.
 * Run this function once after saveToken().
 */
function createSheet() {
  const ss = SpreadsheetApp.openByUrl(SHEET_URL);
  let sheet = ss.getSheetByName(SHEET_NAME);
  if (!sheet) {
    sheet = ss.insertSheet(SHEET_NAME);
    sheet.appendRow([
      "Timestamp (UTC)",
      "Source",
      "Campaign",
      "Ad Set",
      "Ad",
      "Conversion Event",
      "Conversions",
      "Spend",
      "Currency"
    ]);
    Logger.log("Sheet created: " + SHEET_NAME);
  } else {
    Logger.log("Sheet already exists: " + SHEET_NAME);
  }
}

/**
 * Schedule the poller to run every 15 minutes.
 * Run this function once after createSheet().
 */
function setupTrigger() {
  // Remove any existing triggers for pollMetaLeads to avoid duplicates
  ScriptApp.getProjectTriggers().forEach(t => {
    if (t.getHandlerFunction() === "pollMetaLeads") {
      ScriptApp.deleteTrigger(t);
    }
  });

  ScriptApp.newTrigger("pollMetaLeads")
    .timeBased()
    .everyMinutes(15)
    .create();

  Logger.log("Trigger created. pollMetaLeads will run every 15 minutes.");
}

/**
 * Core poller function. Fetches today's Meta insights and writes lead rows to the sheet.
 */
function pollMetaLeads() {
  const props = PropertiesService.getScriptProperties();
  const token = props.getProperty("META_ACCESS_TOKEN");

  if (!token) {
    Logger.log("ERROR: META_ACCESS_TOKEN script property is not set. Run saveToken() first.");
    return;
  }

  try {
    const ss = SpreadsheetApp.openByUrl(SHEET_URL);
    const sheet = ss.getSheetByName(SHEET_NAME);
    if (!sheet) {
      Logger.log("ERROR: Sheet tab '" + SHEET_NAME + "' not found. Run createSheet() first.");
      return;
    }

    const fields = "campaign_name,adset_name,ad_name,actions,spend,account_currency";
    const params = {
      fields: fields,
      date_preset: "today",
      level: "ad",
      access_token: token
    };
    const queryString = Object.keys(params)
      .map(k => encodeURIComponent(k) + "=" + encodeURIComponent(params[k]))
      .join("&");
    const url = `https://graph.facebook.com/${API_VERSION}/${AD_ACCOUNT_ID}/insights?${queryString}`;

    const response = UrlFetchApp.fetch(url, { muteHttpExceptions: true });
    const result = JSON.parse(response.getContentText());

    if (result.error) {
      Logger.log("Meta API Error: " + JSON.stringify(result.error));
      return;
    }

    const insights = result.data || [];
    const now = new Date().toISOString();
    let newRows = 0;

    for (const item of insights) {
      const actions = item.actions || [];
      let leadCount = 0;
      for (const action of actions) {
        if (action.action_type === "lead") {
          leadCount = parseInt(action.value, 10) || 0;
          break;
        }
      }

      if (leadCount > 0) {
        const row = [
          now,
          "Meta",
          item.campaign_name || "",
          item.adset_name || "",
          item.ad_name || "",
          "lead",
          leadCount,
          item.spend || "0",
          item.account_currency || "INR"
        ];
        sheet.appendRow(row);
        newRows++;
      }
    }

    Logger.log(`Polling complete. Appended ${newRows} rows.`);

  } catch (e) {
    Logger.log("Execution error: " + e.message);
  }
}
