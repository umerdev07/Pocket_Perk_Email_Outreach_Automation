begin;

create table if not exists public.workspace_members (
  user_id uuid primary key references auth.users(id) on delete cascade,
  role text not null check (role in ('admin', 'operator', 'reviewer')),
  created_at timestamptz not null default now()
);

alter table public.workspace_members enable row level security;
revoke all on public.workspace_members from anon, authenticated;
grant select, insert, update, delete on public.workspace_members to service_role;

comment on table public.workspace_members is
  'Invite-only PocketPerks workspace membership and application role. Manage memberships from a trusted admin session only.';

commit;
