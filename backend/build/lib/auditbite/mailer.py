from __future__ import annotations
import base64, os, uuid
import httpx


class OutboxSender:
    """Dev/test sender: nothing leaves the machine; the call is recorded in outbound_emails by the caller."""
    def __init__(self):
        self.sent: list[dict] = []

    def send(self, to, subject, body, pdf=None, pdf_name="claim.pdf") -> str:
        pid = f"outbox-{uuid.uuid4()}"
        self.sent.append({"to": to, "subject": subject, "body": body, "pdf_bytes": len(pdf) if pdf else 0, "id": pid})
        return pid


class ResendSender:
    """Resend REST API (https://resend.com/docs/api-reference/emails/send-email)."""
    def __init__(self, api_key: str | None = None, from_addr: str | None = None, client: httpx.Client | None = None):
        self.key = api_key or os.environ["RESEND_API_KEY"]
        self.from_addr = from_addr or os.environ["AUDITBITE_FROM_EMAIL"]
        self.http = client or httpx.Client(timeout=20)

    def send(self, to, subject, body, pdf=None, pdf_name="claim.pdf") -> str:
        payload = {"from": self.from_addr, "to": [to], "subject": subject, "text": body}
        if pdf:
            payload["attachments"] = [{"filename": pdf_name, "content": base64.b64encode(pdf).decode()}]
        r = self.http.post("https://api.resend.com/emails", json=payload, headers={"Authorization": f"Bearer {self.key}"})
        r.raise_for_status()
        return r.json()["id"]
