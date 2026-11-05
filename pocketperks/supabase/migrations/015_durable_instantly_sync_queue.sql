begin;

create extension if not exists pgmq;

do $$
begin
  if not exists (select 1 from pgmq.list_queues()
                 where queue_name = 'pocketperks_instantly_sync') then
    perform pgmq.create('pocketperks_instantly_sync');
  end if;
end;
$$;

create or replace function public.enqueue_instantly_sync_for_run()
returns trigger
language plpgsql
security definer
set search_path = pg_catalog, public, pgmq
as $$
begin
  if new.status = 'completed' and (tg_op = 'INSERT' or old.status is distinct from new.status) then
    perform pgmq.send('pocketperks_instantly_sync', jsonb_build_object('run_id', new.id));
  end if;
  return new;
end;
$$;

drop trigger if exists campaign_runs_enqueue_instantly_sync on public.campaign_runs;
create trigger campaign_runs_enqueue_instantly_sync
after insert or update of status on public.campaign_runs
for each row execute function public.enqueue_instantly_sync_for_run();

create or replace function public.enqueue_instantly_sync_for_reviewed_email()
returns trigger
language plpgsql
security definer
set search_path = pg_catalog, public, pgmq
as $$
begin
  if new.status = 'queued' and old.status is distinct from new.status then
    perform pgmq.send('pocketperks_instantly_sync', jsonb_build_object('run_id', new.run_id));
  end if;
  return new;
end;
$$;

drop trigger if exists personalized_emails_enqueue_instantly_sync on public.personalized_emails;
create trigger personalized_emails_enqueue_instantly_sync
after update of status on public.personalized_emails
for each row execute function public.enqueue_instantly_sync_for_reviewed_email();

create or replace function public.read_instantly_sync_jobs(
  visibility_seconds integer default 300,
  quantity integer default 1
)
returns table(message_id bigint, read_count bigint, message jsonb)
language sql
security definer
set search_path = pg_catalog, public, pgmq
as $$
  select q.msg_id, q.read_ct, q.message
  from pgmq.read('pocketperks_instantly_sync', visibility_seconds, quantity) as q;
$$;

create or replace function public.extend_instantly_sync_job(
  target_message_id bigint,
  visibility_seconds integer default 300
)
returns boolean
language sql
security definer
set search_path = pg_catalog, public, pgmq
as $$
  select exists (
    select 1 from pgmq.set_vt('pocketperks_instantly_sync', target_message_id, visibility_seconds)
  );
$$;

create or replace function public.archive_instantly_sync_job(target_message_id bigint)
returns boolean
language sql
security definer
set search_path = pg_catalog, public, pgmq
as $$
  select pgmq.archive('pocketperks_instantly_sync', target_message_id);
$$;

revoke all on function public.enqueue_instantly_sync_for_run() from public, anon, authenticated;
revoke all on function public.enqueue_instantly_sync_for_reviewed_email() from public, anon, authenticated;
revoke all on function public.read_instantly_sync_jobs(integer, integer) from public, anon, authenticated;
revoke all on function public.extend_instantly_sync_job(bigint, integer) from public, anon, authenticated;
revoke all on function public.archive_instantly_sync_job(bigint) from public, anon, authenticated;

grant execute on function public.read_instantly_sync_jobs(integer, integer) to service_role;
grant execute on function public.extend_instantly_sync_job(bigint, integer) to service_role;
grant execute on function public.archive_instantly_sync_job(bigint) to service_role;

commit;
