const fs=require('node:fs');
const path=require('node:path');

async function controls(page,settings) {
  for(const [label,target,suffix] of [['concurrent calls',settings.concurrency,''],['dial delay',4,'s']]) {
    const down=page.getByRole('button',{name:'Decrease '+label,exact:true});
    const up=page.getByRole('button',{name:'Increase '+label,exact:true});
    for(let i=0;i<20;i++) {
      const current=parseInt(await down.locator('..').locator('span').innerText(),10);
      if(current===target) break;
      const button=current>target?down:up;
      if(!Number.isFinite(current)||!await button.isEnabled()) throw new Error('Requested '+label+' is unavailable');
      await button.click();
    }
    if((await down.locator('..').locator('span').innerText()).trim()!==target+suffix) throw new Error('Could not apply '+label);
  }
  if(!(await page.locator('#retry-toggle span').getAttribute('class')).includes('translate-x-[2px]')) await page.locator('#retry-toggle').click();
  const mode=page.getByRole('button',{name:'Send Now Calls start immediately',exact:true});
  if(!(await mode.getAttribute('class')).includes('border-primary')) await mode.click();
}

async function verify(page,job,settings) {
  if(await page.getByPlaceholder('Enter',{exact:true}).inputValue()!==job.campaignName) throw new Error('Campaign name mismatch');
  if(await page.getByRole('button',{name:new RegExp('^'+settings.number.replace('+','\\+'))}).count()!==1) throw new Error('Caller number mismatch');
  if(await page.getByRole('button',{name:settings.agent,exact:true}).count()!==1) throw new Error('Agent mismatch');
  const table=page.getByRole('table').filter({has:page.getByRole('columnheader',{name:/^PHONE$/i})});
  if(await table.getByRole('row').count()!==job.recipients.length+1) throw new Error('Recipient count mismatch');
  for(const recipient of job.recipients) {
    const row=table.getByRole('row').filter({has:page.getByRole('cell',{name:recipient.phone,exact:true})});
    if(await row.count()!==1) throw new Error('Missing or duplicated recipient in preview');
    if(await row.getByRole('cell',{name:recipient.name,exact:true}).count()!==1 || await row.getByRole('cell',{name:recipient.value1,exact:true}).count()!==1) throw new Error('Recipient field mismatch');
  }
  for(const [label,expected] of [['concurrent calls',String(settings.concurrency)],['dial delay','4s']]) {
    const value=(await page.getByRole('button',{name:'Decrease '+label,exact:true}).locator('..').locator('span').innerText()).trim();
    if(value!==expected) throw new Error(label+' mismatch');
  }
  if(!(await page.locator('#retry-toggle span').getAttribute('class')).includes('translate-x-[2px]')) throw new Error('Retries are not verified off');
  if(!(await page.getByRole('button',{name:'Send Now Calls start immediately',exact:true}).getAttribute('class')).includes('border-primary')) throw new Error('Send Now mode is not selected');
}

