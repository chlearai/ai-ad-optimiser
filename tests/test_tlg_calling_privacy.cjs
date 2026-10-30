const fs=require('fs'),vm=require('vm'),assert=require('assert');
const context=vm.createContext({console});
vm.runInContext(fs.readFileSync(__dirname+'/../backend/integrations/google_apps_script/tlg_calling_analysis.gs','utf8'),context);
const classify=(t,c=false)=>context.tlgCallingClassify_(t,c).metrics;
for(const text of ['Wrong number Connected','Invalid phone number IV done','Incorrect number not interested']) {
  const m=classify(text,true);assert.equal(m.contacted,0);assert.equal(m.not_contactable,1);assert.equal(m.junk,0);
}
assert.equal(classify('Junk lead').contacted,0);
assert.equal(classify('Connected; has not enquired; Junk').junk,1);
assert.equal(classify('Connected IV Done').scheduled,1);
assert.equal(classify('Connected IV Done').visited,1);
assert.equal(classify('IV Done, Not Interested').not_interested,1);
assert.equal(classify('IV Done, Not Interested').interested,0);
assert.equal(classify('RNR / No answer',true).not_contactable,1);
assert.equal(classify('Connected; will get back').pending,1);
assert.equal(classify('').not_called,1);
for(const text of ['','Wrong number','Junk lead','Not interested','Connected;will confirm','IV booked','IV done']) {
 const m=classify(text);
 assert.equal(m.leads,m.called+m.not_called);assert.equal(m.called,m.contacted+m.not_contactable);
 assert.equal(m.contacted,m.interested+m.not_interested+m.junk+m.pending);
 assert.equal(m.interested,m.scheduled+m.not_scheduled);assert.equal(m.scheduled,m.visited+m.not_visited);
}
console.log('Calling classification: invalid-number precedence, exclusive outcomes and all five identities passed.');
const books=[['Jayanagar','1xAm7QLr-fwTnkqm8o6uYzMbqoRd_-zInzbS-SKn_EZ4'],['Yelahanka','1XbWmGBkA7v2ZRf8qZRt0SGLtAzfLgAu2Hhy5-Uc8WdE'],['Whitefield','1j0uXY4aZsiBvoe99eMKNWNsgpYzoFB_Yp2xgai58Fc0'],['New BEL Road','1A4LfmmPJNBOj9GHFPF2dTHa92J6vUuu2Q404rdgS3sI']];
const writes={};
context.SpreadsheetApp={flush(){},openById(id){
 const cells=[];writes[id]=cells;
 const range={};for(const method of ['breakApart','clear','merge','setFontSize','setFontWeight','setWrap','setBackground','setNumberFormat','setFontFamily','setVerticalAlignment','setHorizontalAlignment'])range[method]=()=>range;
 range.setValue=v=>{cells.push(v);return range};range.setValues=v=>{cells.push(v);return range};
 const sheet={getMaxColumns:()=>26,getRange:()=>range};for(const method of ['showColumns','setColumnWidth','setRowHeight','setFrozenRows','setFrozenColumns'])sheet[method]=()=>sheet;
 return {getSheetByName:()=>sheet};
}};
context.Utilities={formatDate:()=> '30 Oct 2026'};
const metrics=['leads','called','not_called','contacted','not_contactable','interested','not_interested','junk','pending','scheduled','not_scheduled','visited','not_visited'];
const snapshot={checked_at:new Date().toISOString(),centres:books.map(([name],i)=>({name,daily:[{date:'2026-10-29',metrics:{...Object.fromEntries(metrics.map(k=>[k,0])),leads:i+1,not_called:i+1},junk_reasons:{},invalid_numbers:0}]}))};
context.tlgCallingWrite_(snapshot,'2026-08-01','2026-10-29');
for(const [name,id] of books){
 const tables=writes[id].filter(Array.isArray);assert.equal(tables.length,2);assert.equal(tables[1].length,1);assert.equal(tables[1][0][0],name);
 for(const [other] of books)if(other!==name)assert.equal(JSON.stringify(writes[id]).includes(other),false,'Foreign centre written into '+name);
 assert.equal(JSON.stringify(tables[1]).includes('Total'),false);
}
assert.equal(snapshot.centres.length,4,'Consolidated snapshot must remain intact for InsightDesk');
console.log('Privacy regression: each workbook contains only its own centre; InsightDesk snapshot retains all four.');

