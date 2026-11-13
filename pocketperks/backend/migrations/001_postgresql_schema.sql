-- Clean, application-owned PostgreSQL schema.
create table users (
  id uuid primary key default gen_random_uuid(), email text not null unique,
  password_hash text not null, is_active boolean not null default true,
  email_verified_at timestamptz, last_login_at timestamptz,
  created_at timestamptz not null default now(), updated_at timestamptz not null default now(),
  check (email = lower(btrim(email)) and email <> '')
);
create table workspace_members (
  user_id uuid primary key references users(id) on delete cascade,
  role text not null check (role in ('admin','operator','reviewer')),
  created_at timestamptz not null default now()
);
create table password_reset_tokens (
  id uuid primary key default gen_random_uuid(), user_id uuid not null references users(id) on delete cascade,
  token_hash text not null unique, expires_at timestamptz not null, used_at timestamptz,
  created_at timestamptz not null default now()
);
create table refresh_sessions (
  id uuid primary key default gen_random_uuid(), user_id uuid not null references users(id) on delete cascade,
  token_hash text not null unique, expires_at timestamptz not null, revoked_at timestamptz,
  created_at timestamptz not null default now(), last_used_at timestamptz
);

create table lead_batches (
  id uuid primary key default gen_random_uuid(), file_name text not null,
  total_rows integer not null default 0 check (total_rows >= 0), imported_count integer not null default 0,
  duplicate_count integer not null default 0, invalid_count integer not null default 0,
  rejected_count integer not null default 0, suppressed_count integer not null default 0 check (suppressed_count >= 0),
  reject_log jsonb not null default '[]'::jsonb, created_at timestamptz not null default now()
);
create table leads (
  id uuid primary key default gen_random_uuid(), batch_id uuid not null references lead_batches(id),
  first_name text, business_name text not null, business_category text,
  email text not null check (email = lower(btrim(email)) and email <> ''), phone text, website text,
  domain text check (domain is null or (domain = lower(btrim(domain)) and domain <> '')),
  city text, full_address text, email_source_url text, verification_result text,
  verification_tool text, quality_flags text, extra jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);
create unique index leads_email_unique on leads(email);
create unique index leads_domain_unique on leads(domain) where domain is not null;
create index leads_batch_id_idx on leads(batch_id);

