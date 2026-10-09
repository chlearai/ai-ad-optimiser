// Add as a NEW file named Reporting.gs in this branch's existing Apps Script project.
// Run installTlgReporting once. This does not change the lead-copy script.
const TLG_REPORT_CONFIG = __TLG_REPORT_CONFIG__;

function installTlgReporting() {
  publishTlgDailyCounts();
  if (!ScriptApp.getProjectTriggers().some(t => t.getHandlerFunction() === 'publishTlgDailyCounts')) {
    ScriptApp.newTrigger('publishTlgDailyCounts').timeBased().everyMinutes(5).create();
  }
  console.log('Lead counts connected to InsightDesk.');
}

function publishTlgDailyCounts() {
  const book = SpreadsheetApp.openById(TLG_REPORT_CONFIG.sheetId);
  const sheet = book.getSheets().find(s => s.getSheetId() === TLG_REPORT_CONFIG.gid);
  if (!sheet) throw new Error('Master worksheet not found');
  const rows = sheet.getDataRange().getValues();
  const timestampIndex = rows[0].findIndex(h => String(h).trim().toLowerCase() === 'timestamp');
  if (timestampIndex < 0) throw new Error('Timestamp header missing');
  const counts = {};
  rows.slice(1).forEach((row, index) => {
    if (!row.some(v => String(v).trim())) return;
    const value = row[timestampIndex];
    const day = value instanceof Date && !isNaN(value.getTime())
      ? Utilities.formatDate(value, book.getSpreadsheetTimeZone(), 'yyyy-MM-dd')
      : String(value).trim().match(/^\d{4}-\d{2}-\d{2}(?=[ T]|$)/)?.[0];
    if (!day) throw new Error('Invalid Timestamp in master row ' + (index + 2));
    counts[day] = (counts[day] || 0) + 1;
  });
  const response = UrlFetchApp.fetch(TLG_REPORT_CONFIG.url, {
    method: 'post',
    contentType: 'application/json',
    headers: {Authorization: 'Bearer ' + TLG_REPORT_CONFIG.token},
    payload: JSON.stringify({daily_counts: counts}),
    muteHttpExceptions: true
  });
  if (response.getResponseCode() !== 200) {
    throw new Error('InsightDesk reporting failed: HTTP ' + response.getResponseCode());
  }
  console.log('Daily sheet lead counts sent. No customer details sent.');
}
