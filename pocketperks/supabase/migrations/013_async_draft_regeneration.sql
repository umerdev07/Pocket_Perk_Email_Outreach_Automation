begin;

alter table public.generic_drafts
  add column if not exists generation_status text not null default 'idle',
  add column if not exists generation_error text,
  add column if not exists generation_started_at timestamptz,
  add column if not exists generation_finished_at timestamptz;

alter table public.generic_drafts
  drop constraint if exists generic_drafts_generation_status_check;

alter table public.generic_drafts
  add constraint generic_drafts_generation_status_check
  check (generation_status in ('idle', 'generating', 'succeeded', 'failed'));

comment on column public.generic_drafts.generation_status is
  'Tracks asynchronous AI draft regeneration separately from editorial approval status.';

commit;
