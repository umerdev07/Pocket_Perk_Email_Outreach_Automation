# PocketPerks Execution Log

This log records completed work and verification reported in this project chat. Secrets and `.env` contents are never recorded here.

## Internal workspace authentication and authorization
- Added invite-only Supabase Auth sign-in to the frontend; application API requests now carry the user's access token and refresh expired sessions.
- Added FastAPI-side token verification and workspace membership lookup. Protected API endpoints require an active workspace member; health remains public for service checks.
- Added Admin, Operator and Reviewer roles. Admin can use all API actions including deletes; Operator handles normal imports/campaigns/personalization; Reviewer gets dashboard and the human approval workflow. Reviewer cannot reach campaign/import endpoints.
- Added `supabase/migrations/010_workspace_auth.sql` for the RLS-enabled `workspace_members` role table. Browser roles have no table grants; server-side role checks use the service key.
- Added frontend/backend environment variable examples and setup notes. New user sign-up must be disabled and employee accounts invited through Supabase Auth; membership roles must be assigned after inviting.
- Not yet applied or verified against the live Supabase project. Do not deploy the frontend/API until migration 010, auth environment values, invite-only settings and at least one admin membership are configured.
- On first local sign-in attempt, the UI showed `Failed to fetch`. Confirmed both env files exist and their Supabase project URLs and public keys match without displaying values. Improved network errors to distinguish Supabase Auth connectivity from backend connectivity. Backend import and frontend typecheck passed; live browser request could not be inspected in this session.
- Added a password-recovery link to the sign-in page and a reset-password page. Recovery uses the email belonging to the Admin Auth user; the user chooses a new password, while the app avoids exposing or reading any existing password. Frontend typecheck passed. Supabase redirect allowlist must include `http://localhost:3000/auth/reset-password` before sending a reset email.
- Auth UI now uses a responsive product-style sign-in layout, clearer recovery/access states, and a dedicated reset screen. Sidebar identity now uses a bounded two-line layout with ellipsis for long email addresses, preventing overlap with the role label.
- Added a `create_app(auth_enabled=False)` test-only seam and updated route tests to opt into it; added offline auth/RBAC regression tests for sign-in requirement, reviewer boundaries and operator approval restrictions. Full pytest invocation produced no output in this environment and was stopped; rerun locally.
- Frontend `npm run typecheck` passed. Production webpack build was blocked by Windows `EPERM` while creating a directory under `frontend/.next` in the OneDrive project; no visual browser check was possible in this session.
- Follow-up visual alignment: changed the auth promo panel from deep navy to the app's existing light paper/white/slate/violet palette, keeping the same responsive layout and violet primary actions. Frontend typecheck passed.
- Deferred the separate Reviewer account and permission check at the user's request; added it plus the local auth smoke/build checks to `docs/planning.md`. The next planned slice is Stage 7.2: verify official Instantly API support for per-lead personalized content, scheduling and minimum write scopes before implementing sending.

## Stage 1 — Foundation
- Created FastAPI backend, Next.js frontend, Supabase initial schema, README, and health check.
- User reported `python -m pytest` with 4 passing tests, `npm run build` passing, and `/api/health` running.
- Supabase project credentials and applying migration 001 remained a user setup task.

## Stage 2 — Lead import
- Added CSV import parsing, 11-column mapping, normalization, duplicate checks, preview and summary.
- Added database persistence for accepted leads, batch counts and reject log; added migration 002.
- Added XLSX support with `openpyxl`, first-sheet parsing, shared validation and importer tests.
- Added Import Leads page with upload, preview, status summaries and Start Campaign placeholder.
- Earlier local environment lacked `python-multipart`/`openpyxl`; installs were blocked by unavailable package network at that time.

## Stage 3 — Campaign setup
- Added campaign create/list/detail backend routes, validation, database module and atomic RPC migration 003.
- Added Campaigns frontend list and draft form with per-step context fields.
- Added `GET /api/lead-batches` and Start Campaign selection modal for batch and approved campaign. Modal Start is intentionally disabled until Stage 5.
- User later reported a successful frontend `npm run build` for the Campaigns page. Other build attempts in this environment encountered a Turbopack internal process error.

