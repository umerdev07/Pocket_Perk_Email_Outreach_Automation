# PocketPerks Execution Log

This log records completed work and verification reported in this project chat. Secrets and `.env` contents are never recorded here.

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
