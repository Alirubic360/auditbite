"""Claims: only APPROVED, positive, unclaimed discrepancies of ONE vendor; letter, PDF, send, recovery, follow-ups."""
from __future__ import annotations
import datetime as dt, html, re
from decimal import Decimal
from typing import Protocol
from .money import D, cents


class ClaimError(ValueError):
    pass


class EmailSender(Protocol):
    def send(self, to: str, subject: str, body: str, pdf: bytes | None = None, pdf_name: str = "claim.pdf") -> str: ...


NUM = re.compile(r"\d[\d,]*\.?\d*")


def numbers_in(text: str) -> set[Decimal]:
    out = set()
    for m in NUM.findall(text):
        try:
            out.add(Decimal(m.replace(",", "").rstrip(".")))
        except Exception:  # noqa: BLE001
            pass
    return out


def letter_numbers_ok(letter: str, allowed: set[Decimal]) -> bool:
    """Guard for AI-written letters: every figure in the text must come from the supplied data (or be the 14-day deadline)."""
    return numbers_in(letter) <= (allowed | {Decimal(14)})


def build_payload(rows: list[dict], vendor: dict, contract_refs: list[str]) -> dict:
    return {"vendor": vendor["name"], "contract_references": contract_refs, "payment_terms_days": vendor["payment_terms_days"],
            "total": str(sum((D(r["amount"]) for r in rows), Decimal(0))),
            "discrepancies": [{"invoice_number": r["invoice_number"], "invoice_date": str(r["invoice_date"]), "type": r["type"],
                               "item": r.get("description"), "billed": str(r["billed"]) if r["billed"] is not None else None,
                               "contract_price": str(r["expected"]) if r["expected"] is not None else None,
                               "quantity": str(r["qty"]) if r.get("qty") is not None else None, "amount": str(r["amount"]),
                               "clause": r.get("source_clause")} for r in rows]}


CATEGORY = {"price_variance": "price above agreed contract price", "duplicate_invoice": "duplicate invoice",
            "math_error": "arithmetic variance on the invoice", "surcharge_excess": "freight/surcharge above contract cap",
            "missing_discount": "agreed discount not applied", "substitution": "item substitution", "unit_mismatch": "unit of measure variance",
            "tax_error": "tax variance"}


def template_letter(payload: dict, currency: str = "USD") -> dict:
    cats = sorted({CATEGORY.get(d["type"], d["type"]) for d in payload["discrepancies"]})
    refs = ", ".join(payload["contract_references"]) or "our supply agreement"
    body = (f"Dear {payload['vendor']} accounts team,\n\n"
            f"During a routine review of your invoices we identified billing variances totaling {payload['total']} {currency}, "
            f"detailed in the table below.\n\n"
            f"The variances fall under: {'; '.join(cats)}. Each line references the applicable terms in {refs}.\n\n"
            f"We kindly request a credit memo or refund for this amount within 14 calendar days. "
            f"If you dispute any line, please reply with the supporting documents and we will review it promptly.\n\n"
            f"Thank you for your prompt attention.\n")
    return {"subject": f"Billing variance claim - {payload['vendor']} - {payload['total']} {currency}", "body": body}


def render_html(letter: dict, payload: dict, org_name: str) -> str:
    e = html.escape
    rows = "".join(
        f"<tr><td>{e(d['invoice_number'] or '')}</td><td>{e(d['invoice_date'])}</td><td>{e(d['item'] or CATEGORY.get(d['type'], d['type']))}</td>"
        f"<td class=n>{e(d['billed'] or '')}</td><td class=n>{e(d['contract_price'] or '')}</td><td class=n>{e(d['quantity'] or '')}</td>"
        f"<td class=n>{e(d['amount'])}</td><td>{e(d['clause'] or '')}</td></tr>" for d in payload["discrepancies"])
    body = "".join(f"<p>{e(p)}</p>" for p in letter["body"].split("\n\n"))
    return f"""<html><head><meta charset=utf-8><style>
body{{font-family:sans-serif;font-size:11px;margin:30px}}h1{{font-size:16px}}table{{border-collapse:collapse;width:100%}}
td,th{{border:1px solid #bbb;padding:4px;text-align:left}}.n{{text-align:right}}</style></head><body>
<h1>{e(letter['subject'])}</h1><p><b>From:</b> {e(org_name)}</p>{body}
<table><tr><th>Invoice</th><th>Date</th><th>Item</th><th>Billed</th><th>Contract</th><th>Qty</th><th>Amount</th><th>Clause</th></tr>{rows}
<tr><td colspan=6><b>Total claimed</b></td><td class=n><b>{e(payload['total'])}</b></td><td></td></tr></table></body></html>"""


