import datetime as dt, uuid
from decimal import Decimal as Dc
import pytest
from auditbite import pipeline
from auditbite.matching import Match
from fakes import FakeAI, inv, ln
from seed import make_org, make_vendor, make_contract, upload


@pytest.fixture
def world(conn, storage):
    org, user = make_org(conn)
    v = make_vendor(conn, org)
    make_contract(conn, org, v, [("CHK-40", "Chicken breast boneless skinless", "LB", "2.00"), ("OIL-EV", "Extra virgin olive oil", "GAL", "30.00")],
                  rules=[{"type": "surcharge_cap", "line_type": "freight", "cap_amount": 0, "source_clause": "Sec 9: no freight on orders over $500"}])
    conn.commit()
    return org, user, v


def run(conn, storage, org, ai, marker):
    iid = upload(conn, storage, org, marker); conn.commit()
    status = pipeline.run_once(conn, storage, ai, ai, ai)
    return iid, status


def discs(conn, iid):
    return conn.execute("select * from discrepancies where invoice_id=%s order by type, amount", (iid,)).fetchall()


def test_clean_invoice_is_audited_without_discrepancies(conn, storage, world):
    org, *_ = world
    ai = FakeAI({"ok": inv(lines=[ln(1, "x", 100, "LB", 2.0, 200, sku="CHK-40")], sub=200, tax=0, total=200)})
    iid, status = run(conn, storage, org, ai, "ok")
    assert status == "audited" and discs(conn, iid) == []
    row = conn.execute("select * from invoices where id=%s", (iid,)).fetchone()
    assert row["vendor_id"] and row["total"] == Dc("200.00") and row["raw_extraction"]["vendor_name"] == "Sysco"   # raw kept for audit trail
    assert conn.execute("select match_type from invoice_lines where invoice_id=%s", (iid,)).fetchone()["match_type"] == "exact"


def test_price_variance_found_with_decimal_amount_and_evidence(conn, storage, world):
    org, *_ = world
    ai = FakeAI({"pv": inv(lines=[ln(1, "x", 100, "LB", 2.2, 220, sku="chk 40")], sub=220, tax=0, total=220)})   # SKU matched loosely
    iid, status = run(conn, storage, org, ai, "pv")
    d, = discs(conn, iid)
    assert status == "needs_review" and d["type"] == "price_variance" and d["amount"] == Dc("20.00")
    assert d["expected"] == Dc("2.0000") and d["billed"] == Dc("2.2000") and d["source_clause"] == "Sec 3.1" and d["review_status"] == "pending"


def test_trigram_auto_match_and_ai_band_and_no_ai_for_nonmatches(conn, storage, world):
    org, *_ = world
    desc_hi = "Chicken breast boneless skinless"                   # similarity 1.0 -> no AI
    desc_mid = "Chicken breast bnls skinless"                           # ambiguous -> AI
    desc_lo = "Paper napkins 500ct"                                # no candidate > 0.4 -> no AI
    sims = {d: conn.execute("select similarity('Chicken breast boneless skinless', %s) s", (d,)).fetchone()["s"] for d in (desc_hi, desc_mid, desc_lo)}
    assert sims[desc_hi] >= 0.8 and 0.4 <= sims[desc_mid] < 0.8 and sims[desc_lo] < 0.4, sims
    item = conn.execute("select id from contract_items where sku='CHK-40' and org_id=%s", (org,)).fetchone()["id"]
    ai = FakeAI({"m": inv(lines=[ln(1, desc_hi, 10, "LB", 2.5, 25), ln(2, desc_mid, 10, "LB", 2.5, 25), ln(3, desc_lo, 1, "EA", 9, 9)])},
                matches={2: Match(str(item), "equivalent", Dc("0.92"), "same product abbreviated")})
    iid, _ = run(conn, storage, org, ai, "m")
    rows = {r["line_no"]: r for r in conn.execute("select * from invoice_lines where invoice_id=%s", (iid,)).fetchall()}
    assert ai.match_calls == 1                                    # one batched AI call, only for line 2
    assert rows[1]["match_type"] == "equivalent" and rows[2]["match_type"] == "equivalent" and rows[3]["match_type"] == "none"
    assert len([d for d in discs(conn, iid) if d["type"] == "price_variance"]) == 2


def test_ai_unsure_or_hallucinated_candidate_means_no_match_no_claim(conn, storage, world):
    org, *_ = world
    ai = FakeAI({"m": inv(lines=[ln(1, "Chicken breast bnls skinless", 10, "LB", 9.0, 90)])},
                matches={1: Match(str(uuid.uuid4()), "equivalent", Dc("0.99"), "hallucinated id")})
    iid, _ = run(conn, storage, org, ai, "m")
    assert conn.execute("select match_type from invoice_lines where invoice_id=%s", (iid,)).fetchone()["match_type"] == "none"
    assert [d for d in discs(conn, iid) if d["type"] == "price_variance"] == []
    ai2 = FakeAI({"m2": inv(lines=[ln(1, "Chicken breast bnls skinless", 10, "LB", 9.0, 90)])},
                 matches={1: Match(str(conn.execute("select id from contract_items where sku='CHK-40' and org_id=%s", (org,)).fetchone()["id"]), "equivalent", Dc("0.6"), "unsure")})
    iid2, _ = run(conn, storage, org, ai2, "m2")
    assert conn.execute("select match_type from invoice_lines where invoice_id=%s", (iid2,)).fetchone()["match_type"] == "none"


