begin;

alter table public.replies
  add column if not exists sender_account_email text;

create table if not exists public.instantly_webhook_events (
  id bigint generated always as identity primary key,
  event_key text not null unique check (event_key ~ '^[a-f0-9]{64}$'),
  payload jsonb not null default '{}'::jsonb,
  status text not null default 'pending'
    check (status in ('pending', 'processing', 'processed', 'failed')),
  attempts integer not null default 0 check (attempts >= 0),
  error_detail text,
  received_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  processed_at timestamptz
);

create index if not exists instantly_webhook_events_pending_idx
  on public.instantly_webhook_events(status, received_at)
  where status in ('pending', 'processing');

alter table public.instantly_webhook_events enable row level security;
revoke all on public.instantly_webhook_events from anon, authenticated;
grant all on public.instantly_webhook_events to service_role;

create or replace function public.claim_instantly_webhook_events(p_limit integer default 10)
returns setof public.instantly_webhook_events
language plpgsql
security invoker
set search_path = public
as $$
begin
  if p_limit < 1 or p_limit > 50 then
    raise exception 'Webhook claim limit must be between 1 and 50';
  end if;

  return query
  with candidates as (
    select e.id
    from public.instantly_webhook_events e
    where e.status = 'pending'
       or (e.status = 'processing' and e.updated_at < now() - interval '10 minutes')
    order by e.received_at, e.id
    for update skip locked
    limit p_limit
  )
  update public.instantly_webhook_events e
  set status = 'processing', attempts = e.attempts + 1,
      error_detail = null, updated_at = now()
  from candidates c
  where e.id = c.id
  returning e.*;
end;
$$;

revoke all on function public.claim_instantly_webhook_events(integer) from public, anon, authenticated;
grant execute on function public.claim_instantly_webhook_events(integer) to service_role;

commit;
