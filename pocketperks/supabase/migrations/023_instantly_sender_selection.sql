begin;

-- Keep the operator-selected Instantly mailboxes with the provider campaign mapping.
alter table public.instantly_campaign_mappings
  add column if not exists sender_account_emails text[] not null default '{}';

alter table public.instantly_campaign_mappings
  drop constraint if exists instantly_campaign_mappings_status_check;

alter table public.instantly_campaign_mappings
  add constraint instantly_campaign_mappings_status_check
  check (status in ('pending', 'draft_created', 'syncing_leads', 'leads_synced',
                    'launching', 'scheduled', 'active', 'paused', 'completed', 'failed'));

commit;
