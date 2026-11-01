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

Fill `SUPABASE_URL`, `SUPABASE_ANON_KEY`, and `SUPABASE_SERVICE_KEY` in your local `.env` yourself.
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
Set `NEXT_PUBLIC_API_URL`, `NEXT_PUBLIC_SUPABASE_URL`, and
`NEXT_PUBLIC_SUPABASE_ANON_KEY` in the frontend environment. The publishable/anon
key is intended for browser use; never put the service key in frontend settings.

Validation: `npm run typecheck` and `npm run build`.
If using a different frontend origin, update backend `FRONTEND_ORIGIN` and restart.

## Project status

This README began as the Stage 1 setup guide. For the current completed and
remaining work, see `log.md`. Email provider sending and durable scheduling are
not enabled by this setup; keep `SEND_MODE=test` until those are reviewed.

## Workspace sign-in and roles

The app uses invite-only Supabase Auth. Apply migration
`supabase/migrations/010_workspace_auth.sql`, then disable new sign-ups in the
Supabase Auth settings and invite each employee from Authentication → Users.
After an invited user exists, assign that user's role in SQL Editor:

```sql
insert into public.workspace_members (user_id, role)
select id, 'admin' from auth.users where lower(email) = lower('ADMIN_EMAIL')
on conflict (user_id) do update set role = excluded.role;
```

Replace `ADMIN_EMAIL` with the invited account's email. Repeat with `operator`
or `reviewer` for other team members. Admin can use every page and delete data;
Operator handles imports, campaigns and personalization; Reviewer can access the
dashboard and human approval workflow. API permissions are checked by FastAPI,
not only by hiding buttons in the browser. Keep the Supabase service key only in
`backend/.env`; the frontend must never receive it.

For password recovery, add `http://localhost:3000/auth/reset-password` to
Supabase Authentication → URL Configuration → Redirect URLs. Add the deployed
app's matching HTTPS URL there when a hosted environment is created.
