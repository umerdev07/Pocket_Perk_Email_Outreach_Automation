begin;

create table public.lead_batches (
  id uuid primary key default gen_random_uuid(),
  file_name text not null,
  total_rows integer not null default 0 check (total_rows >= 0),
  imported_count integer not null default 0,
  duplicate_count integer not null default 0,
  invalid_count integer not null default 0,
  rejected_count integer not null default 0,
  reject_log jsonb not null default '[]'::jsonb,
  created_at timestamptz not null default now()
);

create table public.leads (
  id uuid primary key default gen_random_uuid(),
  batch_id uuid not null references public.lead_batches(id),
  business_name text not null,
  business_category text,
  email text not null check (email = lower(btrim(email)) and email <> ''),
  phone text,
  website text,
  domain text check (domain is null or (domain = lower(btrim(domain)) and domain <> '')),
  city text,
  full_address text,
  email_source_url text,
  verification_result text,
  verification_tool text,
  quality_flags text,
  extra jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);
create unique index leads_email_unique on public.leads(email);
create unique index leads_domain_unique on public.leads(domain) where domain is not null;
create index leads_batch_id_idx on public.leads(batch_id);

create table public.campaigns (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  total_emails integer not null check (total_emails between 1 and 10),
  location text not null,
  instructions text not null,
  status text not null default 'draft' check (status in ('draft', 'approved')),
  created_at timestamptz not null default now()
);

create table public.campaign_steps (
  id uuid primary key default gen_random_uuid(),
  campaign_id uuid not null references public.campaigns(id),
  step_number integer not null check (step_number between 1 and 10),
  context text not null,
  delay_days integer not null check (delay_days >= 0),
  created_at timestamptz not null default now(),
  unique (campaign_id, step_number),
  unique (id, campaign_id)
);
-- First email has no delay; subsequent steps default to three days.
create function public.default_step_delay() returns trigger language plpgsql as $$
begin
  if new.delay_days is null then
    new.delay_days := case when new.step_number = 1 then 0 else 3 end;
  end if;
  return new;
end;
$$;
create trigger campaign_step_delay before insert on public.campaign_steps
for each row execute function public.default_step_delay();

create table public.generic_drafts (
  id uuid primary key default gen_random_uuid(),
  step_id uuid not null unique references public.campaign_steps(id),
  subject text not null,
  body text not null,
  status text not null default 'generated' check (status in ('generated', 'edited', 'approved')),
  approved_at timestamptz,
  updated_at timestamptz not null default now(),
  created_at timestamptz not null default now()
);

create table public.campaign_runs (
  id uuid primary key default gen_random_uuid(),
  campaign_id uuid not null references public.campaigns(id),
  batch_id uuid not null references public.lead_batches(id),
  status text not null default 'running' check (status in ('running', 'completed', 'failed')),
  total_leads integer not null default 0,
  processed integer not null default 0,
  skipped integer not null default 0,
  failed integer not null default 0,
  finished_at timestamptz,
  created_at timestamptz not null default now(),
  unique (id, campaign_id)
);

create table public.personalized_emails (
  id uuid primary key default gen_random_uuid(),
  run_id uuid not null,
  lead_id uuid not null references public.leads(id),
  campaign_id uuid not null references public.campaigns(id),
  step_id uuid not null,
  subject text not null,
  body text not null,
  risk_level text check (risk_level in ('LOW', 'MEDIUM', 'HIGH')),
  risk_reason text,
  status text not null default 'generated' check (status in (
    'generated', 'pending_review', 'approved', 'rejected', 'queued', 'sent', 'sent_test', 'failed'
  )),
  approved_version_hash text,
  reviewed_at timestamptz,
  send_mode text not null default 'test' check (send_mode in ('test', 'live')),
  instantly_ref text,
  send_error text,
  sent_at timestamptz,
  created_at timestamptz not null default now(),
  unique (run_id, lead_id, step_id),
  foreign key (run_id, campaign_id) references public.campaign_runs(id, campaign_id),
  foreign key (step_id, campaign_id) references public.campaign_steps(id, campaign_id)
);
create index personalized_emails_status_idx on public.personalized_emails(status);
create index personalized_emails_risk_level_idx on public.personalized_emails(risk_level);

create table public.replies (
  id uuid primary key default gen_random_uuid(),
  email_id uuid references public.personalized_emails(id),
  lead_id uuid references public.leads(id),
  campaign_id uuid references public.campaigns(id),
  from_email text not null,
  subject text not null,
  body text not null,
  instantly_id text unique,
  received_at timestamptz not null,
  status text not null default 'new' check (status in ('new', 'read')),
  created_at timestamptz not null default now()
);
create index replies_received_at_idx on public.replies(received_at desc);

-- Local MVP: service-key access only. RLS is disabled, so remove browser-role
-- grants explicitly to keep the public REST API inaccessible without the key.
revoke all on public.lead_batches, public.leads, public.campaigns,
  public.campaign_steps, public.generic_drafts, public.campaign_runs,
  public.personalized_emails, public.replies from anon, authenticated;
grant all on public.lead_batches, public.leads, public.campaigns,
  public.campaign_steps, public.generic_drafts, public.campaign_runs,
  public.personalized_emails, public.replies to service_role;

commit;
