from decimal import Decimal as Dc
from auditbite import evaluation as ev
from fakes import inv, ln


def case(cid, got_price):
    return ({"id": cid, "expected": {"printed_total": "22.00", "lines": [{"line_no": 1, "qty": "10", "unit_price": "2.2", "extended_total": "22"}]},
             "expected_findings": []}, inv(lines=[ln(1, "x", 10, "LB", got_price, 22)], total=22.0))


def test_metrics_math_on_synthetic_labels():
    (c1, r1), (c2, r2) = case("a", 2.2), case("b", 2.8)           # second case mis-reads the price
    res = ev.evaluate([c1, c2], lambda c: {"a": r1, "b": r2}[c["id"]], lambda i: [])
    s = ev.summarize(res)
    assert s["numeric_accuracy"] == 7 / 8 and not s["meets_targets"]          # 1 wrong field out of 8
    assert s["claim_precision"] is None and s["recall"] is None                # nothing flagged, nothing expected -> undefined, not 100%


def test_precision_recall_counting():
    assert ev.score_findings([{"type": "price_variance", "line_no": 1}, {"type": "math_error", "line_no": 2}],
                             [{"type": "price_variance", "line_no": 1}, {"type": "price_variance", "line_no": 9}]) == (1, 1, 1)
    s = ev.summarize([{"numeric_ok": 98, "numeric_total": 100, "tp": 19, "fp": 1, "fn": 5, "seconds": 2.0}], 3.0, 15.0, {"input_tokens": 10**6, "output_tokens": 10**5})
    assert s["numeric_accuracy"] == 0.98 and s["claim_precision"] == 0.95 and round(s["recall"], 3) == round(19 / 24, 3)
    assert s["cost_per_invoice_usd"] == 4.5 and s["meets_targets"] is True


def test_missing_line_counts_all_its_fields_wrong():
    ok, tot = ev.score_extraction({"lines": [{"line_no": 3, "qty": "1", "unit_price": "1", "extended_total": "1"}]}, {"lines": []})
    assert (ok, tot) == (0, 3)