## Stage 4 — OpenRouter client (current)
- Added official Chat Completions endpoint client, bearer auth, bounded retries for 429/5xx, timeout and safe custom errors.
- Added JSON response parsing that removes Markdown fences and retries once after invalid JSON. Structured `response_format` was intentionally omitted because free-model support varies; verify per model in official docs before enabling it.
- Added fake-httpx tests and an opt-in live test requiring `RUN_LIVE_TESTS=1` plus `OPENROUTER_API_KEY`.
- User reported OpenRouter client test result: 6 passed, 1 live test skipped.
- Added generic draft generation per campaign step, including previous-step context, per-step failure reporting, regenerate/edit/approve routes and campaign approval status updates.
- Added draft backend tests for generation, one-step failure continuation, edit, approval and regeneration. Latest local pytest invocation did not return output; user should run the stated command to confirm.
- User reported complete backend suite: 23 passed, 1 opt-in live test skipped.
- Added Campaigns page draft review screen with generate-after-save, step-level edit/regenerate/approve actions, progress, status chips, messages, and campaign-list navigation. Backend left unchanged for this part.
- Fixed draft review feedback so a generation response containing the selected step in `failed` is shown as an error rather than a success toast; removed duplicate failure text for steps with no saved draft.
- Added safe, categorized per-step draft failure details from the service (missing key/model, rate limit, timeout, invalid model response, or generation/save error) so OpenRouter and persistence failures are distinguishable without exposing credentials or raw provider payloads.
- Fixed campaign detail handling for PostgREST's one-to-one `generic_drafts` object shape (rather than assuming an array); applied the same object/list normalization to approval and regeneration context paths. Added a regression test for campaign detail.
- User confirmed the campaign review page now loads both generated draft steps after restarting/reloading.

## Stage 5 — Personalization preparation
- Added `POST /api/campaigns/{campaign_id}/start` to verify the campaign is approved and the lead batch exists, then create a `campaign_runs` row with the batch imported count. This first part deliberately does not personalize or generate emails.
- Added a separate campaign run persistence module and fake-Supabase tests for rejected and successful starts.
- User verified the full backend suite: 28 passed, 2 opt-in tests skipped, 1 Starlette/httpx deprecation warning.
- Improved large-run personalization with bounded concurrent lead processing, configurable chunk size/concurrency, and fewer progress writes. Each lead's campaign steps remain ordered, while one lead's failure does not stop other leads. Added a regression test that checks the concurrency ceiling and chunk-level progress updates.
- Added a paginated `GET /api/campaign-runs/{run_id}/emails` endpoint and a run screen panel. It lists generated personalized emails by business and step, lets the user open a subject/body, refreshes as run progress advances, and marks them as drafts that have not been sent. Added pagination and data-link regression tests.
- Added a personalized email history panel to the Start Campaign run view. It refreshes as each processing chunk completes, displays a count and paginated lead/step list, and opens the selected generated subject/body. These are drafts and no send behavior was added.
- Added icon-only actions to campaign and import-history lists, with accessible labels and confirmation dialogs. Added transactional Supabase RPC deletion routines that remove dependent records, preserve leads when deleting a campaign, and block deletion while a linked run is active. Migration 005 must be applied before using these delete actions.
- Stage 6 first backend slice: added rule + OpenRouter risk assessment after each personalized email. HIGH rule flags or an unavailable/invalid AI assessment fail closed to HIGH; final severity uses the higher rule/AI level. LOW is stored as `queued` (ready, not sent), while MEDIUM/HIGH become `pending_review`. Added offline rule/routing tests; Human Approval UI and dashboard remain the next slices.
- Stage 6 next backend slice: added paginated `GET /api/approvals`, editable email review that re-runs risk checks and resets to `pending_review`, plus approve/reject endpoints. Approval stores a subject/body hash and review timestamp; status changes use a conditional database update so stale reviewers cannot overwrite a concurrent change. Added API tests; frontend approval queue/dashboard remain.
- Added the Human Approval frontend page with a paginated risk queue, risk reasons, lead/campaign/step details, editable subject/body, recheck-on-save, and approve/reject actions. Frontend typecheck passed; the local build attempt hit the known Turbopack subprocess panic in this execution environment.
- Start now queues background lead-by-lead personalization using only approved step drafts and supplied campaign/lead data, writes linked `personalized_emails` rows with `generated` status, and updates run progress/final counts. A lead without a business name is skipped with a reason.
- Added migration 004 for `campaign_runs.skip_log` and fake database/OpenRouter tests for linked rows, missing-name skip, and continuing after one lead fails. Compilation passed; the local test invocation did not provide a pytest summary, so run the command below to verify.
- Added Import Leads history, a batch report endpoint with 5/10 lead previews and summary counts, plus report actions to open Start Campaign with that batch preselected. Frontend TypeScript check passed; focused report tests were added.
- Connected the Start Campaign selection modal to the start endpoint; it now requires both selections, displays run ID and personalization state, and polls run progress. Added a GET run-progress endpoint because the plan requires progress visibility. Email sending remains out of scope here.
- Fixed report endpoint validation to accept `limit=5` and `limit=10` query values, and standardized run progress responses to `run_id`. The frontend now stops polling if a progress request errors instead of repeatedly requesting an undefined ID.
- Added an explicitly opt-in interactive OpenRouter diagnostic test. It accepts a terminal prompt and reports a response or safe client error without printing credentials.

