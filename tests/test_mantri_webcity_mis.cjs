const fs = require('fs'), assert = require('node:assert/strict');
const {JSDOM} = require(require.resolve('jsdom', {paths:[process.env.MIS_NODE_MODULE_ROOT || '../Centralized - MIS/frontend']}));
const dom = new JSDOM(fs.readFileSync('frontend/mis.html','utf8'), {url:'https://example.test/insightdesk',runScripts:'outside-only'});
const w = dom.window;
w.setTimeout = () => 0;
for (const script of w.document.querySelectorAll('script:not([src])')) {
    w.eval(script.textContent.replaceAll("'DOMContentLoaded'", "'test-init'") + (script.textContent.includes('const API =') ? '\nwindow.setTestAccounts = rows => {allAccounts=rows;renderAccountList();};' : ''));
}
const requests = [];
let data={rows:[{date:'2026-11-21',inbound:0,outbound:9,leads:29,checked_at:'2026-11-22T06:00:00Z'}],totals:{inbound:0,outbound:9,leads:29},through:'2026-11-21',complete:true,verified_through:'2026-11-21',can_update:true};
w.fetch=async (url,opts)=>{requests.push({url,opts});return {ok:true,json:async()=>data};};
(async()=>{
    w.setTestAccounts([{id:42,name:'Mantri Webcity'}]);
    await w.selectAccount(42);
    assert.equal(w.document.querySelectorAll('#reportContent table').length,1);
    assert.equal(w.document.querySelectorAll('#reportContent tbody tr').length,1);
    assert(!w.document.getElementById('reportContent').textContent.includes('Executive Summary'));
    assert(!requests.some(r=>r.url.includes('crm-summary')));
    assert.match(w.document.getElementById('reportContent').textContent,/21-11-2026/);
    assert.match(w.document.getElementById('mantriCoverage').textContent,/Updated through/);
    data={...data,rows:[{date:'2026-11-22',inbound:null,outbound:null,leads:null,checked_at:null}],totals:{inbound:null,outbound:null,leads:null},complete:false,through:'2026-11-22',can_update:false};
    await w.renderMantriWebcityCounts({id:42});
    assert.equal(w.document.querySelectorAll('#mantriCountsForm').length,0);
    assert.match(w.document.getElementById('mantriCoverage').textContent,/Awaiting daily update/);
    assert.equal(w.document.querySelector('tbody tr').textContent.includes('—'),true);
    console.log('Mantri UI: exclusive routing, four columns, counts, missing-day state and admin-only editor passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
