"""Validate/normalise AI extraction output. Never trust the model's shape or numbers."""
from __future__ import annotations
import datetime as dt
from decimal import Decimal
from .money import D_or_none

LINE_TYPES = {"product", "freight", "surcharge", "discount", "deposit", "tax", "other"}
LOW_CONF = Decimal("0.8")


def _num(v, warns, where):
    try:
        return D_or_none(v)
    except ValueError:
        warns.append(f"{where}: non-numeric value {v!r} dropped")
        return None


def normalize_invoice(raw: dict) -> dict:
    warns = list(raw.get("warnings") or [])
    inv = {k: raw.get(k) for k in ("vendor_name", "invoice_number", "po_number", "currency", "date_raw")}
    iso = raw.get("invoice_date")
    try:
        inv["invoice_date"] = dt.date.fromisoformat(iso) if iso else None
    except (TypeError, ValueError):
        warns.append(f"invoice_date {iso!r} is not ISO; kept in date_raw")
        inv["invoice_date"] = None
        inv["date_raw"] = inv.get("date_raw") or str(iso)
    for k in ("printed_subtotal", "printed_tax", "printed_total"):
        inv[k] = _num(raw.get(k), warns, k)
    lines = []
    for i, ln in enumerate(raw.get("lines") or [], 1):
        lt = ln.get("line_type") if ln.get("line_type") in LINE_TYPES else "other"
        if lt != ln.get("line_type"):
            warns.append(f"line {i}: unknown line_type {ln.get('line_type')!r} -> other")
        try:
            conf = Decimal(str(ln.get("confidence", 0)))
        except Exception:
            conf = Decimal(0)
        conf = max(Decimal(0), min(Decimal(1), conf))
        lines.append({
            "line_no": int(ln.get("line_no") or i), "line_type": lt, "sku": ln.get("sku"),
            "description": ln.get("description") or "", "unit": ln.get("unit"),
            "pack_description": ln.get("pack_description"),
            "qty": _num(ln.get("qty"), warns, f"line {i} qty"),
            "unit_price": _num(ln.get("unit_price"), warns, f"line {i} unit_price"),
            "extended_total": _num(ln.get("extended_total"), warns, f"line {i} extended_total"),
            "confidence": conf})
    inv["lines"], inv["warnings"] = lines, warns
    # Overall confidence = weakest line (a single blurry line makes the invoice unsafe to auto-approve).
    inv["confidence"] = min((l["confidence"] for l in lines), default=Decimal(0))
    return inv
