'use strict';
// Read only the existing Bhago Outbound sequence's visible run details.
const fs=require('node:fs');
const path=require('node:path');
const URL='https://samvaad.sakhaa.ai/dashboard/sequence-history?agent_id=agent_1791891093936_ny5djac7h&agent_name=Bhago+Outbound';
exports.run=async function({page,PRIVATE,assertWorkspace},request){
  if(!/^[a-f0-9]{32}$/.test(request.requestId||''))throw Error('Invalid WhatsApp history request');
  const input=JSON.parse(fs.readFileSync(path.join(PRIVATE,'jobs',request.requestId+'.whatsapp-request.json'),'utf8'));
  const output=path.join(PRIVATE,'jobs',request.requestId+'.whatsapp.json');
  const p=await page.context().newPage();const entries=[];
  try{
    p.setDefaultTimeout(12000);
    await p.goto(URL,{waitUntil:'domcontentloaded'});await assertWorkspace(p);
    await p.locator('#date-filter').selectOption({label:'All time'});
    await p.getByRole('table').waitFor();
    await p.getByRole('table').getByText('Bhago Outbound',{exact:true}).first().waitFor({timeout:20000});
    let more=false;
    for(let pageNo=0;pageNo<100;pageNo++){
      const rows=p.getByRole('table').getByRole('row');const count=await rows.count();
      for(let i=1;i<count;i++){
        const row=rows.nth(i);const cells=await row.getByRole('cell').allTextContents();
        const id=cells[1]?.match(/exec_[a-f0-9]+/)?.[0];
        if(!id || input.known.includes(id))continue;
        if(entries.length>=5){more=true;break;}
        await row.getByRole('button',{name:'View run',exact:true}).click();
        await p.getByText('Source session',{exact:true}).waitFor();
        const text=(await p.locator('body').innerText()).split('Saved execution and message outcomes. Refresh to load the latest status.').pop();
        const callId=text.match(/Source session\s+([a-f0-9-]{36})/i)?.[1]||'';
        const messageIds=[...text.matchAll(/Message ID:\s*(wamid\.[A-Za-z0-9+/=_-]+)/g)].map(m=>m[1]);
        const state=cells[3]?.trim().match(/^(completed|failed|active|waiting)/i)?.[1]?.toLowerCase();
        const executionStatus={completed:'Completed',failed:'Failed',active:'Active',waiting:'Waiting'}[state]||'Unknown';
        entries.push({execution_id:id,call_id:callId,message_ids:messageIds,execution_status:executionStatus,
          failed_before_send:!messageIds.length && executionStatus==='Failed',
          checked_at:new Date().toISOString()});
        await p.getByRole('button',{name:'Close',exact:true}).click();
        await p.getByRole('heading',{name:'Sequence run',exact:true}).waitFor({state:'hidden'});
      }
      if(more)break;
      const next=p.getByRole('button',{name:'Next page',exact:true});
      if(!await next.isEnabled())break;
      if(pageNo===99)throw Error('WhatsApp history exceeds reviewed page limit');
      const first=await rows.nth(1).innerText();await next.click();
      await p.waitForFunction(old=>document.querySelector('tbody tr')?.innerText!==old,first).catch(async()=>{
        if(await rows.nth(1).innerText()===first)throw Error('WhatsApp history page did not advance');
      });
      await p.getByRole('table').getByText('Bhago Outbound',{exact:true}).first().waitFor({timeout:20000});
    }
    fs.writeFileSync(output,JSON.stringify({state:'ready',entries,more,checked_at:new Date().toISOString()}));
  }catch(e){fs.writeFileSync(output,JSON.stringify({state:'needs_attention',entries,message:'Bhago WhatsApp sequence history could not be verified.'}));}
  finally{await p.close();}
};
