/**
 * TLG: paste into Extensions > Apps Script, then run installTlgRouting once.
 * Runs under the Google account that installs the trigger.
 * Masters must be append-only. Do not sort/delete/insert master rows.
 * Tracking is stored in Script Properties, outside the calling sheet.
 * Disable the equivalent InsightDesk server routes before activating this script.
 */
const TLG_ROUTES = [
  {name: 'Yelahanka', source: 'https://docs.google.com/spreadsheets/d/1eQH87CVGtTF2qzPRF8rEFAENy3a65Jts7AXnvo5WBWU/edit', destination: 'https://docs.google.com/spreadsheets/d/1XbWmGBkA7v2ZRf8qZRt0SGLtAzfLgAu2Hhy5-Uc8WdE/edit#gid=0'}
];
const TLG_MARKER = '_InsightDesk_source_row';

function installTlgRouting() {
  // Validate both files and column layouts before enabling the schedule.
  TLG_ROUTES.forEach(route => {
    const sheets = tlgOpenRoute_(route);
    tlgPlan_(sheets.source.getDataRange().getDisplayValues(), sheets.destination.getLastRow() ? sheets.destination.getDataRange().getDisplayValues() : [], sheets.prefix);
  });
  if (!ScriptApp.getProjectTriggers().some(t => t.getHandlerFunction() === 'syncTlgLeads')) {
    ScriptApp.newTrigger('syncTlgLeads').timeBased().everyMinutes(1).create();
  }
  syncTlgLeads();
  console.log('TLG routing installed. Open Executions to review transfers.');
}

function syncTlgLeads() {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(1000)) return;
  const errors = [];
  try {
    TLG_ROUTES.forEach(route => {
      try {
        const sheets = tlgOpenRoute_(route);
        const source = sheets.source.getDataRange().getDisplayValues();
        let destination = sheets.destination.getLastRow() ? sheets.destination.getDataRange().getDisplayValues() : [];
        const props = PropertiesService.getScriptProperties();
        const statePrefix = 'TLG_ROW_' + sheets.prefix + ':';
        const stored = props.getProperties();
        let visibleMarker = destination.length ? destination[0].indexOf(TLG_MARKER) : -1;
        if (visibleMarker >= 0) {
          const migration = {};
          destination.slice(1).forEach((row, i) => { if (row[visibleMarker]) migration[statePrefix + (i + 2)] = String(row[visibleMarker]); });
          if (Object.keys(migration).length) props.setProperties(migration);
          sheets.destination.deleteColumn(visibleMarker + 1);
          destination.forEach(row => row.splice(visibleMarker, 1));
          Object.assign(stored, migration);
        }
        if (destination.length) {
          destination[0].push(TLG_MARKER);
          destination.slice(1).forEach((row, i) => { row[destination[0].length - 1] = stored[statePrefix + (i + 2)] || ''; });
        }
        const plan = tlgPlan_(source, destination, sheets.prefix);
        if (sheets.destination.getMaxColumns() < (plan.headers.length - 1)) {
          sheets.destination.insertColumnsAfter(sheets.destination.getMaxColumns(), (plan.headers.length - 1) - sheets.destination.getMaxColumns());
        }
        if (!destination.length) {
          sheets.destination.getRange(1, 1, 1, (plan.headers.length - 1)).setValues([plan.headers.slice(0, -1).map(tlgLiteral_)]);
        }
        // Mark existing manual copies without changing their lead data or remarks.
        plan.matches.forEach(match => {
          props.setProperty(statePrefix + match.row, match.key);
        });
        SpreadsheetApp.flush();
        // Limit each centre per run so a large backfill continues on later runs.
        const rows = plan.rows.slice(0, 500);
        if (rows.length) {
          const start = Math.max(sheets.destination.getLastRow() + 1, 2);
          const required = start + rows.length - 1;
          if (required > sheets.destination.getMaxRows()) {
            sheets.destination.insertRowsAfter(sheets.destination.getMaxRows(), required - sheets.destination.getMaxRows());
          }
          // Append lead data, then save tracking outside the sheet.
          // Existing branch cells, remarks and follow-ups are never overwritten.
          sheets.destination.getRange(start, 1, rows.length, (plan.headers.length - 1)).setValues(rows.map(row => row.slice(0, -1).map(tlgLiteral_)));
          SpreadsheetApp.flush();
          const saved = {};
          rows.forEach((row, i) => { saved[statePrefix + (start + i)] = String(row[plan.markerIndex]); });
          props.setProperties(saved);
        }
        PropertiesService.getScriptProperties().setProperty('TLG_STATUS_' + route.name, JSON.stringify({at:new Date().toISOString(), copied:rows.length, pending:plan.rows.length-rows.length}));
        console.log(route.name + ': copied ' + rows.length + ', pending ' + (plan.rows.length-rows.length));
      } catch (error) {
        PropertiesService.getScriptProperties().setProperty('TLG_STATUS_' + route.name, JSON.stringify({at:new Date().toISOString(), error:String(error)}));
        errors.push(route.name + ': ' + error.message);
      }
    });
  } finally {
    lock.releaseLock();
  }
  if (errors.length) throw new Error(errors.join(' | '));
}

