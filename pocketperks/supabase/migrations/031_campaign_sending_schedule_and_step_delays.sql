begin;

-- Keep campaign activation dates separate from the daily sending window.
alter table public.campaigns
  add column if not exists daily_send_start time not null default time '09:00',
  add column if not exists daily_send_end time not null default time '17:00',
  add column if not exists sending_days smallint[] not null default array[1,2,3,4,5]::smallint[];

-- Preserve the provider window that existing campaigns previously derived from
-- their campaign start/end clock times. New campaigns use the explicit UI defaults.
update public.campaigns
   set daily_send_start = case
         when (end_at at time zone timezone)::time <= (start_at at time zone timezone)::time
           then time '00:00'
         else (start_at at time zone timezone)::time
       end,
       daily_send_end = case
         when (end_at at time zone timezone)::time <= (start_at at time zone timezone)::time
           then time '23:59'
         else (end_at at time zone timezone)::time
       end
 where start_at is not null and end_at is not null;

alter table public.campaigns
  drop constraint if exists campaigns_daily_send_window_check,
  add constraint campaigns_daily_send_window_check check (daily_send_end > daily_send_start),
  drop constraint if exists campaigns_sending_days_check,
  add constraint campaigns_sending_days_check check (
    cardinality(sending_days) between 1 and 7
    and sending_days <@ array[0,1,2,3,4,5,6]::smallint[]
  );

-- Keep delay_days for older readers, while delay_minutes is the authoritative
-- value for new minute/hour/day follow-up configuration.
alter table public.campaign_steps
  add column if not exists delay_minutes integer;
update public.campaign_steps
   set delay_minutes = delay_days * 1440
 where delay_minutes is null;
alter table public.campaign_steps
  alter column delay_minutes set not null,
  alter column delay_minutes drop default,
  drop constraint if exists campaign_steps_delay_minutes_check,
  add constraint campaign_steps_delay_minutes_check check (delay_minutes between 0 and 43200);

-- Older create/update RPCs omit delay_minutes. Their inserts continue to work:
-- the trigger derives the exact-minute value from the legacy day field.
create or replace function public.default_step_delay()
returns trigger
language plpgsql
as $$
begin
  if new.delay_days is null then
    new.delay_days := case when new.step_number=1 then 0 else 3 end;
  end if;
  if new.delay_minutes is null then
    new.delay_minutes := new.delay_days * 1440;
  end if;
  return new;
end;
$$;

create or replace function public.create_campaign_with_schedule(
  campaign_data jsonb,
  step_contexts jsonb,
  step_delays_minutes jsonb
)
returns jsonb
language plpgsql
security invoker
set search_path = public
as $$
declare
  new_campaign public.campaigns;
  new_step public.campaign_steps;
  start_value timestamptz;
  end_value timestamptz;
  timezone_value text := coalesce(nullif(campaign_data->>'timezone', ''), 'Europe/London');
  daily_start_value time := coalesce(nullif(campaign_data->>'daily_send_start', '')::time, time '09:00');
  daily_end_value time := coalesce(nullif(campaign_data->>'daily_send_end', '')::time, time '17:00');
  sending_days_value smallint[];
  total_value integer := (campaign_data->>'total_emails')::integer;
  step_idx integer;
  context_item text;
  delay_value integer;
  minimum_delay integer;
  step_rows jsonb := '[]'::jsonb;
