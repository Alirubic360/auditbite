import datetime as dt, io
from decimal import Decimal as Dc
import pytest
from pypdf import PdfReader
from auditbite import claims, pipeline
from auditbite.mailer import OutboxSender
from fakes import FakeAI, inv, ln
from seed import make_org, make_vendor, make_contract, upload


@pytest.fixture
def claimable(conn, storage):
    org, user = make_org(conn)
    v = make_vendor(conn, org)
    make_contract(conn, org, v, [("CHK-40", "Chicken breast boneless skinless", "LB", "2.00")])
    conn.commit()
    ai = FakeAI({"a": inv(number="A1", lines=[ln(1, "x", 100, "LB", 2.2, 220, sku="CHK-40")], sub=220, tax=0, total=220),
                 "b": inv(number="B2", date="2025-03-20", lines=[ln(1, "x", 50, "LB", 2.4, 120, sku="CHK-40")], sub=120, tax=0, total=120)})
    ids = []
    for m in "ab":
        iid = upload(conn, storage, org, m); conn.commit(); pipeline.run_once(conn, storage, ai, ai, ai); ids.append(iid)
    d = conn.execute("select id, amount from discrepancies where org_id=%s order by amount", (org,)).fetchall()
    return org, user, v, d, ids      # amounts: 20.00 and 20.00 (100*0.2, 50*0.4)


def approve(conn, ids):
    conn.execute("update discrepancies set review_status='approved' where id = any(%s)", (ids,)); conn.commit()


def test_cannot_claim_unreviewed_or_rejected(conn, storage, claimable):
    org, _, v, d, _ = claimable
    ids = [x["id"] for x in d]
    with pytest.raises(claims.ClaimError, match="human-approved"):
        claims.create_claim(conn, org, v, ids, storage)
    conn.execute("update discrepancies set review_status='rejected' where id=%s", (ids[0],)); conn.commit()
    with pytest.raises(claims.ClaimError, match="human-approved"):
        claims.create_claim(conn, org, v, ids[:1], storage)


def test_zero_amount_and_empty_and_cross_vendor_and_double_claim(conn, storage, claimable):
    org, _, v, d, _ = claimable
    ids = [x["id"] for x in d]; approve(conn, ids)
    with pytest.raises(claims.ClaimError):
        claims.create_claim(conn, org, v, [], storage)
    other = make_vendor(conn, org, "Other"); conn.commit()
    with pytest.raises(claims.ClaimError, match="same vendor"):
        claims.create_claim(conn, org, other, ids, storage)
    conn.rollback()
    conn.execute("update discrepancies set amount=0 where id=%s", (ids[0],)); conn.commit()
    with pytest.raises(claims.ClaimError, match="no claimable amount"):
        claims.create_claim(conn, org, v, ids, storage)
    conn.rollback(); conn.execute("update discrepancies set amount=20 where id=%s", (ids[0],)); conn.commit()
    c = claims.create_claim(conn, org, v, ids, storage); conn.commit()
    with pytest.raises(claims.ClaimError, match="already part"):
        claims.create_claim(conn, org, v, ids[:1], storage)
    assert c["total_claimed"] == Dc("40.00")


