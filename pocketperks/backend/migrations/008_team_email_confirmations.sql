create table if not exists public.workspace_invitations (
  id uuid primary key default gen_random_uuid(),
  email text not null unique check (email = lower(btrim(email)) and email <> ''),
  role text not null check (role in ('admin','operator','reviewer')),
  token_hash text not null unique,
  requested_by uuid references public.users(id) on delete set null,
  expires_at timestamptz not null,
  accepted_at timestamptz,
  created_at timestamptz not null default now()
);
create index if not exists workspace_invitations_pending_idx
  on public.workspace_invitations(expires_at) where accepted_at is null;

create table if not exists public.workspace_role_change_tokens (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.users(id) on delete cascade,
  requested_by uuid references public.users(id) on delete set null,
  new_role text not null check (new_role in ('admin','operator','reviewer')),
  token_hash text not null unique,
  expires_at timestamptz not null,
  accepted_at timestamptz,
  created_at timestamptz not null default now()
);
create unique index if not exists workspace_role_change_pending_user_idx
  on public.workspace_role_change_tokens(user_id) where accepted_at is null;
create index if not exists workspace_role_change_pending_expiry_idx
  on public.workspace_role_change_tokens(expires_at) where accepted_at is null;
