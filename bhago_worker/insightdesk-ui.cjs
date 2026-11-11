'use strict';
const fs=require('node:fs');
const path=require('node:path');
const {assertCallingWindow}=require('./calling-window.cjs');

exports.run=async function(ctx,request) {
  const {page,PRIVATE,prepare,SETTINGS,assertWorkspace}=ctx;
  if(request.stage==='insightdesk_ping') {
    const ready=page.url().startsWith('https://samvaad.sakhaa.ai/dashboard/') &&
      (await page.locator('body').innerText()).toLowerCase().includes('chlearbhago@gmail.com');
    fs.writeFileSync(path.join(PRIVATE,'bridge-browser.json'),JSON.stringify({ready,updatedAt:new Date().toISOString(),pid:process.pid}));
    if(ready && process.env.BHAGO_RUNTIME==='cloud') {
      const temporary=path.join(PRIVATE,'samvaad-state.tmp');
      fs.writeFileSync(temporary,JSON.stringify(await page.context().storageState()),{mode:0o600});
      fs.renameSync(temporary,path.join(PRIVATE,'samvaad-state.json'));
    }
    return;
  }
  if(request.stage==='insightdesk_google_login') {
    const sheet=await page.context().newPage();
    await sheet.goto('https://docs.google.com/spreadsheets/d/1FSNPdW_7BHlq98v4yr8fkaKjayRADJMg0XR7tqpwFMI/edit',{waitUntil:'domcontentloaded'});
    return;
  }
  if(request.stage==='insightdesk_sheet') {
    if(!/^[a-f0-9]{32}$/.test(request.requestId||'')) throw new Error('Invalid sheet request');
    const base=path.join(PRIVATE,'jobs',request.requestId);
    const source=JSON.parse(fs.readFileSync(base+'.sheet-request.json','utf8'));
    const url=new URL(source.url);
    const id=url.pathname.match(/^\/spreadsheets\/d\/([A-Za-z0-9_-]+)(?:\/.*)?$/)?.[1];
    if(url.origin!=='https://docs.google.com'||!id||url.username||url.password) throw new Error('Invalid Google source');
    const sheet=await page.context().newPage();
    try {
      await sheet.goto(source.url,{waitUntil:'domcontentloaded',timeout:30000});
      const body=await sheet.locator('body').innerText();
      if(sheet.url().startsWith('https://accounts.google.com/')||/You need access|Request access|Access denied/i.test(body))
        throw new Error('Sign in to Google as shekhar.chlear@gmail.com in the dedicated Bhago browser to read the saved production sheets.');
      const download=sheet.waitForEvent('download',{timeout:45000}).catch(error=>({error}));
      await sheet.goto('https://docs.google.com/spreadsheets/d/'+id+'/export?format=xlsx',{waitUntil:'commit',timeout:30000}).catch(error=>{
        if(!/ERR_ABORTED|Download is starting/i.test(error.message)) throw error;
      });
      const artifact=await download;
      if(artifact.error) throw artifact.error;
      await artifact.saveAs(base+'.xlsx');
      fs.writeFileSync(base+'.sheet-result.json',JSON.stringify({state:'downloaded'}));
    } catch(error) {
      fs.writeFileSync(base+'.sheet-result.json',JSON.stringify({state:'failed',message:'Google Sheets sign-in or source access is required in the dedicated Bhago browser. Use shekhar.chlear@gmail.com; do not change sheet sharing to public.'}));
    } finally { await sheet.close(); }
    return;
  }
  if(!/^[a-f0-9]{32}$/.test(request.runId||'')) throw new Error('Invalid InsightDesk request');
  const resultFile=path.join(PRIVATE,'jobs',request.runId+'.bridge-result.json');
  const journal=path.join(PRIVATE,'jobs',request.runId+'.launch.json');
  let receipt=null;
  try {
    if(request.stage==='insightdesk_run') {
      if(process.env.BHAGO_RUNTIME==='cloud' && process.env.BHAGO_CALLING_ENABLED!=='true') throw new Error('Bhago cloud worker is in standby until go-live approval.');
      assertCallingWindow();
    }
    if(fs.existsSync(resultFile)||fs.existsSync(journal)) throw new Error('This launch was already attempted. Check its recorded status.');
    const original=JSON.parse(fs.readFileSync(path.join(PRIVATE,'jobs',request.runId+'.json'),'utf8'));
    if(original.runId!==request.runId || !['swiggy','zomato','qwqer','lead_batch','payment_reminder','test','test_lead','test_payment'].includes(original.source)) throw new Error('Run scope mismatch');
    if(original.source==='test_lead' && original.type!=='lead' || original.source==='test_payment' && original.type!=='payment') throw new Error('Test calling type mismatch');
    const {job,csvPath,fingerprint}=prepare(original), settings=SETTINGS[job.type];
    const sourceType={swiggy:'lead',zomato:'lead',qwqer:'lead',lead_batch:'lead',payment_reminder:'payment',test_lead:'lead',test_payment:'payment'}[job.source];
    if(sourceType&&sourceType!==job.type) throw new Error('Source calling type mismatch');
    await page.goto('https://samvaad.sakhaa.ai/dashboard/campaign-calls/create',{waitUntil:'domcontentloaded'});
    await assertWorkspace(page);
    await page.getByRole('button',{name:'Create a New Campaign',exact:true}).waitFor({timeout:15000});
    if(await page.getByRole('row').filter({hasText:job.campaignName}).count()) throw new Error('Campaign already exists');
    await page.getByRole('button',{name:'Create a New Campaign',exact:true}).click();
    await page.getByPlaceholder('Enter',{exact:true}).fill(job.campaignName);
    await page.getByRole('button',{name:'Select phone number...',exact:true}).click();
    await page.getByText(settings.number,{exact:true}).click();
    await page.getByRole('button',{name:'Select agent...',exact:true}).click();
    await page.getByText(settings.agent,{exact:true}).click();
    await page.getByRole('button',{name:'Upload CSV',exact:true}).click();
    await page.locator('input[type="file"]').setInputFiles(csvPath);
    const table=page.getByRole('table').filter({has:page.getByRole('columnheader',{name:/^PHONE$/i})});
    await table.getByRole('row').nth(job.recipients.length).waitFor({timeout:20000});
    // Verify every recipient in order, including deliberately repeated numbers.
    if(await table.getByRole('row').count()!==job.recipients.length+1) throw new Error('Samvaad recipient count differs from the approved preview');
    const rows=table.getByRole('row');
    const headers=(await table.getByRole('columnheader').allTextContents()).map(text=>text.trim().toLowerCase().replace(/\s+/g,''));
    for(let i=0;i<job.recipients.length;i++) {
      const cells=await rows.nth(i+1).getByRole('cell').allTextContents();
      const fields=[['phone',job.recipients[i].phone],['name',job.recipients[i].name],['value1',job.recipients[i].value1],...(job.type==='payment'?[['value2',job.recipients[i].value2],['value3',job.recipients[i].value3]]:[])];
      for(const [header,value] of fields) {
        const column=headers.indexOf(header);
        if(column<0 || String(cells[column]??'').trim()!==String(value??'')) throw new Error('Samvaad '+header+' differs from the current CSV mapping');
      }
    }
    for(const [label,target,suffix] of [['concurrent calls',settings.concurrency,''],['dial delay',4,'s']]) {
      const down=page.getByRole('button',{name:'Decrease '+label,exact:true});
      for(let i=0;i<25;i++) {
        const current=parseInt(await down.locator('..').locator('span').innerText(),10);
        if(current===target) break;
        const change=current>target?down:page.getByRole('button',{name:'Increase '+label,exact:true});
        if(!Number.isFinite(current)||!await change.isEnabled()) throw new Error('Cannot apply '+label);
        await change.click();
      }
      if((await down.locator('..').locator('span').innerText()).trim()!==target+suffix) throw new Error(label+' verification failed');
    }
    if(!(await page.locator('#retry-toggle span').getAttribute('class')).includes('translate-x-[2px]')) await page.locator('#retry-toggle').click();
    if(!(await page.locator('#retry-toggle span').getAttribute('class')).includes('translate-x-[2px]')) throw new Error('Retries are not off');
    const mode=page.getByRole('button',{name:'Send Now Calls start immediately',exact:true});
    if(!(await mode.getAttribute('class')).includes('border-primary')) await mode.click();
    if(!(await mode.getAttribute('class')).includes('border-primary')) throw new Error('Send Now mode is not selected');
    if(await page.getByPlaceholder('Enter',{exact:true}).inputValue()!==job.campaignName) throw new Error('Campaign name mismatch');
    if(await page.getByRole('button',{name:settings.agent,exact:true}).count()!==1) throw new Error('Agent mismatch');
    const caller=page.getByRole('button',{name:new RegExp('^'+settings.number.replace('+','\\+'))});
    if(await caller.count()!==1) throw new Error('Caller number mismatch');
    await require('./calling-window.cjs').configureCallingHours(page);
    const submit=page.getByRole('button',{name:'Send Now',exact:true});
    if(!await submit.isEnabled()) throw new Error('Samvaad launch unavailable');
    await page.screenshot({path:path.join(PRIVATE,'jobs',request.runId+'-ready.png'),fullPage:true});
    if(request.stage==='insightdesk_preflight') {
      fs.writeFileSync(resultFile,JSON.stringify({state:'preview_verified',campaignName:job.campaignName,recipientCount:job.recipients.length,caller:settings.number,agent:settings.agent,columns:await table.getByRole('columnheader').allTextContents(),verifiedAt:new Date().toISOString()}));
      await page.getByRole('button',{name:'Cancel',exact:true}).click();
      return;
    }
    receipt={state:'submitting',campaignName:job.campaignName,recipientCount:job.recipients.length,
      caller:settings.number,agent:settings.agent,concurrency:settings.concurrency,dialDelaySeconds:4,retries:false,
      callingHours:{timeZone:'Asia/Calcutta',from:'10:00',to:'18:00',days:'Every day'},
      fingerprint,csvPath,attemptedAt:new Date().toISOString()};
    // Durable intent BEFORE clicking. Never retry an uncertain submission.
    assertCallingWindow();
    fs.writeFileSync(journal,JSON.stringify(receipt,null,2),{flag:'wx'});
    await submit.click();
    const row=page.getByRole('row').filter({hasText:job.campaignName});
    await row.waitFor({timeout:30000});
    receipt.state='campaign_created';
    receipt.campaignRow=await row.innerText();
    receipt.verifiedAt=new Date().toISOString();
    fs.writeFileSync(journal,JSON.stringify(receipt,null,2));
    fs.writeFileSync(csvPath.replace(/\.csv$/,'.result.json'),JSON.stringify(receipt,null,2));
    await page.screenshot({path:path.join(PRIVATE,'jobs',request.runId+'-launched.png'),fullPage:true});
    fs.writeFileSync(resultFile,JSON.stringify(receipt,null,2));
  } catch(error) {
    fs.writeFileSync(resultFile,JSON.stringify({state:receipt?'uncertain':'failed',message:error.message,campaignName:receipt?.campaignName}));
  }
};
