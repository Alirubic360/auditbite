"""Quality eval (spec section 9). Run on a hand-labelled set of 50-100 REAL invoices before every customer
and after every prompt/model change. Metrics: numeric extraction accuracy, line-match accuracy, claim
precision, recall, cost and latency per invoice.

Dataset (JSON list), one case per invoice:
  {"id": "...", "file": "path/to.pdf", "media_type": "application/pdf",
   "expected": {"invoice_number": "...", "printed_total": "123.45", "lines": [{"line_no":1,"qty":"10","unit_price":"2.2","extended_total":"22"}]},
   "expected_findings": [{"type":"price_variance","line_no":1}]}
"""
from __future__ import annotations
import json, sys, time
from decimal import Decimal
from .extraction import normalize_invoice
from .money import D_or_none

NUMERIC_LINE = ("qty", "unit_price", "extended_total")
NUMERIC_HEAD = ("printed_subtotal", "printed_tax", "printed_total")


def score_extraction(expected: dict, got: dict) -> tuple[int, int]:
    """(correct, total) over numeric fields. A missing/extra line counts every expected field as wrong."""
    ok = tot = 0
    for k in NUMERIC_HEAD:
        if k in expected:
            tot += 1
            ok += D_or_none(expected[k]) == got.get(k)
    got_lines = {l["line_no"]: l for l in got["lines"]}
    for el in expected.get("lines", []):
        gl = got_lines.get(el["line_no"], {})
        for k in NUMERIC_LINE:
            if k in el:
                tot += 1
                ok += D_or_none(el[k]) == gl.get(k)
    return ok, tot


def score_findings(expected: list[dict], got: list[dict]) -> tuple[int, int, int]:
    """(true_positive, false_positive, false_negative), matching on (type, line_no)."""
    e = {(x["type"], x.get("line_no")) for x in expected}
    g = {(x["type"], x.get("line_no")) for x in got}
    return len(e & g), len(g - e), len(e - g)


def summarize(cases: list[dict], price_in_per_mtok=None, price_out_per_mtok=None, usage=None) -> dict:
    ok = sum(c["numeric_ok"] for c in cases); tot = sum(c["numeric_total"] for c in cases)
    tp = sum(c["tp"] for c in cases); fp = sum(c["fp"] for c in cases); fn = sum(c["fn"] for c in cases)
    out = {"invoices": len(cases),
           "numeric_accuracy": (ok / tot) if tot else None, "numeric_target": 0.98,
           "claim_precision": (tp / (tp + fp)) if tp + fp else None, "claim_precision_target": 0.95,
           "recall": (tp / (tp + fn)) if tp + fn else None, "recall_target": 0.70,
           "avg_seconds_per_invoice": (sum(c["seconds"] for c in cases) / len(cases)) if cases else None}
    if usage and price_in_per_mtok is not None and price_out_per_mtok is not None and cases:
        cost = usage["input_tokens"] / 1e6 * price_in_per_mtok + usage["output_tokens"] / 1e6 * price_out_per_mtok
        out["cost_per_invoice_usd"] = cost / len(cases)
    out["meets_targets"] = bool(out["numeric_accuracy"] is not None and out["numeric_accuracy"] >= 0.98
                                and out["claim_precision"] is not None and out["claim_precision"] >= 0.95
                                and out["recall"] is not None and out["recall"] >= 0.70)
    return out


def evaluate(dataset: list[dict], extract_fn, find_fn) -> list[dict]:
    """extract_fn(case)->raw extraction ; find_fn(normalized_invoice)->[{'type','line_no'}] (pure detection, no DB)."""
    results = []
    for case in dataset:
        t0 = time.perf_counter()
        norm = normalize_invoice(extract_fn(case))
        secs = time.perf_counter() - t0
        ok, tot = score_extraction(case["expected"], norm)
        tp, fp, fn = score_findings(case.get("expected_findings", []), find_fn(norm))
        results.append({"id": case["id"], "numeric_ok": ok, "numeric_total": tot, "tp": tp, "fp": fp, "fn": fn, "seconds": secs})
    return results


def main(argv=None):   # pragma: no cover - requires real files and an API key
    import argparse
    from .ai import AnthropicAI
    from .detection import validate_math
    ap = argparse.ArgumentParser(description="Run the AuditBite quality eval against the live model.")
    ap.add_argument("dataset"); ap.add_argument("--price-in", type=float, help="USD per million input tokens (check Anthropic pricing)")
    ap.add_argument("--price-out", type=float, help="USD per million output tokens")
    a = ap.parse_args(argv)
    data = json.load(open(a.dataset))
    ai = AnthropicAI()
    find = lambda inv: [{"type": f.type, "line_no": f.line_no} for f in validate_math(inv)[0]]
    res = evaluate(data, lambda c: ai.extract_invoice(open(c["file"], "rb").read(), c.get("media_type", "application/pdf")), find)
    print(json.dumps(summarize(res, a.price_in, a.price_out, ai.usage), indent=2, default=str))


if __name__ == "__main__":
    main()