exports.run=async function({page,PRIVATE,prepare,SETTINGS,writeStatus,assertWorkspace},request) {
  const rules=JSON.parse(fs.readFileSync(path.join(__dirname,'run-rules.json'),'utf8'));
  if(rules.QWQER?.launchAllowed!==true) throw new Error('QWQER calling is '+(rules.QWQER?.state||'not authorized')+'; no campaign will be prepared or launched');
  if(request.jobFile!=='qwqer-leads-2026-10-10_2026-10-30.json') throw new Error('This source campaign was not authorized');
  const original=JSON.parse(fs.readFileSync(path.join(PRIVATE,'jobs',request.jobFile),'utf8'));
  if(original.test || original.source!=='QWQER' || original.startDate!=='2026-10-10' || original.endDate!=='2026-10-30' || original.expectedCount!==40 || original.recipients.length!==40) throw new Error('Authorized production scope mismatch');
  const {job,csvPath,fingerprint}=prepare(original), settings=SETTINGS[job.type];
  const journal=path.join(PRIVATE,'jobs',job.campaignName+'.launch.json');
  if(fs.existsSync(journal)) throw new Error('Launch already attempted; inspect the journal instead of retrying');
  if(request.stage==='prepare_production') {
    await page.reload({waitUntil:'domcontentloaded'});
    await assertWorkspace(page);
    const existing=page.getByRole('row').filter({hasText:job.campaignName});
    if(await existing.count()) throw new Error('Campaign with this name already exists');
    await page.getByRole('button',{name:'Create a New Campaign',exact:true}).click();
    await page.getByPlaceholder('Enter',{exact:true}).fill(job.campaignName);
    await page.getByRole('button',{name:'Select phone number...',exact:true}).click();
    await page.getByText(settings.number,{exact:true}).click();
    await page.getByRole('button',{name:'Select agent...',exact:true}).click();
    await page.getByText(settings.agent,{exact:true}).click();
    await page.getByRole('button',{name:'Upload CSV',exact:true}).click();
    await page.locator('input[type="file"]').setInputFiles(csvPath);
    const table=page.getByRole('table').filter({has:page.getByRole('columnheader',{name:/^PHONE$/i})});
    await table.getByRole('cell',{name:job.recipients[job.recipients.length-1].phone,exact:true}).waitFor({timeout:15000});
    await controls(page,settings);
    await verify(page,job,settings);
    await page.screenshot({path:path.join(PRIVATE,'qwqer-ready.png'),fullPage:true});
    fs.writeFileSync(path.join(PRIVATE,'qwqer-ready.txt'),await page.locator('body').innerText());
    fs.writeFileSync(path.join(PRIVATE,'jobs',job.campaignName+'.preflight.json'),JSON.stringify({fingerprint,campaignName:job.campaignName,recipientCount:job.recipients.length,caller:settings.number,agent:settings.agent,concurrency:settings.concurrency,delaySeconds:4,retries:false,sourceRecordCount:original.sourceRecordCount,excluded:original.excluded,csvPath,verifiedAt:new Date().toISOString()},null,2));
    writeStatus('production_ready',{campaignName:job.campaignName,recipientCount:job.recipients.length});
  } else if(request.stage==='launch_production') {
    const preflight=JSON.parse(fs.readFileSync(path.join(PRIVATE,'jobs',job.campaignName+'.preflight.json'),'utf8'));
    if(preflight.fingerprint!==fingerprint) throw new Error('Source changed since preview validation');
    await verify(page,job,settings);
    await require('./calling-window.cjs').configureCallingHours(page);
    const submit=page.getByRole('button',{name:'Send Now',exact:true});
    if(!await submit.isEnabled()) throw new Error('Samvaad is not ready to launch');
    const receipt={...preflight,state:'submitting',attemptedAt:new Date().toISOString()};
    fs.writeFileSync(journal,JSON.stringify(receipt,null,2),{flag:'wx'});
    writeStatus('production_submitting',{campaignName:job.campaignName,recipientCount:job.recipients.length});
    require('./calling-window.cjs').assertCallingWindow();
    await submit.click();
    const row=page.getByRole('row').filter({hasText:job.campaignName});
    await row.waitFor({timeout:30000});
    receipt.state='campaign_created';receipt.campaignRow=await row.innerText();receipt.verifiedAt=new Date().toISOString();receipt.pageUrl=page.url();
    fs.writeFileSync(journal,JSON.stringify(receipt,null,2));
    fs.writeFileSync(csvPath.replace(/\.csv$/,'.result.json'),JSON.stringify(receipt,null,2));
    await page.screenshot({path:path.join(PRIVATE,'qwqer-launched.png'),fullPage:true});
    writeStatus('production_campaign_created',{campaignName:job.campaignName,recipientCount:job.recipients.length,campaignRow:receipt.campaignRow});
  }
};
