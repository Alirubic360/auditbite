"""Invoice pipeline: extract -> normalise -> match -> detect -> status. Idempotent per invoice."""
from __future__ import annotations
import datetime as dt
from decimal import Decimal
from . import detection
from .db import J
from .extraction import normalize_invoice, LOW_CONF
from .matching import match_invoice_lines, Match
from .money import cents

CLAIM_MIN_CONF = LOW_CONF


def _vendor_id(conn, org_id, name):
    if not name:
        return None
    r = conn.execute("""select id from vendors where org_id=%s and (lower(name)=lower(%s)
                        or lower(%s) = any (select lower(a) from unnest(aliases) a)) limit 1""", (org_id, name, name)).fetchone()
    return r["id"] if r else None


def _contracts_for(conn, org_id, vendor_id, inv_date):
    if not vendor_id:
        return []
    return conn.execute("""select * from contracts where org_id=%s and vendor_id=%s
        and (valid_from is null or %s is null or valid_from <= %s) and (valid_to is null or %s is null or valid_to >= %s)""",
        (org_id, vendor_id, inv_date, inv_date, inv_date, inv_date)).fetchall()


def claim_next(conn):
    """Atomically take one queued invoice (SKIP LOCKED); also recovers jobs stuck >15 min."""
    return conn.execute("""
        update invoices set status='extracting', locked_at=now(), attempts=attempts+1
        where id = (select id from invoices
                    where (status='uploaded' or (status='extracting' and locked_at < now() - interval '15 minutes'))
                      and attempts < 3
                    order by created_at for update skip locked limit 1)
        returning id, org_id""").fetchone()


