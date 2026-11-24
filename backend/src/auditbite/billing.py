"""Monthly billing: subscription per location + success fee on recovered money. All Decimal, all idempotent."""
from __future__ import annotations
import datetime as dt
from decimal import Decimal
from .db import J
from .money import D, cents
from .payments import PaymentProvider


class BillingError(ValueError):
    pass


def month_start(d: dt.date) -> dt.date:
    return d.replace(day=1)


def next_month(d: dt.date) -> dt.date:
    return (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1)


def generate_due_invoices(conn, now: dt.datetime | None = None) -> int:
    """For every org and every COMPLETED month since its creation: create one billing invoice if something is owed.
    First (partial) month has no subscription. Unbilled recoveries from earlier months roll into the next invoice.
    Safe to run concurrently / repeatedly (unique(org, period) + claims are claimed in the same transaction)."""
    now = now or dt.datetime.now(dt.timezone.utc)
    this_month = month_start(now.date())
    created = 0
    for org in conn.execute("select id, created_at, subscription_per_location_usd as rate from organizations").fetchall():
        first = month_start(org["created_at"].date())
        m = first
        while m < this_month:
            end = next_month(m)
            if conn.execute("select 1 from billing_invoices where org_id=%s and period_start=%s", (org["id"], m)).fetchone():
                m = end; continue
            locs = conn.execute("select count(*) n from locations where org_id=%s", (org["id"],)).fetchone()["n"]
            sub = cents(D(org["rate"]) * locs) if m > first else Decimal("0.00")
            fees = conn.execute("""select id, recovered_amount, fee_amount from claims where org_id=%s and billing_invoice_id is null
                                   and status in ('credited','paid') and fee_amount > 0 and recovered_at < %s::date for update""", (org["id"], end)).fetchall()
            base = cents(sum((D(f["recovered_amount"]) for f in fees), Decimal(0)))
            fee = cents(sum((D(f["fee_amount"]) for f in fees), Decimal(0)))
            total = sub + fee
            if total > 0:
                row = conn.execute("""insert into billing_invoices (org_id, period_start, period_end, location_count, subscription_amount, recovered_base,
                    success_fee_amount, total_usd, due_at) values (%s,%s,%s,%s,%s,%s,%s,%s,%s) on conflict (org_id, period_start) do nothing returning id""",
                    (org["id"], m, end, locs, sub, base, fee, total, now + dt.timedelta(days=14))).fetchone()
                if row:
                    if fees:
                        conn.execute("update claims set billing_invoice_id=%s where id = any(%s)", (row["id"], [f["id"] for f in fees]))
                    created += 1
            m = end
    return created


def create_checkout(conn, provider: PaymentProvider, org_id, billing_id, redirect_url: str, buyer_email: str | None = None, now: dt.datetime | None = None) -> dict:
    now = now or dt.datetime.now(dt.timezone.utc)
    b = conn.execute("select * from billing_invoices where id=%s and org_id=%s for update", (billing_id, org_id)).fetchone()
    if not b:
        raise BillingError("billing invoice not found")
    if b["status"] in ("paid", "void"):
        raise BillingError(f"invoice is {b['status']}")
    if b["provider_invoice_id"] and b["checkout_expires_at"] and b["checkout_expires_at"] > now and b["provider"] == provider.name:
        return b                                        # reuse the live checkout: never create two payable invoices at once
    c = provider.create_invoice(order_id=str(b["id"]), amount_usd=D(b["total_usd"]), description=f"AuditBite {b['period_start']:%Y-%m}",
                                redirect_url=redirect_url, buyer_email=buyer_email)
    exp = dt.datetime.fromtimestamp(c.expires_at, dt.timezone.utc) if c.expires_at else now + dt.timedelta(minutes=60)
    conn.execute("""insert into payment_events (org_id, billing_invoice_id, provider, delivery_id, provider_invoice_id, event_type, observed_status)
                    values (%s,%s,%s,%s,%s,'checkout_created','new')""", (org_id, billing_id, provider.name, f"created:{c.provider_invoice_id}", c.provider_invoice_id))
    return conn.execute("""update billing_invoices set provider=%s, provider_invoice_id=%s, checkout_url=%s, checkout_expires_at=%s, status='open'
                           where id=%s returning *""", (provider.name, c.provider_invoice_id, c.checkout_url, exp, billing_id)).fetchone()


