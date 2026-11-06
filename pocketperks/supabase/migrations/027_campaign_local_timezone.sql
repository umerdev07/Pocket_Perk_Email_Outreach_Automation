begin;

-- Store the campaign creator's IANA timezone beside the absolute UTC instants.
alter table public.campaigns
  add column if not exists timezone text not null default 'Europe/London';

comment on column public.campaigns.timezone is
  'IANA timezone used to enter and schedule this campaign (for example Asia/Karachi).';

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
  timezone_value text := coalesce(nullif(campaign_data->>'timezone', ''), 'Europe/London');
  context_item text;
  step_idx integer := 0;
  step_rows jsonb := '[]'::jsonb;
  new_step public.campaign_steps;
begin
  if not exists (select 1 from pg_timezone_names where name = timezone_value) then
    raise exception 'Invalid campaign timezone';
  end if;
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
                               start_at, end_at, timezone, status)
  values (campaign_data->>'name', (campaign_data->>'total_emails')::integer,
          campaign_data->>'instructions',
          (start_value at time zone timezone_value)::date,
          (end_value at time zone timezone_value)::date,
          start_value, end_value, timezone_value, 'draft')
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
    'timezone', new_campaign.timezone,
    'status', new_campaign.status, 'created_at', new_campaign.created_at, 'steps', step_rows);
end;
$$;

create or replace function public.update_campaign_with_steps(
  target_campaign_id uuid,
  campaign_data jsonb,
  step_contexts jsonb
)
returns jsonb
language plpgsql
security invoker
set search_path = public
as $$
declare
  current_campaign public.campaigns;
  start_value timestamptz;
  end_value timestamptz;
  timezone_value text := coalesce(nullif(campaign_data->>'timezone', ''), 'Europe/London');
  context_item text;
  step_idx integer := 0;
  step_rows jsonb := '[]'::jsonb;
  new_step public.campaign_steps;
begin
  select * into current_campaign
    from public.campaigns
   where id = target_campaign_id
   for update;
  if not found then
    return jsonb_build_object('updated', false, 'reason', 'not_found');
  end if;
  if exists (select 1 from public.campaign_runs where campaign_id = target_campaign_id) then
    return jsonb_build_object('updated', false, 'reason', 'campaign_started');
  end if;
  if exists (
    select 1 from public.campaign_steps s
    join public.generic_drafts d on d.step_id = s.id
    where s.campaign_id = target_campaign_id and d.generation_status = 'generating'
  ) then
    return jsonb_build_object('updated', false, 'reason', 'draft_generation_running');
  end if;
  if not exists (select 1 from pg_timezone_names where name = timezone_value) then
    raise exception 'Invalid campaign timezone';
  end if;
  if nullif(btrim(campaign_data->>'name'), '') is null
     or nullif(btrim(campaign_data->>'instructions'), '') is null
     or jsonb_typeof(step_contexts) <> 'array'
     or jsonb_array_length(step_contexts) <> (campaign_data->>'total_emails')::integer
     or exists (select 1 from jsonb_array_elements_text(step_contexts) c where btrim(c) = '') then
    raise exception 'Invalid campaign step contexts';
  end if;
  start_value := nullif(campaign_data->>'start_at', '')::timestamptz;
  end_value := nullif(campaign_data->>'end_at', '')::timestamptz;
  if start_value is null or end_value is null or end_value <= start_value then
    raise exception 'Invalid campaign date and time range';
  end if;

  delete from public.generic_drafts d
   using public.campaign_steps s
   where s.campaign_id = target_campaign_id and d.step_id = s.id;
  delete from public.campaign_steps where campaign_id = target_campaign_id;

  update public.campaigns
     set name = btrim(campaign_data->>'name'),
         total_emails = (campaign_data->>'total_emails')::integer,
         instructions = btrim(campaign_data->>'instructions'),
         start_at = start_value,
         end_at = end_value,
         start_date = (start_value at time zone timezone_value)::date,
         end_date = (end_value at time zone timezone_value)::date,
         timezone = timezone_value,
         status = 'draft'
   where id = target_campaign_id;

  for context_item in select jsonb_array_elements_text(step_contexts) loop
    step_idx := step_idx + 1;
    insert into public.campaign_steps(campaign_id, step_number, context, delay_days)
    values (target_campaign_id, step_idx, btrim(context_item), case when step_idx = 1 then 0 else 3 end)
    returning * into new_step;
    step_rows := step_rows || jsonb_build_array(jsonb_build_object(
      'id', new_step.id, 'step_number', new_step.step_number,
      'context', new_step.context, 'delay_days', new_step.delay_days
    ));
  end loop;

  return jsonb_build_object('updated', true, 'id', target_campaign_id,
    'name', btrim(campaign_data->>'name'),
    'total_emails', (campaign_data->>'total_emails')::integer,
    'start_at', start_value, 'end_at', end_value,
    'timezone', timezone_value,
    'status', 'draft', 'steps', step_rows);
end;
$$;

revoke all on function public.create_campaign_with_steps(jsonb, jsonb) from public, anon, authenticated;
revoke all on function public.update_campaign_with_steps(uuid, jsonb, jsonb) from public, anon, authenticated;
grant execute on function public.create_campaign_with_steps(jsonb, jsonb) to service_role;
grant execute on function public.update_campaign_with_steps(uuid, jsonb, jsonb) to service_role;

commit;