def test_unit_mismatch_and_substitution_are_flagged_never_priced(conn, storage, world):
    org, *_ = world
    item = conn.execute("select id from contract_items where sku='CHK-40' and org_id=%s", (org,)).fetchone()["id"]
    ai = FakeAI({"u": inv(lines=[ln(1, "x", 2, "CS", 80, 160, sku="CHK-40", pack="6/#10"),
                                 ln(2, "Chicken breast bnls skinless", 10, "LB", 9, 90)])},
                matches={2: Match(str(item), "substitution", Dc("0.9"), "lower grade brand")})
    iid, _ = run(conn, storage, org, ai, "u")
    d = {x["type"]: x for x in discs(conn, iid)}
    assert set(d) == {"unit_mismatch", "substitution"} and all(x["amount"] == 0 for x in d.values())
    assert ai.triage_calls == 2 and d["unit_mismatch"]["ai_verdict"]["verdict"] == "unclear"
    assert ai.triage_calls == 2


def test_duplicate_invoice_detected_by_number_and_by_total_date(conn, storage, world):
    org, *_ = world
    base = lambda n, d, t: inv(number=n, date=d, lines=[ln(1, "x", 1, "LB", t, t, sku="CHK-40")], sub=t, tax=0, total=t)
    ai = FakeAI({"a": base("INV-100", "2025-03-01", 500), "b": base("inv 100", "2025-03-09", 500), "c": base("INV-555", "2025-04-01", 500),
                 "d": base("INV-777", "2025-09-01", 500)})
    ia, _ = run(conn, storage, org, ai, "a")
    ib, _ = run(conn, storage, org, ai, "b")
    ic, _ = run(conn, storage, org, ai, "c")
    idd, _ = run(conn, storage, org, ai, "d")
    assert [d["type"] for d in discs(conn, ia) if d["type"] == "duplicate_invoice"] == []   # original is never the duplicate
    assert [(d["type"], d["amount"], d["related_invoice_id"]) for d in discs(conn, ib) if d["type"] == "duplicate_invoice"] == [("duplicate_invoice", Dc("500.00"), ia)]
    assert any(d["type"] == "duplicate_invoice" for d in discs(conn, ic))                # same total within 90 days
    assert not any(d["type"] == "duplicate_invoice" for d in discs(conn, idd))           # same total but >90 days apart


def test_surcharge_and_math_errors_through_pipeline(conn, storage, world):
    org, *_ = world
    ai = FakeAI({"s": inv(lines=[ln(1, "x", 300, "LB", 2.0, 600, sku="CHK-40"), ln(2, "freight", 1, "EA", 45, 45, line_type="freight")],
                          sub=655.0, tax=0, total=655.0)})
    iid, _ = run(conn, storage, org, ai, "s")
    d = {x["type"]: x for x in discs(conn, iid)}
    assert d["surcharge_excess"]["amount"] == Dc("45.00") and "Sec 9" in d["surcharge_excess"]["source_clause"]
    assert d["math_error"]["amount"] == Dc("10.00")                                       # subtotal 655 vs lines 645


def test_low_confidence_line_goes_to_needs_review(conn, storage, world):
    org, *_ = world
    ai = FakeAI({"l": inv(lines=[ln(1, "x", 100, "LB", 2.0, 200, sku="CHK-40", conf=0.6)], sub=200, tax=0, total=200)})
    iid, status = run(conn, storage, org, ai, "l")
    assert status == "needs_review" and conn.execute("select extraction_confidence c from invoices where id=%s", (iid,)).fetchone()["c"] == Dc("0.6")


def test_unknown_vendor_and_expired_contract_warn_not_audit(conn, storage, world):
    org, *_ = world
    ai = FakeAI({"v": inv(vendor="Mystery Co", lines=[ln(1, "x", 1, "LB", 99, 99, sku="CHK-40")]),
                 "e": inv(date="2026-05-01", lines=[ln(1, "x", 1, "LB", 99, 99, sku="CHK-40")])})
    i1, s1 = run(conn, storage, org, ai, "v"); i2, s2 = run(conn, storage, org, ai, "e")
    for i, s in ((i1, s1), (i2, s2)):
        assert s == "needs_review" and discs(conn, i) == []
        assert conn.execute("select jsonb_array_length(warnings) n from invoices where id=%s", (i,)).fetchone()["n"] >= 1


