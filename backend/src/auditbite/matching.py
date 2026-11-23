"""Line -> contract item: exact SKU, then pg_trgm, then AI only for the ambiguous band (0.4-0.8)."""
from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

AUTO_ACCEPT = Decimal("0.8")
AMBIGUOUS_MIN = Decimal("0.4")


@dataclass
class Match:
    item_id: str | None
    match_type: str            # exact | equivalent | substitution | none
    confidence: Decimal
    note: str = ""
    unit_conversion_needed: bool = False


class AIMatcher(Protocol):
    def match(self, lines: list[dict], candidates: dict[int, list[dict]]) -> dict[int, Match]: ...


def sku_key(s: str | None) -> str | None:
    if not s:
        return None
    k = "".join(ch for ch in s.upper() if ch.isalnum())
    return k or None


def pick_candidates(conn, contract_ids: list[str], description: str, limit: int = 5) -> list[dict]:
    if not contract_ids or not description:
        return []
    return conn.execute(
        """select ci.*, greatest(similarity(ci.canonical_name, %(d)s),
                  coalesce((select max(similarity(a, %(d)s)) from unnest(ci.aliases) a), 0)) as sim
           from contract_items ci where ci.contract_id = any(%(c)s)
           order by sim desc limit %(n)s""",
        {"d": description, "c": contract_ids, "n": limit}).fetchall()


def match_invoice_lines(conn, lines: list[dict], contract_ids: list[str], ai: AIMatcher | None) -> dict[int, Match]:
    """Returns {line_no: Match} for product lines."""
    results: dict[int, Match] = {}
    ambiguous: dict[int, list[dict]] = {}
    by_sku: dict[str, dict] = {}
    if contract_ids:
        for it in conn.execute("select * from contract_items where contract_id = any(%s) and sku is not null", (contract_ids,)).fetchall():
            by_sku.setdefault(sku_key(it["sku"]), it)
    for ln in lines:
        if ln.get("line_type", "product") != "product":
            continue
        k = sku_key(ln.get("sku"))
        if k and k in by_sku:
            results[ln["line_no"]] = Match(str(by_sku[k]["id"]), "exact", Decimal("1"), "SKU match")
            continue
        cands = pick_candidates(conn, contract_ids, ln.get("description") or "")
        if not cands:
            results[ln["line_no"]] = Match(None, "none", Decimal("0"), "no catalog candidates")
            continue
        top = Decimal(str(cands[0]["sim"])).quantize(Decimal("0.001"))
        if top >= AUTO_ACCEPT:
            results[ln["line_no"]] = Match(str(cands[0]["id"]), "equivalent", top, f"text similarity {top}")
        elif top >= AMBIGUOUS_MIN and ai is not None:
            ambiguous[ln["line_no"]] = cands
        else:
            results[ln["line_no"]] = Match(None, "none", top, f"best similarity {top} below {AMBIGUOUS_MIN}" if top < AMBIGUOUS_MIN else "ambiguous, no AI configured")
    if ambiguous and ai is not None:
        ai_lines = [l for l in lines if l["line_no"] in ambiguous]
        ai_res = ai.match(ai_lines, {k: [_slim(c) for c in v] for k, v in ambiguous.items()})
        for ln_no, cands in ambiguous.items():
            m = ai_res.get(ln_no)
            allowed = {str(c["id"]) for c in cands}
            # Defensive: unknown candidate id or low confidence => no match (a false match = false claim).
            if m is None or m.item_id not in allowed or m.match_type == "none" or m.confidence < AUTO_ACCEPT:
                results[ln_no] = Match(None, "none", m.confidence if m else Decimal("0"), (m.note if m else "AI returned nothing") + " [unmatched]")
            else:
                results[ln_no] = m
    return results


def _slim(c: dict) -> dict:
    return {"id": str(c["id"]), "sku": c["sku"], "name": c["canonical_name"], "unit": c["unit"],
            "pack_size": str(c["pack_size"]) if c["pack_size"] is not None else None}