## Dashboard navigation
- Added a dedicated Dashboard item at the top of the left workspace sidebar, before Import Leads.
- Wired it to the existing live dashboard summary endpoint for total leads, campaigns, approvals, queued-not-sent emails, and active runs; kept Import Leads and Human Approval as separate pages.
- Added responsive dashboard metric cards and navigation shortcuts. No backend changes.
- Frontend typecheck passed. Turbopack build hit the known subprocess panic; webpack build compiled, typechecked, prerendered the pages, and printed the route summary.

## Personalized email review by lead
- Replaced the compact single-email preview with a paginated lead list and a full-screen per-lead review view. Each lead view loads all personalized steps for that run, sorted by step number.
- Added a per-lead email detail endpoint and backend coverage for endpoint response and sorted step data.
- Added editable subject/body fields. Saving uses the existing risk reassessment route; edited content is moved to Human Approval (`pending_review`) and remains unsent.
- Frontend typecheck completed without errors. The targeted pytest command did not return output in this execution environment; rerun it from the backend folder locally.

## Import history full batch details
- Made each import filename expand an inline batch report with summary counts, every accepted lead in pages of 50, and the complete not-imported reject log (duplicates, invalid and rejected) in separate paged view.
- Added per-lead personalized email expansion showing campaign, step context, status and editable subject/body across runs for that batch. Saving reuses the risk reassessment endpoint and routes edited emails to Human Approval; no email send action was added.
- Added paginated batch detail and batch-lead email endpoints with backend route/database tests. Frontend typecheck passed. This execution environment did not return pytest output; run `python -m pytest tests/test_lead_batch_report.py -q` from backend to confirm.

## Import Leads responsive UI pass
- Refined Import Leads spacing and the upload controls for narrow viewports; constrained history and large batch tables to their own horizontal scroll regions so they do not widen the whole page.
- Fixed the expanded history details row so its contents are not clipped by the outer table cell. Made batch summary, tabs, pagination, editable email cards, and actions responsive for mobile.
- Frontend typecheck passed. Turbopack build hit its known child-process panic; webpack compiled and typechecked, then prerender stopped with EPERM creating `.next/server/app/_global-error.segments/_global-error` in this environment.