function tlgSheet_(link) {
  const id = link.match(/\/spreadsheets\/d\/([\w-]+)/);
  if (!id) throw new Error('Invalid Google Sheet link');
  const book = SpreadsheetApp.openById(id[1]);
  const gid = link.match(/[?#&]gid=(\d+)/);
  const sheet = gid ? book.getSheets().find(s => s.getSheetId() === Number(gid[1])) : book.getSheets()[0];
  if (!sheet) throw new Error('Worksheet tab not found in ' + book.getName());
  return sheet;
}

function tlgOpenRoute_(route) {
  const source = tlgSheet_(route.source), destination = tlgSheet_(route.destination);
  if (source.getParent().getId() === destination.getParent().getId()) throw new Error('Use separate master and branch spreadsheets');
  return {source, destination, prefix:source.getParent().getId() + ':' + source.getSheetId()};
}

function tlgPlan_(source, destination, prefix) {
  const headers = (source[0] || []).map(String);
  const normalise = value => String(value || '').trim().replace(/\s+/g, ' ').toLowerCase();
  const sourceKeys = headers.map(normalise);
  const selected = ['Timestamp', 'Parent Name', 'Mobile Number', 'Email', 'Child Name', 'Child Age', 'Location'];
  const identity = selected.map(normalise);
  if (identity.some(h => sourceKeys.filter(k => k === h).length !== 1)) {
    throw new Error('Master row 1 must contain each of the seven lead headers exactly once');
  }
  let target = destination.length ? destination[0].map(String) : selected.concat([TLG_MARKER]);
  while (target.length && !normalise(target[target.length - 1])) target.pop();
  let targetKeys = target.map(normalise);
  if (identity.concat([normalise(TLG_MARKER)]).some(h => targetKeys.filter(k => k === h).length > 1) || identity.some(h => !targetKeys.includes(h))) {
    throw new Error('Branch row 1 must contain each of the seven lead headers exactly once');
  }
  if (!targetKeys.includes(normalise(TLG_MARKER))) target = target.concat([TLG_MARKER]);
  targetKeys = target.map(normalise);
  const markerIndex = targetKeys.indexOf(normalise(TLG_MARKER));
  const seen = new Set(destination.slice(1).map(r => String(r[markerIndex] || '')));
  const canonical = (value, header) => {
    const text = String(value == null ? '' : value).trim();
    if (header !== 'timestamp') return text;
    const parts = text.match(/^(\d{4})-(\d{1,2})-(\d{1,2})[ T](\d{1,2}):(\d{2}):(\d{2})$/);
    return parts ? parts.slice(1).map(p => p.padStart(2, '0')).join(':') : text;
  };
  const signature = (row, keys) => JSON.stringify(identity.map(h => canonical(row[keys.indexOf(h)], h)));
  const unmarked = new Map();
  destination.slice(1).forEach((row, index) => {
    if (String(row[markerIndex] || '').trim() || !row.some(v => String(v).trim())) return;
    const sig = signature(row, targetKeys);
    if (!unmarked.has(sig)) unmarked.set(sig, []);
    unmarked.get(sig).push(index + 2);
  });
  const rows = [], matches = [];
  source.slice(1).forEach((row, offset) => {
    const key = prefix + ':' + (offset + 2);
    if (!identity.some(h => String(row[sourceKeys.indexOf(h)] || '').trim()) || seen.has(key)) return;
    const candidates = unmarked.get(signature(row, sourceKeys));
    if (candidates && candidates.length) {
      matches.push({row: candidates.shift(), key});
      return;
    }
    const output = Array(target.length).fill('');
    identity.forEach(header => {
      const value = row[sourceKeys.indexOf(header)];
      output[targetKeys.indexOf(header)] = value == null ? '' : value;
    });
    output[markerIndex] = key;
    rows.push(output);
  });
  return {headers:target, rows, matches, markerIndex};
}

function tlgLiteral_(value) {
  return typeof value === 'string' && value.startsWith('=') ? "'" + value : value;
}

