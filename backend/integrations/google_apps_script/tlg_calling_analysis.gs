/** Reporting only. Does not edit Sheet1, lead-copy functions or their triggers. */
const TLG_CALLING_BOOKS = [
  {name:'Jayanagar',id:'1xAm7QLr-fwTnkqm8o6uYzMbqoRd_-zInzbS-SKn_EZ4'},
  {name:'Yelahanka',id:'1XbWmGBkA7v2ZRf8qZRt0SGLtAzfLgAu2Hhy5-Uc8WdE'},
  {name:'Whitefield',id:'1j0uXY4aZsiBvoe99eMKNWNsgpYzoFB_Yp2xgai58Fc0'},
  {name:'New BEL Road',id:'1A4LfmmPJNBOj9GHFPF2dTHa92J6vUuu2Q404rdgS3sI'}
];
const TLG_CALLING_KEYS = ['leads','called','not_called','contacted','not_contactable','interested','not_interested','junk','pending','scheduled','not_scheduled','visited','not_visited'];
function tlgCallingClassify_(text, hasCalls) {
  text = String(text || '').toLowerCase();
  const invalid = /wrong (mobile |phone |contact )?number|invalid (mobile |phone |contact )?number|number (is )?(wrong|invalid)|incorrect number/.test(text);
  const junk = /junk|invalid (lead|enquir)|wrong enquir|ha[ds](n.t| not) enquir|never enquir|didn.t enquir|hadn.t enquir|classes for adults/.test(text);
  const no = /not int[er]+ested|not intrested|not looking|do not contact|asked not to call/.test(text);
  const visited = /iv done|visit completed|has attended|attended a trial|attended a free trial|attened a free trial/.test(text);
  const scheduled = visited || /iv booked|iv sch[eu]+duled|trial confirmed|confirmed iv|confirmed trial|trial scheduled/.test(text);
  const interest = scheduled || /(^|[^a-z])int(er|re)ested([^a-z]|$)|will schedule|will confirm/.test(text);
  const called = hasCalls || /no response|no answer|not picking|rnr|busy|switched off|not reachable|incoming barred|declining|disconnect|contacted|connected|call back|callback|details shared|shared details|will let|will get back|not int[er]+ested|not intrested|junk|invalid|wrong|iv booked|iv sch[eu]+duled|iv done|attended|visit completed|never enquir|didn.t enquir|hadn.t enquir|classes for adults/.test(text);
  const contactText = text.replace(/not contacted|not connected|uncontacted|unconnected/g, '');
  const contacted = called && !invalid && (scheduled || /(^|[^a-z])(connected|contacted)([^a-z]|$)|callback requested|they will call back|details shared|shared (the )?details|will let|will get back|not int[er]+ested|not intrested|do not contact|asked not to call|ha[ds](n.t| not) enquir|never enquir|didn.t enquir|hadn.t enquir|classes for adults/.test(contactText));
  const m = Object.fromEntries(TLG_CALLING_KEYS.map(k=>[k,0]));
  m.leads=1; m.called=+called; m.not_called=1-m.called;
  m.contacted=+contacted; m.not_contactable=m.called-m.contacted;
  m.junk=+(contacted && junk); m.not_interested=+(contacted && !junk && no);
  m.interested=+(contacted && !junk && !no && interest);
  m.pending=m.contacted-m.junk-m.not_interested-m.interested;
  m.scheduled=+(m.interested && scheduled); m.not_scheduled=m.interested-m.scheduled;
  m.visited=+(m.scheduled && visited); m.not_visited=m.scheduled-m.visited;
  let reason='';
  if(m.junk) reason=/classes for adults/.test(text)?'Adult classes':/age|too old|too young|\b\d+\s*(year|yr)/.test(text)?'Age unsuitable':/ha[ds](n.t| not) enquir|never enquir|didn.t enquir|hadn.t enquir|wrong enquir|not enquir/.test(text)?'Denied enquiry':/location|far|distance|another city|other city|different town/.test(text)?'Location mismatch':/no kid|no child|(don.t|doesn.t|do not|does not).*have.*(kid|child)/.test(text)?'No child':'Unspecified junk';
  return {metrics:m,reason,invalid:+(invalid && called)};
}
function tlgCallingRead_(book) {
  const ss=SpreadsheetApp.openById(book.id), sheet=ss.getSheetByName('Sheet1');
  if(!sheet) throw new Error(book.name+': Sheet1 missing');
  const data=sheet.getDataRange().getValues(), headers=data[0].map(x=>String(x).trim().toLowerCase());
  const parent=headers.indexOf('parent name'), email=headers.indexOf('email'), timestamp=headers.indexOf('timestamp');
  const first=headers.indexOf('call 1 date & time'), last=headers.indexOf('call 3 notes');
  if([parent,email,timestamp].some(x=>x<0)) throw new Error(book.name+': required lead headers missing');
  const days={};
  data.slice(1).forEach(row=>{
    if(!String(row[parent]||'').trim() || /test/i.test(String(row[parent]||'')+' '+String(row[email]||''))) return;
    const value=row[timestamp];
    const day=value instanceof Date && !isNaN(value.getTime())?Utilities.formatDate(value,ss.getSpreadsheetTimeZone(),'yyyy-MM-dd'):(String(value).match(/^\d{4}-\d{2}-\d{2}/)||['unknown'])[0];
    const result=tlgCallingClassify_(row.slice(7).filter(v=>v!=='' && v!==null).join(' '),first>=0 && last>=first && row.slice(first,last+1).some(v=>v!=='' && v!==null));
    if(!days[day]) days[day]={date:day,metrics:Object.fromEntries(TLG_CALLING_KEYS.map(k=>[k,0])),junk_reasons:{},invalid_numbers:0};
    TLG_CALLING_KEYS.forEach(k=>days[day].metrics[k]+=result.metrics[k]);
    if(result.reason) days[day].junk_reasons[result.reason]=(days[day].junk_reasons[result.reason]||0)+1;
    days[day].invalid_numbers+=result.invalid;
  });
  return {name:book.name,daily:Object.values(days).sort((a,b)=>a.date.localeCompare(b.date))};
}
function tlgCallingAggregate_(centre,start,end) {
  const m=Object.fromEntries(TLG_CALLING_KEYS.map(k=>[k,0])), reasons={}; let invalid=0;
  centre.daily.filter(d=>d.date==='unknown'||d.date>=start&&d.date<=end).forEach(d=>{
    TLG_CALLING_KEYS.forEach(k=>m[k]+=d.metrics[k]);
    Object.entries(d.junk_reasons).forEach(([k,v])=>reasons[k]=(reasons[k]||0)+v);
    invalid+=d.invalid_numbers;
  });
  return {name:centre.name,...m,reasons,invalid};
}
function tlgCallingRow_(row) {
  const ratio=(a,b)=>b?a/b:0;
  return [row.name,row.leads,row.called,ratio(row.called,row.leads),row.not_called,ratio(row.not_called,row.leads),row.contacted,ratio(row.contacted,row.called),row.not_contactable,ratio(row.not_contactable,row.called),row.interested,ratio(row.interested,row.contacted),row.not_interested,ratio(row.not_interested,row.contacted),row.junk,ratio(row.junk,row.contacted),row.pending,ratio(row.pending,row.contacted),row.scheduled,ratio(row.scheduled,row.interested),row.not_scheduled,ratio(row.not_scheduled,row.interested),row.visited,ratio(row.visited,row.scheduled),row.not_visited,ratio(row.not_visited,row.scheduled)];
}
function tlgCallingWrite_(snapshot,start,end) {
  const rows=snapshot.centres.map(c=>tlgCallingAggregate_(c,start,end));
  const total={name:'Total',...Object.fromEntries(TLG_CALLING_KEYS.map(k=>[k,rows.reduce((s,r)=>s+r[k],0)]))};
  const headers=['Centre','Leads','Called','%','Not called','%','Contacted','%','Not contactable','%','Interested','%','Not interested','%','Junk','%','Pending / Unclassified','%','Scheduled','%','Not scheduled','%','Visited','%','Not visited','%'];
  TLG_CALLING_BOOKS.forEach(book=>{
    const ss=SpreadsheetApp.openById(book.id), sheet=ss.getSheetByName('Analysis')||ss.insertSheet('Analysis');
    if(sheet.getMaxColumns()<26) sheet.insertColumnsAfter(sheet.getMaxColumns(),26-sheet.getMaxColumns());
    sheet.getRange(1,1,40,26).breakApart().clear(); sheet.showColumns(1,26);
    sheet.getRange('A1:B1').merge().setValue('TLG').setFontSize(16).setFontWeight('bold');
    sheet.getRange('C1:Z1').merge().setValue('Centrewise Calling MIS').setFontSize(16).setFontWeight('bold');
    sheet.getRange('A2:B2').merge().setValue(start+' to '+end).setWrap(true);
    sheet.getRange('C2:Z2').merge().setValue('Updated '+Utilities.formatDate(new Date(snapshot.checked_at),'Asia/Kolkata','dd MMM yyyy, h:mm a')+' IST · Daily refresh 9am IST');
    [[1,10,'Leads Outcome','#dbeafe'],[11,8,'Outcomes of contacted leads','#ede9fe'],[19,8,'Visit outcomes','#d1fae5']].forEach(([col,width,label,color])=>{
      sheet.getRange(4,col===1?3:col,1,col===1?width-2:width).merge().setValue(label).setBackground(color).setFontWeight('bold');
      sheet.getRange(5,col,6,width).setBackground(color);
    });
    sheet.getRange(5,1,1,26).setValues([headers]).setFontWeight('bold').setWrap(true);
    sheet.getRange(6,1,5,26).setValues([...rows,total].map(tlgCallingRow_)).setNumberFormat('0');
    [4,6,8,10,12,14,16,18,20,22,24,26].forEach(c=>sheet.getRange(6,c,5,1).setNumberFormat('0.0%'));
    sheet.getRange('A10:Z10').setFontWeight('bold').setBackground('#e2e8f0');
    sheet.getRange('C12:Z12').merge().setValue('% bases: Called / Not called → Leads; Contacted / Not contactable → Called; outcomes → Contacted; Scheduled / Not scheduled → Interested; Visited / Not visited → Scheduled.').setWrap(true);
    sheet.getRange('C13:Z13').merge().setValue('Wrong / invalid numbers are Not contactable. Scheduled includes completed visits. Test names/emails excluded; repeated submissions remain separate.');
    sheet.getRange('C14:Z14').merge().setValue('Legacy remarks and new calling fields are used. Unknown dates are included for continuity. Conflicting / unclear notes need branch review.');
    sheet.getRange('C16:J16').merge().setValue('Junk reasons — centrewise').setFontWeight('bold');
    let line=17;
    rows.forEach(row=>{
      sheet.getRange(line++,3,1,8).merge().setValue(row.name+' — '+row.junk+' Junk').setFontWeight('bold');
      const entries=Object.entries(row.reasons);
      if(!entries.length) sheet.getRange(line++,3,1,8).merge().setValue('• No contacted leads classified as Junk.');
      entries.forEach(([reason,count])=>sheet.getRange(line++,3,1,8).merge().setValue('• '+reason+': '+count));
      if(row.invalid) sheet.getRange(line++,3,1,8).merge().setValue('• Wrong / invalid numbers: '+row.invalid+' — under Not contactable; excluded from contacted Junk.');
    });
    sheet.getRange(1,1,Math.max(line,14),26).setFontFamily('Arial').setVerticalAlignment('middle');
    sheet.setColumnWidth(1,150);sheet.setColumnWidth(2,70);
    for(let c=3;c<=26;c++) sheet.setColumnWidth(c,c%2===0?72:116);
    sheet.setColumnWidth(17,155);sheet.setRowHeight(5,56);sheet.setFrozenRows(5);sheet.setFrozenColumns(2);
    sheet.getRange(6,2,5,25).setHorizontalAlignment('center');
  });
  SpreadsheetApp.flush();
}
function refreshTlgCallingAnalysis() {
  const lock=LockService.getScriptLock(); if(!lock.tryLock(1000)) throw new Error('Another branch job is running; retry next minute');
  try {
    const now=new Date(), today=Utilities.formatDate(now,'Asia/Kolkata','yyyy-MM-dd');
    const yesterday=new Date(today+'T00:00:00+05:30');yesterday.setDate(yesterday.getDate()-1);
    const end=Utilities.formatDate(yesterday,'Asia/Kolkata','yyyy-MM-dd');
    const snapshot={version:1,checked_at:now.toISOString(),centres:TLG_CALLING_BOOKS.map(tlgCallingRead_)};
    tlgCallingWrite_(snapshot,'2026-08-01',end);
    const response=UrlFetchApp.fetch(TLG_REPORT_CONFIG.url,{method:'post',contentType:'application/json',headers:{Authorization:'Bearer '+TLG_REPORT_CONFIG.token},payload:JSON.stringify({filter_version:1,calling_report:snapshot}),muteHttpExceptions:true});
    if(response.getResponseCode()!==200) throw new Error('Calling analysis publish failed: HTTP '+response.getResponseCode());
    ScriptPropertiesForCalling_().setProperty('TLG_CALLING_LAST_SUCCESS',today);
    console.log('Four Analysis tabs and InsightDesk calling MIS updated. Aggregate counts only.');
  } finally {lock.releaseLock();}
}
function ScriptPropertiesForCalling_(){return PropertiesService.getScriptProperties();}
function tlgCallingDailyTick() {
  const now=new Date(),today=Utilities.formatDate(now,'Asia/Kolkata','yyyy-MM-dd'), hour=Number(Utilities.formatDate(now,'Asia/Kolkata','H'));
  if(hour>=9 && ScriptPropertiesForCalling_().getProperty('TLG_CALLING_LAST_SUCCESS')!==today) refreshTlgCallingAnalysis();
}
function installTlgCallingAnalysis() {
  if(!ScriptApp.getProjectTriggers().some(t=>t.getHandlerFunction()==='tlgCallingDailyTick')) ScriptApp.newTrigger('tlgCallingDailyTick').timeBased().everyMinutes(1).create();
  refreshTlgCallingAnalysis();
}
function updateTlgCallingSheetsNow() {
  const now=new Date(), today=Utilities.formatDate(now,'Asia/Kolkata','yyyy-MM-dd');
  const yesterday=new Date(new Date(today+'T00:00:00+05:30').getTime()-86400000);
  const end=Utilities.formatDate(yesterday,'Asia/Kolkata','yyyy-MM-dd');
  const snapshot={version:1,checked_at:now.toISOString(),centres:TLG_CALLING_BOOKS.map(tlgCallingRead_)};
  tlgCallingWrite_(snapshot,'2026-08-01',end);
  console.log('Combined tables updated in all four Analysis tabs.');
}
