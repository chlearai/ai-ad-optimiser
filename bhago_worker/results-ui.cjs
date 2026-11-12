'use strict';
// Read-only Samvaad result export, using its visible UI and the dedicated session.
const fs=require('node:fs');
const path=require('node:path');
exports.run=async function({page,PRIVATE,assertWorkspace},request){
  if(!/^[a-f0-9]{32}$/.test(request.requestId||'')) throw new Error('Invalid result-export request');
  const output=path.join(PRIVATE,'jobs',request.requestId+'.outcomes.json');
  const resultPage=await page.context().newPage();
  try {
    await resultPage.goto('https://samvaad.sakhaa.ai/dashboard/call-history',{waitUntil:'domcontentloaded'});
    await assertWorkspace(resultPage);
    const types=resultPage.getByRole('combobox').filter({has:resultPage.getByRole('option',{name:'All Types',exact:true})});
    const dates=resultPage.getByRole('combobox').filter({has:resultPage.getByRole('option',{name:'Last 90 days',exact:true})});
    await types.selectOption({label:'Outbound'});
    await dates.selectOption({label:'Last 90 days'});
    await resultPage.getByRole('button',{name:'Refresh',exact:true}).click();
    await resultPage.getByRole('button',{name:'Export ▾',exact:true}).waitFor({state:'visible'});
    await resultPage.getByRole('button',{name:'Export ▾',exact:true}).click();
    const download=resultPage.waitForEvent('download',{timeout:30000});
    await resultPage.getByRole('menuitem',{name:'Export CSV',exact:true}).click();
    const file=await download;
    const csvPath=path.join(PRIVATE,'latest-outcomes.csv');
    const temporary=path.join(PRIVATE,'latest-outcomes.tmp');
    await file.saveAs(temporary);
    fs.renameSync(temporary,csvPath);
    fs.writeFileSync(output,JSON.stringify({state:'downloaded',csvPath,completedAt:new Date().toISOString()}));
  } catch(error) {
    fs.writeFileSync(output,JSON.stringify({state:'failed',message:'Samvaad call history could not be exported; result sync will retry without placing calls.'}));
  } finally {await resultPage.close();}
};