## Import history overflow fix
- Fixed the expanded batch detail layout shown in the user screenshot: moved the full batch panel outside the history table instead of nesting it in a spanning table cell, preventing the wide record table from stretching beyond the card.
- Constrained the history card and its table regions to the available width; the detailed records now scroll horizontally inside their own panel.
- Frontend TypeScript check passed.

## Imported leads horizontal scrolling
- Added a constrained horizontal scroll viewport for full-width lead/source tables, a visible scrollbar, keyboard focus, and left/right scroll buttons for users whose mouse or touchpad does not scroll horizontally.
- Frontend typecheck passed.

## Personalized email detail layout
- Moved the selected lead email editor out of the horizontally scrollable data table and into a separate full-width section below it.
- Changed multiple email steps to a single vertical stack with clear campaign/step headers, context, status, larger editable body fields, and separated save actions. Added a responsive mobile layout.
- Frontend typecheck passed.

## Stage 6 — Context-aware risk reasons and send queue state
- Risk assessment now receives campaign instructions and the current email-step context in addition to the approved generic draft and lead facts. Rule checks also treat numbers/links explicitly present in campaign/step context as supported inputs.
- OpenRouter review prompt now checks whether the personalized email fulfills the correct step, respects campaign instructions, and contains unsupported claims or obvious spam-like wording. It asks for a specific reason and explicitly does not claim to predict actual spam-folder placement/deliverability.
- Human Approval review loads and displays campaign instructions and step context beside the stored risk reason. Editing re-runs risk assessment with that same context.
- Explicit human approval now moves the email to `queued`; the UI says it has not been sent. `sent` remains reserved for a future confirmed Instantly response. Updated `docs/planning.md` status and risk workflow notes.
- Added tests for context being passed into risk assessment, context-mismatch reason, and approve-to-queued (not sent).
- Python compile checks and frontend typecheck passed. Pytest did not complete in this execution environment; rerun the listed focused tests locally.
- Additional review-reason refinement: editing an email now records that the edit itself requires another approval, followed by the latest risk-check reason (even when the check is LOW). Python compile checks passed; frontend typecheck passed. Focused pytest execution stalled in this environment before a summary.

## Campaign personalization run failure diagnostics
- Campaign run progress now includes a safe `error_detail` field for fatal background setup/database failures. Known application errors are shown directly; unexpected exceptions expose only their type, never provider request text or credentials.
- Added a fallback status update without optional diagnostic/skip-log columns, so a missing migration does not prevent the run from being marked failed.
- Import page now shows the backend-provided run failure detail instead of only the generic “check backend run details” message.
- Added Supabase migration `006_campaign_run_error_detail.sql` and a regression test for safe diagnostics and status fallback.
- Frontend typecheck passed. Targeted pytest execution stalled without output in this environment; run `python -m pytest tests/test_personalization.py tests/test_campaign_runs.py -q` from backend to verify locally. Apply migration 006 in Supabase SQL Editor before expecting `error_detail` to appear.


## Campaign location removal and date fields
- Removed campaign-level location from the create/list/detail APIs, Supabase queries, approval context, personalized-email batch details, and campaign/start selectors. Campaign instructions and per-email contexts remain the targeting/content guidance.
- Added required start/end date fields to campaign creation, persisted them on campaigns, validated that end date is not earlier than start date, and show the date range in the Campaigns list. Existing rows may have null dates until recreated or migrated manually.
- Added migration `007_campaign_dates_remove_location.sql`; it makes the legacy location field optional (preserving old values), adds date columns, checks range order, and updates the create-with-steps RPC. The app no longer reads or writes campaign location. Apply it in Supabase SQL Editor before creating/listing campaigns with the updated backend.
- Updated Stage 3 and the Instantly stage in `docs/planning.md` with durable, restart-safe scheduling requirements and the confirmed date/delay behavior.
- Actual sending/scheduling is not enabled by this change: date fields only persist campaign window. Backend tests and frontend typecheck are being checked.


