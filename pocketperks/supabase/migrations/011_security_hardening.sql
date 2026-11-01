begin;

-- Keep every application table deny-by-default for browser roles. The API uses
-- the server-only service_role key, which Supabase documents as bypassing RLS.
alter table public.lead_batches enable row level security;
alter table public.leads enable row level security;
alter table public.campaigns enable row level security;
alter table public.campaign_steps enable row level security;
alter table public.generic_drafts enable row level security;
alter table public.campaign_runs enable row level security;
alter table public.personalized_emails enable row level security;
alter table public.replies enable row level security;
alter table public.workspace_members enable row level security;

revoke all on public.lead_batches, public.leads, public.campaigns,
  public.campaign_steps, public.generic_drafts, public.campaign_runs,
  public.personalized_emails, public.replies, public.workspace_members
  from anon, authenticated;

-- Prevent two active personalization runs from multiplying OpenRouter usage
-- for the same campaign and imported batch. Completed runs may be restarted.
create unique index if not exists campaign_runs_one_active_campaign_batch
  on public.campaign_runs (campaign_id, batch_id)
  where status = 'running';

commit;
