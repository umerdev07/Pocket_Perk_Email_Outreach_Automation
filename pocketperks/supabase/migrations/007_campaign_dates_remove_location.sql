begin;

alter table public.campaigns
  alter column location drop not null,
  add column if not exists start_date date,
  add column if not exists end_date date;

comment on column public.campaigns.location is
  'Legacy value retained for existing records; new campaigns and application APIs do not use campaign location.';

alter table public.campaigns
  drop constraint if exists campaigns_date_range_check;

alter table public.campaigns
  add constraint campaigns_date_range_check
  check (start_date is null or end_date is null or end_date >= start_date);

create or replace function public.create_campaign_with_steps(campaign_data jsonb, step_contexts jsonb)
returns jsonb
language plpgsql
security invoker
set search_path = public
as $$
declare
  new_campaign public.campaigns;
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
  if (campaign_data->>'start_date')::date is null
     or (campaign_data->>'end_date')::date is null
     or (campaign_data->>'end_date')::date < (campaign_data->>'start_date')::date then
    raise exception 'Invalid campaign date range';
  end if;

  insert into public.campaigns(name, total_emails, instructions, start_date, end_date, status)
  values (campaign_data->>'name', (campaign_data->>'total_emails')::integer,
          campaign_data->>'instructions', (campaign_data->>'start_date')::date,
          (campaign_data->>'end_date')::date, 'draft')
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
    'status', new_campaign.status, 'created_at', new_campaign.created_at, 'steps', step_rows);
end;
$$;

revoke all on function public.create_campaign_with_steps(jsonb, jsonb) from public, anon, authenticated;
grant execute on function public.create_campaign_with_steps(jsonb, jsonb) to service_role;

commit;