## Personalization failure visibility and calibrated approval review
- Campaign run detail now carries a bounded per-step `failure_log` (lead business name, step number, safe error reason), so partial OpenRouter/DB failures can be identified rather than inferred from a single failed-lead counter. Unexpected exception text and credentials are not logged. Added migration `008_campaign_run_failure_log.sql`; a missing optional column falls back to saving run completion without the log.
- Risk prompt now treats the selected imported batch as the campaign audience and does not infer a city is exclusive unless campaign instructions explicitly exclude other areas. It still flags a false city claim and material campaign constraint violations.
- Added Human Approval “Recheck risk” for existing pending items. LOW is moved to `queued`; MEDIUM/HIGH stay pending. This does not mark an email sent.
- Latest local API read currently reports zero lead batches/runs/approvals, so the particular missing rows and screenshot’s seven approvals were not present in the connected database to inspect.
- Added focused tests for safe per-step failure details, city-scope calibration, review recheck routing and run detail retrieval. Apply migration 008 before relying on failure logs.

## Frontend UI polish and English copy
- Refined the shared palette toward a calm café-inspired green and neutral theme, with clearer hover/focus states and softer card surfaces.
- Contained the lead-import preview table within its card with an internal horizontal scrollbar, preventing wide columns from expanding the page on desktop or mobile. Tightened small-screen behavior for campaign listings, navigation, dialogs, and report layouts.
- Replaced visible Roman Urdu interface messages with English. No API calls, application state, handlers, or backend behavior were changed.
- Frontend typecheck and CSS parsing passed. Production build could not complete: Next.js Turbopack exited while starting its CSS worker (`creating new process`, exit code 0); retry `npm run build` in the local Windows terminal.

## Draft step deletion and lighter spam review
- Added a campaign-step DELETE endpoint backed by Supabase RPC migration 009. It allows draft or approved campaigns with no runs, keeps at least one email, removes the step draft, closes step-number gaps, resets the campaign to draft, and requires remaining approved drafts to be reviewed again.
- Added a per-email audit table and trigger, plus a Human Approval "Review history" tab showing automated risk checks, automatic queueing, edits, and human decisions.
- Narrowed risk assessment to obvious content/format spam signals. Campaign fit, city, and unsupported-claim judgments no longer send emails to Human Approval. Low-risk emails are automatically queued after checks or edits; medium/high signals remain for a human. Text checks cannot guarantee Gmail Inbox/Spam placement, and queued does not mean sent.
- Added focused endpoint/risk/history tests and updated Stage 3/6 planning notes. Python compileall and frontend typecheck pass. Pytest execution stalled in this environment after reaching the mocked async AI test; rerun `python -m pytest tests/test_campaigns.py tests/test_approvals.py tests/test_risk_service.py tests/test_personalization.py -q` locally. Apply `supabase/migrations/009_email_review_history_and_step_delete.sql` in Supabase SQL Editor before using step deletion or review history.

## Spam-likelihood estimate refinement
- Strengthened the text-only spam risk detector with named signals for deceptive urgency, unrealistic promises/prizes, pressure to click or verify, financial bait, fake reply/forward subjects, excess caps/punctuation, long copy, and multiple links.
- HIGH is reserved for multiple strong signals; MEDIUM identifies a concrete signal; LOW has no strong text signal and is automatically queued. Human Approval now describes this as a spam-likelihood/content estimate and shows the matched reason.
- The OpenRouter risk prompt now explicitly estimates content-level spam resemblance and excludes campaign fit, geography, and factual review. It does not claim to predict Gmail routing; actual Gmail filters also consider sender authentication, reputation, recipient behavior, and other unavailable signals.
- Rule-only risk tests: 11 passed. Frontend typecheck and Python compileall passed. The entire async risk test module stalled at the first mocked async AI call in this environment; rerun `python -m pytest tests/test_risk_service.py -q` locally.

