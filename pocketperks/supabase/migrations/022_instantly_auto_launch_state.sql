begin;

-- Track activation in progress so a restart never blindly repeats an uncertain launch.
alter table public.instantly_campaign_mappings
  drop constraint if exists instantly_campaign_mappings_status_check;

alter table public.instantly_campaign_mappings
  add constraint instantly_campaign_mappings_status_check
  check (status in ('pending', 'draft_created', 'syncing_leads', 'leads_synced',
                    'launching', 'active', 'paused', 'completed', 'failed'));

commit;
