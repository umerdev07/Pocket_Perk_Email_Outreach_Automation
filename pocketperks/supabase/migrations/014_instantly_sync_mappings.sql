begin;

-- Keep the Instantly provider objects mapped to PocketPerks-owned runs.
-- These records are operational metadata; Supabase remains the source of truth.
create table public.instantly_campaign_mappings (
  id uuid primary key default gen_random_uuid(),
  run_id uuid not null unique references public.campaign_runs(id) on delete cascade,
  instantly_campaign_id text unique,
  status text not null default 'pending'
    check (status in ('pending', 'draft_created', 'syncing_leads', 'leads_synced', 'active', 'paused', 'completed', 'failed')),
  error_detail text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.instantly_lead_mappings (
  id uuid primary key default gen_random_uuid(),
  campaign_mapping_id uuid not null references public.instantly_campaign_mappings(id) on delete cascade,
  lead_id uuid not null references public.leads(id) on delete cascade,
  instantly_lead_id text,
  status text not null default 'pending'
    check (status in ('pending', 'synced', 'skipped', 'failed', 'unknown')),
  error_detail text,
  synced_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (campaign_mapping_id, lead_id)
);

create unique index instantly_lead_mappings_provider_id_unique
  on public.instantly_lead_mappings (campaign_mapping_id, instantly_lead_id)
  where instantly_lead_id is not null;
create index instantly_lead_mappings_status_idx
  on public.instantly_lead_mappings (campaign_mapping_id, status);

alter table public.instantly_campaign_mappings enable row level security;
alter table public.instantly_lead_mappings enable row level security;
revoke all on public.instantly_campaign_mappings, public.instantly_lead_mappings
  from anon, authenticated;
grant all on public.instantly_campaign_mappings, public.instantly_lead_mappings
  to service_role;

commit;
