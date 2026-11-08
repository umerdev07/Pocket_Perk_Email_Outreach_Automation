begin;

alter table public.campaign_steps
  add column if not exists trigger_type text not null default 'scheduled'
    check (trigger_type in ('scheduled', 'after_conversation', 'close_loop'));

alter table public.personalized_emails
  add column if not exists manual_send_state text
    check (manual_send_state in ('sending', 'sent', 'unknown'));

comment on column public.campaign_steps.trigger_type is
  'scheduled steps are sent by the Instantly sequence; after_conversation steps require a confirmed manual thread reply.';
comment on column public.personalized_emails.manual_send_state is
  'Idempotency guard for an explicitly triggered after-conversation email.';

create or replace function public.set_campaign_step_triggers(step_triggers jsonb)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  item jsonb;
  step_id_value uuid;
  trigger_value text;
  updated_count integer := 0;
begin
  if jsonb_typeof(step_triggers) is distinct from 'array' then
    raise exception 'Invalid step triggers';
  end if;
  for item in select value from jsonb_array_elements(step_triggers)
  loop
    step_id_value := (item->>'step_id')::uuid;
    trigger_value := item->>'trigger_type';
    if trigger_value not in ('scheduled', 'after_conversation', 'close_loop') then
      raise exception 'Invalid step trigger';
    end if;
    update public.campaign_steps set trigger_type = trigger_value
     where id = step_id_value and step_number > 1;
    if not found then
      raise exception 'Email 1 cannot use a manual trigger, or the step does not exist';
    end if;
    updated_count := updated_count + 1;
  end loop;
  return jsonb_build_object('updated', updated_count);
end;
$$;

revoke all on function public.set_campaign_step_triggers(jsonb) from public, anon, authenticated;
grant execute on function public.set_campaign_step_triggers(jsonb) to service_role;

commit;
