-- Keep an automatically extended sending deadline isolated to one run.
-- The campaign's approved schedule remains unchanged for future runs.
alter table public.campaign_runs
  add column if not exists schedule_end_at timestamptz;

comment on column public.campaign_runs.schedule_end_at is
  'Per-run end timestamp when workload estimation extends the approved campaign window.';
