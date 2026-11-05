begin;

-- Save the required Instantly sender selection atomically with the run.
-- The background queue consumer can therefore never create an Engage without
-- knowing which sending inboxes the operator selected before starting.
alter table public.campaign_runs
  add column if not exists sender_account_emails text[] not null default '{}';

commit;