def process_invoice(conn, storage, extractor, matcher, classifier, invoice_id) -> str:
    inv_row = conn.execute("select * from invoices where id=%s", (invoice_id,)).fetchone()
    org = inv_row["org_id"]
    try:
        data = storage.get(inv_row["file_path"])
        raw = extractor.extract_invoice(data, inv_row["media_type"])
        inv = normalize_invoice(raw)
    except Exception as e:  # noqa: BLE001 - any extraction failure parks the invoice for a human
        conn.execute("update invoices set status='failed', error=%s, locked_at=null where id=%s", (str(e)[:500], invoice_id))
        conn.commit()
        return "failed"

    vendor_id = _vendor_id(conn, org, inv["vendor_name"])
    warnings = list(inv["warnings"])
    if vendor_id is None:
        warnings.append(f"Vendor {inv['vendor_name']!r} not found in this organization; no contract audit performed. Add the vendor and reprocess.")
    contracts = _contracts_for(conn, org, vendor_id, inv["invoice_date"])
    if vendor_id and not contracts:
        warnings.append("No contract valid on the invoice date for this vendor (or invoice date missing); price audit skipped.")
    contract_ids = [c["id"] for c in contracts]

    # Idempotent re-run: replace prior machine output. The API refuses reprocess once a human has reviewed
    # a discrepancy, so no reviewed decision is ever deleted here.
    conn.execute("delete from discrepancies where invoice_id=%s", (invoice_id,))
    conn.execute("delete from invoice_lines where invoice_id=%s", (invoice_id,))

    conn.execute("""update invoices set vendor_id=%s, invoice_number=%s, invoice_date=%s, date_raw=%s, po_number=%s, currency=%s,
                    subtotal=%s, tax=%s, total=%s, extraction_confidence=%s, raw_extraction=%s where id=%s""",
                 (vendor_id, inv["invoice_number"], inv["invoice_date"], inv["date_raw"], inv["po_number"], inv["currency"],
                  inv["printed_subtotal"], inv["printed_tax"], inv["printed_total"], inv["confidence"], J(raw), invoice_id))

    matches: dict[int, Match] = match_invoice_lines(conn, inv["lines"], contract_ids, matcher) if contract_ids else {}
    items = {}
    line_ids: dict[int, str] = {}
    for ln in inv["lines"]:
        m = matches.get(ln["line_no"])
        r = conn.execute("""insert into invoice_lines (org_id, invoice_id, line_no, line_type, description, sku, qty, unit, pack_description,
              unit_price, line_total, confidence, matched_item_id, match_type, match_confidence, match_note)
              values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id""",
            (org, invoice_id, ln["line_no"], ln["line_type"], ln["description"], ln["sku"], ln["qty"], ln["unit"], ln["pack_description"],
             ln["unit_price"], ln["extended_total"], ln["confidence"],
             m.item_id if m else None, m.match_type if m else None, m.confidence if m else None, m.note if m else None)).fetchone()
        line_ids[ln["line_no"]] = r["id"]
        if m and m.item_id:
            items[ln["line_no"]] = conn.execute("select * from contract_items where id=%s", (m.item_id,)).fetchone()

    findings: list[tuple[detection.Finding, str | None]] = []   # (finding, extra note)
    math_f, math_w = detection.validate_math(inv)
    warnings += math_w
    findings += [(f, None) for f in math_f]
    for ln in inv["lines"]:
        m, item = matches.get(ln["line_no"]), items.get(ln["line_no"])
        if not (m and item):
            continue
        if m.match_type == "substitution":
            findings.append((detection.Finding("substitution", Decimal("0.00"), f"Vendor substitution suspected: {m.note}",
                                               ln["line_no"], confidence=m.confidence * ln["confidence"], needs_ai_triage=True), None))
            continue
        um = detection.detect_unit_mismatch(ln, item)
        eff = ln
        if um:
            eff = detection.convert_line(ln, item)
            if eff is None:
                findings.append((um, m.note))
                continue
        pv = detection.detect_price_variance(eff, item)
        if pv and eff is not ln:
            pv.evidence += " [" + eff["converted_note"] + "]"
            pv.confidence = Decimal("0.7")     # conversion-based findings always go through the review queue (<0.8)
        if pv:
            if eff is ln:
                pv.confidence = (m.confidence * ln["confidence"]).quantize(Decimal("0.001"))
            findings.append((pv, m.note))
    # surcharge caps: use the contract that owns matched items (or the only valid contract)
    owner = None
    if items:
        owner_id = next(iter(items.values()))["contract_id"]
        owner = next((c for c in contracts if c["id"] == owner_id), None)
    elif len(contracts) == 1:
        owner = contracts[0]
    findings += [(f, None) for f in detection.detect_surcharges(inv, owner)]

    # duplicate invoice (needs a known vendor)
    if vendor_id:
        dups = conn.execute("select * from find_duplicate_invoices(%s,%s,%s,%s,%s,%s)",
                            (org, vendor_id, inv["printed_total"], inv["invoice_number"], inv["invoice_date"], invoice_id)).fetchall()
        if dups and inv["printed_total"]:
            d = dups[0]
            findings.append((detection.Finding(
                "duplicate_invoice", cents(inv["printed_total"]),
                f"Likely duplicate of invoice {d['invoice_number']} dated {d['invoice_date']} (same vendor, "
                f"{'same number' if d['invoice_number'] and inv['invoice_number'] and d['invoice_number'].upper() == inv['invoice_number'].upper() else 'same total within 90 days'}).",
                related_invoice_id=str(d["id"]), expected=Decimal(0), billed=inv["printed_total"], confidence=Decimal("0.9")), None))

    for f, note in findings:
        verdict = None
        if f.needs_ai_triage and classifier is not None:
            try:
                verdict = classifier.triage({"type": f.type, "evidence": f.evidence, "billed": f.billed, "expected": f.expected})
            except Exception as e:  # noqa: BLE001 - triage is advisory
                warnings.append(f"AI triage failed for line {f.line_no}: {e}")
        conn.execute("""insert into discrepancies (org_id, invoice_id, line_id, related_invoice_id, type, expected, billed, amount, evidence,
                        source_clause, confidence, ai_verdict) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                     (org, invoice_id, line_ids.get(f.line_no), f.related_invoice_id, f.type, f.expected, f.billed, f.amount,
                      f.evidence + (f" [match: {note}]" if note else ""), f.source_clause, f.confidence, J(verdict) if verdict else None))

    needs_review = (inv["confidence"] < LOW_CONF or bool(warnings) or bool(findings) or not inv["lines"])
    status = "needs_review" if needs_review else "audited"
    conn.execute("update invoices set status=%s, warnings=%s, error=null, locked_at=null where id=%s", (status, J(warnings), invoice_id))
    conn.commit()
    return status


def run_once(conn, storage, extractor, matcher, classifier) -> str | None:
    job = claim_next(conn)
    conn.commit()
    if not job:
        return None
    return process_invoice(conn, storage, extractor, matcher, classifier, job["id"])
