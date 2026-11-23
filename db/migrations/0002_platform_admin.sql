-- Platform operators (the SaaS owner's staff). Not tenant users: the admin API uses the service connection
-- and checks this table; end-user roles get no access at all (RLS on, no policy, no grants).
create table platform_admins (
  user_id uuid primary key,
  created_at timestamptz not null default now()
);
alter table platform_admins enable row level security;
alter table platform_admins force row level security;
-- Grant an admin (run once, as DB owner):  insert into platform_admins (user_id) values ('<supabase auth user id>');
