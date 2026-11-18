alter table public.users
  add column if not exists access_removed_by_admin boolean not null default false;
