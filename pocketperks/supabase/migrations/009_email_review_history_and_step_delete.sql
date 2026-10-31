begin;

create table public.email_review_history (
  id uuid primary key default gen_random_uuid(),
  -- Keep the audit snapshot if the source email/campaign is deleted later.
  email_id uuid not null,
  action text not null,
  status text not null,
  risk_level text,
  risk_reason text,
  subject text not null,
  business_name text,
  recipient_email text,
  campaign_name text,
  step_number integer,
  created_at timestamptz not null default now()
);
create index email_review_history_created_idx on public.email_review_history(created_at desc, id desc);
create index email_review_history_email_idx on public.email_review_history(email_id, created_at desc);

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

create or replace function public.delete_campaign_step(target_campaign_id uuid, target_step_id uuid)
returns jsonb
language plpgsql
security invoker
set search_path = public
as $$
declare
  current_total integer;
  removed_number integer;
  current_status text;
  remaining_step record;
begin
  select total_emails, status into current_total, current_status
    from public.campaigns where id = target_campaign_id for update;
  if current_total is null then
    return jsonb_build_object('deleted', false, 'reason', 'not_found');
  end if;
  if not exists (select 1 from public.campaign_steps where id = target_step_id and campaign_id = target_campaign_id) then
    return jsonb_build_object('deleted', false, 'reason', 'not_found');
  end if;
  if current_total <= 1 then
    return jsonb_build_object('deleted', false, 'reason', 'last_step');
  end if;
  if exists (select 1 from public.campaign_runs where campaign_id = target_campaign_id) then
    return jsonb_build_object('deleted', false, 'reason', 'campaign_started');
  end if;

  select step_number into removed_number from public.campaign_steps where id = target_step_id;
  delete from public.generic_drafts where step_id = target_step_id;
  delete from public.campaign_steps where id = target_step_id;

  -- Close the sequence gap from low to high; each target slot is vacant by
  -- then, so the unique constraint stays valid without out-of-range numbers.
  for remaining_step in
    select id, step_number from public.campaign_steps
     where campaign_id = target_campaign_id and step_number > removed_number
     order by step_number asc
  loop
    update public.campaign_steps
       set step_number = remaining_step.step_number - 1,
           delay_days = case when remaining_step.step_number - 1 = 1 then 0 else 3 end
     where id = remaining_step.id;
  end loop;

  update public.generic_drafts d
     set status = 'edited', approved_at = null, updated_at = now()
    from public.campaign_steps s
   where s.campaign_id = target_campaign_id and d.step_id = s.id and d.status = 'approved';
  update public.campaigns
     set total_emails = current_total - 1, status = 'draft'
   where id = target_campaign_id;

  return jsonb_build_object('deleted', true, 'campaign_id', target_campaign_id,
                            'total_emails', current_total - 1);
end;
$$;

revoke all on public.email_review_history from anon, authenticated;
grant all on public.email_review_history to service_role;
revoke all on function public.record_email_review_history() from public, anon, authenticated;
revoke all on function public.delete_campaign_step(uuid, uuid) from public, anon, authenticated;
grant execute on function public.delete_campaign_step(uuid, uuid) to service_role;

commit;
