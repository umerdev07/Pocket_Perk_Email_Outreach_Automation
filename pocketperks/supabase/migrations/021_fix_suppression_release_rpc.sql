begin;

-- Fix the release RPC's PL/pgSQL parameter/column name collision in migration 020.
create or replace function public.release_email_suppression(
  target_email text,
  release_reason text,
  actor_id uuid
)
returns table(email text, active boolean, already_released boolean)
language plpgsql
security invoker
set search_path = public
as $$
declare
  normalized_email text := lower(btrim(target_email));
  normalized_reason text := left(btrim(coalesce(release_reason, '')), 300);
  current_active boolean;
  linked_lead_id uuid;
  linked_campaign_id uuid;
begin
  if length(normalized_reason) < 10 then
    raise exception 'A release reason of at least 10 characters is required';
  end if;

  select s.active, s.lead_id, s.campaign_id
  into current_active, linked_lead_id, linked_campaign_id
  from public.email_suppressions s
  where s.email = normalized_email
  for update;

  if not found then
    raise exception 'Suppression not found' using errcode = 'P0002';
  end if;

  if not current_active then
    return query select normalized_email, false, true;
    return;
  end if;

  update public.email_suppressions s
  set active = false, released_at = now(), released_by = actor_id,
      release_reason = normalized_reason
  where s.email = normalized_email;

  insert into public.email_suppression_events(
    email, event_type, source, reason, actor_user_id, lead_id, campaign_id
  ) values (
    normalized_email, 'released', 'admin', normalized_reason,
    actor_id, linked_lead_id, linked_campaign_id
  );

  return query select normalized_email, false, false;
end;
$$;

revoke all on function public.release_email_suppression(text, text, uuid) from public, anon, authenticated;
grant execute on function public.release_email_suppression(text, text, uuid) to service_role;

commit;
