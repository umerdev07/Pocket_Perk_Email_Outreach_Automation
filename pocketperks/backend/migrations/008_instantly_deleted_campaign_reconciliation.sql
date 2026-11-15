alter table public.instantly_campaign_mappings
  add column if not exists provider_checked_at timestamptz,
  add column if not exists provider_deleted_at timestamptz;

create index if not exists instantly_campaign_mappings_reconcile_idx
  on public.instantly_campaign_mappings(provider_checked_at)
  where instantly_campaign_id is not null and provider_deleted_at is null;
