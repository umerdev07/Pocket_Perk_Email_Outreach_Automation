begin;

alter table public.email_suppressions
  add column if not exists active boolean not null default true,
  add column if not exists released_at timestamptz,
  add column if not exists released_by uuid references auth.users(id) on delete set null,
  add column if not exists release_reason text;

create table public.email_suppression_events (
  id uuid primary key default gen_random_uuid(),
  email text not null check (email = lower(btrim(email)) and email <> ''),
  event_type text not null check (event_type in ('suppressed', 'released')),
  source text not null check (source in ('reply_auto', 'reply_manual', 'admin')),
  reason text not null,
  actor_user_id uuid references auth.users(id) on delete set null,
  lead_id uuid references public.leads(id) on delete set null,
  campaign_id uuid references public.campaigns(id) on delete set null,
  created_at timestamptz not null default now()
);

create index email_suppression_events_email_created_idx
  on public.email_suppression_events(email, created_at desc);

-- Preserve the opt-outs already recorded by migration 019 in the audit log.
insert into public.email_suppression_events(email, event_type, source, reason, lead_id, campaign_id, created_at)
select email, 'suppressed', source, reason, lead_id, campaign_id, created_at
from public.email_suppressions;

create or replace function public.record_email_suppressions(p_records jsonb)
returns integer
language plpgsql
security invoker
set search_path = public
as $$
declare
  item jsonb;
  normalized_email text;
  was_active boolean;
  suppression_reason text;
  suppression_source text;
  linked_lead_id uuid;
  linked_campaign_id uuid;
  actor_id uuid;
  recorded_count integer := 0;
begin
  if jsonb_typeof(p_records) <> 'array' then
    raise exception 'Suppression records must be an array';
  end if;

  for item in select value from jsonb_array_elements(p_records)
  loop
    normalized_email := lower(btrim(item->>'email'));
    suppression_reason := left(coalesce(nullif(btrim(item->>'reason'), ''),
      'Recipient requested no further email.'), 300);
    suppression_source := item->>'source';
    linked_lead_id := nullif(item->>'lead_id', '')::uuid;
    linked_campaign_id := nullif(item->>'campaign_id', '')::uuid;
    actor_id := nullif(item->>'actor_user_id', '')::uuid;

    if normalized_email !~ '^[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}$'
       or suppression_source not in ('reply_auto', 'reply_manual') then
      raise exception 'Invalid suppression record';
    end if;

    select s.active into was_active
    from public.email_suppressions s
    where s.email = normalized_email
    for update;

    if not found then
      insert into public.email_suppressions(email, reason, source, lead_id, campaign_id, active)
      values (normalized_email, suppression_reason, suppression_source, linked_lead_id, linked_campaign_id, true);
    elsif not was_active then
      update public.email_suppressions
      set reason = suppression_reason, source = suppression_source,
          lead_id = coalesce(linked_lead_id, lead_id),
          campaign_id = coalesce(linked_campaign_id, campaign_id), active = true,
          released_at = null, released_by = null, release_reason = null,
          created_at = now()
      where email = normalized_email;
    else
      continue;
    end if;

    insert into public.email_suppression_events(
      email, event_type, source, reason, actor_user_id, lead_id, campaign_id
    ) values (
      normalized_email, 'suppressed', suppression_source, suppression_reason,
      actor_id, linked_lead_id, linked_campaign_id
    );
    recorded_count := recorded_count + 1;
  end loop;

  return recorded_count;
end;
$$;

create or replace function public.release_email_suppression(
  target_email text,
  release_reason text,
  actor_id uuid
)
returns table(email text, active boolean, already_released boolean)
language plpgsql
security invoker
set search_path = public
as $$
declare
  normalized_email text := lower(btrim(target_email));
  current_active boolean;
  linked_lead_id uuid;
  linked_campaign_id uuid;
begin
  if length(btrim(coalesce(release_reason, ''))) < 10 then
    raise exception 'A release reason of at least 10 characters is required';
  end if;

  select s.active, s.lead_id, s.campaign_id
  into current_active, linked_lead_id, linked_campaign_id
  from public.email_suppressions s
  where s.email = normalized_email
  for update;

  if not found then
    raise exception 'Suppression not found' using errcode = 'P0002';
  end if;

  if not current_active then
    return query select normalized_email, false, true;
    return;
  end if;

  update public.email_suppressions s
  set active = false, released_at = now(), released_by = actor_id,
      release_reason = left(btrim(release_reason), 300)
  where s.email = normalized_email;

  insert into public.email_suppression_events(
    email, event_type, source, reason, actor_user_id, lead_id, campaign_id
  ) values (
    normalized_email, 'released', 'admin', left(btrim(release_reason), 300),
    actor_id, linked_lead_id, linked_campaign_id
  );

  return query select normalized_email, false, false;
end;
$$;

alter table public.email_suppression_events enable row level security;
revoke all on public.email_suppression_events from anon, authenticated;
grant all on public.email_suppression_events to service_role;

revoke all on function public.record_email_suppressions(jsonb) from public, anon, authenticated;
grant execute on function public.record_email_suppressions(jsonb) to service_role;
revoke all on function public.release_email_suppression(text, text, uuid) from public, anon, authenticated;
grant execute on function public.release_email_suppression(text, text, uuid) to service_role;

commit;
