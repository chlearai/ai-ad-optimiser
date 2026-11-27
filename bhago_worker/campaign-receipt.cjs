'use strict';

function validateCard(text, job, settings) {
  const lines=String(text).split(/\r?\n/).map(x=>x.trim()).filter(Boolean);
  if(!lines.includes(job.campaignName)||!lines.includes(settings.agent)) throw new Error('Campaign identity does not match the launch');
  const status=lines.find(x=>['Active','Completed'].includes(x));
  const counts=String(text).match(/\bCALLS\s+(\d+)\s*\/\s*(\d+)/i);
  if(!status||!counts||Number(counts[2])!==job.recipients.length) throw new Error('Campaign is not active with the expected recipient count');
  return {campaign_status:status,recipientCount:Number(counts[2]),campaignRow:text};
}

async function verifyCreated(page, job, settings) {
  const heading=page.getByRole('heading',{name:job.campaignName,exact:true});
  try { await heading.waitFor({state:'visible',timeout:30000}); }
  catch (_) {
    // Read-only reconciliation after a delayed response; never submit again.
    await page.goto('https://samvaad.sakhaa.ai/dashboard/campaign-calls',{waitUntil:'domcontentloaded'});
    await heading.waitFor({state:'visible',timeout:15000});
  }
  const card=page.locator('div.product-panel').filter({has:heading});
  if(await heading.count()!==1||await card.count()!==1) throw new Error('Campaign receipt is ambiguous; inspect Samvaad before any retry');
  return validateCard(await card.innerText(),job,settings);
}

module.exports={validateCard,verifyCreated};
