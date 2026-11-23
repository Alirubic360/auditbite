"""E2E harness: real FastAPI + real Postgres + real worker loop, with a canned AI (no API key in CI).
Usage: python tests/e2e_server.py   (env: DATABASE_URL, SUPABASE_JWT_SECRET, STORAGE_DIR)"""
import os, sys, threading, time
sys.path.insert(0, os.path.dirname(__file__))
import uvicorn
from auditbite import pipeline
from auditbite.api import Deps, create_app
from auditbite.db import service_conn
from auditbite.mailer import OutboxSender
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
app = create_app(Deps(storage, ai, sender))


@app.get("/__outbox")
def outbox():
    return sender.sent


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")
