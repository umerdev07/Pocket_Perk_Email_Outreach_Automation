begin;

-- Durable cursor for incremental, restart-safe reply polling. Start with a
-- bounded one-day backfill, then keep a two-hour overlap on future checks.
create table public.instantly_reply_sync_state (
  id boolean primary key default true check (id = true),
  last_checked_at timestamptz not null default (now() - interval '1 day'),
  sync_from timestamptz,
  starting_after text,
  updated_at timestamptz not null default now()
);

insert into public.instantly_reply_sync_state (id)
values (true)
on conflict (id) do nothing;

alter table public.instantly_reply_sync_state enable row level security;
revoke all on public.instantly_reply_sync_state from anon, authenticated;
grant all on public.instantly_reply_sync_state to service_role;

commit;
