begin;

create or replace function public.preview_lead_batch_cleanup(target_batch_id uuid)
returns jsonb
language plpgsql
security invoker
set search_path = public
as $$
declare
  batch_row public.lead_batches;
  lead_total bigint;
  run_total bigint;
  running_total bigint;
  email_total bigint;
  reply_total bigint;
  mapping_total bigint;
  lead_mapping_total bigint;
  active_provider_total bigint;
  provider_campaigns jsonb;
begin
  select * into batch_row from public.lead_batches where id = target_batch_id;
  if not found then
    return jsonb_build_object('found', false);
  end if;

  select count(*) into lead_total from public.leads where batch_id = target_batch_id;
  select count(*), count(*) filter (where status = 'running')
    into run_total, running_total
    from public.campaign_runs where batch_id = target_batch_id;
  select count(*) into email_total from public.personalized_emails
    where run_id in (select id from public.campaign_runs where batch_id = target_batch_id);
  select count(*) into reply_total from public.replies
    where lead_id in (select id from public.leads where batch_id = target_batch_id)
       or email_id in (
         select pe.id from public.personalized_emails pe
         join public.campaign_runs cr on cr.id = pe.run_id
         where cr.batch_id = target_batch_id
       );
  select count(*) into mapping_total from public.instantly_campaign_mappings
    where run_id in (select id from public.campaign_runs where batch_id = target_batch_id);
  select count(*) into lead_mapping_total from public.instantly_lead_mappings ilm
    join public.instantly_campaign_mappings icm on icm.id = ilm.campaign_mapping_id
    join public.campaign_runs cr on cr.id = icm.run_id
    where cr.batch_id = target_batch_id;
  select count(*) into active_provider_total from public.instantly_campaign_mappings icm
    join public.campaign_runs cr on cr.id = icm.run_id
    where cr.batch_id = target_batch_id
      and icm.status in ('launching', 'scheduled', 'active');
  select coalesce(jsonb_agg(jsonb_build_object(
           'campaign_id', instantly_campaign_id, 'status', status
         )), '[]'::jsonb)
    into provider_campaigns
    from public.instantly_campaign_mappings
   where run_id in (select id from public.campaign_runs where batch_id = target_batch_id)
     and instantly_campaign_id is not null;

  return jsonb_build_object(
    'found', true,
    'batch_id', batch_row.id,
    'file_name', batch_row.file_name,
    'lead_count', lead_total,
    'run_count', run_total,
    'running_run_count', running_total,
    'personalized_email_count', email_total,
    'reply_count', reply_total,
    'instantly_mapping_count', mapping_total,
    'instantly_lead_mapping_count', lead_mapping_total,
    'active_or_scheduled_instantly_campaign_count', active_provider_total,
    'instantly_campaigns', provider_campaigns,
    'campaign_definitions_untouched', true,
    'external_instantly_objects_untouched', true
  );
end;
$$;

-- Do not remove the local pointer to a campaign that can still send externally.
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
  if exists (
    select 1 from public.instantly_campaign_mappings icm
    join public.campaign_runs cr on cr.id = icm.run_id
    where cr.batch_id = target_batch_id
      and icm.status in ('launching', 'scheduled', 'active')
  ) then
    return jsonb_build_object('deleted', false, 'reason', 'active_provider_campaign');
  end if;

  delete from public.replies
    where lead_id in (select id from public.leads where batch_id = target_batch_id)
       or email_id in (
         select pe.id from public.personalized_emails pe
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

revoke all on function public.preview_lead_batch_cleanup(uuid) from public, anon, authenticated;
revoke all on function public.delete_lead_batch_and_related_data(uuid) from public, anon, authenticated;
grant execute on function public.preview_lead_batch_cleanup(uuid) to service_role;
grant execute on function public.delete_lead_batch_and_related_data(uuid) to service_role;

commit;
