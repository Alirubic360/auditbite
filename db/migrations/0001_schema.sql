-- AuditBite schema (Supabase/Postgres). Multi-tenant by organization, RLS everywhere.
-- Deviations from the spec PDF (all intentional, see docs in README):
--  * org_members + user_org_ids() drive RLS; every tenant table carries org_id.
--  * Composite FKs (id, org_id) make cross-tenant references impossible at DB level.
--  * No UNIQUE(org_id, vendor_id, invoice_number): it would make the duplicate invoice
--    we must DETECT impossible to store. A plain index + find_duplicate_invoices() instead.
--  * Queue columns on invoices (SKIP LOCKED) replace Celery/Redis.

create extension if not exists pg_trgm;

create table organizations (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  success_fee_rate numeric(4,3) not null default 0.25 check (success_fee_rate between 0 and 1),
  default_tolerance_pct numeric(5,2) not null default 0.5 check (default_tolerance_pct >= 0),
  inbox_slug text unique,                       -- invoices+<slug>@inbound domain
  created_at timestamptz not null default now()
);

create table org_members (
  org_id uuid not null references organizations(id) on delete cascade,
  user_id uuid not null,
  role text not null default 'member' check (role in ('owner','member')),
  primary key (org_id, user_id)
);
create index on org_members (user_id);

create function public.user_org_ids() returns setof uuid
language sql stable security definer set search_path = public as $$
  select org_id from org_members where user_id = auth.uid()
$$;

create table locations (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references organizations(id) on delete cascade,
  name text not null,
  account_number text,
  unique (id, org_id)
);

create table vendors (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references organizations(id) on delete cascade,
  name text not null,
  aliases text[] not null default '{}',
  contact_email text,
  payment_terms_days int not null default 30,
  unique (id, org_id)
);
create index on vendors (org_id, lower(name));

create table contracts (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references organizations(id) on delete cascade,
  vendor_id uuid not null,
  reference text,
  valid_from date,
  valid_to date,
  rebate_rules jsonb not null default '[]',      -- e.g. [{"type":"surcharge_cap","line_type":"freight","cap_amount":0,"source_clause":"..."}]
  file_path text,
  created_at timestamptz not null default now(),
  unique (id, org_id),
  foreign key (vendor_id, org_id) references vendors(id, org_id),
  check (valid_to is null or valid_from is null or valid_to >= valid_from)
);

create table contract_items (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references organizations(id) on delete cascade,
  contract_id uuid not null,
  sku text,
  canonical_name text not null,
  aliases text[] not null default '{}',
  unit text not null,
  pack_size numeric,
  contract_price numeric(12,4) not null check (contract_price >= 0),
  tolerance_pct numeric(5,2) not null default 0.5 check (tolerance_pct >= 0),
  needs_index boolean not null default false,    -- formula price: cannot be audited without an index
  source_clause text,
  unique (id, org_id),
  foreign key (contract_id, org_id) references contracts(id, org_id) on delete cascade
);
create index contract_items_name_trgm on contract_items using gin (canonical_name gin_trgm_ops);
create index on contract_items (contract_id, upper(sku));

create table invoices (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references organizations(id) on delete cascade,
  location_id uuid,
  vendor_id uuid,
  invoice_number text,
  invoice_date date,
  date_raw text,
  po_number text,
  currency text,
  subtotal numeric(12,2), tax numeric(12,2), total numeric(12,2),   -- as PRINTED
  file_path text not null,
  media_type text not null,
  original_filename text,
  file_sha256 text,
  status text not null default 'uploaded'
    check (status in ('uploaded','extracting','needs_review','audited','claimed','recovered','failed')),
  extraction_confidence numeric(4,3),
  raw_extraction jsonb,
  warnings jsonb not null default '[]',
  attempts int not null default 0,
  locked_at timestamptz,
  error text,
  created_at timestamptz not null default now(),
  unique (id, org_id),
  foreign key (location_id, org_id) references locations(id, org_id),
  foreign key (vendor_id, org_id) references vendors(id, org_id)
);
create index on invoices (org_id, status, created_at desc);
create index on invoices (org_id, vendor_id, invoice_number);
create index on invoices (status, locked_at) where status = 'uploaded';

create table invoice_lines (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references organizations(id) on delete cascade,
  invoice_id uuid not null,
  line_no int,
  line_type text not null default 'product'
    check (line_type in ('product','freight','surcharge','discount','deposit','tax','other')),
  description text,
  sku text,
  qty numeric(12,3),
  unit text,
  pack_description text,
  unit_price numeric(12,4),
  line_total numeric(12,2),
  confidence numeric(4,3),
  matched_item_id uuid,
  match_type text check (match_type in ('exact','equivalent','substitution','none')),
  match_confidence numeric(4,3),
  match_note text,
  unique (id, org_id),
  foreign key (invoice_id, org_id) references invoices(id, org_id) on delete cascade,
  foreign key (matched_item_id, org_id) references contract_items(id, org_id)
);
create index on invoice_lines (invoice_id);

create table discrepancies (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references organizations(id) on delete cascade,
  invoice_id uuid not null,
  line_id uuid,
  related_invoice_id uuid,
  type text not null check (type in
    ('price_variance','duplicate_invoice','missing_discount','unit_mismatch','substitution','math_error','tax_error','surcharge_excess')),
  expected numeric(12,4), billed numeric(12,4),
  amount numeric(12,2) not null check (amount >= 0),
  evidence text,
  source_clause text,
  confidence numeric(4,3),
  ai_verdict jsonb,
  review_status text not null default 'pending' check (review_status in ('pending','approved','rejected')),
  reviewed_by uuid, reviewed_at timestamptz,
  created_at timestamptz not null default now(),
  unique (id, org_id),
  foreign key (invoice_id, org_id) references invoices(id, org_id) on delete cascade,
  foreign key (line_id, org_id) references invoice_lines(id, org_id) on delete cascade,
  foreign key (related_invoice_id, org_id) references invoices(id, org_id) on delete set null (related_invoice_id)
);
create index on discrepancies (org_id, review_status);
create index on discrepancies (invoice_id);