begin
  if not exists (select 1 from pg_timezone_names where name = timezone_value) then
    raise exception 'Invalid campaign timezone';
  end if;
  if nullif(btrim(campaign_data->>'name'), '') is null
     or nullif(btrim(campaign_data->>'instructions'), '') is null
     or total_value not between 1 and 10
     or jsonb_typeof(step_contexts) is distinct from 'array'
     or jsonb_array_length(step_contexts) <> total_value
     or exists (select 1 from jsonb_array_elements_text(step_contexts) c where btrim(c) = '')
     or jsonb_typeof(step_delays_minutes) is distinct from 'array'
     or jsonb_array_length(step_delays_minutes) <> total_value then
    raise exception 'Invalid campaign setup';
  end if;
  if jsonb_typeof(campaign_data->'sending_days') is distinct from 'array'
     or jsonb_array_length(campaign_data->'sending_days') = 0 then
    raise exception 'Select at least one sending day';
  end if;
  select array_agg(day_value::smallint order by day_value::smallint)
    into sending_days_value
    from jsonb_array_elements_text(campaign_data->'sending_days') day_value;
  if cardinality(sending_days_value) not between 1 and 7
     or not (sending_days_value <@ array[0,1,2,3,4,5,6]::smallint[])
     or cardinality(sending_days_value) <> (select count(distinct d) from unnest(sending_days_value) d) then
    raise exception 'Invalid campaign sending days';
  end if;
  if daily_end_value <= daily_start_value then
    raise exception 'Daily sending end time must be later than start time';
  end if;
  start_value := nullif(campaign_data->>'start_at', '')::timestamptz;
  end_value := nullif(campaign_data->>'end_at', '')::timestamptz;
  if start_value is null or end_value is null or end_value <= start_value then
    raise exception 'Invalid campaign date and time range';
  end if;
  for step_idx in 1..total_value loop
    delay_value := (step_delays_minutes->>(step_idx - 1))::integer;
    minimum_delay := case when step_idx = 1 then 0 else 15 end;
    if delay_value is null or delay_value < minimum_delay
       or delay_value > 43200 or (step_idx = 1 and delay_value <> 0) then
      raise exception 'Invalid email step delay';
    end if;
  end loop;

  insert into public.campaigns(name,total_emails,instructions,start_date,end_date,start_at,end_at,
                               timezone,daily_send_start,daily_send_end,sending_days,status)
  values (btrim(campaign_data->>'name'),total_value,btrim(campaign_data->>'instructions'),
          (start_value at time zone timezone_value)::date,(end_value at time zone timezone_value)::date,
          start_value,end_value,timezone_value,daily_start_value,daily_end_value,sending_days_value,'draft')
  returning * into new_campaign;

  for step_idx in 1..total_value loop
    context_item := btrim(step_contexts->>(step_idx - 1));
    delay_value := (step_delays_minutes->>(step_idx - 1))::integer;
    insert into public.campaign_steps(campaign_id,step_number,context,delay_days,delay_minutes)
    values (new_campaign.id,step_idx,context_item,delay_value / 1440,delay_value)
    returning * into new_step;
    step_rows := step_rows || jsonb_build_array(jsonb_build_object(
      'id',new_step.id,'step_number',new_step.step_number,'context',new_step.context,
      'delay_days',new_step.delay_days,'delay_minutes',new_step.delay_minutes));
  end loop;
  return jsonb_build_object('id',new_campaign.id,'name',new_campaign.name,
    'total_emails',new_campaign.total_emails,'instructions',new_campaign.instructions,
    'start_date',new_campaign.start_date,'end_date',new_campaign.end_date,
    'start_at',new_campaign.start_at,'end_at',new_campaign.end_at,'timezone',new_campaign.timezone,
    'daily_send_start',new_campaign.daily_send_start,'daily_send_end',new_campaign.daily_send_end,
    'sending_days',new_campaign.sending_days,'status',new_campaign.status,
    'created_at',new_campaign.created_at,'steps',step_rows);
end;
$$;

create or replace function public.update_campaign_with_schedule(
  target_campaign_id uuid,
  campaign_data jsonb,
  step_contexts jsonb,
  step_delays_minutes jsonb
)
returns jsonb
language plpgsql
security invoker
set search_path = public
as $$
declare
  current_campaign public.campaigns;
  new_step public.campaign_steps;
  start_value timestamptz;
  end_value timestamptz;
  timezone_value text := coalesce(nullif(campaign_data->>'timezone', ''), 'Europe/London');
  daily_start_value time := coalesce(nullif(campaign_data->>'daily_send_start', '')::time, time '09:00');
  daily_end_value time := coalesce(nullif(campaign_data->>'daily_send_end', '')::time, time '17:00');
  sending_days_value smallint[];
  total_value integer := (campaign_data->>'total_emails')::integer;
  step_idx integer;
  delay_value integer;
  minimum_delay integer;
  step_rows jsonb := '[]'::jsonb;
