import base64, uuid
from decimal import Decimal as Dc
import pytest
from fastapi.testclient import TestClient
from auditbite import pipeline
from auditbite.api import Deps, create_app
from auditbite.mailer import OutboxSender
from conftest import token
from fakes import FakeAI, inv, ln, pdf


@pytest.fixture
def env(dburl, storage, conn):
    ai = FakeAI({"pv": inv(number="P1", lines=[ln(1, "x", 100, "LB", 2.2, 220, sku="CHK-40")], sub=220, tax=0, total=220)},
                contract={"vendor_name": "Sysco", "reference": "C-9", "items": [{"canonical_name": "Beef", "unit": "LB", "contract_price": 5, "needs_index": False}], "warnings": []})
    sender = OutboxSender()
    client = TestClient(create_app(Deps(storage, ai, sender)))
    return client, ai, sender, conn, storage


def H(u): return {"Authorization": f"Bearer {token(u)}"}


def onboard(client, name="Acme"):
    u = uuid.uuid4()
    assert client.post("/onboarding", json={"org_name": name}, headers=H(u)).status_code == 201
    return u


def test_auth_required_and_bad_tokens(env):
    client, *_ = env
    assert client.get("/invoices").status_code == 401
    assert client.get("/invoices", headers={"Authorization": "Bearer nope"}).status_code == 401
    import jwt
    forged = jwt.encode({"sub": str(uuid.uuid4()), "aud": "authenticated"}, "wrong-secret-wrong-secret-wrong-secret-1", algorithm="HS256")
    assert client.get("/invoices", headers={"Authorization": f"Bearer {forged}"}).status_code == 401
    assert client.get("/healthz").json() == {"ok": True}


def test_user_without_org_gets_403_and_cannot_onboard_twice(env):
    client, *_ = env
    u = uuid.uuid4()
    assert client.get("/dashboard", headers=H(u)).status_code == 403
    client.post("/onboarding", json={"org_name": "A"}, headers=H(u))
    assert client.post("/onboarding", json={"org_name": "B"}, headers=H(u)).status_code == 409


