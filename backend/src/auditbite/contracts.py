from __future__ import annotations
import csv, io
from decimal import Decimal
from .money import D_or_none


class ContractImportError(ValueError):
    pass


COLUMNS = {"sku", "canonical_name", "unit", "pack_size", "contract_price", "tolerance_pct", "aliases", "source_clause"}


def parse_csv(data: bytes, default_tolerance: Decimal = Decimal("0.5")) -> list[dict]:
    """Manual import. Required headers: canonical_name, unit, contract_price. Aliases separated by '|'."""
    try:
        rdr = csv.DictReader(io.StringIO(data.decode("utf-8-sig")))
    except UnicodeDecodeError as e:
        raise ContractImportError("CSV must be UTF-8") from e
    if not rdr.fieldnames or not {"canonical_name", "unit", "contract_price"} <= {h.strip() for h in rdr.fieldnames}:
        raise ContractImportError("CSV needs headers: canonical_name, unit, contract_price (optional: sku, pack_size, tolerance_pct, aliases, source_clause)")
    out = []
    for n, row in enumerate(rdr, 2):
        row = {k.strip(): (v or "").strip() for k, v in row.items() if k}
        if not any(row.values()):
            continue
        try:
            price = D_or_none(row["contract_price"])
            if price is None or price < 0:
                raise ValueError("price")
            out.append({"sku": row.get("sku") or None, "canonical_name": row["canonical_name"], "unit": row["unit"],
                        "pack_size": D_or_none(row.get("pack_size")), "contract_price": price,
                        "tolerance_pct": D_or_none(row.get("tolerance_pct")) or default_tolerance,
                        "aliases": [a.strip() for a in row.get("aliases", "").split("|") if a.strip()],
                        "needs_index": False, "source_clause": row.get("source_clause") or None})
            if not row["canonical_name"] or not row["unit"]:
                raise ValueError("name/unit")
        except (ValueError, ArithmeticError):
            raise ContractImportError(f"row {n}: invalid canonical_name/unit/contract_price") from None
    if not out:
        raise ContractImportError("CSV has no data rows")
    return out


def draft_from_extraction(raw: dict, default_tolerance: Decimal = Decimal("0.5")) -> dict:
    """AI output -> an editable draft. Formula-priced items keep contract_price=0 and needs_index=true (never audited)."""
    items = []
    for it in raw.get("items", []):
        price = D_or_none(it.get("contract_price"))
        needs = bool(it.get("needs_index")) or price is None
        items.append({"sku": it.get("sku"), "canonical_name": it["canonical_name"], "unit": it["unit"],
                      "pack_size": D_or_none(it.get("pack_size")), "contract_price": price or Decimal(0),
                      "tolerance_pct": default_tolerance, "aliases": [], "needs_index": needs, "source_clause": it.get("source_clause")})
    rules = [{"type": "surcharge_cap", **c} for c in raw.get("surcharge_caps", [])]
    rules += [{"type": "other", **t} for t in raw.get("other_terms", [])]
    return {"vendor_name": raw.get("vendor_name"), "reference": raw.get("reference"), "valid_from": raw.get("valid_from"),
            "valid_to": raw.get("valid_to"), "items": items, "rebate_rules": rules, "warnings": raw.get("warnings", [])}