create table campaigns (
  id uuid primary key default gen_random_uuid(), name text not null,
  total_emails integer not null check (total_emails between 1 and 10), location text,
  instructions text not null, status text not null default 'draft' check (status in ('draft','approved')),
  start_date date, end_date date, start_at timestamptz, end_at timestamptz,
  timezone text not null default 'Europe/London', daily_send_start time not null default time '09:00',
  daily_send_end time not null default time '17:00', sending_days smallint[] not null default array[1,2,3,4,5]::smallint[],
  created_at timestamptz not null default now(),
  check (start_date is null or end_date is null or end_date >= start_date),
  check (start_at is null or end_at is null or end_at > start_at), check (daily_send_end > daily_send_start),
  check (cardinality(sending_days) between 1 and 7 and sending_days <@ array[0,1,2,3,4,5,6]::smallint[])
);
create table campaign_steps (
  id uuid primary key default gen_random_uuid(), campaign_id uuid not null references campaigns(id),
  step_number integer not null check (step_number between 1 and 10), context text not null,
  delay_days integer not null check (delay_days >= 0), delay_minutes integer not null check (delay_minutes between 0 and 43200),
  trigger_type text not null default 'scheduled' check (trigger_type in ('scheduled','after_conversation','close_loop')),
  created_at timestamptz not null default now(), unique (campaign_id,step_number), unique (id,campaign_id)
);
create table generic_drafts (
  id uuid primary key default gen_random_uuid(), step_id uuid not null unique references campaign_steps(id),
  subject text not null, body text not null, status text not null default 'generated' check (status in ('generated','edited','approved')),
  approved_at timestamptz, generation_status text not null default 'idle' check (generation_status in ('idle','generating','succeeded','failed')),
  generation_error text, generation_started_at timestamptz, generation_finished_at timestamptz,
  updated_at timestamptz not null default now(), created_at timestamptz not null default now()
);
create table campaign_runs (
  id uuid primary key default gen_random_uuid(), campaign_id uuid not null references campaigns(id),
  batch_id uuid not null references lead_batches(id), status text not null default 'running' check (status in ('running','completed','failed')),
  total_leads integer not null default 0, processed integer not null default 0, skipped integer not null default 0, failed integer not null default 0,
  skip_log jsonb not null default '[]'::jsonb, failure_log jsonb not null default '[]'::jsonb, error_detail text,
  sender_account_emails text[] not null default '{}', schedule_end_at timestamptz, finished_at timestamptz,
  created_at timestamptz not null default now(), unique (id,campaign_id)
);
create unique index campaign_runs_one_active_campaign_batch on campaign_runs(campaign_id,batch_id) where status='running';
create table personalized_emails (
  id uuid primary key default gen_random_uuid(), run_id uuid not null, lead_id uuid not null references leads(id),
  campaign_id uuid not null references campaigns(id), step_id uuid not null, subject text not null, body text not null,
  risk_level text check (risk_level in ('LOW','MEDIUM','HIGH')), risk_reason text,
  status text not null default 'generated' check (status in ('generated','pending_review','approved','rejected','queued','sent','sent_test','failed','bounced','cancelled')),
  approved_version_hash text, reviewed_at timestamptz, send_mode text not null default 'test' check (send_mode in ('test','live')),
  instantly_ref text, send_error text, sent_at timestamptz, bounced_at timestamptz,
  bounce_type text check (bounce_type is null or bounce_type in ('hard','soft','unknown')),
  manual_send_state text check (manual_send_state is null or manual_send_state in ('sending','sent','unknown')),
  created_at timestamptz not null default now(), unique (run_id,lead_id,step_id),
  foreign key (run_id,campaign_id) references campaign_runs(id,campaign_id),
  foreign key (step_id,campaign_id) references campaign_steps(id,campaign_id)
);
create index personalized_emails_status_idx on personalized_emails(status);
create index personalized_emails_risk_level_idx on personalized_emails(risk_level);
create table replies (
  id uuid primary key default gen_random_uuid(), email_id uuid references personalized_emails(id),
  lead_id uuid references leads(id), campaign_id uuid references campaigns(id), from_email text not null,
  subject text not null, body text not null, instantly_id text unique, sender_account_email text,
  received_at timestamptz not null, status text not null default 'new' check (status in ('new','read')),
  created_at timestamptz not null default now()
);
create index replies_received_at_idx on replies(received_at desc);
create table email_review_history (
  id uuid primary key default gen_random_uuid(), email_id uuid not null, action text not null, status text not null,
  risk_level text, risk_reason text, subject text not null, business_name text, recipient_email text,
  campaign_name text, step_number integer, created_at timestamptz not null default now()
);
create index email_review_history_created_idx on email_review_history(created_at desc,id desc);
create index email_review_history_email_idx on email_review_history(email_id,created_at desc);

