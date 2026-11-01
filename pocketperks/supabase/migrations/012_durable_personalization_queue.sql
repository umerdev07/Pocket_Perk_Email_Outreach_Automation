begin;

-- PGMQ is a logged (durable) Postgres queue. Queue payloads contain only a run ID.
create extension if not exists pgmq;

do $$
declare
  queue_missing boolean;
  existing_run record;
begin
  queue_missing := not exists (
    select 1 from pgmq.list_queues()
    where queue_name = 'pocketperks_personalization'
  );
  if queue_missing then
    perform pgmq.create('pocketperks_personalization');
    -- Recover in-flight runs created before the durable queue was installed.
    for existing_run in select id from public.campaign_runs where status = 'running' loop
      perform pgmq.send('pocketperks_personalization', jsonb_build_object('run_id', existing_run.id));
    end loop;
  end if;
end;
$$;

create unique index if not exists campaign_runs_one_active_campaign_batch
  on public.campaign_runs (campaign_id, batch_id)
  where status = 'running';

create or replace function public.enqueue_personalization_run()
returns trigger
language plpgsql
security definer
set search_path = pg_catalog, public, pgmq
as $$
begin
  perform pgmq.send(
    'pocketperks_personalization',
    jsonb_build_object('run_id', new.id)
  );
  return new;
end;
$$;

drop trigger if exists campaign_runs_enqueue_personalization on public.campaign_runs;
create trigger campaign_runs_enqueue_personalization
after insert on public.campaign_runs
for each row execute function public.enqueue_personalization_run();

create or replace function public.read_personalization_jobs(
  visibility_seconds integer default 120,
  quantity integer default 1
)
returns table(message_id bigint, read_count bigint, message jsonb)
language sql
security definer
set search_path = pg_catalog, public, pgmq
as $$
  select q.msg_id, q.read_ct, q.message
  from pgmq.read('pocketperks_personalization', visibility_seconds, quantity) as q;
$$;

create or replace function public.extend_personalization_job(
  target_message_id bigint,
  visibility_seconds integer default 120
)
returns boolean
language sql
security definer
set search_path = pg_catalog, public, pgmq
as $$
  select exists (
    select 1 from pgmq.set_vt('pocketperks_personalization', target_message_id, visibility_seconds)
  );
$$;

create or replace function public.archive_personalization_job(target_message_id bigint)
returns boolean
language sql
security definer
set search_path = pg_catalog, public, pgmq
as $$
  select pgmq.archive('pocketperks_personalization', target_message_id);
$$;

revoke all on function public.enqueue_personalization_run() from public, anon, authenticated;
revoke all on function public.read_personalization_jobs(integer, integer) from public, anon, authenticated;
revoke all on function public.extend_personalization_job(bigint, integer) from public, anon, authenticated;
revoke all on function public.archive_personalization_job(bigint) from public, anon, authenticated;

grant execute on function public.read_personalization_jobs(integer, integer) to service_role;
grant execute on function public.extend_personalization_job(bigint, integer) to service_role;
grant execute on function public.archive_personalization_job(bigint) to service_role;

commit;
