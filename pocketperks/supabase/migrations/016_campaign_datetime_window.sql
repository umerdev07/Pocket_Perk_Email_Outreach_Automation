begin;

alter table public.campaigns
  add column if not exists start_at timestamptz,
  add column if not exists end_at timestamptz;

-- Preserve older date-only campaigns as UK-local day windows.
update public.campaigns
set start_at = (start_date::timestamp at time zone 'Europe/London')
where start_at is null and start_date is not null;

update public.campaigns
set end_at = ((end_date + 1)::timestamp at time zone 'Europe/London') - interval '1 second'
where end_at is null and end_date is not null;

alter table public.campaigns
  drop constraint if exists campaigns_datetime_window_check;
alter table public.campaigns
  add constraint campaigns_datetime_window_check
  check (start_at is null or end_at is null or end_at > start_at);

comment on column public.campaigns.start_at is
  'Requested campaign window start, stored as an absolute timestamp; UI inputs are Europe/London local time.';
comment on column public.campaigns.end_at is
  'Requested campaign window end, stored as an absolute timestamp; UI inputs are Europe/London local time.';

create or replace function public.create_campaign_with_steps(campaign_data jsonb, step_contexts jsonb)
returns jsonb
language plpgsql
security invoker
set search_path = public
as $$
declare
  new_campaign public.campaigns;
  start_value timestamptz;
  end_value timestamptz;
  context_item text;
  step_idx integer := 0;
  step_rows jsonb := '[]'::jsonb;
  new_step public.campaign_steps;
begin
  if jsonb_typeof(step_contexts) <> 'array'
     or jsonb_array_length(step_contexts) <> (campaign_data->>'total_emails')::integer
     or exists (select 1 from jsonb_array_elements_text(step_contexts) c where btrim(c) = '') then
    raise exception 'Invalid campaign step contexts';
  end if;

  start_value := nullif(campaign_data->>'start_at', '')::timestamptz;
  end_value := nullif(campaign_data->>'end_at', '')::timestamptz;
  if start_value is null or end_value is null or end_value <= start_value then
    raise exception 'Invalid campaign date and time range';
  end if;

  insert into public.campaigns(name, total_emails, instructions, start_date, end_date,
                               start_at, end_at, status)
  values (campaign_data->>'name', (campaign_data->>'total_emails')::integer,
          campaign_data->>'instructions',
          (start_value at time zone 'Europe/London')::date,
          (end_value at time zone 'Europe/London')::date,
          start_value, end_value, 'draft')
  returning * into new_campaign;

  for context_item in select jsonb_array_elements_text(step_contexts) loop
    step_idx := step_idx + 1;
    insert into public.campaign_steps(campaign_id, step_number, context, delay_days)
    values (new_campaign.id, step_idx, context_item, case when step_idx = 1 then 0 else 3 end)
    returning * into new_step;
    step_rows := step_rows || jsonb_build_array(jsonb_build_object(
      'id', new_step.id, 'step_number', new_step.step_number,
      'context', new_step.context, 'delay_days', new_step.delay_days
    ));
  end loop;

  return jsonb_build_object('id', new_campaign.id, 'name', new_campaign.name,
    'total_emails', new_campaign.total_emails, 'instructions', new_campaign.instructions,
    'start_date', new_campaign.start_date, 'end_date', new_campaign.end_date,
    'start_at', new_campaign.start_at, 'end_at', new_campaign.end_at,
    'status', new_campaign.status, 'created_at', new_campaign.created_at, 'steps', step_rows);
end;
$$;

revoke all on function public.create_campaign_with_steps(jsonb, jsonb) from public, anon, authenticated;
grant execute on function public.create_campaign_with_steps(jsonb, jsonb) to service_role;

commit;