## Stage 7.1 — Instantly read-only account client
- Verified from Instantly API v2 docs that connected sending accounts are listed with `GET https://api.instantly.ai/api/v2/accounts`, bearer authentication, `accounts:read`, a `limit` up to 100 and `next_starting_after` cursor pagination.
- Added `backend/app/integrations/instantly_client.py` to list pages of connected accounts and return only the fields needed for review. It never sends email; it handles missing key, authentication/scope failure, plan access, timeout, rate limits, service errors, malformed responses and repeated cursors without logging keys or raw provider payloads.
- Added fake-client coverage and an opt-in real read-only check in `backend/tests/test_instantly_client.py`. User reports the focused suite passed: 7 passed, 1 opt-in test skipped.
- User ran the opt-in live test with the key stored in `backend/.env`: 1 passed; Instantly returned 6 connected sending accounts. This was a read-only API call and sent no email. No database, frontend or send behavior changed.

## Stage 7.2 — Official Instantly API and sending design review (2026-11-05)
- Reviewed official Instantly API v2 docs for campaign creation/activation, bulk lead addition, custom variables, schedules, sequence delays, email listing and webhook events.
- Confirmed supported route: create/use an Instantly campaign with sequence templates, add leads to that campaign using `POST /api/v2/leads/add` in chunks of up to 1,000, then activate through the campaign endpoint. Each lead can carry custom variables for its personalized subject/body at every email step; subject and body variable replacement is supported. User approved syncing campaign leads into Instantly; Supabase remains PocketPerks' source of truth.
- Confirmed campaigns support sender account selection, sending windows, active days, timezone, start/end dates and per-step delays. Schedule date fields are date-only; PocketPerks must enforce exact UK start/end datetime via its durable scheduler and pause the provider campaign at the end boundary.
- Confirmed `email_sent`, `reply_received`, `email_bounced` and `lead_unsubscribed` webhook events. Use webhook delivery as primary status/reply path, with `GET /api/v2/emails` polling fallback and idempotent event handling.
- Required least-privilege API scopes: existing `accounts:read`, `campaigns:read`; add `campaigns:create`, `campaigns:update`, `leads:create`, `webhooks:create` (and `webhooks:read` if needed to inspect existing hooks), plus `emails:read` for polling fallback. Create a new v2 key because existing-key scopes cannot be expanded. Avoid `all:all`. `emails:create` is only for the optional immediate test-email endpoint, which sends a real test email and is not dry-run mode.
- Sending account rule recorded: use only the five `@apppocketperks.com` accounts; exclude `praveen@takeawaymax.co.uk`; begin controlled verification with one sender. Keep warmup running. No API write call or email was sent during this docs review.
- Stage 7.3 is now next: implement durable test-mode sending and lead sync, sender/account mapping, per-step schedule enforcement, opt-out and eligibility gates, provider status tracking, and replies webhook/polling. Live sending remains disabled until controlled validation and explicit authorization.
- User clarified follow-up timing: use a gap of a few hours between steps, not the previous 3-day default. Exact number of hours is still open. This step delay is distinct from the overall sending pace across recipients/accounts; begin controlled tests with one sender and verify low-rate/sequential sending before using multiple accounts. Pacing may reduce burst risk but cannot guarantee Inbox placement.

## Security review — upload and workload hardening
- Added backend upload limits: 15 MB maximum file size, 10,000 data rows, XLSX expanded-size/compression-ratio/component-count checks, and XLSX workbook structure validation. CSV parsing now stops at the row cap instead of first loading an unbounded number of rows.
- Existing-lead lookups and accepted-lead inserts now use bounded chunks (300 lookups / 500 inserts); incomplete insert batches trigger cleanup. Email validation is restricted to a safe conventional format before values are placed in database filters.
- OpenRouter personalization concurrency is shared across runs in each API process, and lead-field content is explicitly treated as untrusted data in the personalization prompt. Database uniqueness prevents two active runs for the same campaign/batch pair.
- Added migration `011_security_hardening.sql` to enable deny-by-default RLS on application tables, revoke browser-role grants, and add the active-run uniqueness constraint. Apply this migration in Supabase SQL Editor before relying on the database protections.
- Added response security headers and disabled HTTP caching for API responses containing workspace data. The source upload is not retained as an object, so a public CDN is not appropriate; private storage is only needed if we later retain source files or move uploads to direct-to-storage ingestion.
- Verification: Python `compileall` and frontend `npm run typecheck` passed. `npm run build` failed in the existing Turbopack CSS worker startup (`creating new process`, child Node exited with code 0); this matches the previously recorded environment failure. Automated tests were not run in this review. `npm audit` could not reach `registry.npmjs.org` in the earlier review, so dependency vulnerability status remains unverified. The subsequent PGMQ entry below records the durable worker change.