def test_end_to_end_two_tenants_isolated_over_http(env):
    client, ai, sender, conn, storage = env
    ua, ub = onboard(client, "Alpha"), onboard(client, "Beta")
    v = client.post("/vendors", json={"name": "Sysco", "contact_email": "ar@sysco.test"}, headers=H(ua)).json()
    ct = client.post("/contracts", json={"vendor_id": v["id"], "reference": "C-1", "valid_from": "2025-01-01", "valid_to": "2025-12-31",
                     "items": [{"sku": "CHK-40", "canonical_name": "Chicken breast", "unit": "LB", "contract_price": "2.00", "source_clause": "Sec 3"}]}, headers=H(ua))
    assert ct.status_code == 201
    up = client.post("/invoices", files=[("files", ("inv.pdf", pdf("pv"), "application/pdf"))], headers=H(ua))
    assert up.status_code == 201
    iid = up.json()[0]["id"]
    # --- Beta cannot see or touch Alpha's data ---
    assert client.get("/invoices", headers=H(ub)).json() == []
    assert client.get(f"/invoices/{iid}", headers=H(ub)).status_code == 404
    assert client.get(f"/invoices/{iid}/file-url", headers=H(ub)).status_code == 404
    assert client.post(f"/invoices/{iid}/reprocess", headers=H(ub)).status_code == 404
    assert client.get("/contracts", headers=H(ub)).json() == []
    assert client.patch(f"/vendors/{v['id']}", json={"name": "pwn"}, headers=H(ub)).status_code == 404
    assert client.post("/contracts", json={"vendor_id": v["id"], "items": []}, headers=H(ub)).status_code == 422   # foreign vendor
    # --- worker processes the queued invoice ---
    assert pipeline.run_once(conn, storage, ai, ai, ai) in ("needs_review",)
    conn.commit()
    d = client.get(f"/invoices/{iid}", headers=H(ua)).json()
    assert d["status"] == "needs_review" and d["vendor_name"] == "Sysco" and d["lines"][0]["contract_price"] == "2.0000"
    disc = d["discrepancies"][0]
    assert disc["type"] == "price_variance" and disc["amount"] == "20.00"             # money serialised as exact strings
    # filters
    assert len(client.get("/invoices?status=needs_review", headers=H(ua)).json()) == 1 and client.get("/invoices?status=audited", headers=H(ua)).json() == []
    # cannot create a claim from a pending discrepancy
    r = client.post("/claims", json={"vendor_id": v["id"], "discrepancy_ids": [disc["id"]]}, headers=H(ua))
    assert r.status_code == 422 and "human-approved" in r.json()["detail"]
    # Beta cannot approve Alpha's discrepancy
    assert client.patch(f"/discrepancies/{disc['id']}", json={"review_status": "approved"}, headers=H(ub)).status_code == 404
    # approve, claim, send, credit
    assert client.patch(f"/discrepancies/{disc['id']}", json={"review_status": "approved"}, headers=H(ua)).json()["review_status"] == "approved"
    assert client.post(f"/invoices/{iid}/reprocess", headers=H(ua)).status_code == 409          # reviewed -> locked
    assert len(client.get("/discrepancies/claimable", headers=H(ua)).json()) == 1
    cl = client.post("/claims", json={"vendor_id": v["id"], "discrepancy_ids": [disc["id"]]}, headers=H(ua)).json()
    assert cl["total_claimed"] == "20.00" and cl["status"] == "draft"
    assert client.get(f"/claims/{cl['id']}", headers=H(ub)).status_code == 404
    assert client.post(f"/claims/{cl['id']}/send", headers=H(ub)).status_code == 422       # RLS hides the claim from Beta
    assert sender.sent == []                                                                    # nothing emailed before human click
    pdf_url = client.get(f"/claims/{cl['id']}/pdf-url", headers=H(ua)).json()["url"]
    got = client.get(pdf_url)
    assert got.status_code == 200 and got.content.startswith(b"%PDF")
    assert client.get(pdf_url.replace("sig=", "sig=0")).status_code == 403                       # tampered signature
    assert client.get(pdf_url.replace("exp=", "exp=1")).status_code == 403
    assert client.post(f"/claims/{cl['id']}/send", headers=H(ua)).json()["status"] == "sent" and len(sender.sent) == 1
    assert client.post(f"/claims/{cl['id']}/credit", json={"amount": "21", "status": "credited"}, headers=H(ua)).status_code == 422
    done = client.post(f"/claims/{cl['id']}/credit", json={"amount": "20", "status": "credited"}, headers=H(ua)).json()
    assert done["recovered_amount"] == "20.00" and done["fee_amount"] == "5.00"
    full = client.get(f"/claims/{cl['id']}", headers=H(ua)).json()
    assert len(full["followups"]) == 2 and {f["status"] for f in full["followups"]} == {"cancelled"} and full["emails"][0]["kind"] == "claim"
    dash = client.get("/dashboard", headers=H(ua)).json()
    assert dash["recovered_cash"] == "20.00" and dash["discrepancies_found"] == "20.00" and dash["top_vendors"][0]["name"] == "Sysco"
    assert dash["success_rate"] and Dc(dash["success_rate"]) == 1
    assert client.get("/dashboard", headers=H(ub)).json()["recovered_cash"] == "0"
    assert client.get("/metrics/precision", headers=H(ua)).json()[0]["precision"] == "1.000"
    log = conn.execute("select action from audit_log where org_id=(select org_id from org_members where user_id=%s)", (ua,)).fetchall()
    assert {"discrepancy.review", "claim.send", "org.create"} <= {r["action"] for r in log}


def test_upload_validation(env):
    client, *_ = env
    u = onboard(client)
    bad = client.post("/invoices", files=[("files", ("x.pdf", b"<html>not a pdf</html>", "application/pdf"))], headers=H(u))
    assert bad.status_code == 422                                         # content sniffed, extension/content-type ignored
    ok = client.post("/invoices", files=[("files", ("x.exe", pdf("a"), "application/octet-stream")), ("files", ("y.png", b"\x89PNG\r\n\x1a\n" + b"0" * 10, "image/png"))], headers=H(u))
    assert ok.status_code == 201 and [x["media_type"] for x in ok.json()] == ["application/pdf", "image/png"]
    assert client.post("/invoices", files=[("files", ("e.pdf", b"", "application/pdf"))], headers=H(u)).status_code == 422


