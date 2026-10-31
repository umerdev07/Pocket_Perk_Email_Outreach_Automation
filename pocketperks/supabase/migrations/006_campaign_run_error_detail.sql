begin;

alter table public.campaign_runs
  add column if not exists error_detail text;

commit;
