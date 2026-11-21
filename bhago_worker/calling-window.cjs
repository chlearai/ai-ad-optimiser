'use strict';
function assertCallingWindow(now = new Date(), startHour = 10) {
  const parts = new Intl.DateTimeFormat('en-GB', {
    timeZone: 'Asia/Kolkata', hour: '2-digit', minute: '2-digit', hourCycle: 'h23'
  }).formatToParts(now);
  const minutes = Number(parts.find(p => p.type === 'hour').value) * 60 +
    Number(parts.find(p => p.type === 'minute').value);
  if (minutes < startHour*60 || minutes >= 1080) {
    throw new Error(`Bhago calling is allowed only from ${startHour}:00 to 18:00 IST. Start a new RUN during that window.`);
  }
}
const DAYS=['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'];
async function hoursDialog(page) {
  const zone=await page.getByRole('button',{name:'Campaign timezone',exact:true,includeHidden:true}).innerText();
  if(!/Asia\/(Calcutta|Kolkata)/.test(zone)) throw new Error('Campaign timezone must be Asia/Calcutta before calling hours are configured');
  const dialog=page.getByRole('dialog',{name:'When Calls Can Run',exact:true});
  if(!await dialog.isVisible()) {
    await page.getByText('When Calls Can Run',{exact:true}).locator('..').getByRole('button').click();
  }
  await dialog.waitFor({state:'visible',timeout:10000});
  if(await dialog.locator('input[type="time"]').count()!==2) throw new Error('Expected exactly one calling time range; extra ranges must be reviewed');
  return dialog;
}
async function verifyDialog(dialog, start) {
  if(await dialog.getByLabel('Range 1 start',{exact:true}).inputValue()!==start ||
     await dialog.getByLabel('Range 1 end',{exact:true}).inputValue()!=='18:00') throw new Error('Calling hours must be 10:00–18:00 IST');
  for(const day of DAYS) if(!await dialog.getByRole('checkbox',{name:day,exact:true}).isChecked()) throw new Error('All seven calling days must be selected');
}
async function configureCallingHours(page, start='10:00') {
  const dialog=await hoursDialog(page);
  await dialog.getByLabel('Range 1 start',{exact:true}).fill(start);
  await dialog.getByLabel('Range 1 start',{exact:true}).press('ArrowUp');
  await dialog.getByLabel('Range 1 start',{exact:true}).press('ArrowDown');
  await dialog.getByLabel('Range 1 end',{exact:true}).fill('18:00');
  await dialog.getByLabel('Range 1 end',{exact:true}).press('ArrowUp');
  await dialog.getByLabel('Range 1 end',{exact:true}).press('ArrowDown');
  await dialog.getByRole('button',{name:'Every day',exact:true}).click();
  await verifyDialog(dialog,start);
  await dialog.getByRole('button',{name:'Save',exact:true}).click();
  await dialog.waitFor({state:'hidden',timeout:10000});
  // Reopen to verify saved campaign settings rather than only input edits.
  const saved=await hoursDialog(page);
  await verifyDialog(saved,start);
  await saved.getByRole('button',{name:'Cancel',exact:true}).click();
  await saved.waitFor({state:'hidden',timeout:10000});
}
module.exports = { assertCallingWindow, configureCallingHours };
