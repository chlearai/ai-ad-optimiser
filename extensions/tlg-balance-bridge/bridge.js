window.addEventListener('message', async event => {
  if (event.source !== window || event.origin !== location.origin) return;
  const request = event.data;
  if (request?.type !== 'INSIGHTDESK_TLG_BALANCE_REQUEST' || typeof request.requestId !== 'string') return;
  const reply = data => window.postMessage({type:'INSIGHTDESK_TLG_BALANCE_RESPONSE',requestId:request.requestId,...data},location.origin);
  if (request.action === 'ping') { reply({connected:true}); return; }
  if (request.action !== 'read') return;
  try { reply(await chrome.runtime.sendMessage({type:'TLG_READ_FUNDS'})); }
  catch { reply({error:'The balance connection was disconnected. Reload InsightDesk and try again.'}); }
});
