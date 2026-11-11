'use strict';
function assertCallingWindow(now = new Date()) {
  const parts = new Intl.DateTimeFormat('en-GB', {
    timeZone: 'Asia/Kolkata', hour: '2-digit', minute: '2-digit', hourCycle: 'h23'
  }).formatToParts(now);
  const minutes = Number(parts.find(p => p.type === 'hour').value) * 60 +
    Number(parts.find(p => p.type === 'minute').value);
  if (minutes < 600 || minutes >= 1080) {
    throw new Error('Bhago calling is allowed only from 10:00 am to 6:00 pm IST. Start a new RUN during that window.');
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
async function verifyDialog(dialog) {
  if(await dialog.getByLabel('Range 1 start',{exact:true}).inputValue()!=='10:00' ||
     await dialog.getByLabel('Range 1 end',{exact:true}).inputValue()!=='18:00') throw new Error('Calling hours must be 10:00–18:00 IST');
  for(const day of DAYS) if(!await dialog.getByRole('checkbox',{name:day,exact:true}).isChecked()) throw new Error('All seven calling days must be selected');
}
async function configureCallingHours(page) {
  const dialog=await hoursDialog(page);
  await dialog.getByLabel('Range 1 start',{exact:true}).fill('10:00');
  await dialog.getByLabel('Range 1 end',{exact:true}).fill('18:00');
  await dialog.getByRole('button',{name:'Every day',exact:true}).click();
  await verifyDialog(dialog);
  await dialog.getByRole('button',{name:'Save',exact:true}).click();
  await dialog.waitFor({state:'hidden',timeout:10000});
  // Reopen to verify saved campaign settings rather than only input edits.
  const saved=await hoursDialog(page);
  await verifyDialog(saved);
  await saved.getByRole('button',{name:'Cancel',exact:true}).click();
  await saved.waitFor({state:'hidden',timeout:10000});
}
module.exports = { assertCallingWindow, configureCallingHours };
