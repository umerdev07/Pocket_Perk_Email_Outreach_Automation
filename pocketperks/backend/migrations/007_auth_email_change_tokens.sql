create table if not exists public.email_change_tokens (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.users(id) on delete cascade,
  old_email text not null,
  new_email text not null,
  token_hash text not null unique,
  purpose text not null check (purpose in ('current','new')),
  expires_at timestamptz not null,
  used_at timestamptz,
  created_at timestamptz not null default now(),
  check (old_email = lower(btrim(old_email)) and new_email = lower(btrim(new_email)))
);
create index if not exists email_change_tokens_user_idx
  on public.email_change_tokens(user_id, expires_at) where used_at is null;

create table if not exists public.auth_login_attempts (
  email_hash text primary key,
  failed_count integer not null default 0 check (failed_count >= 0),
  window_started_at timestamptz not null default now(),
  locked_until timestamptz,
  updated_at timestamptz not null default now()
);
