begin;

-- Optional recipient name lets personalization follow the kit's named greeting
-- while preserving compatibility with existing lead files.
alter table public.leads
  add column if not exists first_name text;


comment on column public.leads.first_name is
  'Optional verified decision-maker first name used only for email greeting personalization.';


commit;
