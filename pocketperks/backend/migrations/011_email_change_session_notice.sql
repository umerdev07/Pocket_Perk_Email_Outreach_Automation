alter table public.refresh_sessions
  add column if not exists revoked_reason text;
