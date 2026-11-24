import datetime as dt, hashlib, hmac, json, uuid
from decimal import Decimal as Dc
import httpx, psycopg, pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from auditbite import billing, claims
from auditbite.api import Deps, create_app
from auditbite.mailer import OutboxSender
from auditbite.payments import BTCPayProvider, Checkout, InvalidSignature, ProviderInvoice
from conftest import token
from seed import make_org, make_vendor

UTC = dt.timezone.utc
H = lambda u: {"Authorization": f"Bearer {token(u)}"}


def org_with(conn, created, rate="10.00", locations=2, fee_claims=()):
    org, user = make_org(conn)
    conn.execute("update organizations set created_at=%s, subscription_per_location_usd=%s where id=%s", (created, rate, org))
    for i in range(locations):
        conn.execute("insert into locations (org_id,name) values (%s,%s)", (org, f"L{i}"))
    v = make_vendor(conn, org)
    for rec_at, total, rec in fee_claims:
        conn.execute("""insert into claims (org_id,vendor_id,total_claimed,status,recovered_amount,fee_amount,recovered_at)
                        values (%s,%s,%s,'credited',%s,%s,%s)""", (org, v, total, rec, Dc(rec) * Dc("0.25"), rec_at))
    conn.commit()
    return org, user, v


def test_generation_first_month_free_fee_rolls_and_is_idempotent(conn):
    org, _, v = org_with(conn, dt.datetime(2025, 10, 5, tzinfo=UTC), fee_claims=[(dt.datetime(2025, 10, 15, tzinfo=UTC), "40", "20.00"),
                                                                                  (dt.datetime(2025, 11, 20, tzinfo=UTC), "100", "100.00")])
    now = dt.datetime(2025, 12, 2, tzinfo=UTC)
    assert billing.generate_due_invoices(conn, now) == 2; conn.commit()
    inv = {str(r["period_start"]): r for r in conn.execute("select * from billing_invoices where org_id=%s", (org,)).fetchall()}
    assert inv["2025-10-01"]["subscription_amount"] == 0 and inv["2025-10-01"]["success_fee_amount"] == Dc("5.00") and inv["2025-10-01"]["total_usd"] == Dc("5.00")
    nov = inv["2025-11-01"]
    assert nov["subscription_amount"] == Dc("20.00") and nov["success_fee_amount"] == Dc("25.00") and nov["recovered_base"] == Dc("100.00") and nov["total_usd"] == Dc("45.00")
    assert billing.generate_due_invoices(conn, now) == 0                                 # idempotent
    assert {r["billing_invoice_id"] for r in conn.execute("select billing_invoice_id from claims where org_id=%s", (org,)).fetchall()} == {inv["2025-10-01"]["id"], nov["id"]}
    cid = conn.execute("select id from claims where org_id=%s limit 1", (org,)).fetchone()["id"]
    with pytest.raises(claims.ClaimError, match="already been billed"):                  # a billed recovery can't be edited
        claims.record_credit(conn, org, cid, "1")


def test_nothing_owed_no_invoice_and_current_month_not_billed(conn):
    org, *_ = org_with(conn, dt.datetime(2025, 12, 1, tzinfo=UTC))
    assert billing.generate_due_invoices(conn, dt.datetime(2025, 12, 20, tzinfo=UTC)) == 0   # same month as creation, nothing completed
    org2, *_ = org_with(conn, dt.datetime(2025, 10, 1, tzinfo=UTC), rate="0", fee_claims=[])
    assert billing.generate_due_invoices(conn, dt.datetime(2025, 12, 2, tzinfo=UTC)) == 0
    assert conn.execute("select count(*) n from billing_invoices where org_id=%s", (org2,)).fetchone()["n"] == 0


