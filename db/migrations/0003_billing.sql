-- Crypto billing: monthly invoice = subscription (per location) + success fee on money actually recovered.
alter table organizations add column subscription_per_location_usd numeric(12,2) not null default 0
  check (subscription_per_location_usd >= 0);
alter table claims add column recovered_at timestamptz;

create table billing_invoices (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references organizations(id) on delete cascade,
  period_start date not null,                         -- first day of the billed month
  period_end date not null,                           -- first day of the NEXT month (exclusive)
  location_count int not null,
  subscription_amount numeric(12,2) not null,
  recovered_base numeric(12,2) not null,              -- recovered money the fee was computed on
  success_fee_amount numeric(12,2) not null,
  total_usd numeric(12,2) not null check (total_usd > 0),
  status text not null default 'open' check (status in ('open','processing','paid','void')),
  provider text, provider_invoice_id text, checkout_url text, checkout_expires_at timestamptz,
  due_at timestamptz not null,
  paid_at timestamptz, paid_note text,
  created_at timestamptz not null default now(),
  unique (org_id, period_start),
  unique (id, org_id)
);
create index on billing_invoices (status, due_at);

alter table claims add column billing_invoice_id uuid;
alter table claims add foreign key (billing_invoice_id, org_id) references billing_invoices(id, org_id);

-- Append-only log of every payment-provider interaction (service role only).
create table payment_events (
  id bigserial primary key,
  org_id uuid references organizations(id) on delete cascade,
  billing_invoice_id uuid,
  provider text not null,
  delivery_id text not null,
  provider_invoice_id text,
  event_type text,
  observed_status text,
  note text,                                          -- e.g. amount_mismatch, duplicate_payment, overpaid, manual_mark_paid
  raw jsonb,
  created_at timestamptz not null default now(),
  unique (provider, delivery_id)
);
create index on payment_events (provider, provider_invoice_id);
create index on payment_events (note) where note is not null;

alter table billing_invoices enable row level security;
alter table billing_invoices force row level security;
create policy tenant_read on billing_invoices for select using (org_id in (select user_org_ids()));
alter table payment_events enable row level security;
alter table payment_events force row level security;      -- no policy, no grant: invisible to end users

do $$ begin
  if exists (select 1 from pg_roles where rolname = 'authenticated') then
    grant select on billing_invoices to authenticated;     -- users read; only the server writes status
  end if;
end $$;
