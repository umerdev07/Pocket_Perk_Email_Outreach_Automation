-- Native PostgreSQL replacement for Supabase PGMQ migrations 012 and 015.
-- Jobs use the durable background_jobs table and are claimed by app.postgres_queue.

create or replace function public.enqueue_personalization_run()
returns trigger language plpgsql security definer set search_path = pg_catalog, public as $$
begin
  insert into public.background_jobs(queue_name, payload)
  values ('personalization', jsonb_build_object('run_id', new.id));
  return new;
end;
$$;

create or replace function public.enqueue_instantly_sync_for_run()
returns trigger language plpgsql security definer set search_path = pg_catalog, public as $$
begin
  if new.status = 'completed' and (tg_op = 'INSERT' or old.status is distinct from new.status) then
    insert into public.background_jobs(queue_name, payload)
    values ('instantly_sync', jsonb_build_object('run_id', new.id));
  end if;
  return new;
end;
$$;

create or replace function public.enqueue_instantly_sync_for_reviewed_email()
returns trigger language plpgsql security definer set search_path = pg_catalog, public as $$
begin
  if new.status = 'queued' and old.status is distinct from new.status then
    insert into public.background_jobs(queue_name, payload)
    values ('instantly_sync', jsonb_build_object('run_id', new.run_id));
  end if;
  return new;
end;
$$;

drop trigger if exists campaign_runs_enqueue_personalization on public.campaign_runs;
create trigger campaign_runs_enqueue_personalization
after insert on public.campaign_runs
for each row execute function public.enqueue_personalization_run();

drop trigger if exists campaign_runs_enqueue_instantly_sync on public.campaign_runs;
create trigger campaign_runs_enqueue_instantly_sync
after insert or update of status on public.campaign_runs
for each row execute function public.enqueue_instantly_sync_for_run();

drop trigger if exists personalized_emails_enqueue_instantly_sync on public.personalized_emails;
create trigger personalized_emails_enqueue_instantly_sync
after update of status on public.personalized_emails
for each row execute function public.enqueue_instantly_sync_for_reviewed_email();

revoke all on function public.enqueue_personalization_run() from public;
revoke all on function public.enqueue_instantly_sync_for_run() from public;
revoke all on function public.enqueue_instantly_sync_for_reviewed_email() from public;

-- Recover runs that existed before installing the durable native queue.
insert into public.background_jobs(queue_name, payload)
select 'personalization', jsonb_build_object('run_id', id)
from public.campaign_runs where status = 'running';
