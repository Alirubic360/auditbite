"""Worker: drains the invoice queue and sends due follow-ups. `python -m auditbite.worker`."""
from __future__ import annotations
import os, time
from . import claims, pipeline
from .ai import AnthropicAI
from .db import service_conn
from .mailer import OutboxSender, ResendSender
from .storage import LocalStorage


def build_deps():
    storage = LocalStorage(os.environ.get("STORAGE_DIR", "./storage"), os.environ.get("FILE_SIGN_SECRET", "dev-only-change-me"))
    ai = AnthropicAI() if os.environ.get("ANTHROPIC_API_KEY") else None
    sender = ResendSender() if os.environ.get("RESEND_API_KEY") else OutboxSender()
    return storage, ai, sender


def tick(conn, storage, ai, sender) -> dict:
    done = {"processed": 0, "followups": 0}
    while True:
        if ai is None:
            break   # no AI configured: leave invoices queued instead of failing them
        if pipeline.run_once(conn, storage, ai, ai, ai) is None:
            break
        done["processed"] += 1
    done["followups"] = claims.run_followups(conn, sender)
    conn.commit()
    return done


def main():
    storage, ai, sender = build_deps()
    if ai is None:
        print("ANTHROPIC_API_KEY not set: invoices stay queued; follow-ups still run")
    while True:
        with service_conn() as conn:
            r = tick(conn, storage, ai, sender)
        if r["processed"] or r["followups"]:
            print(r, flush=True)
        time.sleep(float(os.environ.get("WORKER_POLL_SECONDS", "5")))


if __name__ == "__main__":
    main()