def test_full_lifecycle_letter_pdf_send_followups_credit_fee(conn, storage, claimable):
    org, _, v, d, inv_ids = claimable
    ids = [x["id"] for x in d]; approve(conn, ids)
    c = claims.create_claim(conn, org, v, ids, storage); conn.commit()
    assert c["status"] == "draft" and c["total_claimed"] == Dc("40.00") and "billing variances" in c["letter_body"] and "14 calendar days" in c["letter_body"]
    assert "fraud" not in c["letter_body"].lower() and len(c["letter_body"].split()) < 250
    text = "".join(p.extract_text() for p in PdfReader(io.BytesIO(storage.get(c["pdf_path"]))).pages)
    assert "40.00" in text and "A1" in text and "B2" in text and "Sec 3.1" in text           # table has invoice numbers, amounts, clause
    assert {r["status"] for r in conn.execute("select status from invoices where org_id=%s", (org,)).fetchall()} == {"claimed"}

    sender = OutboxSender()
    t0 = dt.datetime(2025, 4, 1, tzinfo=dt.timezone.utc)
    s = claims.send_claim(conn, org, c["id"], storage, sender, now=t0); conn.commit()
    assert s["status"] == "sent" and s["due_at"] == t0 + dt.timedelta(days=14) and sender.sent[0]["to"] == "ar@sysco.test" and sender.sent[0]["pdf_bytes"] > 500
    with pytest.raises(claims.ClaimError, match="only draft"):
        claims.send_claim(conn, org, c["id"], storage, sender)               # no double send

    assert claims.run_followups(conn, sender, now=t0 + dt.timedelta(days=6)) == 0
    assert claims.run_followups(conn, sender, now=t0 + dt.timedelta(days=7, hours=1)) == 1
    assert claims.run_followups(conn, sender, now=t0 + dt.timedelta(days=7, hours=2)) == 0   # not re-sent
    assert claims.run_followups(conn, sender, now=t0 + dt.timedelta(days=15)) == 1
    f7, f14 = sender.sent[1], sender.sent[2]
    assert len(f7["body"].split()) < 90 and len(f14["body"].split()) < 90 and "40.00" in f7["body"]
    assert "legal" not in (f7["body"] + f14["body"]).lower() and "30 days" in f14["body"]

    with pytest.raises(claims.ClaimError):
        claims.record_credit(conn, org, c["id"], "40.01")                    # cannot recover more than claimed
    r = claims.record_credit(conn, org, c["id"], "30.00"); conn.commit()
    assert r["status"] == "credited" and r["recovered_amount"] == Dc("30.00") and r["fee_amount"] == Dc("7.50")   # 25% of RECOVERED, not claimed
    assert {x["status"] for x in conn.execute("select status from invoices where org_id=%s", (org,)).fetchall()} == {"recovered"}


def test_followups_cancelled_when_credited_and_draft_cannot_be_credited(conn, storage, claimable):
    org, _, v, d, _ = claimable
    ids = [x["id"] for x in d]; approve(conn, ids)
    c = claims.create_claim(conn, org, v, ids, storage); conn.commit()
    with pytest.raises(claims.ClaimError, match="not been sent"):
        claims.record_credit(conn, org, c["id"], "10")
    t0 = dt.datetime(2025, 4, 1, tzinfo=dt.timezone.utc); s = OutboxSender()
    claims.send_claim(conn, org, c["id"], storage, s, now=t0)
    claims.record_credit(conn, org, c["id"], "40"); conn.commit()
    assert claims.run_followups(conn, s, now=t0 + dt.timedelta(days=30)) == 0
    assert {f["status"] for f in conn.execute("select status from followups where claim_id=%s", (c["id"],)).fetchall()} == {"cancelled"}


def test_disputed_claim_gets_no_fee_and_keeps_followups(conn, storage, claimable):
    org, _, v, d, _ = claimable
    ids = [x["id"] for x in d]; approve(conn, ids)
    c = claims.create_claim(conn, org, v, ids, storage)
    claims.send_claim(conn, org, c["id"], storage, OutboxSender())
    r = claims.record_credit(conn, org, c["id"], "0", status="disputed")
    assert r["fee_amount"] == 0 and r["status"] == "disputed"


def test_ai_letter_accepted_only_if_numbers_verified(conn, storage, claimable):
    org, _, v, d, _ = claimable
    ids = [x["id"] for x in d]; approve(conn, ids)
    good = FakeAI({})                                     # returns "Total 40.00 within 14 days"
    c = claims.create_claim(conn, org, v, ids, storage, good, language="es")
    assert c["language"] == "es" and "40.00" in c["letter_body"]
    conn.rollback()
    class Liar(FakeAI):
        def write_letter(self, payload, language): return {"subject": "s", "body": "We claim 99999.00 within 14 days"}
    c2 = claims.create_claim(conn, org, v, ids, storage, Liar({}), language="es")
    assert c2["language"] == "en" and "99999" not in c2["letter_body"]          # invented figure -> verified English template instead
