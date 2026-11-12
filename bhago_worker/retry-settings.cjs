'use strict';
exports.configure=async function(page){
  const toggle=page.locator('#retry-toggle');
  if(!(await toggle.locator('span').getAttribute('class')).includes('translate-x-[22px]')) {
    // The observed switch uses translate-x-[2px] when off.
    if((await toggle.locator('span').getAttribute('class')).includes('translate-x-[2px]')) await toggle.click();
  }
  const attempts=page.getByText('Retry Attempts',{exact:true}).locator('..').locator('..');
  for(let i=0;i<5;i++) {
    const value=Number((await attempts.locator('span').innerText()).trim());
    if(value===1) break;
    if(!Number.isFinite(value)||value<1) throw new Error('Cannot verify Samvaad retry count');
    await attempts.getByRole('button').nth(0).click();
  }
  if((await attempts.locator('span').innerText()).trim()!=='1') throw new Error('Exactly one retry is required: two total attempts.');
  const decrease=page.getByRole('button',{name:'Decrease retry interval',exact:true});
  for(let i=0;i<30;i++) {
    const value=(await decrease.locator('..').locator('span').innerText()).trim();
    if(value==='5m') break;
    if(!await decrease.isEnabled()) throw new Error('Cannot set retry interval to five minutes');
    await decrease.click();
  }
  if((await decrease.locator('..').locator('span').innerText()).trim()!=='5m') throw new Error('Retry interval verification failed');
  for(const label of ['No Answer','Busy','Declined','Inactivity','Voicemail','AI / Technical Error','Unknown Error','ASR Error']) {
    const button=page.getByRole('button',{name:label,exact:true});
    const selected=(await button.getAttribute('class')).includes('border-primary');
    const wanted=label!=='Inactivity';
    if(selected!==wanted) await button.click();
    if((await button.getAttribute('class')).includes('border-primary')!==wanted) throw new Error('Retry outcome verification failed');
  }
};
