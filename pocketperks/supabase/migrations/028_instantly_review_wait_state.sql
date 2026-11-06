begin;

-- Distinguish a review hold from a failed provider sync. The same Instantly
-- draft is reused and receives leads only after they become queued.
alter table public.instantly_campaign_mappings
  drop constraint if exists instantly_campaign_mappings_status_check;

alter table public.instantly_campaign_mappings
  add constraint instantly_campaign_mappings_status_check
  check (status in ('pending', 'draft_created', 'syncing_leads', 'leads_synced',
                    'launching', 'scheduled', 'awaiting_review', 'active', 'paused',
                    'completed', 'failed'));

comment on column public.instantly_campaign_mappings.status is
  'Provider lifecycle state; awaiting_review means personalized emails must be approved before lead sync.';

commit;
