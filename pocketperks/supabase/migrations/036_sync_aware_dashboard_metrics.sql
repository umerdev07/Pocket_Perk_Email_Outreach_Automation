begin;

-- A local queued draft is not provider-queued until its lead mapping is synced.
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

revoke all on function public.dashboard_run_email_totals(uuid) from public, anon, authenticated;
grant execute on function public.dashboard_run_email_totals(uuid) to service_role;

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

revoke all on function public.dashboard_activity(integer) from public, anon, authenticated;
grant execute on function public.dashboard_activity(integer) to service_role;

commit;
