'use strict';
// Permanently stop only the registered daily Bhago campaign. Never resume or re-run it.
const fs=require('node:fs'),path=require('node:path');
exports.run=async function({page,PRIVATE,assertWorkspace},request){
 if(!/^[a-f0-9]{32}$/.test(request.requestId||''))throw new Error('Invalid control request');
 const base=path.join(PRIVATE,'jobs',request.requestId),resultFile=base+'.control-result.json';
 try {
  const task=JSON.parse(fs.readFileSync(base+'.control.json','utf8'));
  if(!['stop','inspect'].includes(task.action)||!/^((Lead_Calling)|(Payment_Reminder))_/.test(task.campaign))throw new Error('Invalid managed campaign');
  await page.goto('https://samvaad.sakhaa.ai/dashboard/campaign-calls',{waitUntil:'domcontentloaded'});
  await assertWorkspace(page);
  const card=page.locator('div.product-panel').filter({has:page.getByRole('heading',{name:task.campaign,exact:true})});
  await card.waitFor({timeout:20000});
  if(await card.count()!==1)throw new Error('Managed campaign is missing or ambiguous');
  for(const [label,state] of [['Completed','complete'],['Stopped','stopped']]){
   if(await card.locator('span').filter({hasText:new RegExp('^'+label+'$')}).count()===1){
    fs.writeFileSync(resultFile,JSON.stringify({state}));return;
   }
  }
  if(task.action==='inspect'){fs.writeFileSync(resultFile,JSON.stringify({state:'pending'}));return;}
  const button=card.getByRole('button',{name:/^Stop(?: Campaign)?$/i});
  if(await button.count()!==1)throw new Error('Stop control is unavailable; new daily campaigns held');
  const nativeConfirm=async dialog=>{
   if(dialog.type()==='confirm'&&/stop/i.test(dialog.message()))await dialog.accept();else await dialog.dismiss();
  };
  page.once('dialog',nativeConfirm);
  try {
   await button.click();
   const dialog=page.getByRole('dialog');
   if(await dialog.count()===1 && /stop[\s\S]*campaign|campaign[\s\S]*stop/i.test(await dialog.innerText())){
    await dialog.getByRole('button',{name:/^Stop(?: Campaign)?$/i}).click();
   }
   await card.locator('span').filter({hasText:/^Stopped$/}).waitFor({state:'visible',timeout:20000});
  } finally {page.removeListener('dialog',nativeConfirm);}
  fs.writeFileSync(resultFile,JSON.stringify({state:'stopped'}));
 } catch(error){fs.writeFileSync(resultFile,JSON.stringify({state:'needs_attention',message:error.message.slice(0,300)}));}
};