def apply_provider_state(conn, provider: PaymentProvider, provider_invoice_id: str, delivery_id: str, event_type: str = "", raw: dict | None = None,
                         now: dt.datetime | None = None) -> str:
    """Re-fetch the invoice from the provider and update ours. Returns a short outcome string (also stored in payment_events)."""
    now = now or dt.datetime.now(dt.timezone.utc)
    link = conn.execute("select org_id, billing_invoice_id from payment_events where provider=%s and provider_invoice_id=%s and billing_invoice_id is not null limit 1",
                        (provider.name, provider_invoice_id)).fetchone()
    if conn.execute("select 1 from payment_events where provider=%s and delivery_id=%s", (provider.name, delivery_id)).fetchone():
        return "replay_ignored"                          # webhook redelivery: already processed
    if not link:
        conn.execute("insert into payment_events (provider, delivery_id, provider_invoice_id, event_type, note, raw) values (%s,%s,%s,%s,'unknown_invoice',%s)",
                     (provider.name, delivery_id, provider_invoice_id, event_type, J(raw or {})))
        return "unknown_invoice"
    pi = provider.fetch_invoice(provider_invoice_id)
    b = conn.execute("select * from billing_invoices where id=%s for update", (link["billing_invoice_id"],)).fetchone()
    note, outcome = pi.note, pi.status
    if pi.order_id != str(b["id"]) or pi.currency.upper() != "USD" or pi.amount != D(b["total_usd"]):
        note, outcome = "amount_or_order_mismatch", "mismatch_ignored"       # never mark paid on a mismatch; a human looks at it
    elif pi.status == "paid":
        if b["status"] == "paid":
            if b["provider_invoice_id"] != provider_invoice_id:
                note = "duplicate_payment"                                      # a second checkout also got paid: refund needed
            outcome = "already_paid"
        else:
            conn.execute("update billing_invoices set status='paid', paid_at=%s where id=%s", (now, b["id"]))
    elif pi.status == "processing" and b["status"] == "open" and b["provider_invoice_id"] == provider_invoice_id:
        conn.execute("update billing_invoices set status='processing' where id=%s", (b["id"],))
    elif pi.status in ("expired", "invalid") and b["status"] == "processing" and b["provider_invoice_id"] == provider_invoice_id:
        conn.execute("update billing_invoices set status='open', checkout_expires_at=%s where id=%s", (now, b["id"]))
    conn.execute("""insert into payment_events (org_id, billing_invoice_id, provider, delivery_id, provider_invoice_id, event_type, observed_status, note, raw)
                    values (%s,%s,%s,%s,%s,%s,%s,%s,%s)""", (link["org_id"], b["id"], provider.name, delivery_id, provider_invoice_id, event_type, pi.status, note, J(raw or {})))
    return outcome


def mark_paid_manually(conn, billing_id, admin_id, reason: str, now: dt.datetime | None = None):
    """Admin-only escape hatch (payment received outside the gateway). Always logged."""
    if not reason.strip():
        raise BillingError("a reason is required")
    b = conn.execute("select * from billing_invoices where id=%s for update", (billing_id,)).fetchone()
    if not b:
        raise BillingError("billing invoice not found")
    if b["status"] == "paid":
        raise BillingError("already paid")
    conn.execute("update billing_invoices set status='paid', paid_at=%s, paid_note=%s where id=%s", (now or dt.datetime.now(dt.timezone.utc), reason, billing_id))
    conn.execute("insert into payment_events (org_id, billing_invoice_id, provider, delivery_id, event_type, observed_status, note, raw) values (%s,%s,'manual',%s,'manual_mark_paid','paid','manual_mark_paid',%s)",
                 (b["org_id"], billing_id, f"manual:{billing_id}:{admin_id}", J({"admin": str(admin_id), "reason": reason})))
    return conn.execute("select * from billing_invoices where id=%s", (billing_id,)).fetchone()
