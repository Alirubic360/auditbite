"""E2E harness: real FastAPI + real Postgres + real worker loop, with a canned AI (no API key in CI).
Usage: python tests/e2e_server.py   (env: DATABASE_URL, SUPABASE_JWT_SECRET, STORAGE_DIR)"""
import os, sys, threading, time
sys.path.insert(0, os.path.dirname(__file__))
import uvicorn
from auditbite import pipeline
from auditbite.api import Deps, create_app
from auditbite.db import service_conn
from auditbite.mailer import OutboxSender
from auditbite.payments import BTCPayProvider
from auditbite.storage import LocalStorage
from fakes import FakeAI, inv, ln

ai = FakeAI({"e2e-over": inv(vendor="Sysco", number="E2E-1001", date="2025-03-04",
                             lines=[ln(1, "Chicken breast boneless skinless", 100, "LB", 2.2, 220, sku="CHK-40"),
                                    ln(2, "Olive oil EV", 4, "GAL", 30.0, 120.0, sku="OIL-EV", conf=0.95)], sub=340, tax=0, total=340)},
            contract=None)
storage = LocalStorage(os.environ["STORAGE_DIR"], "e2e-sign")
sender = OutboxSender()


def worker():
    while True:
        try:
            with service_conn() as c:
                while pipeline.run_once(c, storage, ai, ai, ai):
                    pass
        except Exception as e:  # noqa: BLE001
            print("worker error", e, flush=True)
        time.sleep(0.5)


threading.Thread(target=worker, daemon=True).start()
import hashlib, hmac, json as _json
import httpx
from fastapi import Request
from fastapi.responses import HTMLResponse
WH = "e2e-hook"
_fake = {}   # in-process stand-in for a BTCPay Server (the REAL BTCPayProvider talks to it over real HTTP)
provider = BTCPayProvider("http://127.0.0.1:8000/__btcpay", "STORE", "e2e-key", WH)
app = create_app(Deps(storage, ai, sender, provider))


@app.post("/__btcpay/api/v1/stores/{store}/invoices")
async def fake_create(store: str, request: Request):
    if request.headers.get("authorization") != "token e2e-key":
        return HTMLResponse("unauthorized", 401)
    b = await request.json(); pid = f"BT{len(_fake) + 1}"
    _fake[pid] = {"id": pid, "status": "New", "additionalStatus": "None", "amount": b["amount"], "currency": b["currency"], "metadata": b["metadata"], "redirect": b["checkout"]["redirectURL"]}
    return {"id": pid, "checkoutLink": f"http://localhost:8000/__btcpay/checkout/{pid}", "expirationTime": time.time() + 3600}


@app.get("/__btcpay/api/v1/stores/{store}/invoices/{pid}")
def fake_get(store: str, pid: str):
    return {k: v for k, v in _fake[pid].items() if k != "redirect"}


@app.get("/__btcpay/checkout/{pid}", response_class=HTMLResponse)
def fake_checkout(pid: str):
    i = _fake[pid]
    return f"<html><body><h1>Fake BTCPay checkout</h1><p id=amt>{i['amount']} {i['currency']}</p><p id=st>{i['status']}</p></body></html>"


@app.post("/__btcpay/settle/{pid}")
def fake_settle(pid: str, amount: str | None = None):
    """Test control: the customer 'paid'. Fires a correctly signed webhook like BTCPay would."""
    _fake[pid]["status"] = "Settled"
    if amount: _fake[pid]["amount"] = amount
    body = _json.dumps({"deliveryId": f"del-{pid}-{time.time()}", "invoiceId": pid, "type": "InvoiceSettled"}).encode()
    r = httpx.post("http://127.0.0.1:8000/webhooks/btcpay", content=body, headers={"BTCPay-Sig": "sha256=" + hmac.new(WH.encode(), body, hashlib.sha256).hexdigest(), "Content-Type": "application/json"})
    return {"webhook_status": r.status_code, "body": r.json()}


@app.get("/__outbox")
def outbox():
    return sender.sent


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")