## Durable personalization queue — PGMQ
- Added migration `012_durable_personalization_queue.sql`: creates a logged PGMQ queue and an insert trigger that enqueues each campaign run in the same database transaction. Queue functions are wrapped in service-role-only RPCs; workers lease messages, extend leases during long runs, archive completed jobs, and retry fatal runs up to three deliveries.
- Replaced request-scoped FastAPI background execution with a lifespan worker that drains PGMQ after startup. If the API stops mid-run, the queue message becomes visible after its lease expires; the restarted worker resumes it.
- Personalization now checks saved lead/step pairs by bounded lead chunk before calling OpenRouter and uses conflict-ignore inserts, preventing duplicate personalized rows and avoiding repeat AI calls after interrupted runs.
- Updated Stage 5 planning notes. Apply migration `011_security_hardening.sql` first, then `012_durable_personalization_queue.sql` in Supabase SQL Editor. The queue worker only runs when the API starts with database settings configured. It does not send email.
- Automated tests were not run. Re-run Python compile checks, focused campaign-run/personalization tests, and the frontend build in the local Windows environment before relying on this queue migration.

## Personalization run failure visibility fix
- Fixed the queue worker retrying every failed run as though it were a temporary OpenRouter rate limit. That behavior cleared progress and error details, which could leave a permanent setup/database failure looking like `running` with zero processed leads.
- The worker now retries only the explicit OpenRouter rate-limit outcome. Other failed runs keep their failure status/details and their queue message is archived. Added focused tests for both permanent failure and retryable rate-limit paths.
- Apply no database migration. Restart the backend so the worker loads this code; an already leased PGMQ job will be picked up again when its visibility delay expires.

## Local Docker backend setup
- Added a production-style Python 3.14 backend Dockerfile, Docker build exclusions for `.env` and development files, and a Compose service. Compose keeps the API bound to `127.0.0.1:8001` and explicitly enforces `SEND_MODE=test`.
- Docker Desktop was launched. The backend image built and container `pocketperks-backend-1` started as healthy. Its `/api/health` returned backend `ok`, database `connected`, and send mode `test`. Host port 8000 was already occupied, so Docker is published at `127.0.0.1:8001`. No OS group or Docker permissions were changed.

## Stage 7.3 — Instantly write client (first increment)
- The operator created a separate least-privilege Instantly API v2 key with campaign create/update, lead create, account/campaign/email reads, and webhook create/read scopes. Its live read-only accounts test passed and returned 6 connected accounts. No key value is recorded here.
- Added `create_campaign` and `add_campaign_leads` to the Instantly integration client. Lead payloads are validated locally and split into provider-sized chunks of at most 1,000. Provider errors are sanitized. Write POSTs are not automatically retried because a timeout may leave an ambiguous provider-side result.
- Campaign creation does not activate a campaign. No activation function or email-send function was added, and no Instantly write request was executed.
- Added mocked tests for campaign creation, chunking, preflight validation, and ambiguous timeout behavior. Operator ran the focused client suite: 11 passed, 1 live test skipped.
- Added migration `014_instantly_sync_mappings.sql` and `app/instantly_sync_db.py` for one provider-campaign mapping per local run and one provider-lead mapping per local lead, with bounded upserts and service-role-only RLS/grants. No migration has been applied.
- Migration 014 was applied by the operator in Supabase; it created only the new mapping tables/policies and did not send email.
- Added mocked DB tests for idempotent campaign mapping creation, chunked lead mapping writes, and validation. Operator ran the focused mapping DB suite: 5 passed.
- Instantly lead-sync client now maps provider `created_leads[].index` and IDs back to local input indexes, reports skipped/invalid/blocklist/duplicate counts, and enables `skip_if_in_campaign` for retry-safe duplicate handling. Operator ran the combined focused suites: 17 passed, 1 live test skipped. Python compile and `git diff --check` pass. No Instantly write call or email send was made.