def test_disputed_or_zero_fee_claims_are_never_billed(conn):
    org, _, v = org_with(conn, dt.datetime(2025, 10, 1, tzinfo=UTC), rate="0")
    conn.execute("insert into claims (org_id,vendor_id,total_claimed,status,recovered_amount,fee_amount,recovered_at) values (%s,%s,50,'disputed',0,0,null)", (org, v)); conn.commit()
    assert billing.generate_due_invoices(conn, dt.datetime(2025, 12, 2, tzinfo=UTC)) == 0


# ---------- provider adapter ----------
def provider(handler):
    return BTCPayProvider("https://pay.example.com/", "STORE1", "KEY", "whsec", httpx.Client(transport=httpx.MockTransport(handler)))


def sig(body: bytes, secret=b"whsec"): return "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()


def test_btcpay_create_request_shape():
    seen = {}
    def h(req):
        seen["r"] = req
        return httpx.Response(200, json={"id": "INV1", "checkoutLink": "https://pay.example.com/i/INV1", "expirationTime": 1900000000})
    c = provider(h).create_invoice(order_id="ord-1", amount_usd=Dc("45.00"), description="AuditBite 2025-11", redirect_url="https://app/billing")
    body = json.loads(seen["r"].content)
    assert str(seen["r"].url) == "https://pay.example.com/api/v1/stores/STORE1/invoices" and seen["r"].headers["authorization"] == "token KEY"
    assert body["amount"] == "45.00" and body["currency"] == "USD" and body["metadata"]["orderId"] == "ord-1" and body["checkout"]["redirectURL"] == "https://app/billing"
    assert c.provider_invoice_id == "INV1" and c.checkout_url.endswith("/INV1")


def test_btcpay_webhook_signature():
    p = provider(lambda r: httpx.Response(200))
    body = json.dumps({"deliveryId": "d1", "invoiceId": "INV1", "type": "InvoiceSettled"}).encode()
    ev = p.verify_webhook({"BTCPay-Sig": sig(body)}, body)
    assert (ev.delivery_id, ev.provider_invoice_id, ev.event_type) == ("d1", "INV1", "InvoiceSettled")
    for bad in ({}, {"BTCPay-Sig": "sha256=00"}, {"BTCPay-Sig": sig(body, b"other")}, {"BTCPay-Sig": sig(body + b" ")}):
        with pytest.raises(InvalidSignature):
            p.verify_webhook(bad, body)
    junk = b'{"x":1}'
    with pytest.raises(InvalidSignature):
        p.verify_webhook({"BTCPay-Sig": sig(junk)}, junk)


@pytest.mark.parametrize("status,add,norm,note", [("Settled", None, "paid", None), ("Settled", "PaidOver", "paid", "overpaid"), ("Processing", None, "processing", None),
                                                  ("Expired", "PaidPartial", "expired", "paid_partial"), ("Invalid", None, "invalid", None), ("New", None, "new", None), ("Weird", None, "invalid", None)])
def test_btcpay_status_mapping(status, add, norm, note):
    p = provider(lambda r: httpx.Response(200, json={"id": "I", "status": status, "additionalStatus": add, "amount": "45.00", "currency": "USD", "metadata": {"orderId": "o"}}))
    pi = p.fetch_invoice("I")
    assert (pi.status, pi.note, pi.amount, pi.order_id) == (norm, note, Dc("45.00"), "o")


# ---------- state machine with a scriptable provider ----------
class Scripted:
    name = "btcpay"
    def __init__(self): self.state = {}; self.n = 0
    def create_invoice(self, *, order_id, amount_usd, description, redirect_url, buyer_email=None):
        self.n += 1; pid = f"P{self.n}"
        self.state[pid] = ProviderInvoice(pid, "new", amount_usd, "USD", order_id)
        return Checkout(pid, f"https://pay/{pid}", (dt.datetime.now(UTC) + dt.timedelta(minutes=60)).timestamp())
    def fetch_invoice(self, pid): return self.state[pid]
    def verify_webhook(self, h, b): raise NotImplementedError


