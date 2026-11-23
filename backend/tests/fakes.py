"""Deterministic stand-ins for the AI roles. They return canned data keyed by a marker inside the uploaded file."""
from decimal import Decimal
from auditbite.matching import Match


def pdf(marker: str) -> bytes:
    return b"%PDF-1.4\nFIXTURE:" + marker.encode()


class FakeAI:
    def __init__(self, invoices: dict, matches: dict | None = None, contract: dict | None = None):
        self.invoices, self.matches, self.contract = invoices, matches or {}, contract
        self.match_calls = 0
        self.triage_calls = 0

    def extract_invoice(self, data, media_type):
        marker = data.split(b"FIXTURE:")[1].decode()
        return self.invoices[marker]

    def extract_contract(self, data, media_type):
        return self.contract

    def match(self, lines, candidates):
        self.match_calls += 1
        return {l["line_no"]: self.matches[l["line_no"]] for l in lines if l["line_no"] in self.matches}

    def triage(self, facts):
        self.triage_calls += 1
        return {"verdict": "unclear", "defensibility": "low", "likely_vendor_objection": "pack size", "recommended_action": "review"}

    def write_letter(self, payload, language):
        return {"subject": f"[{language}] claim {payload['total']}", "body": f"Total {payload['total']} within 14 days"}


def inv(vendor="Sysco", number="INV-1", date="2025-03-04", lines=None, sub=None, tax=None, total=None, warnings=None):
    return {"vendor_name": vendor, "invoice_number": number, "invoice_date": date, "currency": "USD",
            "printed_subtotal": sub, "printed_tax": tax, "printed_total": total, "lines": lines or [], "warnings": warnings or []}


def ln(no, desc, qty, unit, price, ext, sku=None, conf=0.95, line_type="product", pack=None):
    return {"line_no": no, "line_type": line_type, "sku": sku, "description": desc, "qty": qty, "unit": unit,
            "pack_description": pack, "unit_price": price, "extended_total": ext, "confidence": conf}