create table claims (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references organizations(id) on delete cascade,
  vendor_id uuid not null,
  total_claimed numeric(12,2) not null default 0,
  status text not null default 'draft'
    check (status in ('draft','sent','disputed','credited','paid','written_off')),
  letter_subject text, letter_body text, language text not null default 'en',
  sent_at timestamptz, due_at timestamptz,
  recovered_amount numeric(12,2) not null default 0 check (recovered_amount >= 0),
  fee_amount numeric(12,2) not null default 0,
  pdf_path text,
  created_at timestamptz not null default now(),
  unique (id, org_id),
  foreign key (vendor_id, org_id) references vendors(id, org_id),
  check (recovered_amount <= total_claimed)
);

create table claim_items (
  claim_id uuid not null,
  discrepancy_id uuid not null,
  org_id uuid not null references organizations(id) on delete cascade,
  amount numeric(12,2) not null,
  primary key (claim_id, discrepancy_id),
  unique (discrepancy_id),                      -- a discrepancy can be claimed only once
  foreign key (claim_id, org_id) references claims(id, org_id) on delete cascade,
  foreign key (discrepancy_id, org_id) references discrepancies(id, org_id)
);

create table followups (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references organizations(id) on delete cascade,
  claim_id uuid not null,
  day int not null check (day in (7,14)),
  due_at timestamptz not null,
  status text not null default 'scheduled' check (status in ('scheduled','sent','cancelled')),
  sent_at timestamptz,
  unique (claim_id, day),
  foreign key (claim_id, org_id) references claims(id, org_id) on delete cascade
);

create table outbound_emails (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references organizations(id) on delete cascade,
  claim_id uuid,
  kind text not null check (kind in ('claim','followup_7','followup_14','review_alert')),
  to_email text not null, subject text not null, body text not null,
  provider text not null, provider_id text,
  created_at timestamptz not null default now()
);

create table audit_log (
  id bigserial primary key,
  org_id uuid not null references organizations(id) on delete cascade,
  user_id uuid,
  action text not null,
  entity text, entity_id uuid,
  detail jsonb,
  created_at timestamptz not null default now()
);

-- Duplicate detection: same vendor AND (same number OR same printed total within 90 days).
create function find_duplicate_invoices(p_org uuid, p_vendor uuid, p_total numeric,
                                        p_number text, p_date date, p_exclude uuid)
returns table (id uuid, invoice_number text, invoice_date date, total numeric, created_at timestamptz)
language sql stable as $$
  select i.id, i.invoice_number, i.invoice_date, i.total, i.created_at
  from invoices i
  where i.org_id = p_org and i.vendor_id = p_vendor and i.id <> p_exclude
    and i.status <> 'failed'
    and ( (p_number is not null and nullif(btrim(p_number),'') is not null
           and upper(regexp_replace(i.invoice_number, '[^A-Za-z0-9]', '', 'g'))
             = upper(regexp_replace(p_number, '[^A-Za-z0-9]', '', 'g')))
       or (p_total is not null and i.total = p_total and p_date is not null and i.invoice_date is not null
           and abs(i.invoice_date - p_date) <= 90) )
    and (i.created_at < (select created_at from invoices where id = p_exclude)
         or (i.created_at = (select created_at from invoices where id = p_exclude) and i.id < p_exclude))
  order by i.created_at
$$;

-- Row Level Security ---------------------------------------------------------
do $$
declare t text;
begin
  foreach t in array array['locations','vendors','contracts','contract_items','invoices','invoice_lines',
                           'discrepancies','claims','claim_items','followups','outbound_emails','audit_log']
  loop
    execute format('alter table %I enable row level security', t);
    execute format('alter table %I force row level security', t);
    execute format($p$create policy tenant_isolation on %I for all
                      using (org_id in (select user_org_ids()))
                      with check (org_id in (select user_org_ids()))$p$, t);
  end loop;
end $$;

alter table organizations enable row level security;
alter table organizations force row level security;
create policy org_select on organizations for select using (id in (select user_org_ids()));
create policy org_update on organizations for update
  using (id in (select user_org_ids())) with check (id in (select user_org_ids()));

alter table org_members enable row level security;
alter table org_members force row level security;
create policy member_select on org_members for select using (user_id = auth.uid());

-- Grants (Supabase roles). audit_log/outbound_emails are append-only for users.
do $$
begin
  if exists (select 1 from pg_roles where rolname = 'authenticated') then
    grant usage on schema public to authenticated;
    grant select, insert, update, delete on locations, vendors, contracts, contract_items, invoices,
      invoice_lines, discrepancies, claims, claim_items, followups to authenticated;
    grant select, update on organizations to authenticated;
    grant select on org_members to authenticated;
    grant select, insert on outbound_emails, audit_log to authenticated;   -- append-only for users
    grant usage on all sequences in schema public to authenticated;
    grant execute on function find_duplicate_invoices(uuid,uuid,numeric,text,date,uuid) to authenticated;
    revoke all on all tables in schema public from anon;
  end if;
end $$;
