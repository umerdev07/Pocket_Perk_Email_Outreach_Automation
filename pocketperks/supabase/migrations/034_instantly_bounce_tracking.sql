begin;

alter table public.personalized_emails
  drop constraint if exists personalized_emails_status_check;
alter table public.personalized_emails
  add constraint personalized_emails_status_check
  check (status in ('generated', 'pending_review', 'approved', 'rejected', 'queued',
                    'sent', 'sent_test', 'failed', 'bounced', 'cancelled'));
alter table public.personalized_emails
  add column if not exists bounced_at timestamptz,
  add column if not exists bounce_type text check (bounce_type is null or bounce_type in ('hard', 'soft', 'unknown'));

-- Keep provider bounce signals separate from recipient opt-outs, while allowing
-- hard/unknown bounces to participate in the existing global send-block list.
alter table public.email_suppressions
  drop constraint if exists email_suppressions_source_check;
alter table public.email_suppressions
  add constraint email_suppressions_source_check
  check (source in ('reply_auto', 'reply_manual', 'bounce_auto'));

alter table public.email_suppression_events
  drop constraint if exists email_suppression_events_source_check;
alter table public.email_suppression_events
  add constraint email_suppression_events_source_check
  check (source in ('reply_auto', 'reply_manual', 'admin', 'bounce_auto'));

create table if not exists public.instantly_bounce_events (
  event_key text primary key check (event_key ~ '^[a-f0-9]{64}$'),
  provider_campaign_id text,
  campaign_mapping_id uuid references public.instantly_campaign_mappings(id) on delete set null,
  run_id uuid references public.campaign_runs(id) on delete set null,
  campaign_id uuid references public.campaigns(id) on delete set null,
  lead_id uuid references public.leads(id) on delete set null,
  email text check (email is null or (email = lower(btrim(email)) and email <> '')),
  bounce_type text not null check (bounce_type in ('hard', 'soft', 'unknown')),
  reason text,
  step_number integer check (step_number is null or step_number between 1 and 10),
  email_account text,
  event_at timestamptz not null,
  match_status text not null default 'unmatched' check (match_status in ('matched', 'unmatched')),
  created_at timestamptz not null default now()
);

create index if not exists instantly_bounce_events_email_at_idx
  on public.instantly_bounce_events(email, event_at desc);
create index if not exists instantly_bounce_events_run_at_idx
  on public.instantly_bounce_events(run_id, event_at desc);
create index if not exists instantly_bounce_events_type_at_idx
  on public.instantly_bounce_events(bounce_type, event_at desc);

alter table public.instantly_bounce_events enable row level security;
revoke all on public.instantly_bounce_events from anon, authenticated;
grant all on public.instantly_bounce_events to service_role;

-- Small, bounded dashboard response; no raw webhook payload or message body is exposed.
create or replace function public.dashboard_bounce_summary()
returns jsonb
language sql
stable
security invoker
set search_path = public
as $$
  with counts as (
    select
      count(*)::integer as total,
      count(*) filter (where bounce_type = 'hard')::integer as hard,
      count(*) filter (where bounce_type = 'soft')::integer as soft,
      count(*) filter (where bounce_type = 'unknown')::integer as unknown
    from public.instantly_bounce_events
  ), recent as (
    select coalesce(jsonb_agg(jsonb_build_object(
      'event_key', b.event_key,
      'email', b.email,
      'bounce_type', b.bounce_type,
      'reason', b.reason,
      'step_number', b.step_number,
      'email_account', b.email_account,
      'event_at', b.event_at,
      'match_status', b.match_status,
      'business_name', l.business_name,
      'campaign_name', c.name
    ) order by b.event_at desc), '[]'::jsonb) as items
    from (
      select * from public.instantly_bounce_events
      order by event_at desc limit 10
    ) b
    left join public.leads l on l.id = b.lead_id
    left join public.campaigns c on c.id = b.campaign_id
  )
  select jsonb_build_object(
    'available', true,
    'total', counts.total,
    'hard', counts.hard,
    'soft', counts.soft,
    'unknown', counts.unknown,
    'recent', recent.items
  )
  from counts cross join recent;
$$;

revoke all on function public.dashboard_bounce_summary() from public, anon, authenticated;
grant execute on function public.dashboard_bounce_summary() to service_role;

-- Accept automatic address blocks created from a hard or unclassified bounce.
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
       or suppression_source not in ('reply_auto', 'reply_manual', 'bounce_auto') then
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
          released_at = null, released_by = null, release_reason = null, created_at = now()
      where email = normalized_email;
    else
      -- Preserve an existing opt-out reason/source; never downgrade it to a bounce block.
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

revoke all on function public.record_email_suppressions(jsonb) from public, anon, authenticated;
grant execute on function public.record_email_suppressions(jsonb) to service_role;

commit;
