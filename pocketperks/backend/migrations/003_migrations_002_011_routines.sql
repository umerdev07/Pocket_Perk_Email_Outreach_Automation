-- PostgreSQL routines ported from Supabase migrations 003, 005, and 009.

-- Base tables, columns, and indexes are already defined in migration 001.

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

  insert into public.campaigns(name, total_emails, location, instructions, status)
  values (campaign_data->>'name', (campaign_data->>'total_emails')::integer,
          campaign_data->>'location', campaign_data->>'instructions', 'draft')
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
    'total_emails', new_campaign.total_emails, 'location', new_campaign.location,
    'instructions', new_campaign.instructions, 'status', new_campaign.status,
    'created_at', new_campaign.created_at, 'steps', step_rows);
end;
$$;

revoke all on function public.create_campaign_with_steps(jsonb,jsonb) from public;

create or replace function public.delete_campaign_and_related_data(target_campaign_id uuid)
returns jsonb
language plpgsql
security invoker
set search_path = public
as $$
begin
  if not exists (select 1 from public.campaigns where id = target_campaign_id) then
    return jsonb_build_object('deleted', false, 'reason', 'not_found');
  end if;
  if exists (select 1 from public.campaign_runs where campaign_id = target_campaign_id and status = 'running') then
    return jsonb_build_object('deleted', false, 'reason', 'active_run');
  end if;

  delete from public.replies
    where campaign_id = target_campaign_id
       or email_id in (select id from public.personalized_emails where campaign_id = target_campaign_id);
  delete from public.personalized_emails where campaign_id = target_campaign_id;
  delete from public.campaign_runs where campaign_id = target_campaign_id;
  delete from public.generic_drafts
    where step_id in (select id from public.campaign_steps where campaign_id = target_campaign_id);
  delete from public.campaign_steps where campaign_id = target_campaign_id;
  delete from public.campaigns where id = target_campaign_id;

  return jsonb_build_object('deleted', true);
end;
$$;

revoke all on function public.delete_campaign_and_related_data(uuid) from public;

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

  delete from public.replies
    where lead_id in (select id from public.leads where batch_id = target_batch_id)
       or email_id in (
         select pe.id
         from public.personalized_emails pe
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

create or replace function public.record_email_review_history()
returns trigger
language plpgsql
security invoker
set search_path = public
as $$
declare
  event_action text;
  secondary_action text;
  lead_name text;
  lead_address text;
  campaign_title text;
  step_index integer;
begin
  if tg_op = 'INSERT' then
    event_action := 'risk_assessed';
  elsif new.reviewed_at is distinct from old.reviewed_at and new.reviewed_at is not null then
    event_action := case when new.status = 'rejected' then 'human_rejected' else 'human_approved' end;
  elsif new.subject is distinct from old.subject or new.body is distinct from old.body then
    event_action := 'email_edited';
  elsif new.status is distinct from old.status and new.status = 'queued' then
    event_action := 'auto_queued';
  elsif new.status is distinct from old.status and new.status = 'pending_review' then
    event_action := 'sent_to_review';
  elsif new.risk_level is distinct from old.risk_level or new.risk_reason is distinct from old.risk_reason then
    event_action := 'risk_rechecked';
  elsif new.status is distinct from old.status then
    event_action := 'status_changed';
  else
    return new;
  end if;

  select l.business_name, l.email, c.name, s.step_number
    into lead_name, lead_address, campaign_title, step_index
    from public.personalized_emails e
    left join public.leads l on l.id = e.lead_id
    left join public.campaigns c on c.id = e.campaign_id
    left join public.campaign_steps s on s.id = e.step_id
   where e.id = new.id;

  insert into public.email_review_history(
    email_id, action, status, risk_level, risk_reason, subject,
    business_name, recipient_email, campaign_name, step_number
  ) values (
    new.id, event_action, new.status, new.risk_level, new.risk_reason, new.subject,
    lead_name, lead_address, campaign_title, step_index
  );
  if tg_op = 'UPDATE' and new.status is distinct from old.status then
    secondary_action := case
      when new.status = 'queued' and new.reviewed_at is null then 'auto_queued'
      when new.status = 'pending_review' then 'sent_to_review'
      else null
    end;
    if secondary_action is not null and secondary_action <> event_action then
      insert into public.email_review_history(
        email_id, action, status, risk_level, risk_reason, subject,
        business_name, recipient_email, campaign_name, step_number
      ) values (
        new.id, secondary_action, new.status, new.risk_level, new.risk_reason, new.subject,
        lead_name, lead_address, campaign_title, step_index
      );
    end if;
  end if;
  return new;
end;
$$;

drop trigger if exists personalized_email_review_history on public.personalized_emails;

create trigger personalized_email_review_history
after insert or update of subject, body, status, risk_level, risk_reason, reviewed_at
on public.personalized_emails
for each row execute function public.record_email_review_history();

revoke all on function public.record_email_review_history() from public;
