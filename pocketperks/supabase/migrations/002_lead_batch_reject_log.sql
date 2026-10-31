begin;

alter table public.lead_batches
  add column if not exists reject_log jsonb not null default '[]'::jsonb;

commit;
