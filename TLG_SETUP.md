# TLG automatic lead routing

After deployment, open The Little Gym in InsightDesk. Nine centres are seeded automatically. Paste each master and branch worksheet URL, including its gid. Settings save on change. Only administrators can edit routes.

## One-time integration setup

- Configure Railway secret TLG_GOOGLE_SA_JSON with a Google service account JSON. Enable the Google Sheets API and grant this service account access to the master sheets and edit access to the branch sheets. Never commit this secret.
- Connect Meta and Google Ads using the account OAuth buttons. Existing account customer IDs, app credentials and Google Ads developer token must be configured. Consent requires the account owner.
- The application's existing init_db creates the new tlg_centres table on startup. No live database migration was executed during development.

## Automatic operation

The existing scheduler routes leads every minute. First run includes all non-empty source rows after the header. Repeat submissions are retained. Source rows must remain append-only: do not sort, insert or delete master rows. A destination marker column identifies each original source row; do not edit or remove it. Markers are written with each lead so an ambiguous append response can be reconciled on retry. Branch sheets may be sorted; branch remarks and follow-up fields are never updated by syncing.

Master headers must be unique and non-empty. Existing branch headers must contain matching master column names. Empty branch sheets receive master headers plus Lead Status, Remarks, Next Follow-up and the internal marker column. Writes use RAW values. Routing between tabs in the same spreadsheet is blocked to avoid loops. Changing an established route requires a reviewed migration. Separate server processes must use PostgreSQL for row-level locking; local SQLite supports only a single scheduler process.

MIS queries Meta and Google Ads directly. Campaign names match centre names after removing spaces/punctuation; unmatched campaigns appear as Unmapped. Google Results are platform conversions (not necessarily exclusively leads); Meta Results use the aggregate lead action. Totals are calculated independently per platform. Failed integrations display Unavailable rather than zero spend. Meta status is campaign effective status; Live indicates an active campaign, not a guarantee of ad-level delivery. OAuth login alone does not grant Google Sheets access.

## Validation

Run: python -m unittest discover -s tests -p test_tlg_routing.py

Deployment and real OAuth/Sheets/advertising API verification remain pending. No production leads have been transferred by this implementation session.

## Initial launch scope

Only Yelahanka and Whitefield are enabled by default; the MIS includes those two centres. Other centres remain listed for later configuration. Meta reads verified TLG ad account 929681660424354 using the connected account credentials. OAuth consent is still required.