## Stage 7.3 — durable inactive draft sync worker
- Added a distinct PGMQ sync queue migration (`015_durable_instantly_sync_queue.sql`) that queues newly completed runs and newly queued/reviewed personalized emails. It deliberately does not backfill old completed runs. It carries only a run ID and uses service-role-only worker RPCs.
- Added a background worker that checks each run is completed and its campaign/generic drafts are approved, then creates an Instantly draft and syncs only leads with every personalized step in `queued` state. Per-lead email subjects and bodies are passed as merge variables; follow-up steps default to a configurable four-hour interval. Provider drafts are restricted to a UK weekday schedule and campaign date window, but remain inactive.
- Added bounded Supabase loaders and status mappings. Lead mappings are marked `unknown` before provider writes so ambiguous timeouts are not automatically replayed. Campaign creation outcomes that cannot be confirmed as an inactive draft are stopped before lead sync. There is still no activate/send method.
- Apply `supabase/migrations/015_durable_instantly_sync_queue.sql` after 014, then restart the backend container. This implementation has not yet been run against Supabase/Instantly and no real provider write was made. Run focused mock tests and inspect the mapping/status results before any dummy-address pilot.

## Campaign date-and-time window
- Campaign creation now accepts UK-local start and end date/time values, resolves them to timezone-aware timestamps in the API, and retains date-only fields for legacy views/clients. The form defaults to a test window starting five minutes from now and ending at 23:59 UK time on the same date (or the next date if necessary).
- Added migration `016_campaign_datetime_window.sql` to preserve legacy date windows and persist `start_at`/`end_at`; apply it after migration 015, then restart the backend. Campaign lists and detail responses include the timestamps.
- Instantly's current draft schedule supports date-only campaign boundaries plus a separate daily sending window. The integration maps the UK dates into the inactive draft and keeps the configured four-hour follow-up delay as a sequence gap; this does not prove actual sending timing and does not activate/send anything. Exact datetime enforcement, campaign activation, live send, replies, opt-out handling, and verification remain Stage 7 work. No live Instantly write or email was sent in this change.
- Frontend `npm run typecheck` passed. The selected backend pytest command did not produce results within 90 seconds and was interrupted; backend regression tests remain to be rerun locally after the migration.
- Added focused offline payload tests for UK/BST date conversion, approved-step enforcement, personalized merge variables and the configured four-hour follow-up gap. `python -m pytest tests/test_instantly_sync_service.py -q` passed (4 tests). The existing async mapping DB suite stalled in this execution environment before producing test results; no live Instantly or Supabase write was attempted.

## Stage 8 — Lead Replies inbox and Instantly dashboard connection (initial slice)
- Connected the Lead Replies navigation page to Instantly's API v2 received-email list using the existing `emails:read` permission. Replies are matched to imported leads and mapped campaigns where possible, then saved idempotently in the existing Supabase `replies` table.
- Added a durable pagination cursor and migration `017_instantly_reply_sync_state.sql`; the backend polls once per minute while online, continues through large reply backlogs in bounded batches, and the UI also supports manual refresh and loading older replies. The UI shows sender, business/campaign context, subject, preview, full body, unread status, search, and local read state.
- Extended the dashboard with Instantly campaign/lead sync counts and reply totals, and added minute refresh while the dashboard is open. No campaign launch, send, reply-send, or Instantly lead write action was added in this slice.
- Apply migration 017 in Supabase and rebuild/restart the backend plus redeploy the frontend before using this feature. Automated tests/build were not run during this change; verify locally before relying on production behavior.
