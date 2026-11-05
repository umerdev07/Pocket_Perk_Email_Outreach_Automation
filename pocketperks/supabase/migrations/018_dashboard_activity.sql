begin;

-- Aggregate chart data in Postgres rather than transferring raw lead/reply rows.
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
    select (created_at at time zone 'utc')::date as day, count(*)::integer as count
    from public.leads
    where created_at >= now() - make_interval(days => p_days)
    group by 1
  ), reply_counts as (
    select (received_at at time zone 'utc')::date as day, count(*)::integer as count
    from public.replies
    where received_at >= now() - make_interval(days => p_days)
    group by 1
  ), daily_activity as (
    select d.day, coalesce(l.count, 0) as leads, coalesce(r.count, 0) as replies
    from date_spine d
    left join lead_counts l using (day)
    left join reply_counts r using (day)
  ), campaign_status as (
    select coalesce(jsonb_agg(jsonb_build_object('status', status, 'count', amount)
                              order by status), '[]'::jsonb) as value
    from (select status, count(*)::integer as amount from public.campaigns group by status) c
  ), email_status as (
    select coalesce(jsonb_agg(jsonb_build_object('status', status, 'count', amount)
                              order by status), '[]'::jsonb) as value
    from (select status, count(*)::integer as amount from public.personalized_emails group by status) e
  )
  select jsonb_build_object(
    'days', coalesce((select jsonb_agg(jsonb_build_object(
      'date', day, 'leads', leads, 'replies', replies
    ) order by day) from daily_activity), '[]'::jsonb),
    'campaign_status', (select value from campaign_status),
    'email_status', (select value from email_status)
  ) into result;

  return result;
end;
$$;

revoke all on function public.dashboard_activity(integer) from public, anon, authenticated;
grant execute on function public.dashboard_activity(integer) to service_role;

commit;
