'use strict';
// Local, UI-only Samvaad worker. Never reads another Chrome profile or saves passwords.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');

const PRIVATE = process.env.BHAGO_PRIVATE_DIR || path.join(__dirname, 'private');
const ORIGIN = 'https://samvaad.sakhaa.ai';
const EXPECTED_ACCOUNT = 'chlearbhago@gmail.com';
const OUTPUT = process.env.BHAGO_OUTPUT_DIR || 'C:/Users/Shekhar Raju/Downloads/Clients/Bhago';
const SETTINGS = {
  lead: {number:'+918069458173', agent:'Bhago Mobility Outbound', concurrency:1, folder:'Lead Calling'},
  payment: {number:'+918069458175', agent:'Bhago Payment recovery Agent outbound', concurrency:1, folder:'Payment Reminders'}
};

function writeStatus(state, extra={}) {
  fs.mkdirSync(PRIVATE, {recursive:true});
  const data={state,updatedAt:new Date().toISOString(),pid:process.pid,...extra};
  const temp=path.join(PRIVATE,'status.tmp');
  fs.writeFileSync(temp, JSON.stringify(data,null,2));
  fs.renameSync(temp,path.join(PRIVATE,'status.json'));
  console.log(JSON.stringify(data));
}

function normalizePhone(value) {
  const raw=String(value).trim().replace(/^'/,'');
  if (!/^[+\d\s()-]+$/.test(raw)) throw new Error('Invalid phone characters');
  let digits=raw.replace(/\D/g,'');
  if (digits.length===12 && digits.startsWith('91')) digits=digits.slice(2);
  if (digits.length===11 && digits.startsWith('0')) digits=digits.slice(1);
  if (!/^[6-9]\d{9}$/.test(digits)) throw new Error('Invalid Indian mobile number');
  return '+91'+digits;
}

function validateJob(job) {
  if(job.source==='qwqer') throw new Error('QWQER calling is suspended until explicitly reinstated by Shekhar.');
  if (!SETTINGS[job.type]) throw new Error('Calling type must be lead or payment');
  if (!/^[A-Za-z0-9_-]{1,160}$/.test(job.campaignName||'')) throw new Error('Invalid campaign name');
  if (!Array.isArray(job.recipients)||!job.recipients.length) throw new Error('No recipients');
  const rows=job.recipients.map(row=>{
    const phone=normalizePhone(row.phone), name=String(row.name||'').trim();
    if (!name || /^[=+@-]/.test(name) || /[,"\r\n]/.test(name)) throw new Error('Name needs review before CSV upload');
    let value=String(row.value1||'').trim();
    {
      const cities={bangalore:'Bangalore',bengaluru:'Bangalore',bangaore:'Bangalore',delhi:'Delhi','new delhi':'Delhi'};
      value=cities[value.toLowerCase()]||value;
      if(/[,"\r\n]/.test(value)||/^[=+@-]/.test(value)) value='';
    }
    const result={phone,name,value1:value};
    if(row.source_ref) result.source_ref=row.source_ref;
    if(row.sourceDate) result.sourceDate=row.sourceDate;
    if(job.type==='payment') {
      if(!Object.hasOwn(row,'value3')) throw new Error('Payment field mapping changed; reread the saved sheet before creating this campaign');
      const vehicle=String(row.value2??'').trim();
      if(!/^[A-Za-z0-9 -]{5,20}$/.test(vehicle)) throw new Error('Invalid vehicle number');
      const amount=String(row.value3??'').trim();
      if(amount&&!/^\d+(?:\.\d+)?$/.test(amount)) throw new Error('Invalid outstanding amount');
      result.value2=vehicle;
      result.value3=amount;
      if(row.sourceDate) {
        const sourceDate=String(row.sourceDate);
        if(!/^\d{4}-\d{2}-\d{2}$/.test(sourceDate)) throw new Error('Invalid payment source date');
        result.sourceDate=sourceDate;
      }
    }
    return result;
  });
  return {...job,recipients:rows};
}

function csvText(job) {
  const payment=job.type==='payment';
  return 'Phone,Name,Value 1'+(payment?',Value 2,Value 3':'')+'\r\n'+job.recipients.map(r=>[r.phone,r.name,r.value1,...(payment?[r.value2??'',r.value3??'']:[])].join(',')).join('\r\n')+'\r\n';
}

function prepare(job) {
  job=validateJob(job);
  const directory=path.join(OUTPUT,job.test?'Test Runs':SETTINGS[job.type].folder);
  fs.mkdirSync(directory,{recursive:true});
  const csvPath=path.join(directory,job.campaignName+'.csv');
  const content=csvText(job);
  if(fs.existsSync(csvPath)&&fs.readFileSync(csvPath,'utf8')!==content) throw new Error('Existing file differs; choose a new campaign name');
  fs.writeFileSync(csvPath,content);
  const fingerprint=crypto.createHash('sha256').update(JSON.stringify(job)).digest('hex');
  return {job,csvPath,fingerprint};
}

async function capture(page,name) {
  await page.screenshot({path:path.join(PRIVATE,name+'.png'),fullPage:true});
  // Visible page text only. Never cookies, tokens, localStorage, or input values.
  fs.writeFileSync(path.join(PRIVATE,name+'.txt'),await page.locator('body').innerText());
}

async function assertWorkspace(page) {
  if (!page.url().startsWith(ORIGIN+'/dashboard/')) throw new Error('Login required in the dedicated browser');
  await page.getByText(EXPECTED_ACCOUNT,{exact:false}).first().waitFor({state:'attached',timeout:30000});
}

async function findField(page,label) {
  const byLabel=page.getByLabel(label,{exact:true});
  if(await byLabel.count()===1) return byLabel;
  const anchor=page.getByText(label,{exact:true});
  if(await anchor.count()!==1) throw new Error('Cannot identify field: '+label);
  for(let levels=1;levels<=3;levels++) {
    const container=anchor.locator('xpath='+Array(levels).fill('..').join('/'));
    const field=container.locator('input,select,[role="combobox"]');
    if(await field.count()===1) return field;
  }
  throw new Error('Inspect form before mapping field: '+label);
}

async function choose(page,label,value) {
  const field=await findField(page,label);
  if(await field.evaluate(el=>el.tagName)==='SELECT') {
    const options=await field.locator('option').allTextContents();
    const wanted=options.find(text=>text.replace(/\s/g,'').includes(value.replace(/\s/g,'')));
    if(!wanted) throw new Error('Requested '+label+' is unavailable');
    await field.selectOption({label:wanted});
  } else {
    await field.click();
    const option=page.getByRole('option',{name:value,exact:true});
    if(await option.count()===1) await option.click();
    else throw new Error('Inspect '+label+' options before selection');
  }
}

async function inspectCampaign(page) {
  await page.goto(ORIGIN+'/dashboard/campaign-calls/create',{waitUntil:'domcontentloaded'});
  await assertWorkspace(page);
  const createButton=page.getByRole('button',{name:'Create a New Campaign',exact:true});
  await createButton.or(page.getByText('Campaign Call Name',{exact:true})).first().waitFor({timeout:15000});
  if(await createButton.isVisible().catch(()=>false)) await createButton.click();
  await page.getByText('Campaign Call Name',{exact:true}).waitFor({timeout:15000});
  await capture(page,'campaign-form');
  const fields=await page.locator('input,select,button,[role="combobox"],[role="switch"]').evaluateAll(els=>els.map(el=>({
    tag:el.tagName,type:el.getAttribute('type'),role:el.getAttribute('role'),name:el.getAttribute('name'),
    placeholder:el.getAttribute('placeholder'),label:el.getAttribute('aria-label'),text:el.tagName==='INPUT'?'':el.innerText
  })));
  fs.writeFileSync(path.join(PRIVATE,'form-controls.json'),JSON.stringify(fields,null,2));
  writeStatus('form_inspected',{message:'Campaign form inspected; no data uploaded or calls started.'});
}

async function waitForCommands(page) {
  const requestFile=path.join(PRIVATE,'command.json');
  writeStatus('browser_ready',{account:EXPECTED_ACCOUNT,message:'Dedicated browser remains open; waiting for a workflow command.'});
  while(!page.isClosed()) {
    if(fs.existsSync(requestFile)) {
      const request=JSON.parse(fs.readFileSync(requestFile,'utf8'));
      fs.unlinkSync(requestFile);
      try {
        if(request.action==='inspect') await inspectCampaign(page);
        else if(request.action==='campaign_step') {
          delete require.cache[require.resolve('./campaign-ui.cjs')];
          await require('./campaign-ui.cjs').step({page,PRIVATE,prepare,SETTINGS,writeStatus,assertWorkspace},request);
        }
        else if(request.action==='stop') return;
        else throw new Error('Unknown browser command');
      } catch(error) {
        await capture(page,'last-screen').catch(()=>{});
        writeStatus('needs_attention',{message:error.message});
      }
    }
    await new Promise(resolve=>setTimeout(resolve,1000));
  }
}

async function main() {
  const command=process.argv[2]||'login';
  fs.mkdirSync(PRIVATE,{recursive:true});
  if(command==='prepare') {
    const prepared=prepare(JSON.parse(fs.readFileSync(process.argv[3],'utf8')));
    writeStatus('csv_prepared',{csvPath:prepared.csvPath,recipientCount:prepared.job.recipients.length});
    return;
  }
  const {chromium}=require(process.env.BHAGO_PLAYWRIGHT_MODULE||'C:/Users/Shekhar Raju/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
  if(command==='cloud' || command==='cloud-verify') {
    const state=path.join(PRIVATE,'samvaad-state.json');
    if(!fs.existsSync(state)) throw new Error('Authorize the Bhago Samvaad cloud session before starting this worker');
    const browser=await chromium.launch({headless:true,args:['--disable-dev-shm-usage']});
    try {
      const cloudContext=await browser.newContext({storageState:state,viewport:{width:1440,height:1000},acceptDownloads:true});
      const cloudPage=await cloudContext.newPage();
      await cloudPage.goto(ORIGIN+'/dashboard/analytics',{waitUntil:'domcontentloaded'});
      try {
        await assertWorkspace(cloudPage);
      } catch(error) {
        if(command==='cloud-verify') {
          await capture(cloudPage,'cloud-verification-failed').catch(()=>{});
        }
        throw error;
      }
      if(command==='cloud-verify') {
        await inspectCampaign(cloudPage);
      } else {
        await waitForCommands(cloudPage);
      }
    } finally {
      await browser.close();
    }
    return;
  }
  const context=await chromium.launchPersistentContext(path.join(PRIVATE,'browser-profile'),{
    channel:'chrome',headless:command==='export-cloud-session',viewport:null,acceptDownloads:true
  });
  let page=context.pages()[0]||await context.newPage();
  try {
    if(command==='login') {
      writeStatus('login_required',{message:'Sign in manually as '+EXPECTED_ACCOUNT+' in the new dedicated browser.'});
      await page.goto(ORIGIN+'/dashboard/analytics',{waitUntil:'domcontentloaded'});
      // Keep this dedicated browser available for the user's one-time sign-in.
      const deadline=Date.now()+30*60*1000;
      while(Date.now()<deadline) {
        if(context.pages().length===0) throw new Error('Dedicated browser was closed before login verification');
        const dashboardPages=context.pages().filter(p=>p.url().startsWith(ORIGIN+'/dashboard/'));
        if(dashboardPages.length) page=dashboardPages[dashboardPages.length-1];
        if(page.url().startsWith(ORIGIN+'/dashboard/')) {
          const accountVisible=(await page.locator('body').innerText().catch(()=>'' )).toLowerCase().includes(EXPECTED_ACCOUNT);
          if(accountVisible) {
            await capture(page,'login-confirmed');
            writeStatus('ready',{account:EXPECTED_ACCOUNT,message:'Dedicated login verified; no calls started.'});
            try { await inspectCampaign(page); }
            catch(error) {
              await capture(page,'last-screen').catch(()=>{});
              writeStatus('needs_attention',{message:error.message});
            }
            await waitForCommands(page);
            return;
          }
        }
        await new Promise(resolve=>setTimeout(resolve,2000));
      }
      throw new Error('Login was not completed within 30 minutes');
    }
    await page.goto(ORIGIN+'/dashboard/analytics',{waitUntil:'domcontentloaded'});
    await assertWorkspace(page);
    if(command==='export-cloud-session') {
      await require('./campaign-ui.cjs').step({page,PRIVATE,prepare,SETTINGS,writeStatus,assertWorkspace},{stage:'export_cloud_session'});
      return;
    }
    if(command==='inspect') {
      await inspectCampaign(page);
      return;
    }
    // Live launch is deliberately unavailable until this workspace's current form is verified.
    throw new Error('Use inspect first. Campaign launch controls must be verified before enabling test launch.');
  } catch(error) {
    await capture(page,'last-screen').catch(()=>{});
    writeStatus('needs_attention',{message:error.message});
    process.exitCode=1;
  } finally {
    await context.close();
  }
}

module.exports={SETTINGS,normalizePhone,validateJob,csvText,prepare};
if(require.main===module) main().catch(error=>{writeStatus('failed',{message:error.message});process.exitCode=1;});
