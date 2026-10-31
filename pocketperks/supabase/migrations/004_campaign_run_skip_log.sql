begin;

alter table public.campaign_runs
  add column if not exists skip_log jsonb not null default '[]'::jsonb;

commit;
