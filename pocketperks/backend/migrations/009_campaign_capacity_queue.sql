-- Durable workspace-wide send-capacity reservations. Instantly remains the
-- delivery scheduler; this table gates when a PocketPerks run may be synced.
create table if not exists public.campaign_capacity_reservations (
  run_id uuid primary key references public.campaign_runs(id) on delete cascade,
  queue_order bigserial not null unique,
  requested_daily_emails integer not null check (requested_daily_emails > 0),
  window_start timestamptz not null,
  window_end timestamptz not null,
  state text not null default 'queued' check (state in ('queued','reserved','completed','released')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  admitted_at timestamptz,
  check (window_end > window_start)
);

create index if not exists campaign_capacity_reservations_state_window_idx
  on public.campaign_capacity_reservations(state, window_start, window_end, created_at);

-- Preserve capacity held by campaigns already running before this migration.
insert into public.campaign_capacity_reservations
  (run_id,requested_daily_emails,window_start,window_end,state,admitted_at)
select r.id,
       greatest(1,least(c.daily_email_limit,
         r.total_leads * greatest(1,(select count(*) from public.campaign_steps s
           where s.campaign_id=c.id and coalesce(s.trigger_type,'scheduled')<>'after_conversation')))),
       c.start_at,coalesce(r.schedule_end_at,c.end_at),'reserved',now()
  from public.campaign_runs r
  join public.campaigns c on c.id=r.campaign_id
  left join public.instantly_campaign_mappings m on m.run_id=r.id
 where r.status='running'
    or (m.provider_deleted_at is null and m.status=any(array[
      'draft_created','syncing_leads','awaiting_review','leads_synced','scheduled','launching','active','paused'
    ]::text[]))
on conflict(run_id) do nothing;
