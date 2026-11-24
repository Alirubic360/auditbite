"""Crypto payment providers. Invoices are denominated in USD; the provider locks the crypto rate at checkout creation.
We NEVER trust a webhook body: it is only a trigger, the status is re-fetched from the provider API."""
from __future__ import annotations
import hashlib, hmac, json, os
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol
import httpx


class InvalidSignature(Exception):
    pass


@dataclass
class Checkout:
    provider_invoice_id: str
    checkout_url: str
    expires_at: float | None = None      # unix seconds


@dataclass
class WebhookEvent:
    delivery_id: str
    provider_invoice_id: str
    event_type: str


@dataclass
class ProviderInvoice:
    provider_invoice_id: str
    status: str                          # new | processing | paid | expired | invalid
    amount: Decimal
    currency: str
    order_id: str | None
    note: str | None = None              # e.g. 'overpaid', 'paid_late', 'marked_by_merchant'


class PaymentProvider(Protocol):
    name: str
    def create_invoice(self, *, order_id: str, amount_usd: Decimal, description: str, redirect_url: str, buyer_email: str | None = None) -> Checkout: ...
    def verify_webhook(self, headers: dict, body: bytes) -> WebhookEvent: ...
    def fetch_invoice(self, provider_invoice_id: str) -> ProviderInvoice: ...


class BTCPayProvider:
    """BTCPay Server Greenfield API (self-hosted or third-party hosted). Docs: docs.btcpayserver.org/API/Greenfield/v1/"""
    name = "btcpay"
    _STATUS = {"New": "new", "Processing": "processing", "Settled": "paid", "Expired": "expired", "Invalid": "invalid"}

    def __init__(self, base_url: str, store_id: str, api_key: str, webhook_secret: str, client: httpx.Client | None = None):
        self.base, self.store, self.key, self.secret = base_url.rstrip("/"), store_id, api_key, webhook_secret.encode()
        self.http = client or httpx.Client(timeout=30)

    def _h(self):
        return {"Authorization": f"token {self.key}", "Content-Type": "application/json"}

    def create_invoice(self, *, order_id, amount_usd, description, redirect_url, buyer_email=None) -> Checkout:
        body = {"amount": str(amount_usd), "currency": "USD", "metadata": {"orderId": order_id, "itemDesc": description, **({"buyerEmail": buyer_email} if buyer_email else {})},
                "checkout": {"redirectURL": redirect_url, "expirationMinutes": 60}}
        r = self.http.post(f"{self.base}/api/v1/stores/{self.store}/invoices", json=body, headers=self._h())
        r.raise_for_status()
        j = r.json()
        return Checkout(j["id"], j["checkoutLink"], float(j["expirationTime"]) if j.get("expirationTime") else None)

    def verify_webhook(self, headers, body) -> WebhookEvent:
        sig = {k.lower(): v for k, v in headers.items()}.get("btcpay-sig", "")
        expected = "sha256=" + hmac.new(self.secret, body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            raise InvalidSignature("bad BTCPay-Sig")
        j = json.loads(body)
        if not j.get("invoiceId") or not j.get("deliveryId"):
            raise InvalidSignature("malformed webhook")
        return WebhookEvent(str(j["deliveryId"]), j["invoiceId"], str(j.get("type", "")))

    def fetch_invoice(self, provider_invoice_id) -> ProviderInvoice:
        r = self.http.get(f"{self.base}/api/v1/stores/{self.store}/invoices/{provider_invoice_id}", headers=self._h())
        r.raise_for_status()
        j = r.json()
        add = j.get("additionalStatus")
        note = {"PaidOver": "overpaid", "PaidLate": "paid_late", "PaidPartial": "paid_partial", "Marked": "marked_by_merchant"}.get(add)
        return ProviderInvoice(provider_invoice_id, self._STATUS.get(j.get("status"), "invalid"), Decimal(str(j["amount"])), j.get("currency", ""),
                               (j.get("metadata") or {}).get("orderId"), note)


def build_provider() -> PaymentProvider | None:
    e = os.environ
    if all(e.get(k) for k in ("BTCPAY_URL", "BTCPAY_STORE_ID", "BTCPAY_API_KEY", "BTCPAY_WEBHOOK_SECRET")):
        return BTCPayProvider(e["BTCPAY_URL"], e["BTCPAY_STORE_ID"], e["BTCPAY_API_KEY"], e["BTCPAY_WEBHOOK_SECRET"])
    return None
