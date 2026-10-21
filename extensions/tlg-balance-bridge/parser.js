/* Only visible billing text is read. Amount owed is never used as available funds. */
function parseTlgBillingText(text) {
  const matches = [...text.matchAll(/(?:^|\n)Funds\s*\n\s*₹\s*([\d,]+\.\d{2})\s*\n\s*₹\s*([\d,]+\.\d{2})\s+available after your next balance payment\./gi)];
  if (matches.length !== 1 || !/TLG Ad Account/i.test(text)) {
    throw new Error('The TLG available balance is not visible. Sign in to Meta Billing in this Chrome profile, then click UPDATE again.');
  }
  const funds = Number(matches[0][1].replaceAll(',', ''));
  const available = Number(matches[0][2].replaceAll(',', ''));
  if (!Number.isFinite(funds) || !Number.isFinite(available) || available < 0 || funds < available) {
    throw new Error('Meta returned an invalid funds reading. The previous balance was retained.');
  }
  return {funds, available};
}
if (typeof module !== 'undefined') module.exports = {parseTlgBillingText};
