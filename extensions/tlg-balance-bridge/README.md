# InsightDesk TLG Balance

Install once in the Chrome profile where you use InsightDesk and Meta Billing.

1. Open Chrome Extensions (`chrome://extensions`), enable Developer mode and choose Load unpacked.
2. Select this folder. Reload InsightDesk, and sign in to Meta Billing in the same profile.

UPDATE opens a temporary visible Meta Billing tab for ad account 929681660424354, reads the visible Funds and Available after next payment amounts, closes that tab, and saves the verified reading through your existing InsightDesk session. The verification timestamp changes only when that save succeeds. Failed reads preserve the previous amount and timestamp.

The extension does not read cookies, passwords, card details or customer sheets. It does not make ad or payment changes. The Meta host permission permits page access on adsmanager.facebook.com; the implementation only reads the fixed TLG Billing URL. It does not run while Chrome is closed. It must be installed and Meta must be signed in in the Chrome profile where UPDATE is clicked. Other browsers require their own installation.