def render_pdf(html_doc: str) -> bytes:
    from weasyprint import HTML
    return HTML(string=html_doc).write_pdf()


def create_claim(conn, org_id: str, vendor_id: str, discrepancy_ids: list[str], storage, letter_writer=None, language="en") -> dict:
    if not discrepancy_ids:
        raise ClaimError("no discrepancies selected")
    rows = conn.execute("""
        select d.id, d.type, d.amount, d.billed, d.expected, d.source_clause, d.review_status, d.confidence,
               i.invoice_number, i.invoice_date, i.vendor_id, i.currency, l.description, l.qty,
               exists(select 1 from claim_items ci where ci.discrepancy_id = d.id) as already_claimed
        from discrepancies d join invoices i on i.id = d.invoice_id left join invoice_lines l on l.id = d.line_id
        where d.id = any(%s) and d.org_id = %s order by i.invoice_date, i.invoice_number""", (discrepancy_ids, org_id)).fetchall()
    if len(rows) != len(set(discrepancy_ids)):
        raise ClaimError("unknown discrepancy id")
    for r in rows:
        if r["review_status"] != "approved":
            raise ClaimError("every discrepancy must be human-approved before it can be claimed")
        if r["already_claimed"]:
            raise ClaimError("a discrepancy is already part of another claim")
        if D(r["amount"]) <= 0:
            raise ClaimError("discrepancy has no claimable amount; set an amount at review time")
        if str(r["vendor_id"]) != str(vendor_id):
            raise ClaimError("all discrepancies in a claim must belong to the same vendor")
    vendor = conn.execute("select * from vendors where id=%s and org_id=%s", (vendor_id, org_id)).fetchone()
    org = conn.execute("select name from organizations where id=%s", (org_id,)).fetchone()
    contract_refs = [r["reference"] for r in conn.execute("select reference from contracts where vendor_id=%s and org_id=%s and reference is not null", (vendor_id, org_id)).fetchall()]
    payload = build_payload(rows, vendor, contract_refs)
    total = cents(sum((D(r["amount"]) for r in rows), Decimal(0)))
    currency = rows[0]["currency"] or "USD"
    letter = template_letter(payload, currency)
    used_lang = "en"
    if letter_writer is not None and language != "en":
        try:
            cand = letter_writer.write_letter(payload, language)
            allowed = set()
            for v in [payload["total"], *payload["contract_references"]]:
                allowed |= numbers_in(str(v))
            for d in payload["discrepancies"]:
                for v in d.values():
                    allowed |= numbers_in(str(v or ""))
            if letter_numbers_ok(cand["subject"] + " " + cand["body"], allowed):
                letter, used_lang = cand, language
        except Exception:  # noqa: BLE001 - fall back to the verified English template
            pass
    language = used_lang
    claim = conn.execute("""insert into claims (org_id, vendor_id, total_claimed, letter_subject, letter_body, language)
                            values (%s,%s,%s,%s,%s,%s) returning *""", (org_id, vendor_id, total, letter["subject"], letter["body"], language)).fetchone()
    for r in rows:
        conn.execute("insert into claim_items (claim_id, discrepancy_id, org_id, amount) values (%s,%s,%s,%s)", (claim["id"], r["id"], org_id, r["amount"]))
    pdf = render_pdf(render_html(letter, payload, org["name"]))
    path = f"{org_id}/claims/{claim['id']}.pdf"
    storage.put(path, pdf)
    conn.execute("update claims set pdf_path=%s where id=%s", (path, claim["id"]))
    conn.execute("""update invoices set status='claimed' where org_id=%s and status in ('needs_review','audited')
                    and id in (select invoice_id from discrepancies where id = any(%s))""", (org_id, discrepancy_ids))
    return conn.execute("select * from claims where id=%s", (claim["id"],)).fetchone()


def send_claim(conn, org_id, claim_id, storage, sender: EmailSender, now: dt.datetime | None = None) -> dict:
    now = now or dt.datetime.now(dt.timezone.utc)
    claim = conn.execute("select * from claims where id=%s and org_id=%s for update", (claim_id, org_id)).fetchone()
    if not claim:
        raise ClaimError("claim not found")
    if claim["status"] != "draft":
        raise ClaimError("only draft claims can be sent")
    vendor = conn.execute("select * from vendors where id=%s", (claim["vendor_id"],)).fetchone()
    if not vendor["contact_email"]:
        raise ClaimError("vendor has no contact email")
    pid = sender.send(vendor["contact_email"], claim["letter_subject"], claim["letter_body"], storage.get(claim["pdf_path"]), f"claim-{claim['id']}.pdf")
    conn.execute("""insert into outbound_emails (org_id, claim_id, kind, to_email, subject, body, provider, provider_id)
                    values (%s,%s,'claim',%s,%s,%s,%s,%s)""", (org_id, claim_id, vendor["contact_email"], claim["letter_subject"], claim["letter_body"], type(sender).__name__, pid))
    conn.execute("update claims set status='sent', sent_at=%s, due_at=%s where id=%s", (now, now + dt.timedelta(days=14), claim_id))
    for day in (7, 14):
        conn.execute("insert into followups (org_id, claim_id, day, due_at) values (%s,%s,%s,%s)", (org_id, claim_id, day, now + dt.timedelta(days=day)))
    return conn.execute("select * from claims where id=%s", (claim_id,)).fetchone()


