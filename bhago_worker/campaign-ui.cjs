const fs=require('node:fs');
const path=require('node:path');
exports.step=async function({page,PRIVATE,prepare,SETTINGS,writeStatus,assertWorkspace},request){
  delete require.cache[require.resolve('./calling-window.cjs')];
  delete require.cache[require.resolve('./samvaad-worker.cjs')];
  const latestWorker=require('./samvaad-worker.cjs');
  prepare=latestWorker.prepare;
  SETTINGS=latestWorker.SETTINGS;
  if(['insightdesk_run','insightdesk_preflight','insightdesk_ping','insightdesk_sheet','insightdesk_google_login'].includes(request.stage)) {
    delete require.cache[require.resolve('./insightdesk-ui.cjs')];
    return require('./insightdesk-ui.cjs').run({page,PRIVATE,prepare,SETTINGS,writeStatus,assertWorkspace},request);
  }
  await assertWorkspace(page);
  if(request.stage==='export_cloud_session') {
    const state=await page.context().storageState();
    const filtered={cookies:state.cookies.filter(c=>c.domain.replace(/^\./,'')==='samvaad.sakhaa.ai'),origins:state.origins.filter(o=>o.origin==='https://samvaad.sakhaa.ai')};
    fs.writeFileSync(path.join(PRIVATE,'samvaad-cloud-bootstrap.json'),JSON.stringify(filtered),{mode:0o600});
    writeStatus('cloud_session_exported',{message:'Only the dedicated Bhago Samvaad session was saved privately for approved cloud setup; no calls started.'});
    return;
  }
  if(request.stage==='configure_hours_probe') {
    await require('./calling-window.cjs').configureCallingHours(page);
    await page.screenshot({path:path.join(PRIVATE,'calling-hours-verified.png'),fullPage:true});
    writeStatus('calling_hours_verified',{timeZone:'Asia/Calcutta',from:'10:00',to:'18:00',days:'Every day',message:'Saved form settings verified; no campaign submitted.'});
    return;
  }
  const namedLeadTest=['test-lead-002.json','test-lead-003.json','test-lead-004.json'].includes(request.jobFile);
  const namedPaymentTest=['test-payment-002.json','test-payment-003.json'].includes(request.jobFile);
  if(request.jobFile && !namedLeadTest && !namedPaymentTest && request.jobFile!=='qwqer-leads-2026-10-10_2026-10-30.json') throw new Error('Unknown job file');
  const leadTestFile=namedLeadTest?path.join(PRIVATE,'jobs',request.jobFile):path.join(PRIVATE,'test-lead-job.json');
  const paymentTestFile=namedPaymentTest?path.join(PRIVATE,'jobs',request.jobFile):path.join(PRIVATE,'test-payment-job.json');
  if(request.stage==='prepare_production'||request.stage==='launch_production') {
    delete require.cache[require.resolve('./production-ui.cjs')];
    await require('./production-ui.cjs').run({page,PRIVATE,prepare,SETTINGS,writeStatus,assertWorkspace},request);
    return;
  }
  if(request.stage==='hold_qwqer') {
    const name=await page.getByPlaceholder('Enter',{exact:true}).inputValue();
    if(name!=='Lead_Calling_2026-10-10_to_2026-10-30_2026-10-30') throw new Error('Unexpected form; leaving it unchanged');
    await page.getByRole('button',{name:'Cancel',exact:true}).click();
  } else if(request.stage==='select_number') {
    const {job}=prepare(JSON.parse(fs.readFileSync(path.join(PRIVATE,'test-lead-job.json'),'utf8')));
    await page.getByPlaceholder('Enter',{exact:true}).fill(job.campaignName);
    await page.getByRole('button',{name:'Select phone number...',exact:true}).click();
  } else if(request.stage==='select_agent') {
    await page.getByText('+918069458173',{exact:true}).click();
    await page.getByRole('button',{name:'Select agent...',exact:true}).click();
  } else if(request.stage==='upload_test') {
    const {csvPath}=prepare(JSON.parse(fs.readFileSync(path.join(PRIVATE,'test-lead-job.json'),'utf8')));
    await page.getByText('Bhago Mobility Outbound',{exact:true}).click();
    await page.getByRole('button',{name:'Upload CSV',exact:true}).click();
    await page.locator('input[type="file"]').setInputFiles(csvPath);
    await page.getByText(/Total:\s*1\s*contacts?/).waitFor({timeout:15000});
  } else if(request.stage==='reload_csv') {
    const file=path.join('C:/Users/Shekhar Raju/Downloads/Clients/Bhago/Test Runs',JSON.parse(fs.readFileSync(path.join(PRIVATE,'test-lead-job.json'),'utf8')).campaignName+'.csv');
    await page.locator('input[type="file"]').setInputFiles(file);
    await page.getByText(/Total:\s*1\s*contacts?/).waitFor({timeout:15000});
  } else if(request.stage==='rebuild_test') {
    const job=JSON.parse(fs.readFileSync(path.join(PRIVATE,'test-lead-job.json'),'utf8'));
    const csvPath=path.join('C:/Users/Shekhar Raju/Downloads/Clients/Bhago/Test Runs',job.campaignName+'.csv');
    await page.getByRole('button',{name:'Cancel',exact:true}).click();
    await page.getByRole('button',{name:'Create a New Campaign',exact:true}).click();
    await page.getByPlaceholder('Enter',{exact:true}).fill(job.campaignName);
    await page.getByRole('button',{name:'Select phone number...',exact:true}).click();
    await page.getByText('+918069458173',{exact:true}).click();
    await page.getByRole('button',{name:'Select agent...',exact:true}).click();
    await page.getByText('Bhago Mobility Outbound',{exact:true}).click();
    await page.getByRole('button',{name:'Upload CSV',exact:true}).click();
    await page.locator('input[type="file"]').setInputFiles(csvPath);
    const table=page.getByRole('table').filter({has:page.getByRole('columnheader',{name:/^PHONE$/i})});
    await table.getByRole('cell',{name:'+918050977977',exact:true}).waitFor({timeout:10000});
    if(await table.getByRole('row').count()!==2) throw new Error('Expected exactly one test recipient');
    if(await table.getByRole('cell',{name:'Shekhar',exact:true}).count()!==1) throw new Error('Test recipient name mismatch');
    if(await table.getByRole('cell',{name:'Bangalore',exact:true}).count()!==1) throw new Error('Test city mismatch');
  } else if(request.stage==='prepare_payment_test'||request.stage==='prepare_lead_test') {
    const payment=request.stage==='prepare_payment_test';
    const {job,csvPath}=prepare(JSON.parse(fs.readFileSync(payment?paymentTestFile:leadTestFile,'utf8')));
    if(!job.test||job.recipients.length!==1) throw new Error('Expected a one-recipient test job');
    const config=SETTINGS[payment?'payment':'lead'];
    await page.reload({waitUntil:'domcontentloaded'});
    await assertWorkspace(page);
    await page.getByRole('button',{name:'Create a New Campaign',exact:true}).waitFor({timeout:15000});
    if(await page.getByRole('row').filter({hasText:job.campaignName}).count()) throw new Error('This campaign already exists');
    await page.getByRole('button',{name:'Create a New Campaign',exact:true}).click();
    await page.getByPlaceholder('Enter',{exact:true}).fill(job.campaignName);
    await page.getByRole('button',{name:'Select phone number...',exact:true}).click();
    await page.getByText(config.number,{exact:true}).click();
    await page.getByRole('button',{name:'Select agent...',exact:true}).click();
    await page.getByText(config.agent,{exact:true}).click();
    await page.getByRole('button',{name:'Upload CSV',exact:true}).click();
    await page.locator('input[type="file"]').setInputFiles(csvPath);
    const table=page.getByRole('table').filter({has:page.getByRole('columnheader',{name:/^PHONE$/i})});
    await table.getByRole('cell',{name:job.recipients[0].phone,exact:true}).waitFor({timeout:10000});
    if(await table.getByRole('row').count()!==2) throw new Error('Expected exactly one test recipient');
    const decrease=page.getByRole('button',{name:'Decrease concurrent calls',exact:true});
    for(let i=0;i<10;i++) {
      const count=Number((await decrease.locator('..').locator('span').innerText()).trim());
      if(count===config.concurrency) break;
      const change=count>config.concurrency?decrease:page.getByRole('button',{name:'Increase concurrent calls',exact:true});
      if(!Number.isFinite(count)||!await change.isEnabled()) throw new Error('Cannot set the configured concurrent-call count');
      await change.click();
    }
    const delayButton=page.getByRole('button',{name:'Decrease dial delay',exact:true});
    for(let i=0;i<10;i++) {
      const seconds=parseInt(await delayButton.locator('..').locator('span').innerText(),10);
      if(seconds===4) break;
      if(seconds<4||!await delayButton.isEnabled()) throw new Error('Cannot set dial delay to 4 seconds');
      await delayButton.click();
    }
    if(!(await page.locator('#retry-toggle span').getAttribute('class')).includes('translate-x-[2px]')) await page.locator('#retry-toggle').click();
  } else if(request.stage==='launch_test') {
    const payment=request.callingType==='payment';
    const {job,csvPath}=prepare(JSON.parse(fs.readFileSync(payment?paymentTestFile:leadTestFile,'utf8')));
    const config=SETTINGS[payment?'payment':'lead'];
    if(!job.test||job.type!==(payment?'payment':'lead')||job.recipients.length!==1) throw new Error('Only the authorized one-recipient test is allowed');
    const journal=(namedLeadTest||namedPaymentTest)?path.join(PRIVATE,'jobs',job.campaignName+'.launch.json'):path.join(PRIVATE,payment?'payment-test-launch-journal.json':'test-launch-journal.json');
    if(fs.existsSync(journal)) throw new Error('Launch was already attempted; verify its recorded result instead of retrying');
    if(await page.getByPlaceholder('Enter',{exact:true}).inputValue()!==job.campaignName) throw new Error('Campaign name mismatch');
    if(await page.getByRole('button',{name:new RegExp('^'+config.number.replace('+','\\+'))}).count()!==1) throw new Error('Caller number mismatch');
    if(await page.getByRole('button',{name:config.agent,exact:true}).count()!==1) throw new Error('Agent mismatch');
    const table=page.getByRole('table').filter({has:page.getByRole('columnheader',{name:/^PHONE$/i})});
    if(await table.getByRole('row').count()!==2) throw new Error('Expected exactly one recipient before launch');
    for(const value of [job.recipients[0].phone,job.recipients[0].name,job.recipients[0].value1]) {
      if(await table.getByRole('cell',{name:value,exact:true}).count()!==1) throw new Error('Recipient preview mismatch');
    }
    const concurrency=await page.getByRole('button',{name:'Decrease concurrent calls',exact:true}).locator('..').locator('span').innerText();
    const delay=await page.getByRole('button',{name:'Decrease dial delay',exact:true}).locator('..').locator('span').innerText();
    if(concurrency.trim()!==String(config.concurrency)||delay.trim()!=='4s') throw new Error('Dialing settings mismatch');
    const retryClass=await page.locator('#retry-toggle span').getAttribute('class');
    if(!retryClass.includes('translate-x-[2px]')) throw new Error('Retries are not verified off');
    const sendMode=page.getByRole('button',{name:'Send Now Calls start immediately',exact:true});
    if(!(await sendMode.getAttribute('class')).includes('border-primary')) throw new Error('Send Now mode is not selected');
    await require('./calling-window.cjs').configureCallingHours(page);
    const submit=page.getByRole('button',{name:'Send Now',exact:true});
    if(!await submit.isEnabled()) throw new Error('Samvaad does not allow submission');
    const receipt={campaignName:job.campaignName,recipientCount:1,recipientLast4:job.recipients[0].phone.slice(-4),caller:config.number,agent:config.agent,concurrency:config.concurrency,dialDelaySeconds:4,retries:false,csvPath,attemptedAt:new Date().toISOString(),state:'submitting'};
    const proofPrefix=namedLeadTest?request.jobFile.replace('test-lead-','lead-test-').replace('.json',''):namedPaymentTest?request.jobFile.replace('test-payment-','payment-test-').replace('.json',''):payment?'payment-test':'test';
    await page.screenshot({path:path.join(PRIVATE,proofPrefix+'-before-launch.png'),fullPage:true});
    fs.writeFileSync(journal,JSON.stringify(receipt,null,2),{flag:'wx'});
    writeStatus('submitting_test',{campaignName:job.campaignName,recipientCount:1});
    require('./calling-window.cjs').assertCallingWindow();
    await submit.click();
    const created=page.getByRole('row').filter({hasText:job.campaignName});
    await created.waitFor({timeout:25000});
    receipt.state='campaign_created';
    receipt.visibleCampaignRow=await created.innerText();
    receipt.verifiedAt=new Date().toISOString();
    receipt.pageUrl=page.url();
    fs.writeFileSync(journal,JSON.stringify(receipt,null,2));
    fs.writeFileSync(csvPath.replace(/\.csv$/,'.result.json'),JSON.stringify(receipt,null,2));
    await page.screenshot({path:path.join(PRIVATE,proofPrefix+'-launched.png'),fullPage:true});
    writeStatus('test_campaign_created',{campaignName:job.campaignName,campaignRow:receipt.visibleCampaignRow});
  } else if(request.stage!=='inspect') throw new Error('This campaign step is not implemented yet');
  await page.screenshot({path:path.join(PRIVATE,'campaign-current.png'),fullPage:true});
  fs.writeFileSync(path.join(PRIVATE,'campaign-current.txt'),await page.locator('body').innerText());
  fs.writeFileSync(path.join(PRIVATE,'campaign-current.html'),await page.locator('main').count()?await page.locator('main').innerHTML():await page.locator('body').innerHTML());
  const controls=await page.locator('input,button,[role="option"],[role="switch"]').evaluateAll(els=>els.map(el=>({tag:el.tagName,type:el.type||null,role:el.getAttribute('role'),placeholder:el.getAttribute('placeholder'),label:el.getAttribute('aria-label'),text:el.tagName==='INPUT'?'':el.innerText,disabled:!!el.disabled,min:el.min||null,max:el.max||null,value:el.tagName==='INPUT'?el.value:null})));
  fs.writeFileSync(path.join(PRIVATE,'campaign-current-controls.json'),JSON.stringify(controls,null,2));
  if(request.stage!=='launch_test') writeStatus('inspection_complete',{url:page.url()});
};
