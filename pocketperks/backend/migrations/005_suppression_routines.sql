-- Native PostgreSQL suppression management from Supabase migrations 020 and 021.
-- Suppression tables and audit indexes are defined in 001_postgresql_schema.sql.
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
  normalized_reason text := left(btrim(coalesce(release_reason, '')), 300);
  current_active boolean;
  linked_lead_id uuid;
  linked_campaign_id uuid;
begin
  if length(normalized_reason) < 10 then
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
      release_reason = normalized_reason
  where s.email = normalized_email;

  insert into public.email_suppression_events(
    email, event_type, source, reason, actor_user_id, lead_id, campaign_id
  ) values (
    normalized_email, 'released', 'admin', normalized_reason,
    actor_id, linked_lead_id, linked_campaign_id
  );

  return query select normalized_email, false, false;
end;
$$;
revoke all on function public.record_email_suppressions(jsonb) from public;
revoke all on function public.release_email_suppression(text, text, uuid) from public;