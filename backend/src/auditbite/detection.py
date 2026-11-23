"""Deterministic discrepancy detection. AI never does arithmetic (spec rule 1)."""
from __future__ import annotations
from dataclasses import dataclass, field
from decimal import Decimal
from .money import D, D_or_none, cents
from .units import norm_unit, pack_factor

MATH_TOL = Decimal("0.02")


@dataclass
class Finding:
    type: str
    amount: Decimal
    evidence: str
    line_no: int | None = None
    expected: Decimal | None = None
    billed: Decimal | None = None
    source_clause: str | None = None
    related_invoice_id: str | None = None
    confidence: Decimal = Decimal("1")
    needs_ai_triage: bool = False


def validate_math(inv: dict) -> tuple[list[Finding], list[str]]:
    """Internal consistency of the invoice. Only OVER-billing becomes a finding;
    under-billing is returned as a warning (we never claim money the vendor didn't charge)."""
    out: list[Finding] = []
    warns: list[str] = []
    line_sum = Decimal(0)
    all_ext = True
    for ln in inv["lines"]:
        ext = D_or_none(ln.get("extended_total"))
        if ext is None:
            all_ext = False
        else:
            line_sum += ext
        q, p = D_or_none(ln.get("qty")), D_or_none(ln.get("unit_price"))
        if q is not None and p is not None and ext is not None and ln.get("line_type", "product") in ("product", "other"):
            calc = cents(q * p)
            diff = ext - calc
            if abs(diff) > MATH_TOL:
                if diff > 0:
                    out.append(Finding("math_error", cents(diff), f"Line {ln['line_no']}: {q} x {p} = {calc}, but line total printed {ext}.",
                                       ln["line_no"], expected=calc, billed=ext,
                                       confidence=Decimal(str(min(float(ln.get("confidence", 1)), 1.0)))))
                else:
                    warns.append(f"Line {ln['line_no']}: printed total {ext} is below qty x price {calc} (under-billed; not claimable).")
    sub, tax, tot = (D_or_none(inv.get(k)) for k in ("printed_subtotal", "printed_tax", "printed_total"))
    non_tax = sum((D_or_none(l.get("extended_total")) or Decimal(0)) for l in inv["lines"] if l.get("line_type") != "tax")
    if sub is not None and all_ext:
        diff = sub - non_tax
        if abs(diff) > MATH_TOL:
            if diff > 0:
                out.append(Finding("math_error", cents(diff), f"Printed subtotal {sub} exceeds the sum of printed lines {non_tax}.",
                                   expected=non_tax, billed=sub))
            else:
                warns.append(f"Printed subtotal {sub} is below the sum of lines {non_tax}.")
    if sub is not None and tot is not None:
        exp_total = sub + (tax or Decimal(0))
        diff = tot - exp_total
        if abs(diff) > MATH_TOL:
            if diff > 0:
                out.append(Finding("math_error", cents(diff), f"Printed total {tot} exceeds subtotal {sub} + tax {tax or 0} = {exp_total}.",
                                   expected=exp_total, billed=tot))
            else:
                warns.append(f"Printed total {tot} is below subtotal + tax {exp_total}.")
    return out, warns


def detect_price_variance(line: dict, item: dict) -> Finding | None:
    if line.get("line_type", "product") != "product" or item.get("needs_index"):
        return None
    if line.get("unit_price") is None or line.get("qty") is None:
        return None
    billed, allowed = D(line["unit_price"]), D(item["contract_price"])
    tol = allowed * D(item["tolerance_pct"]) / 100
    if billed > allowed + tol:
        amount = cents((billed - allowed) * D(line["qty"]))
        return Finding("price_variance", amount,
                       f"Billed {billed} per {line.get('unit')} vs contract {allowed} per {item['unit']} "
                       f"(tolerance {item['tolerance_pct']}%), qty {D(line['qty'])}.",
                       line["line_no"], expected=allowed, billed=billed, source_clause=item.get("source_clause"))
    return None


def convert_line(line: dict, item: dict) -> dict | None:
    """Restate a pack-unit line (e.g. 2 CS @ 80, '4/5 LB') in the contract's unit (LB) ONLY when the printed pack text
    unambiguously names the contract unit. Anything else returns None and stays a human-review mismatch."""
    f = pack_factor(line.get("pack_description"), item.get("unit"))
    if f is None or line.get("qty") is None or line.get("unit_price") is None:
        return None
    return {**line, "unit": item["unit"], "qty": D(line["qty"]) * f, "unit_price": D(line["unit_price"]) / f,
            "converted_note": f"converted {line.get('unit')} ({line.get('pack_description')}) -> {item['unit']}, factor {f}, "
                              f"printed {D(line['qty'])} x {D(line['unit_price'])}"}


def detect_unit_mismatch(line: dict, item: dict) -> Finding | None:
    lu, iu = norm_unit(line.get("unit")), norm_unit(item.get("unit"))
    if lu and iu and lu != iu:
        return Finding("unit_mismatch", Decimal("0.00"),
                       f"Invoice unit {line.get('unit')} differs from contract unit {item['unit']} "
                       f"(pack: {line.get('pack_description') or 'n/a'} vs {item.get('pack_size') or 'n/a'}); price not compared. "
                       "Needs human conversion; not claimable as-is.",
                       line["line_no"], needs_ai_triage=True, confidence=Decimal("0.5"))
    return None


def detect_surcharges(inv: dict, contract: dict | None) -> list[Finding]:
    """rebate_rules entries of type 'surcharge_cap': {line_type, cap_amount?, cap_pct?, source_clause}."""
    if not contract:
        return []
    out = []
    product_total = sum((D_or_none(l.get("extended_total")) or Decimal(0)) for l in inv["lines"] if l.get("line_type") == "product")
    for rule in contract.get("rebate_rules") or []:
        if rule.get("type") != "surcharge_cap":
            continue
        charged = sum((D_or_none(l.get("extended_total")) or Decimal(0)) for l in inv["lines"] if l.get("line_type") == rule["line_type"])
        if charged <= 0:
            continue
        caps = []
        if rule.get("cap_amount") is not None:
            caps.append(D(rule["cap_amount"]))
        if rule.get("cap_pct") is not None:
            caps.append(cents(product_total * D(rule["cap_pct"]) / 100))
        if not caps:
            continue
        cap = min(caps)
        if charged > cap:
            out.append(Finding("surcharge_excess", cents(charged - cap),
                               f"{rule['line_type']} charges {charged} exceed contract cap {cap}.",
                               expected=cap, billed=charged, source_clause=rule.get("source_clause")))
    return out