def test_vendor_alias_matches(conn, storage, world):
    org, _, v = world
    conn.execute("update vendors set aliases=array['SYSCO FOODS INC'] where id=%s", (v,)); conn.commit()
    ai = FakeAI({"a": inv(vendor="sysco foods inc", lines=[ln(1, "x", 1, "LB", 2, 2, sku="CHK-40")], sub=2, tax=0, total=2)})
    iid, _ = run(conn, storage, org, ai, "a")
    assert conn.execute("select vendor_id from invoices where id=%s", (iid,)).fetchone()["vendor_id"] == v


def test_extraction_failure_marks_failed_and_retries_are_bounded(conn, storage, world):
    org, *_ = world
    class Boom(FakeAI):
        def extract_invoice(self, d, m): raise RuntimeError("model output truncated")
    iid, status = run(conn, storage, org, Boom({}), "x")
    assert status == "failed" and "truncated" in conn.execute("select error from invoices where id=%s", (iid,)).fetchone()["error"]
    assert pipeline.run_once(conn, storage, Boom({}), None, None) is None             # failed invoices are not picked again


def test_queue_skip_locked_and_stale_recovery(conn, storage, world, dburl):
    import psycopg
    org, *_ = world
    iid = upload(conn, storage, org, "q"); conn.commit()
    other = psycopg.connect(dburl, row_factory=psycopg.rows.dict_row)
    first = pipeline.claim_next(conn)                         # conn holds the row lock (uncommitted)
    assert first["id"] == iid and pipeline.claim_next(other) is None      # worker 2 skips it, no double processing
    conn.commit(); other.rollback()
    assert pipeline.claim_next(other) is None                 # now 'extracting' and fresh
    conn.execute("update invoices set locked_at = now() - interval '1 hour' where id=%s", (iid,)); conn.commit()
    assert pipeline.claim_next(other)["id"] == iid            # crashed worker recovered
    other.commit(); other.close()


def test_reprocess_is_idempotent(conn, storage, world):
    org, *_ = world
    ai = FakeAI({"pv": inv(lines=[ln(1, "x", 100, "LB", 2.2, 220, sku="CHK-40")], sub=220, tax=0, total=220)})
    iid, _ = run(conn, storage, org, ai, "pv")
    conn.execute("update invoices set status='uploaded', attempts=0 where id=%s", (iid,)); conn.commit()
    pipeline.run_once(conn, storage, ai, ai, ai)
    assert len(discs(conn, iid)) == 1 and conn.execute("select count(*) n from invoice_lines where invoice_id=%s", (iid,)).fetchone()["n"] == 1


def test_abbreviated_descriptions_below_0_4_are_not_sent_to_ai(conn, storage, world):
    """Known MVP limit (spec 3.3): heavy abbreviations score < 0.4 and are left unmatched. The fix is vendor aliases."""
    org, *_ = world
    ai = FakeAI({"m": inv(lines=[ln(1, "CKN BRST BNLS SKNLS 40#", 10, "LB", 9.0, 90)])})
    iid, _ = run(conn, storage, org, ai, "m")
    assert ai.match_calls == 0 and conn.execute("select match_type from invoice_lines where invoice_id=%s", (iid,)).fetchone()["match_type"] == "none"
    conn.execute("update contract_items set aliases=array['CKN BRST BNLS SKNLS 40#'] where sku='CHK-40' and org_id=%s", (org,)); conn.commit()
    conn.execute("update invoices set status='uploaded', attempts=0 where id=%s", (iid,)); conn.commit()
    pipeline.run_once(conn, storage, ai, ai, ai)
    assert conn.execute("select match_type from invoice_lines where invoice_id=%s", (iid,)).fetchone()["match_type"] == "equivalent"


def test_case_to_pound_conversion_is_applied_only_when_pack_text_is_unambiguous(conn, storage, world):
    org, *_ = world
    ai = FakeAI({"c": inv(lines=[ln(1, "x", 2, "CS", 44.00, 88.00, sku="CHK-40", pack="4/5 LB"),     # 40 LB at 2.20/LB vs 2.00 -> 8.00
                                 ln(2, "x", 2, "CS", 40.10, 80.20, sku="CHK-40", pack="4/5 LB"),     # 2.005/LB: within 0.5% tolerance
                                 ln(3, "x", 2, "CS", 80.00, 160.0, sku="CHK-40", pack="6/#10")],      # not a weight: cannot convert
                          sub=328.2, tax=0, total=328.2)})
    iid, _ = run(conn, storage, org, ai, "c")
    d = discs(conn, iid)
    pv = [x for x in d if x["type"] == "price_variance"]
    assert len(pv) == 1 and pv[0]["amount"] == Dc("8.00") and "converted CS (4/5 LB) -> LB" in pv[0]["evidence"] and pv[0]["confidence"] < Dc("0.8")
    assert [x["type"] for x in d if x["type"] == "unit_mismatch"] == ["unit_mismatch"]       # only line 3 stays a human mismatch
