# PocketPerks — Stage 1

Separate local foundation; the existing planning document is unchanged.
No keys, sample businesses, AI calls or email sending are included.

## 1. Database

In your Supabase project's SQL Editor, run
`supabase/migrations/001_initial_schema.sql` once. It creates all eight tables.
The lead schema includes the eleven mapped import fields, domain, and extra JSON.
Email and non-null domain have unique indexes. Browser roles have no table access;
only the backend uses the service key. Never put that key in the frontend.

## 2. Backend (PowerShell)

From `pocketperks/backend`:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Fill `SUPABASE_URL` and `SUPABASE_SERVICE_KEY` in your local `.env` yourself.
Keep `SEND_MODE=test`. Other provider settings can stay blank for Stage 1.

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

`http://localhost:8000/api/health` returns 200 only when a real Supabase read
succeeds against the migrated `lead_batches` table. Without setup it returns 503
with backend connected and database unavailable; no fake success is reported.
Offline tests replace the database check and do not prove real connectivity.

## 3. Frontend (second terminal)

From `pocketperks/frontend`:

```powershell
npm ci
Copy-Item .env.example .env.local
npm run dev
```

Open `http://localhost:3000`. The page calls the backend from your browser and
shows backend, database and sending mode, with loading, error and retry states.
Only `NEXT_PUBLIC_API_URL` belongs in the frontend environment.

Validation: `npm run typecheck` and `npm run build`.
If using a different frontend origin, update backend `FRONTEND_ORIGIN` and restart.

## Stage boundary

Stage 1's real connection exit check remains pending until you run the migration,
add your Supabase credentials and see both connections succeed in the browser.
Import, campaigns, AI, approval, sending and replies are later stages.