@pytest.fixture
def billed(conn):
    org, user, v = org_with(conn, dt.datetime(2025, 10, 1, tzinfo=UTC), fee_claims=[(dt.datetime(2025, 10, 10, tzinfo=UTC), "40", "20.00")])
    billing.generate_due_invoices(conn, dt.datetime(2025, 11, 2, tzinfo=UTC)); conn.commit()
    bid = conn.execute("select id from billing_invoices where org_id=%s", (org,)).fetchone()["id"]
    return org, user, bid, Scripted()


def status(conn, bid): return conn.execute("select status, provider_invoice_id from billing_invoices where id=%s", (bid,)).fetchone()


def test_paid_only_after_provider_confirms_exact_amount(conn, billed):
    org, _, bid, p = billed
    b = billing.create_checkout(conn, p, org, bid, "https://app/billing"); conn.commit()
    assert b["status"] == "open" and b["checkout_url"] == "https://pay/P1"
    again = billing.create_checkout(conn, p, org, bid, "https://app/billing")
    assert again["provider_invoice_id"] == "P1" and p.n == 1                              # live checkout reused
    p.state["P1"] = ProviderInvoice("P1", "processing", Dc("5.00"), "USD", str(bid))
    assert billing.apply_provider_state(conn, p, "P1", "d1") == "processing" and status(conn, bid)["status"] == "processing"
    p.state["P1"] = ProviderInvoice("P1", "paid", Dc("4.99"), "USD", str(bid))              # underpaid amount => never paid
    assert billing.apply_provider_state(conn, p, "P1", "d2") == "mismatch_ignored" and status(conn, bid)["status"] == "processing"
    p.state["P1"] = ProviderInvoice("P1", "paid", Dc("5.00"), "USD", str(uuid.uuid4()))      # someone else's order id
    assert billing.apply_provider_state(conn, p, "P1", "d3") == "mismatch_ignored" and status(conn, bid)["status"] == "processing"
    p.state["P1"] = ProviderInvoice("P1", "paid", Dc("5.00"), "EUR", str(bid))
    assert billing.apply_provider_state(conn, p, "P1", "d4") == "mismatch_ignored"
    p.state["P1"] = ProviderInvoice("P1", "paid", Dc("5.00"), "USD", str(bid), "overpaid")
    assert billing.apply_provider_state(conn, p, "P1", "d5") == "paid" and status(conn, bid)["status"] == "paid"
    assert billing.apply_provider_state(conn, p, "P1", "d5") == "replay_ignored"            # same delivery id twice
    assert billing.apply_provider_state(conn, p, "P1", "d6") == "already_paid"
    notes = {r["note"] for r in conn.execute("select note from payment_events where billing_invoice_id=%s", (bid,)).fetchall()}
    assert {"amount_or_order_mismatch", "overpaid"} <= notes
    with pytest.raises(billing.BillingError):
        billing.create_checkout(conn, p, org, bid, "x")                                      # paid invoices can't be re-checked-out


def test_expiry_reopens_and_second_checkout_double_payment_is_flagged(conn, billed):
    org, _, bid, p = billed
    billing.create_checkout(conn, p, org, bid, "x")
    p.state["P1"] = ProviderInvoice("P1", "processing", Dc("5.00"), "USD", str(bid)); billing.apply_provider_state(conn, p, "P1", "a")
    p.state["P1"] = ProviderInvoice("P1", "expired", Dc("5.00"), "USD", str(bid)); billing.apply_provider_state(conn, p, "P1", "b")
    assert status(conn, bid)["status"] == "open"
    conn.execute("update billing_invoices set checkout_expires_at = now() - interval '1 minute' where id=%s", (bid,))
    b2 = billing.create_checkout(conn, p, org, bid, "x")
    assert b2["provider_invoice_id"] == "P2"                                                  # expired checkout replaced
    p.state["P2"] = ProviderInvoice("P2", "paid", Dc("5.00"), "USD", str(bid)); assert billing.apply_provider_state(conn, p, "P2", "c") == "paid"
    p.state["P1"] = ProviderInvoice("P1", "paid", Dc("5.00"), "USD", str(bid))                # old checkout also gets paid later
    billing.apply_provider_state(conn, p, "P1", "d")
    assert "duplicate_payment" in {r["note"] for r in conn.execute("select note from payment_events where billing_invoice_id=%s", (bid,)).fetchall()}


