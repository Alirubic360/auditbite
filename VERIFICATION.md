# Verification report (2026-11-23)

Environment: Postgres 16 (local), Python 3.13, Node 22, headless Chromium. **No Anthropic, Supabase or Resend credentials were available.**

## What was actually executed
| Check | Result |
|---|---|
| Schema applies on Postgres 16 | pass |
| `pytest` (60 tests; fresh DB per test) | **60 passed** |
| Tenant isolation as the real `authenticated` role on all 14 tables: no cross-org SELECT; cross-org INSERT blocked; UPDATE/DELETE affect 0 rows; cross-tenant FK blocked; `anon` has no access; audit log/membership not writable | pass |
| Detection maths (Decimal; tolerance boundary; under-billing never claimed; formula prices skipped; 2-cent math tolerance; surcharge caps) | pass |
| Pipeline: exact SKU / trigram / AI band / hallucinated AI id rejected / duplicates (number and total+date, 90-day limit) / low confidence → review / unknown vendor / expired contract / failure handling / idempotent reprocess | pass |
| Queue: two workers never take the same invoice (SKIP LOCKED); crashed-worker recovery | pass |
| Claims: only approved, positive, unclaimed, same-vendor; PDF generated and text-checked; no double send; follow-ups at day 7/14 exactly once; fee = 25 % of *recovered*; credit > claimed rejected | pass |
| API over HTTP: 401/403 paths, forged JWT, org B cannot read/patch/approve/send/credit org A's data, signed-URL tamper/expiry, upload sniffing, webhook secret | pass |
| Anthropic/Resend adapters: request shape (forced tool, model, document/image block, injection rule in system prompt), truncated output rejected | pass (fake transport) |
| Frontend `tsc` + `next build` | pass |
| Playwright E2E against live stack (API + worker + Postgres + built Next.js) — 25 checks: onboarding → vendor/location → CSV contract → upload → worker audit → problem line highlighted → approve → claim → PDF → send (exactly 1 email, after click) → credit → fee $5.00 → dashboard; dark mode, RTL (fa), mobile no-overflow, org B isolation via UI, no console errors | **25/25** |

Bugs found and fixed *by this verification*: AI-letter fallback left the claim labelled with the wrong language; Decimals were serialised as floats by FastAPI; audit/email inserts lacked grants; request models defined inside the app factory were treated as query params; inline-edit input lost focus every keystroke (component remount).

## NOT verified (be explicit)
* **Real extraction/matching accuracy.** The AI was a canned stand-in, so the spec's targets (numeric ≥ 98 %, claim precision ≥ 95 %, recall ≥ 70 %) are **unmeasured**. Run `evaluation.py` on 50–100 labelled real invoices with a real key before any customer.
* Real Supabase Auth/Storage/Realtime (JWTs were minted with the same HS256 mechanism; storage is local disk; polling instead of Realtime), real Resend delivery, and the browser's PDF viewer pane (headless Chromium does not render PDFs; fixture PDFs are not real invoices).
* Non-English claim letters / Persian text in the generated PDF (font coverage untested); only English is template-tested.
* Load, concurrency beyond two workers, and security review/pen-test.
