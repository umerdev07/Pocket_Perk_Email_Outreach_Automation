# Native PostgreSQL migration and deployment

## Safety baseline

- Start point: Git commit `9d7832e` on `main`.
- Work happens on `codex/migrate-postgres-auth`; `main` remains unchanged.
- Existing backend baseline: `119 passed, 3 skipped`.
- The PostgreSQL-only runtime is now the target. Legacy provider migration SQL is retained as historical source material; no VPS or production cutover is performed from this branch.
- Existing untracked `.vscode/` and `frontend/AGENTS.md` are not part of this migration.

## Runtime and data scope

The application runtime uses a local PostgreSQL connection (`DATABASE_URL`) for authentication, leads/import history, campaigns/drafts, personalization, campaign runs, approval, replies, suppression, dashboard, webhook events and durable workers. Supabase migration SQL remains in `supabase/migrations` only as a historical reference for the native SQL ports.

No Supabase-to-PostgreSQL data copy is planned for this fresh VPS setup. Existing Supabase data is not automatically imported; production starts with a new PostgreSQL database and an admin created by the bootstrap command.

## Implementation status

- Native PostgreSQL schema and numbered migrations: `backend/migrations/001` through `007`.
- PostgreSQL repositories and workflows: authentication, imports/batches, campaign/draft review, personalization, approval, replies, suppression, dashboard, webhook processing, Instantly mapping/status sync, and durable worker queues.
- Runtime boot requires `DATABASE_URL`; request authentication, health checks, and enabled workers use the PostgreSQL pool. Frontend API calls use same-origin paths, proxied privately to the backend by Next.js.
- Docker Compose defines persistent PostgreSQL, a migration gate, API/workers, and frontend. No migration/data import from the previous hosted database is intended for this new deployment.
- Remaining release gate: run backend/frontend builds and the full PostgreSQL workflow regression on this exact tree; review mail credentials, first-admin bootstrap, backup/restore, Nginx virtual host and TLS. Do not use live sending or point DNS at this stack until these checks are signed off.

## Implemented locally

- Added `backend/migrations/001_postgresql_schema.sql` with a consolidated native PostgreSQL schema, app-owned user/session/reset-token tables, and native queue storage.
- Added `backend/migrations/002_campaign_schedule_routines.sql` with PostgreSQL versions of campaign create/update, step-delete, schedule validation, and manual-trigger routines from Supabase migrations 031 and 033. The elevated `SECURITY DEFINER` setter is revoked from `PUBLIC` pending explicit application-role grants.
- Validated both migrations and their campaign routines on a disposable PostgreSQL 18 Alpine container using tmpfs only. The test exercised create, update, manual trigger, and step deletion. No VPS or production data was involved.
- Added `app.postgres.apply_migrations()` and `python -m app.migrate` with a migration ledger and per-file transactions. Reapplying reports the schema up to date.
- Added the `asyncpg` driver and included SQL migration files in the backend image. The local backend Docker image builds successfully.
- Added an isolated PostgreSQL queue primitive with atomic `SKIP LOCKED` claims, leases, retry/dead-letter limits, and completion. Disposable PostgreSQL integration tests verified concurrent claims, lease recovery, bounded retries, completion, and the campaign routines.
- Backend regression suite after this foundation: `121 passed, 3 skipped`.
- The native queue primitive is connected to campaign start, personalization and Instantly-sync workers. Queue leases, completion and retry visibility use `background_jobs`.
- Added PostgreSQL-backed campaign-run creation/status, bounded personalization reads/writes, suppression checks, review queue/history/actions, dashboard aggregates, Instantly campaign/lead mappings, sent-state reconciliation, reply sync/cursor/management, suppression release/audit, bounce handling, webhook queueing, and explicit conversation follow-up sending. Existing API paths retain their response shapes and use PostgreSQL.
- Added native PostgreSQL JSON/JSONB codecs so JSON documents are encoded once and decoded to Python values consistently across queue, run logs, imports, and campaign routines.
- App startup requires PostgreSQL and starts workers against its managed pool. Health checks use PostgreSQL directly. Instantly and AI remain external services; PocketPerks persistence and queues are local PostgreSQL.
- Compose now defines persistent PostgreSQL, ordered migrations, backend/workers and frontend. The frontend calls a same-origin `/api` route proxied privately to the backend; no provider/domain URL is hard-coded into browser configuration.
- Static syntax parsing has been run across backend modules. Full PostgreSQL integration/regression execution remains a release gate.
- App startup now refuses to run without PostgreSQL. Campaign management and lead import/history/cleanup routes directly use their native PostgreSQL repositories; authentication and health checks no longer consult the previous provider.
- Draft generation, regeneration, approval/editing, campaign create/update/list/detail/delete, and campaign-step deletion now use the same selected PostgreSQL repository. The full backend regression suite passes after these route changes: `123 passed, 7 skipped` in a network-disabled Docker container. The skipped checks require a disposable PostgreSQL test database.
- This is still a local implementation, not a production cutover. Do not deploy or send live mail until backend/frontend builds and PostgreSQL workflow regression pass, database backup/restore is prepared, SMTP/admin bootstrap are configured, and Nginx/domain/TLS are reviewed. Supabase data is not migrated; keep the old project intact until the new VPS workflow is accepted.
- Local migration branch only; production deployment, Supabase data, and VPS services have not been changed.

## Deployment constraints already observed

- VPS: AlmaLinux 9.6, Docker Engine/Compose installed.
- Existing host PostgreSQL 18, Redis, and Nginx are running. Do not stop them as part of this app migration.
- Existing Docker containers were removed with owner approval; volumes were retained.
- Nginx already serves other Venturesky hostnames on ports 80/443. Add a separate virtual host; do not replace existing site configurations.
- PocketPerks PostgreSQL must not publish port 5432. Preserve Instantly MX/SPF/DKIM/DMARC DNS records when configuring the app hostname.

## First Docker startup

1. Copy `.env.example` to `.env`, choose a long random PostgreSQL password, and set `APP_ORIGIN` to the actual HTTPS hostname once confirmed.
2. Copy `backend/.env.example` to `backend/.env`; set SMTP, AI, sending-provider keys, JWT secret, and test/live sending mode deliberately. Do not paste secrets into Git.
3. Compose injects the private in-network `DATABASE_URL` into the migration and backend containers from the same database/user/password values.
4. Run `docker compose up --build -d`. The database health check gates the migration service; successful migrations gate the backend; frontend waits for backend health.
5. Create the first admin inside the backend container with the one-time bootstrap command, then verify sign-in and workflows before Nginx is pointed at the app.

The Docker Compose configuration requires the actual app origin and non-empty database credentials. It does not contain fake domain defaults. Published frontend/API ports bind to loopback for Nginx; PostgreSQL has no host port.
