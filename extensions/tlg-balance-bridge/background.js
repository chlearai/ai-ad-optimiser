importScripts('parser.js');
const INSIGHT_ORIGIN = 'https://ai-ad-optimiser-production-dd12.up.railway.app';
const BILLING_URL = 'https://adsmanager.facebook.com/adsmanager/billing_hub/accounts/details/?asset_id=929681660424354&business_id=245258276024421&payment_account_id=929681660424354';
let busy = false;
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message?.type !== 'TLG_READ_FUNDS') return;
  let url;
  try { url = new URL(sender.url); } catch { return; }
  if (url.origin !== INSIGHT_ORIGIN || url.pathname !== '/insightdesk' || sender.frameId !== 0) return;
  if (busy) { sendResponse({error:'A balance update is already running. Please wait.'}); return; }
  busy = true;
  readFreshFunds().then(sendResponse).catch(error => sendResponse({error:error.message})).finally(() => {busy=false;});
  return true;
});
async function readFreshFunds() {
  const tab = await chrome.tabs.create({url:BILLING_URL, active:false});
  try {
    for (let attempt=0; attempt<45; attempt++) {
      await new Promise(resolve => setTimeout(resolve,1000));
      const current = await chrome.tabs.get(tab.id);
      if (current.status !== 'complete') continue;
      let result;
      try {
        [result] = await chrome.scripting.executeScript({
          target:{tabId:tab.id},
          func:() => ({url:location.href,text:document.body?.innerText || ''})
        });
      } catch { continue; }
      const page = result?.result;
      if (!page) continue;
      const pageUrl = new URL(page.url);
      if (pageUrl.hostname !== 'adsmanager.facebook.com' || !pageUrl.pathname.includes('/billing_hub/accounts/details/')) continue;
      if (pageUrl.searchParams.get('asset_id') !== '929681660424354') throw new Error('Wrong Meta account. The previous balance was retained.');
      try {
        const reading = parseTlgBillingText(page.text);
        return {...reading,checked_at:new Date().toISOString(),ad_account_id:'929681660424354'};
      } catch(error) { if (attempt===44) throw error; }
    }
    throw new Error('Meta Billing could not be read. Open Meta Billing and sign in in this Chrome profile, then click UPDATE again.');
  } finally { await chrome.tabs.remove(tab.id).catch(() => {}); }
}
