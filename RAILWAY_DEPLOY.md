# Railway Deployment Guide

## 1. Push code to GitHub
Ensure your code is committed and pushed to a GitHub repository.

## 2. Create Railway project
1. Go to [https://railway.app](https://railway.app)
2. Click **New Project** → **Deploy from GitHub repo**
3. Select your repository
4. Railway will detect `railway.json` and use the Dockerfile

## 3. Add environment variables
Go to your Railway service → **Variables** tab and add:

```
ADOPTIMA_JWT_SECRET=your-long-random-secret-key-here
DATABASE_URL=postgresql://postgres:YOUR_PASSWORD@db.mfmtilbrpigxlstzgmzd.supabase.co:5432/postgres
ADOPTIMA_PUBLIC_BASE_URL=https://your-railway-domain.up.railway.app
HOST=0.0.0.0
PORT=8000
```

**Important:**
- Generate a secure `ADOPTIMA_JWT_SECRET` (32+ random characters).
- Replace `YOUR_PASSWORD` in `DATABASE_URL` with your actual Supabase database password.
- If `DATABASE_URL` is set, the app uses Supabase PostgreSQL. Otherwise it falls back to SQLite at `ADOPTIMA_DB_PATH`.

## 4. Add a persistent volume (SQLite data will survive redeploys)
1. In Railway, go to your service → **Volumes** tab
2. Click **New Volume**
3. Mount path: `/app/data`
4. Size: 1 GB (enough for SQLite)

## 5. Deploy
Railway will build the Dockerfile and start the app.

## 6. Set up the first Super Admin
After first deploy, visit:
```
https://your-railway-domain.com/api/auth/onboarding-required
```
If it returns `{"onboarding_required":true}`, open the landing page and create the first Super Admin.

## 7. Create BM users
Log in as Super Admin → go to RevenueOps → System → Users → Add User.
Assign accounts to the BM.

## 8. Give BM the URL
Send the BM this URL:
```
https://your-railway-domain.com
```
They log in with their email and password and see only assigned accounts.

## Important notes
- Do **not** expose `ADOPTIMA_JWT_SECRET` or backend credentials.
- SQLite on Railway is fine for moderate usage; for heavy use, migrate to PostgreSQL later.
- OAuth redirect URLs (Google/Meta) must be updated to use your Railway domain after connecting accounts.

## API access and health

Set a persistent, random ADOPTIMA_JWT_SECRET in Railway so login sessions and OAuth
callbacks survive process restarts. API documentation is disabled unless
ENABLE_API_DOCS=true. Machine callbacks are listed with their access requirements
in backend/api_access.py. Configure the relevant Meta webhook verification and
signature secrets before enabling lead delivery.

Railway uses /health for process liveness. A Postgres connection failure is
logged at startup and can fall back to SQLite. Keeping /health independent of
database availability avoids restart loops during a database outage. Authenticated
admins can check the selected database using /health/db.
