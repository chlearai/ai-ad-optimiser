'use strict';
// Only a registered daily Bhago campaign can be paused/resumed by this handler.
const fs=require('node:fs'),path=require('node:path');
exports.run=async function({page,PRIVATE,assertWorkspace},request){
 if(!/^[a-f0-9]{32}$/.test(request.requestId||''))throw new Error('Invalid control request');
 const base=path.join(PRIVATE,'jobs',request.requestId);
 const resultFile=base+'.control-result.json';
 try {
  const task=JSON.parse(fs.readFileSync(base+'.control.json','utf8'));
  if(!['pause','resume','inspect'].includes(task.action)||!/^((Lead_Calling)|(Payment_Reminder))_/.test(task.campaign))throw new Error('Invalid managed campaign');
  await page.goto('https://samvaad.sakhaa.ai/dashboard/campaign-calls',{waitUntil:'domcontentloaded'});
  await assertWorkspace(page);
  const card=page.locator('div.product-panel').filter({has:page.getByRole('heading',{name:task.campaign,exact:true})});
  await card.waitFor({timeout:20000});
  if(await card.count()!==1)throw new Error('Managed campaign is missing or ambiguous');
  if(/\bCompleted\b/.test(await card.innerText())) {
   fs.writeFileSync(resultFile,JSON.stringify({state:'complete'}));return;
  }
  if(task.action==='inspect'){fs.writeFileSync(resultFile,JSON.stringify({state:'pending'}));return;}
  const target=task.action==='pause'?'paused':'submitted';
  const opposite=card.getByRole('button',{name:task.action==='pause'?/^Resume(?: Campaign)?$/i:/^Pause(?: Campaign)?$/i});
  if(await opposite.count()===1) {fs.writeFileSync(resultFile,JSON.stringify({state:target}));return;}
  const button=card.getByRole('button',{name:task.action==='pause'?/^Pause(?: Campaign)?$/i:/^Resume(?: Campaign)?$/i});
  if(await button.count()!==1)throw new Error('Managed campaign control is unavailable; daily ordering is held');
  await button.click();
  await opposite.waitFor({state:'visible',timeout:20000});
  fs.writeFileSync(resultFile,JSON.stringify({state:target}));
 } catch(error){fs.writeFileSync(resultFile,JSON.stringify({state:'needs_attention',message:error.message.slice(0,300)}));}
};
