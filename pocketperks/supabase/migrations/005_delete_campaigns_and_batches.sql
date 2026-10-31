begin;

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

revoke all on function public.delete_campaign_and_related_data(uuid) from public, anon, authenticated;
revoke all on function public.delete_lead_batch_and_related_data(uuid) from public, anon, authenticated;
grant execute on function public.delete_campaign_and_related_data(uuid) to service_role;
grant execute on function public.delete_lead_batch_and_related_data(uuid) to service_role;

commit;