def test_unknown_invoice_event_is_recorded_not_applied(conn, billed):
    org, _, bid, p = billed
    p.state["ZZ"] = ProviderInvoice("ZZ", "paid", Dc("5.00"), "USD", str(bid))
    assert billing.apply_provider_state(conn, p, "ZZ", "x") == "unknown_invoice" and status(conn, bid)["status"] == "open"


def test_manual_mark_paid_requires_reason_and_is_logged(conn, billed):
    org, _, bid, p = billed
    with pytest.raises(billing.BillingError):
        billing.mark_paid_manually(conn, bid, uuid.uuid4(), "  ")
    r = billing.mark_paid_manually(conn, bid, uuid.uuid4(), "paid by wire, ref 123"); assert r["status"] == "paid" and "wire" in r["paid_note"]
    with pytest.raises(billing.BillingError):
        billing.mark_paid_manually(conn, bid, uuid.uuid4(), "again")


# ---------- RLS on the new tables ----------
def test_rls_billing_tables(conn, billed, dburl):
    from auditbite.db import user_tx
    org, user, bid, p = billed
    other, other_user, _ = org_with(conn, dt.datetime(2025, 10, 1, tzinfo=UTC), fee_claims=[(dt.datetime(2025, 10, 10, tzinfo=UTC), "40", "20.00")])
    billing.generate_due_invoices(conn, dt.datetime(2025, 11, 2, tzinfo=UTC)); billing.create_checkout(conn, p, org, bid, "x"); conn.commit()
    with user_tx(str(user)) as c:
        assert [str(r["id"]) for r in c.execute("select id from billing_invoices").fetchall()] == [str(bid)]      # only own invoice
        for sql in ("update billing_invoices set status='paid'", "delete from billing_invoices"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute("savepoint s"); c.execute(sql)
            c.execute("rollback to s")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with user_tx(str(user)) as c:
            c.execute("select * from payment_events")                                          # provider logs are server-only


# ---------- HTTP API ----------
@pytest.fixture
def web(dburl, storage, conn):
    secret = b"whsec"
    state = {"inv": {}}
    def handler(req):
        if req.method == "POST":
            body = json.loads(req.content); pid = f"BT{len(state['inv']) + 1}"
            state["inv"][pid] = {"id": pid, "status": "New", "additionalStatus": "None", "amount": body["amount"], "currency": "USD", "metadata": body["metadata"]}
            return httpx.Response(200, json={"id": pid, "checkoutLink": f"https://pay.example.com/i/{pid}", "expirationTime": 4102444800})
        return httpx.Response(200, json=state["inv"][str(req.url).rsplit("/", 1)[1]])
    prov = BTCPayProvider("https://pay.example.com", "S", "K", "whsec", httpx.Client(transport=httpx.MockTransport(handler)))
    return TestClient(create_app(Deps(storage, None, OutboxSender(), prov))), state, conn


def hook(client, pid, delivery, secret=b"whsec", typ="InvoiceSettled"):
    body = json.dumps({"deliveryId": delivery, "invoiceId": pid, "type": typ}).encode()
    return client.post("/webhooks/btcpay", content=body, headers={"BTCPay-Sig": sig(body, secret), "Content-Type": "application/json"})


def test_http_checkout_webhook_flow_and_isolation(web):
    client, state, conn = web
    org, user, _ = org_with(conn, dt.datetime(2025, 10, 1, tzinfo=UTC), fee_claims=[(dt.datetime(2025, 10, 10, tzinfo=UTC), "40", "20.00")])
    other = uuid.uuid4(); client.post("/onboarding", json={"org_name": "Other"}, headers=H(other))
    billing.generate_due_invoices(conn, dt.datetime(2025, 11, 2, tzinfo=UTC)); conn.commit()
    mine = client.get("/billing", headers=H(user)).json()
    assert mine["payments_enabled"] and len(mine["invoices"]) == 1 and mine["invoices"][0]["total_usd"] == "5.00" and client.get("/billing", headers=H(other)).json()["invoices"] == []
    bid = mine["invoices"][0]["id"]
    assert client.post(f"/billing/invoices/{bid}/checkout", headers=H(other)).status_code == 404                 # not yours
    assert client.post(f"/billing/invoices/{bid}/checkout").status_code == 401
    r = client.post(f"/billing/invoices/{bid}/checkout", headers=H(user)); assert r.status_code == 200 and r.json()["checkout_url"].endswith("/BT1")
    assert client.post(f"/billing/invoices/{bid}/checkout", headers=H(user)).json()["checkout_url"].endswith("/BT1") and len(state["inv"]) == 1
    assert hook(client, "BT1", "d1", secret=b"wrong").status_code == 401                                         # forged webhook
    assert client.post("/webhooks/btcpay", content=b"{}", headers={"BTCPay-Sig": "x"}).status_code == 401
    assert hook(client, "BT1", "d2").json() == {"outcome": "new"}                                               # provider still says New: nothing changes
    assert client.get("/billing", headers=H(user)).json()["invoices"][0]["status"] == "open"
    state["inv"]["BT1"]["status"] = "Settled"
    assert hook(client, "BT1", "d3").json() == {"outcome": "paid"}
    assert hook(client, "BT1", "d3").json() == {"outcome": "replay_ignored"}
    assert client.get("/billing", headers=H(user)).json()["invoices"][0]["status"] == "paid"
    assert client.post(f"/billing/invoices/{bid}/checkout", headers=H(user)).status_code == 409


def test_http_admin_billing(web):
    client, state, conn = web
    org, user, _ = org_with(conn, dt.datetime(2025, 10, 1, tzinfo=UTC), fee_claims=[(dt.datetime(2025, 10, 10, tzinfo=UTC), "40", "20.00")])
    admin = uuid.uuid4(); conn.execute("insert into platform_admins (user_id) values (%s)", (admin,)); conn.commit()
    assert client.post("/admin/billing/generate", headers=H(user)).status_code == 403 and client.get("/admin/billing", headers=H(user)).status_code == 403
    assert client.post("/admin/billing/generate", headers=H(admin)).json()["created"] >= 1
    inv = client.get("/admin/billing", headers=H(admin)).json()["invoices"][0]
    assert inv["org_name"] and inv["status"] == "open"
    assert client.post(f"/admin/billing/{inv['id']}/mark-paid", json={"reason": "x"}, headers=H(admin)).status_code == 422
    assert client.post(f"/admin/billing/{inv['id']}/mark-paid", json={"reason": "wire transfer #42"}, headers=H(admin)).json()["status"] == "paid"
    assert client.post(f"/admin/billing/{inv['id']}/mark-paid", json={"reason": "again please"}, headers=H(admin)).status_code == 409
    assert client.patch(f"/admin/orgs/{org}", json={"subscription_per_location_usd": "25"}, headers=H(admin)).json()["subscription_per_location_usd"] == "25.00"
    assert client.patch(f"/admin/orgs/{org}", json={}, headers=H(admin)).status_code == 422


def test_payments_not_configured(dburl, storage):
    c = TestClient(create_app(Deps(storage, None, OutboxSender(), None)))
    u = uuid.uuid4(); c.post("/onboarding", json={"org_name": "N"}, headers=H(u))
    assert c.get("/billing", headers=H(u)).json()["payments_enabled"] is False
    assert c.post(f"/billing/invoices/{uuid.uuid4()}/checkout", headers=H(u)).status_code == 503
    assert c.post("/webhooks/btcpay", content=b"{}").status_code == 404
