const fs = require('fs'), assert = require('node:assert/strict');
const {JSDOM} = require(require.resolve('jsdom', {paths:['../Centralized - MIS/frontend']}));
const dom = new JSDOM(fs.readFileSync('frontend/mis.html', 'utf8'), {url:'https://example.test/insightdesk', runScripts:'outside-only'});
const w = dom.window;
w.setTimeout = () => 0;
for (const script of w.document.querySelectorAll('script:not([src])')) {
    w.eval(script.textContent.replaceAll("'DOMContentLoaded'", "'test-init'") + (script.textContent.includes("const API =") ? '\nwindow.testSetAccounts = rows => { allAccounts = rows; renderAccountList(); };' : ''));
}
let slots = Array.from({length:3}, () => ({name:'', url:''}));
w.fetch = async (url, opts) => {
    if (opts.method === 'PUT') {
        const slot = Number(url.split('/').pop()) - 1;
        slots[slot] = JSON.parse(opts.body);
        return {ok:true, json:async () => slots[slot]};
    }
    return {ok:true, json:async () => slots};
};
(async () => {
    w.testSetAccounts([{id:7,name:'EduInfra Expand'}]);
    await w.selectAccount(7);
    assert.equal(w.document.querySelectorAll('#eduInfraSlots form').length, 3);
    assert(!w.document.getElementById('reportContent').textContent.includes('Executive Summary'));
    const field = w.document.getElementById('eduUrl1');
    field.value = 'https://docs.google.com/spreadsheets/d/test-sheet/edit#gid=2';
    await w.saveEduInfraSheet({preventDefault(){}, currentTarget:w.document.getElementById('eduSheet1')}, 7, 1);
    assert.equal(w.document.getElementById('eduOpen1').href, field.value);
    await w.renderEduInfraSheets(7);
    assert.equal(w.document.getElementById('eduUrl1').value, slots[0].url);
    w.updateEduInfraOpen(1, 'javascript:alert(1)');
    assert(!w.document.getElementById('eduOpen1').hasAttribute('href'));
    slots[0].name = '<img src=x onerror=alert(1)>';
    await w.renderEduInfraSheets(7);
    assert.equal(w.document.querySelectorAll('#eduInfraSlots img').length, 0);
    w.document.getElementById('eduUrl1').value = '';
    await w.saveEduInfraSheet({preventDefault(){}, currentTarget:w.document.getElementById('eduSheet1')}, 7, 1);
    assert(!w.document.getElementById('eduOpen1').hasAttribute('href'));
    assert.equal(w.document.getElementById('eduStatus1').textContent, 'Slot cleared');
    console.log('EduInfra UI: three slots, save/reload, safe Open links, escaping and clearing passed');
})().catch(e => {console.error(e); process.exitCode=1;});
