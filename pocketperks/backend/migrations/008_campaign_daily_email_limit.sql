-- Campaign-wide cap includes first touches and all automatic follow-ups.
alter table public.campaigns
  add column if not exists daily_email_limit integer not null default 100,
  add constraint campaigns_daily_email_limit_check check (daily_email_limit between 1 and 10000);
