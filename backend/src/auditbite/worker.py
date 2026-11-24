"""Worker: drains the invoice queue and sends due follow-ups. `python -m auditbite.worker`."""
from __future__ import annotations
import os, time
from . import billing, claims, pipeline
from .ai import AnthropicAI
from .db import service_conn
from .mailer import OutboxSender, ResendSender
from .storage import LocalStorage, SupabaseStorage


def build_deps():
    if os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_SERVICE_KEY"):
        storage = SupabaseStorage(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"], os.environ.get("SUPABASE_BUCKET", "auditbite"))
    else:
        secret = os.environ.get("FILE_SIGN_SECRET")
        if not secret and os.environ.get("AUDITBITE_ENV") == "production":
            raise RuntimeError("FILE_SIGN_SECRET is required in production (or configure Supabase Storage)")
        storage = LocalStorage(os.environ.get("STORAGE_DIR", "./storage"), secret or "dev-only-change-me")
    ai = AnthropicAI() if os.environ.get("ANTHROPIC_API_KEY") else None
    sender = ResendSender() if os.environ.get("RESEND_API_KEY") else OutboxSender()
    return storage, ai, sender


def tick(conn, storage, ai, sender) -> dict:
    done = {"processed": 0, "followups": 0, "billing_invoices": 0}
    while True:
        if ai is None:
            break   # no AI configured: leave invoices queued instead of failing them
        if pipeline.run_once(conn, storage, ai, ai, ai) is None:
            break
        done["processed"] += 1
    done["followups"] = claims.run_followups(conn, sender)
    done["billing_invoices"] = billing.generate_due_invoices(conn)
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