def record_credit(conn, org_id, claim_id, amount, status="credited") -> dict:
    """Log the vendor's credit memo. Success fee is computed on the amount actually RECOVERED, not claimed."""
    amount = cents(D(amount))
    if status not in ("credited", "paid", "disputed", "written_off"):
        raise ClaimError("invalid status")
    claim = conn.execute("select * from claims where id=%s and org_id=%s for update", (claim_id, org_id)).fetchone()
    if not claim:
        raise ClaimError("claim not found")
    if claim["status"] == "draft":
        raise ClaimError("claim has not been sent")
    if claim.get("billing_invoice_id"):
        raise ClaimError("this recovery has already been billed; it can no longer be changed")
    if amount < 0 or amount > D(claim["total_claimed"]):
        raise ClaimError("recovered amount must be between 0 and the amount claimed")
    rate = D(conn.execute("select success_fee_rate from organizations where id=%s", (org_id,)).fetchone()["success_fee_rate"])
    fee = cents(amount * rate) if status in ("credited", "paid") else Decimal(0)
    conn.execute("update claims set status=%s, recovered_amount=%s, fee_amount=%s, recovered_at=case when %s > 0 then coalesce(recovered_at, now()) else null end where id=%s",
                 (status, amount, fee, fee, claim_id))
    if status in ("credited", "paid"):
        conn.execute("""update followups set status='cancelled' where claim_id=%s and status='scheduled'""", (claim_id,))
        conn.execute("""update invoices set status='recovered' where org_id=%s and id in
                        (select d.invoice_id from claim_items ci join discrepancies d on d.id=ci.discrepancy_id where ci.claim_id=%s)""", (org_id, claim_id))
    return conn.execute("select * from claims where id=%s", (claim_id,)).fetchone()


FOLLOWUP = {
    7: ("Reminder: billing variance claim {ref}", "Hello,\n\nA friendly reminder about our claim of {amount} sent on {sent}. Could you please confirm the expected date for the credit memo or refund?\n\nThank you."),
    14: ("Follow-up: billing variance claim {ref} - payment terms", "Hello,\n\nWe have not yet received a response to our claim of {amount} sent on {sent}. Under the agreed payment terms ({terms} days), we would like to resolve this now. Could we schedule a call this week?\n\nThank you."),
}


def run_followups(conn, sender: EmailSender, now: dt.datetime | None = None) -> int:
    """Send due follow-ups for claims still open. Deterministic templates: no legal threats, <90 words."""
    now = now or dt.datetime.now(dt.timezone.utc)
    due = conn.execute("""select f.*, c.total_claimed, c.sent_at, c.vendor_id, c.status as claim_status from followups f
                          join claims c on c.id=f.claim_id where f.status='scheduled' and f.due_at <= %s
                          order by f.due_at for update of f skip locked""", (now,)).fetchall()
    n = 0
    for f in due:
        if f["claim_status"] not in ("sent", "disputed"):
            conn.execute("update followups set status='cancelled' where id=%s", (f["id"],))
            continue
        v = conn.execute("select * from vendors where id=%s", (f["vendor_id"],)).fetchone()
        subj, body = FOLLOWUP[f["day"]]
        ref = str(f["claim_id"])[:8]
        fill = dict(ref=ref, amount=f"{f['total_claimed']}", sent=f["sent_at"].date().isoformat(), terms=v["payment_terms_days"])
        subj, body = subj.format(**fill), body.format(**fill)
        pid = sender.send(v["contact_email"], subj, body)
        conn.execute("""insert into outbound_emails (org_id, claim_id, kind, to_email, subject, body, provider, provider_id)
                        values (%s,%s,%s,%s,%s,%s,%s,%s)""", (f["org_id"], f["claim_id"], f"followup_{f['day']}", v["contact_email"], subj, body, type(sender).__name__, pid))
        conn.execute("update followups set status='sent', sent_at=%s where id=%s", (now, f["id"]))
        n += 1
    return n