create table instantly_campaign_mappings (
  id uuid primary key default gen_random_uuid(), run_id uuid not null unique references campaign_runs(id) on delete cascade,
  instantly_campaign_id text unique, status text not null default 'pending' check (status in ('pending','draft_created','syncing_leads','leads_synced','launching','scheduled','awaiting_review','active','paused','completed','failed')),
  error_detail text, sender_account_emails text[] not null default '{}',
  created_at timestamptz not null default now(), updated_at timestamptz not null default now()
);
create table instantly_lead_mappings (
  id uuid primary key default gen_random_uuid(), campaign_mapping_id uuid not null references instantly_campaign_mappings(id) on delete cascade,
  lead_id uuid not null references leads(id) on delete cascade, instantly_lead_id text,
  status text not null default 'pending' check (status in ('pending','synced','skipped','failed','unknown')),
  error_detail text, synced_at timestamptz, created_at timestamptz not null default now(), updated_at timestamptz not null default now(),
  unique (campaign_mapping_id,lead_id)
);
create unique index instantly_lead_mappings_provider_id_unique on instantly_lead_mappings(campaign_mapping_id,instantly_lead_id) where instantly_lead_id is not null;
create index instantly_lead_mappings_status_idx on instantly_lead_mappings(campaign_mapping_id,status);
create table instantly_reply_sync_state (
  id boolean primary key default true check (id=true), last_checked_at timestamptz not null default (now()-interval '1 day'),
  sync_from timestamptz, starting_after text, updated_at timestamptz not null default now()
);
insert into instantly_reply_sync_state(id) values (true);
create table email_suppressions (
  email text primary key check (email=lower(btrim(email)) and email<>''), reason text not null,
  source text not null check (source in ('reply_auto','reply_manual','bounce_auto')),
  lead_id uuid references leads(id) on delete set null, campaign_id uuid references campaigns(id) on delete set null,
  active boolean not null default true, released_at timestamptz, released_by uuid references users(id) on delete set null,
  release_reason text, created_at timestamptz not null default now()
);
create index email_suppressions_created_at_idx on email_suppressions(created_at desc);
create table email_suppression_events (
  id uuid primary key default gen_random_uuid(), email text not null check (email=lower(btrim(email)) and email<>''),
  event_type text not null check (event_type in ('suppressed','released')),
  source text not null check (source in ('reply_auto','reply_manual','admin','bounce_auto')), reason text not null,
  actor_user_id uuid references users(id) on delete set null, lead_id uuid references leads(id) on delete set null,
  campaign_id uuid references campaigns(id) on delete set null, created_at timestamptz not null default now()
);
create index email_suppression_events_email_created_idx on email_suppression_events(email,created_at desc);
create table instantly_webhook_events (
  id bigint generated always as identity primary key, event_key text not null unique check (event_key ~ '^[a-f0-9]{64}$'),
  payload jsonb not null default '{}'::jsonb, status text not null default 'pending' check (status in ('pending','processing','processed','failed')),
  attempts integer not null default 0 check (attempts>=0), error_detail text, received_at timestamptz not null default now(),
  updated_at timestamptz not null default now(), processed_at timestamptz
);
create index instantly_webhook_events_pending_idx on instantly_webhook_events(status,received_at) where status in ('pending','processing');
create table instantly_bounce_events (
  event_key text primary key check (event_key ~ '^[a-f0-9]{64}$'), provider_campaign_id text,
  campaign_mapping_id uuid references instantly_campaign_mappings(id) on delete set null,
  run_id uuid references campaign_runs(id) on delete set null, campaign_id uuid references campaigns(id) on delete set null,
  lead_id uuid references leads(id) on delete set null,
  email text check (email is null or (email=lower(btrim(email)) and email<>'')),
  bounce_type text not null check (bounce_type in ('hard','soft','unknown')), reason text,
  step_number integer check (step_number is null or step_number between 1 and 10), email_account text,
  event_at timestamptz not null, match_status text not null default 'unmatched' check (match_status in ('matched','unmatched')),
  created_at timestamptz not null default now()
);
create index instantly_bounce_events_email_at_idx on instantly_bounce_events(email,event_at desc);
create index instantly_bounce_events_run_at_idx on instantly_bounce_events(run_id,event_at desc);
create index instantly_bounce_events_type_at_idx on instantly_bounce_events(bounce_type,event_at desc);

-- Native durable jobs replace Supabase's pgmq extension.
create table background_jobs (
  id bigint generated always as identity primary key,
  queue_name text not null check (queue_name in ('personalization','instantly_sync')),
  payload jsonb not null, status text not null default 'pending' check (status in ('pending','processing','completed','dead')),
  attempts integer not null default 0 check (attempts>=0), available_at timestamptz not null default now(),
  lease_expires_at timestamptz, last_error text, created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(), completed_at timestamptz
);
create index background_jobs_claim_idx on background_jobs(queue_name,available_at,id) where status='pending';
create index background_jobs_expired_lease_idx on background_jobs(queue_name,lease_expires_at,id) where status='processing';
