-- Native PostgreSQL routines ported from Supabase migrations 026, 029, 034, and 036.

-- The later routines replace older definitions where the schema evolved.

create or replace function public.preview_lead_batch_cleanup(target_batch_id uuid)
returns jsonb
language plpgsql
security invoker
set search_path = public
as $$
declare
  batch_row public.lead_batches;
  lead_total bigint;
  run_total bigint;
  running_total bigint;
  email_total bigint;
  reply_total bigint;
  mapping_total bigint;
  lead_mapping_total bigint;
  active_provider_total bigint;
  provider_campaigns jsonb;
begin
  select * into batch_row from public.lead_batches where id = target_batch_id;
  if not found then
    return jsonb_build_object('found', false);
  end if;

  select count(*) into lead_total from public.leads where batch_id = target_batch_id;
  select count(*), count(*) filter (where status = 'running')
    into run_total, running_total
    from public.campaign_runs where batch_id = target_batch_id;
  select count(*) into email_total from public.personalized_emails
    where run_id in (select id from public.campaign_runs where batch_id = target_batch_id);
  select count(*) into reply_total from public.replies
    where lead_id in (select id from public.leads where batch_id = target_batch_id)
       or email_id in (
         select pe.id from public.personalized_emails pe
         join public.campaign_runs cr on cr.id = pe.run_id
         where cr.batch_id = target_batch_id
       );
  select count(*) into mapping_total from public.instantly_campaign_mappings
    where run_id in (select id from public.campaign_runs where batch_id = target_batch_id);
  select count(*) into lead_mapping_total from public.instantly_lead_mappings ilm
    join public.instantly_campaign_mappings icm on icm.id = ilm.campaign_mapping_id
    join public.campaign_runs cr on cr.id = icm.run_id
    where cr.batch_id = target_batch_id;
  select count(*) into active_provider_total from public.instantly_campaign_mappings icm
    join public.campaign_runs cr on cr.id = icm.run_id
    where cr.batch_id = target_batch_id
      and icm.status in ('launching', 'scheduled', 'active');
  select coalesce(jsonb_agg(jsonb_build_object(
           'campaign_id', instantly_campaign_id, 'status', status
         )), '[]'::jsonb)
    into provider_campaigns
    from public.instantly_campaign_mappings
   where run_id in (select id from public.campaign_runs where batch_id = target_batch_id)
     and instantly_campaign_id is not null;

  return jsonb_build_object(
    'found', true,
    'batch_id', batch_row.id,
    'file_name', batch_row.file_name,
    'lead_count', lead_total,
    'run_count', run_total,
    'running_run_count', running_total,
    'personalized_email_count', email_total,
    'reply_count', reply_total,
    'instantly_mapping_count', mapping_total,
    'instantly_lead_mapping_count', lead_mapping_total,
    'active_or_scheduled_instantly_campaign_count', active_provider_total,
    'instantly_campaigns', provider_campaigns,
    'campaign_definitions_untouched', true,
    'external_instantly_objects_untouched', true
  );
end;
$$;

revoke all on function public.preview_lead_batch_cleanup(uuid) from public;

create or replace function public.delete_lead_batch_and_related_data(target_batch_id uuid)
returns jsonb
language plpgsql
security invoker
set search_path = public
as $$
begin
  if not exists (select 1 from public.lead_batches where id = target_batch_id) then
    return jsonb_build_object('deleted', false, 'reason', 'not_found');
  end if;
  if exists (select 1 from public.campaign_runs where batch_id = target_batch_id and status = 'running') then
    return jsonb_build_object('deleted', false, 'reason', 'active_run');
  end if;
  if exists (
    select 1 from public.instantly_campaign_mappings icm
    join public.campaign_runs cr on cr.id = icm.run_id
    where cr.batch_id = target_batch_id
      and icm.status in ('launching', 'scheduled', 'active')
  ) then
    return jsonb_build_object('deleted', false, 'reason', 'active_provider_campaign');
  end if;

  delete from public.replies
    where lead_id in (select id from public.leads where batch_id = target_batch_id)
       or email_id in (
         select pe.id from public.personalized_emails pe
         join public.campaign_runs cr on cr.id = pe.run_id
         where cr.batch_id = target_batch_id
       );
  delete from public.personalized_emails
    where run_id in (select id from public.campaign_runs where batch_id = target_batch_id);
  delete from public.campaign_runs where batch_id = target_batch_id;
  delete from public.leads where batch_id = target_batch_id;
  delete from public.lead_batches where id = target_batch_id;
  return jsonb_build_object('deleted', true);
end;
$$;

revoke all on function public.delete_lead_batch_and_related_data(uuid) from public;

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

revoke all on function public.claim_instantly_webhook_events(integer) from public;

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

revoke all on function public.dashboard_bounce_summary() from public;

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

revoke all on function public.record_email_suppressions(jsonb) from public;

create or replace function public.dashboard_run_email_totals(p_run_id uuid)
returns jsonb
language sql
stable
security invoker
set search_path = public
as $$
  select jsonb_build_object(
    'queued', count(*) filter (where pe.status = 'queued' and exists (
      select 1
      from public.instantly_campaign_mappings cm
      join public.instantly_lead_mappings lm on lm.campaign_mapping_id = cm.id
      where cm.run_id = pe.run_id and lm.lead_id = pe.lead_id and lm.status = 'synced'
    )),
    'not_added', count(*) filter (where pe.status = 'queued' and not exists (
      select 1
      from public.instantly_campaign_mappings cm
      join public.instantly_lead_mappings lm on lm.campaign_mapping_id = cm.id
      where cm.run_id = pe.run_id and lm.lead_id = pe.lead_id and lm.status = 'synced'
    )),
    'sent', count(*) filter (where pe.status in ('sent', 'sent_test')),
    'failed', count(*) filter (where pe.status = 'failed'),
    'pending_review', count(*) filter (where pe.status = 'pending_review'),
    'bounced', count(*) filter (where pe.status = 'bounced'),
    'cancelled', count(*) filter (where pe.status = 'cancelled')
  )
  from public.personalized_emails pe
  where pe.run_id = p_run_id;
