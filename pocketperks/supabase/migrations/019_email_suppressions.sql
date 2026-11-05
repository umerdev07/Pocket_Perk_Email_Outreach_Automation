begin;

alter table public.lead_batches
  add column if not exists suppressed_count integer not null default 0
    check (suppressed_count >= 0);

create table if not exists public.email_suppressions (
  email text primary key check (email = lower(btrim(email)) and email <> ''),
  reason text not null,
  source text not null check (source in ('reply_auto', 'reply_manual')),
  lead_id uuid references public.leads(id) on delete set null,
  campaign_id uuid references public.campaigns(id) on delete set null,
  created_at timestamptz not null default now()
);

create index if not exists email_suppressions_created_at_idx
  on public.email_suppressions(created_at desc);

alter table public.email_suppressions enable row level security;
revoke all on public.email_suppressions from anon, authenticated;
grant all on public.email_suppressions to service_role;

commit;