def test_csv_and_ai_contract_import_and_path_ownership(env):
    client, ai, *_ = env
    u = onboard(client)
    r = client.post("/contracts/parse-csv", files={"file": ("c.csv", b"canonical_name,unit,contract_price\nRice,LB,1.10\n", "text/csv")}, headers=H(u))
    assert r.json()["items"][0]["contract_price"] == "1.10"
    assert client.post("/contracts/parse-csv", files={"file": ("c.csv", b"x,y\n1,2\n", "text/csv")}, headers=H(u)).status_code == 422
    d = client.post("/contracts/extract", files={"file": ("c.pdf", pdf("c"), "application/pdf")}, headers=H(u)).json()
    assert d["items"][0]["canonical_name"] == "Beef" and d["file_path"]
    other = onboard(client, "Other")
    v = client.post("/vendors", json={"name": "V"}, headers=H(other)).json()
    stolen = client.post("/contracts", json={"vendor_id": v["id"], "file_path": d["file_path"], "items": []}, headers=H(other))
    assert stolen.status_code == 403                                      # cannot attach another org's stored file


def test_settings_and_validation(env):
    client, *_ = env
    u = onboard(client)
    assert client.patch("/settings", json={"success_fee_rate": "0.2", "default_tolerance_pct": "1"}, headers=H(u)).json()["success_fee_rate"] == "0.200"
    assert client.patch("/settings", json={"success_fee_rate": "1.5"}, headers=H(u)).status_code == 422
    assert client.patch(f"/discrepancies/{uuid.uuid4()}", json={"review_status": "bogus"}, headers=H(u)).status_code == 422


def test_zero_amount_discrepancy_needs_amount_to_approve(env):
    client, ai, sender, conn, storage = env
    u = onboard(client)
    org = conn.execute("select org_id from org_members where user_id=%s", (u,)).fetchone()["org_id"]
    i = conn.execute("insert into invoices (org_id,file_path,media_type) values (%s,'p','application/pdf') returning id", (org,)).fetchone()["id"]
    d = conn.execute("insert into discrepancies (org_id,invoice_id,type,amount,confidence) values (%s,%s,'unit_mismatch',0,0.5) returning id", (org, i)).fetchone()["id"]
    conn.commit()
    assert client.get("/review", headers=H(u)).json()[0]["id"] == str(d)
    assert client.patch(f"/discrepancies/{d}", json={"review_status": "approved"}, headers=H(u)).status_code == 422
    assert client.patch(f"/discrepancies/{d}", json={"review_status": "approved", "amount": "12.345"}, headers=H(u)).json()["amount"] == "12.35"


def test_inbound_email_webhook(env):
    client, ai, sender, conn, storage = env
    u = onboard(client, "Mail Co")
    slug = conn.execute("select inbox_slug from organizations where id=(select org_id from org_members where user_id=%s)", (u,)).fetchone()["inbox_slug"]
    body = {"to": f"invoices+{slug}@inbound.example.com", "attachments": [{"filename": "a.pdf", "content_base64": base64.b64encode(pdf("pv")).decode()},
                                                                         {"filename": "evil.exe", "content_base64": base64.b64encode(b"MZ").decode()}]}
    assert client.post("/webhooks/inbound-email", json=body).status_code == 401
    assert client.post("/webhooks/inbound-email", json=body, headers={"x-webhook-secret": "wrong"}).status_code == 401
    r = client.post("/webhooks/inbound-email", json=body, headers={"x-webhook-secret": "hook-secret"})
    assert r.status_code == 202 and r.json() == {"accepted": 1, "rejected": 1}
    assert len(client.get("/invoices", headers=H(u)).json()) == 1
    body["to"] = "invoices+nope@inbound.example.com"
    assert client.post("/webhooks/inbound-email", json=body, headers={"x-webhook-secret": "hook-secret"}).status_code == 404


def test_path_traversal_blocked(env):
    client, *_ = env
    import time
    storage = env[4]
    exp = int(time.time()) + 100
    path = "../../etc/passwd"
    sig = storage._sig(path, exp)                                         # even with a valid signature the root jail holds
    with pytest.raises(ValueError):
        storage.get(path)
    assert client.get(f"/files/{path}?exp={exp}&sig={sig}").status_code != 200