$$;

revoke all on function public.dashboard_run_email_totals(uuid) from public;

create or replace function public.dashboard_activity(p_days integer default 14)
returns jsonb
language plpgsql
stable
security invoker
set search_path = public
as $$
declare
  result jsonb;
begin
  if p_days < 7 or p_days > 90 then
    raise exception 'Dashboard activity range must be between 7 and 90 days';
  end if;

  with date_spine as (
    select generate_series(
      (now() at time zone 'utc')::date - (p_days - 1),
      (now() at time zone 'utc')::date,
      interval '1 day'
    )::date as day
  ), lead_counts as (
    select (created_at at time zone 'utc')::date as day, count(*)::integer as amount
    from public.leads
    where created_at >= now() - make_interval(days => p_days)
    group by 1
  ), reply_counts as (
    select (received_at at time zone 'utc')::date as day, count(*)::integer as amount
    from public.replies
    where received_at >= now() - make_interval(days => p_days)
    group by 1
  ), email_counts as (
    select (coalesce(pe.sent_at, pe.created_at) at time zone 'utc')::date as day,
      count(*) filter (where pe.status in ('sent', 'sent_test'))::integer as sent,
      count(*) filter (where pe.status = 'queued' and exists (
        select 1
        from public.instantly_campaign_mappings cm
        join public.instantly_lead_mappings lm on lm.campaign_mapping_id = cm.id
        where cm.run_id = pe.run_id and lm.lead_id = pe.lead_id and lm.status = 'synced'
      ))::integer as queued,
      count(*) filter (where pe.status = 'queued' and not exists (
        select 1
        from public.instantly_campaign_mappings cm
        join public.instantly_lead_mappings lm on lm.campaign_mapping_id = cm.id
        where cm.run_id = pe.run_id and lm.lead_id = pe.lead_id and lm.status = 'synced'
      ))::integer as not_added
    from public.personalized_emails pe
    where coalesce(pe.sent_at, pe.created_at) >= now() - make_interval(days => p_days)
      and pe.status in ('sent', 'sent_test', 'queued')
    group by 1
  ), bounce_counts as (
    select (event_at at time zone 'utc')::date as day, count(*)::integer as amount
    from public.instantly_bounce_events
    where event_at >= now() - make_interval(days => p_days)
    group by 1
  ), daily_activity as (
    select d.day,
      coalesce(l.amount, 0) as leads,
      coalesce(r.amount, 0) as replies,
      coalesce(e.sent, 0) as sent,
      coalesce(e.queued, 0) as queued,
      coalesce(e.not_added, 0) as not_added,
      coalesce(b.amount, 0) as bounced
    from date_spine d
    left join lead_counts l using (day)
    left join reply_counts r using (day)
    left join email_counts e using (day)
    left join bounce_counts b using (day)
  ), campaign_status as (
    select coalesce(jsonb_agg(jsonb_build_object('status', status, 'count', amount)
                              order by status), '[]'::jsonb) as value
    from (select status, count(*)::integer as amount from public.campaigns group by status) c
  ), email_status as (
    select coalesce(jsonb_agg(jsonb_build_object('status', status, 'count', amount)
                              order by status), '[]'::jsonb) as value
    from (
      select case
        when pe.status = 'queued' and not exists (
          select 1
          from public.instantly_campaign_mappings cm
          join public.instantly_lead_mappings lm on lm.campaign_mapping_id = cm.id
          where cm.run_id = pe.run_id and lm.lead_id = pe.lead_id and lm.status = 'synced'
        ) then 'not_added'
        else pe.status
      end as status, count(*)::integer as amount
      from public.personalized_emails pe
      group by 1
    ) e
  ), delivery_totals as (
    select jsonb_build_object(
      'queued', count(*) filter (where pe.status = 'queued' and exists (
        select 1
        from public.instantly_campaign_mappings cm
        join public.instantly_lead_mappings lm on lm.campaign_mapping_id = cm.id
        where cm.run_id = pe.run_id and lm.lead_id = pe.lead_id and lm.status = 'synced'
      )),
      'not_added', count(*) filter (where pe.status = 'queued' and not exists (
        select 1
        from public.instantly_campaign_mappings cm
        join public.instantly_lead_mappings lm on lm.campaign_mapping_id = cm.id
        where cm.run_id = pe.run_id and lm.lead_id = pe.lead_id and lm.status = 'synced'
      ))
    ) as value
    from public.personalized_emails pe
  )
  select jsonb_build_object(
    'days', coalesce((select jsonb_agg(jsonb_build_object(
      'date', day, 'leads', leads, 'replies', replies,
      'sent', sent, 'queued', queued, 'not_added', not_added, 'bounced', bounced
    ) order by day) from daily_activity), '[]'::jsonb),
    'campaign_status', (select value from campaign_status),
    'email_status', (select value from email_status),
    'delivery_totals', (select value from delivery_totals)
  ) into result;

  return result;
end;
$$;

revoke all on function public.dashboard_activity(integer) from public;