begin
  select * into current_campaign from public.campaigns where id=target_campaign_id for update;
  if not found then return jsonb_build_object('updated',false,'reason','not_found'); end if;
  if exists(select 1 from public.campaign_runs where campaign_id=target_campaign_id) then
    return jsonb_build_object('updated',false,'reason','campaign_started');
  end if;
  if exists(select 1 from public.campaign_steps s join public.generic_drafts d on d.step_id=s.id
            where s.campaign_id=target_campaign_id and d.generation_status='generating') then
    return jsonb_build_object('updated',false,'reason','draft_generation_running');
  end if;
  if not exists(select 1 from pg_timezone_names where name=timezone_value)
     or nullif(btrim(campaign_data->>'name'),'') is null
     or nullif(btrim(campaign_data->>'instructions'),'') is null
     or total_value not between 1 and 10
     or jsonb_typeof(step_contexts) is distinct from 'array' or jsonb_array_length(step_contexts)<>total_value
     or exists(select 1 from jsonb_array_elements_text(step_contexts) c where btrim(c)='')
     or jsonb_typeof(step_delays_minutes) is distinct from 'array' or jsonb_array_length(step_delays_minutes)<>total_value then
    raise exception 'Invalid campaign setup';
  end if;
  if jsonb_typeof(campaign_data->'sending_days') is distinct from 'array'
     or jsonb_array_length(campaign_data->'sending_days')=0 then
    raise exception 'Select at least one sending day';
  end if;
  select array_agg(day_value::smallint order by day_value::smallint)
    into sending_days_value from jsonb_array_elements_text(campaign_data->'sending_days') day_value;
  if cardinality(sending_days_value) not between 1 and 7
     or not (sending_days_value <@ array[0,1,2,3,4,5,6]::smallint[])
     or cardinality(sending_days_value)<>(select count(distinct d) from unnest(sending_days_value) d)
     or daily_end_value<=daily_start_value then
    raise exception 'Invalid campaign schedule';
  end if;
  start_value:=nullif(campaign_data->>'start_at','')::timestamptz;
  end_value:=nullif(campaign_data->>'end_at','')::timestamptz;
  if start_value is null or end_value is null or end_value<=start_value then
    raise exception 'Invalid campaign date and time range';
  end if;
  for step_idx in 1..total_value loop
    delay_value:=(step_delays_minutes->>(step_idx-1))::integer;
    minimum_delay:=case when step_idx=1 then 0 else 15 end;
    if delay_value is null or delay_value<minimum_delay
       or delay_value>43200 or (step_idx=1 and delay_value<>0) then
      raise exception 'Invalid email step delay';
    end if;
  end loop;

  delete from public.generic_drafts d using public.campaign_steps s
   where s.campaign_id=target_campaign_id and d.step_id=s.id;
  delete from public.campaign_steps where campaign_id=target_campaign_id;
  update public.campaigns set name=btrim(campaign_data->>'name'),total_emails=total_value,
    instructions=btrim(campaign_data->>'instructions'),start_at=start_value,end_at=end_value,
    start_date=(start_value at time zone timezone_value)::date,
    end_date=(end_value at time zone timezone_value)::date,timezone=timezone_value,
    daily_send_start=daily_start_value,daily_send_end=daily_end_value,
    sending_days=sending_days_value,status='draft'
   where id=target_campaign_id;
  for step_idx in 1..total_value loop
    delay_value:=(step_delays_minutes->>(step_idx-1))::integer;
    insert into public.campaign_steps(campaign_id,step_number,context,delay_days,delay_minutes)
    values(target_campaign_id,step_idx,btrim(step_contexts->>(step_idx-1)),delay_value/1440,delay_value)
    returning * into new_step;
    step_rows:=step_rows||jsonb_build_array(jsonb_build_object(
      'id',new_step.id,'step_number',new_step.step_number,'context',new_step.context,
      'delay_days',new_step.delay_days,'delay_minutes',new_step.delay_minutes));
  end loop;
  return jsonb_build_object('updated',true,'id',target_campaign_id,'name',btrim(campaign_data->>'name'),
    'total_emails',total_value,'start_at',start_value,'end_at',end_value,'timezone',timezone_value,
    'daily_send_start',daily_start_value,'daily_send_end',daily_end_value,'sending_days',sending_days_value,
    'status','draft','steps',step_rows);
end;
$$;

-- Keep the new exact delay column coherent when an unused draft step is deleted
-- and the remaining steps are renumbered.
create or replace function public.delete_campaign_step(target_campaign_id uuid, target_step_id uuid)
returns jsonb
language plpgsql
security invoker
set search_path = public
as $$
declare
  current_total integer;
  removed_number integer;
  remaining_step record;
begin
  select total_emails into current_total from public.campaigns
   where id=target_campaign_id for update;
  if current_total is null then return jsonb_build_object('deleted',false,'reason','not_found'); end if;
  if not exists(select 1 from public.campaign_steps where id=target_step_id and campaign_id=target_campaign_id) then
    return jsonb_build_object('deleted',false,'reason','not_found');
  end if;
  if current_total<=1 then return jsonb_build_object('deleted',false,'reason','last_step'); end if;
  if exists(select 1 from public.campaign_runs where campaign_id=target_campaign_id) then
    return jsonb_build_object('deleted',false,'reason','campaign_started');
  end if;
  select step_number into removed_number from public.campaign_steps where id=target_step_id;
  delete from public.generic_drafts where step_id=target_step_id;
  delete from public.campaign_steps where id=target_step_id;
  for remaining_step in
    select id,step_number,delay_minutes from public.campaign_steps
     where campaign_id=target_campaign_id and step_number>removed_number order by step_number asc
  loop
    update public.campaign_steps set
      step_number=remaining_step.step_number-1,
      delay_minutes=case when remaining_step.step_number-1=1 then 0 else remaining_step.delay_minutes end,
      delay_days=case when remaining_step.step_number-1=1 then 0 else remaining_step.delay_minutes/1440 end
     where id=remaining_step.id;
  end loop;
  update public.generic_drafts d set status='edited',approved_at=null,updated_at=now()
    from public.campaign_steps s
   where s.campaign_id=target_campaign_id and d.step_id=s.id and d.status='approved';
  update public.campaigns set total_emails=current_total-1,status='draft' where id=target_campaign_id;
  return jsonb_build_object('deleted',true,'campaign_id',target_campaign_id,'total_emails',current_total-1);
end;
$$;

revoke all on function public.create_campaign_with_schedule(jsonb,jsonb,jsonb) from public,anon,authenticated;
revoke all on function public.update_campaign_with_schedule(uuid,jsonb,jsonb,jsonb) from public,anon,authenticated;
grant execute on function public.create_campaign_with_schedule(jsonb,jsonb,jsonb) to service_role;
grant execute on function public.update_campaign_with_schedule(uuid,jsonb,jsonb,jsonb) to service_role;
revoke all on function public.delete_campaign_step(uuid,uuid) from public,anon,authenticated;
grant execute on function public.delete_campaign_step(uuid,uuid) to service_role;

commit;
