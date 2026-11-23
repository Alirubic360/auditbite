from decimal import Decimal as Dc
import pytest
from auditbite import detection as dt, claims, contracts
from auditbite.extraction import normalize_invoice
from auditbite.money import D, cents
from auditbite.units import norm_unit
from fakes import ln, inv


def test_decimal_never_float_noise():
    assert D(0.1) + D(0.2) == Dc("0.3")          # float 0.1+0.2 != 0.3; via str() we are exact
    assert D(None) == 0 and cents(Dc("2.345")) == Dc("2.35") and cents(Dc("2.344")) == Dc("2.34")
    with pytest.raises(ValueError):
        D("abc")

def test_units():
    assert norm_unit("lbs") == "LB" and norm_unit(" Cs. ") == "CS" and norm_unit("each") == "EA" and norm_unit(None) is None

ITEM = {"contract_price": Dc("2.0000"), "tolerance_pct": Dc("0.5"), "unit": "LB", "needs_index": False, "source_clause": "Sec 3"}

def test_price_variance_over_tolerance():
    f = dt.detect_price_variance(ln(1, "x", 100, "LB", 2.2, 220), ITEM)
    assert f.type == "price_variance" and f.amount == Dc("20.00") and f.expected == Dc("2.0000") and f.source_clause == "Sec 3"

def test_price_within_tolerance_and_boundary_not_flagged():
    assert dt.detect_price_variance(ln(1, "x", 100, "LB", 2.01, 201), ITEM) is None      # 0.5% of 2.00 = 0.01 -> 2.01 allowed
    assert dt.detect_price_variance(ln(1, "x", 100, "LB", 2.0101, 201.01), ITEM) is not None
    assert dt.detect_price_variance(ln(1, "x", 100, "LB", 1.5, 150), ITEM) is None       # cheaper is never a claim

def test_formula_price_never_audited():
    assert dt.detect_price_variance(ln(1, "x", 1, "LB", 99, 99), {**ITEM, "needs_index": True}) is None

def test_non_product_lines_not_price_audited():
    assert dt.detect_price_variance(ln(1, "fuel", 1, "EA", 50, 50, line_type="surcharge"), ITEM) is None

def test_unit_mismatch_has_no_amount():
    f = dt.detect_unit_mismatch(ln(1, "x", 2, "CS", 80, 160, pack="4/5 LB"), ITEM)
    assert f.type == "unit_mismatch" and f.amount == 0
    assert dt.detect_unit_mismatch(ln(1, "x", 2, "lbs", 2, 4), ITEM) is None   # LBS == LB

def test_math_error_line_and_totals():
    i = normalize_invoice(inv(lines=[ln(1, "a", 10, "EA", 3.00, 31.00), ln(2, "b", 1, "EA", 5, 5)], sub=36.0, tax=0, total=40.0))
    f, w = dt.validate_math(i)
    kinds = sorted((x.type, x.amount) for x in f)
    assert kinds == [("math_error", Dc("1.00")), ("math_error", Dc("4.00"))]   # line 1: 31 vs 30 ; total 40 vs 36

def test_underbilling_is_warning_not_claim():
    i = normalize_invoice(inv(lines=[ln(1, "a", 10, "EA", 3.00, 29.00)], sub=29.0, tax=0, total=29.0))
    f, w = dt.validate_math(i)
    assert f == [] and any("under-billed" in x for x in w)

def test_math_tolerates_two_cents():
    i = normalize_invoice(inv(lines=[ln(1, "a", 3, "EA", 3.333, 10.01)], sub=10.01, tax=0, total=10.01))
    assert dt.validate_math(i)[0] == []

def test_clean_invoice_no_findings():
    i = normalize_invoice(inv(lines=[ln(1, "a", 2, "EA", 5, 10)], sub=10, tax=0.8, total=10.8))
    assert dt.validate_math(i) == ([], [])

def test_surcharge_cap_amount_and_pct():
    i = normalize_invoice(inv(lines=[ln(1, "p", 1, "EA", 1000, 1000), ln(2, "freight", 1, "EA", 120, 120, line_type="freight")]))
    c1 = {"rebate_rules": [{"type": "surcharge_cap", "line_type": "freight", "cap_amount": 0, "source_clause": "no freight"}]}
    assert dt.detect_surcharges(i, c1)[0].amount == Dc("120.00")
    c2 = {"rebate_rules": [{"type": "surcharge_cap", "line_type": "freight", "cap_pct": 5, "source_clause": "cap 5%"}]}
    assert dt.detect_surcharges(i, c2)[0].amount == Dc("70.00")           # 120 - 5% of 1000
    c3 = {"rebate_rules": [{"type": "surcharge_cap", "line_type": "freight", "cap_pct": 15, "source_clause": "cap 15%"}]}
    assert dt.detect_surcharges(i, c3) == []

def test_normalize_is_defensive():
    n = normalize_invoice({"vendor_name": "V", "invoice_number": "1", "invoice_date": "03/04/2025", "printed_total": "12.50",
                           "lines": [{"line_no": 1, "line_type": "weird", "description": "d", "qty": "abc", "confidence": 7}], "warnings": []})
    assert n["invoice_date"] is None and n["date_raw"] == "03/04/2025" and n["printed_total"] == Dc("12.50")
    l = n["lines"][0]
    assert l["line_type"] == "other" and l["qty"] is None and l["confidence"] == 1 and len(n["warnings"]) >= 3
    assert normalize_invoice({"lines": []})["confidence"] == 0       # no lines => never "confident"

def test_confidence_is_weakest_line():
    n = normalize_invoice(inv(lines=[ln(1, "a", 1, "EA", 1, 1, conf=0.99), ln(2, "b", 1, "EA", 1, 1, conf=0.5)]))
    assert n["confidence"] == Dc("0.5")

def test_letter_number_guard():
    allowed = {Dc("120.00"), Dc("120")}
    assert claims.letter_numbers_ok("Total 120.00 within 14 days", allowed)
    assert not claims.letter_numbers_ok("Total 125.00 within 14 days", allowed)      # invented figure rejected

def test_csv_import():
    ok = contracts.parse_csv(b"sku,canonical_name,unit,contract_price,aliases\nA1,Chicken breast,LB,2.50,CKN BRST|chx\n")
    assert ok[0]["contract_price"] == Dc("2.50") and ok[0]["aliases"] == ["CKN BRST", "chx"] and ok[0]["tolerance_pct"] == Dc("0.5")
    for bad in (b"a,b\n1,2\n", b"canonical_name,unit,contract_price\nX,LB,abc\n", b"canonical_name,unit,contract_price\n"):
        with pytest.raises(contracts.ContractImportError):
            contracts.parse_csv(bad)

def test_contract_draft_formula_price():
    d = contracts.draft_from_extraction({"items": [{"canonical_name": "Beef", "unit": "LB", "contract_price": None, "needs_index": False,
                                                    "source_clause": "USDA + 8%"}], "surcharge_caps": [{"line_type": "freight", "cap_pct": 3, "source_clause": "x"}], "warnings": []})
    assert d["items"][0]["needs_index"] is True and d["rebate_rules"][0]["type"] == "surcharge_cap"
