# AuditBite — supplier-invoice audit SaaS for restaurant groups

Implementation of the "حسابرس فاکتور رستوران" spec (MVP). **Separate product from the AutoRankLab WordPress site:**
this folder is self-contained and touches nothing in the site, theme, snippets or content.

Core rules from the spec, as enforced in code:
1. **AI reads and classifies; code does the math.** All money is `Decimal`/`numeric`; the model never adds or compares numbers.
2. **Every claim is defensible:** a discrepancy needs a matched contract item (SKU / trigram / AI ≥ 0.8), a computed amount, evidence text and the contract clause.
3. **A human gates every step:** discrepancies are `pending` until approved; claims are built only from approved ones; emails go out only on the "Send to vendor" click.

## Layout
```
db/migrations/0002_platform_admin.sql  platform_admins table (operator access)
db/migrations/0001_schema.sql   tables, composite tenant FKs, RLS, find_duplicate_invoices()
db/test/auth_shim.sql           local stand-in for Supabase auth.uid()/roles (tests only)
backend/src/auditbite/          money, detection, matching, extraction, ai, pipeline, claims, contracts, metrics, evaluation, api, worker
backend/tests/                  62 tests (unit, real-Postgres RLS, pipeline, claims, API, adapters, eval maths)
frontend/                       Next.js 15 + TypeScript + Tailwind: customer app (English only): dashboard, invoices(+[id]), review, contracts, claims(+[id]), settings; `/admin` = Persian RTL platform-admin console
e2e/run.js                      Playwright run against the live stack (29 checks)
```

## Run locally
```bash
# DB (any Postgres 14+; on Supabase just run the migration, skip auth_shim.sql)
psql $DATABASE_URL -f db/test/auth_shim.sql   # plain Postgres only
psql $DATABASE_URL -f db/migrations/0001_schema.sql

cd backend && pip install -e '.[dev]' && cp .env.example .env   # fill in values, export them
uvicorn auditbite.main:app --port 8000      # API
python -m auditbite.worker                  # queue worker (needs ANTHROPIC_API_KEY; without it invoices stay queued)
python -m pytest                            # needs a Postgres reachable via TEST_PG_ADMIN (see tests/conftest.py)

cd ../frontend && cp .env.example .env.local && npm install && npm run build && npm run start
```
Quality eval before each customer / prompt change: `python -m auditbite.evaluation labelled.json --price-in X --price-out Y`
(format in `evaluation.py`; prices are arguments on purpose — check Anthropic's current pricing).

## Languages
* **Customer app: English (Latin) only.** No language switch, LTR.
* **Platform-admin console (`/admin`): Persian, RTL.** Operator-only: all orgs, queue depth, failed invoices (retry), per-org success-fee rate. Grant access with
  `insert into platform_admins (user_id) values ('<auth user id>');` — tenant users get 403 and cannot read the table.
* Claim letters to vendors are English by default.

## Where this deviates from the PDF (and why)
| Spec | Built | Reason |
|---|---|---|
| `unique(org_id, vendor_id, invoice_number)` | plain index + `find_duplicate_invoices()` | the unique constraint would make the duplicate invoice we must *detect* impossible to store |
| RLS "write a policy per table" | `org_members` + `user_org_ids()`; `org_id` on every table; composite FKs `(id, org_id)` | policies are uniform and a row can never reference another tenant's vendor/contract, even with a forged id |
| Celery/Redis or Edge Functions | Postgres queue (`FOR UPDATE SKIP LOCKED`, stale-lock recovery, 3 attempts) | fewer moving parts for an MVP; spec allows either |
| Supabase Realtime | 4–5 s polling | same UX, no extra service |
| `float`-free money (spec) | API returns decimals as strings; browser does no arithmetic | avoids float drift end-to-end |
| Spec's `missing_discount`, `tax_error` | not auto-detected | spec itself says start with 3 types; early-payment/volume rebates need payment & period data. Added `surcharge_excess` (freight/fuel caps) instead |
| 3.4 triage prompt | only for `unit_mismatch`/`substitution`, advisory (never auto-approves) | per spec: gray cases only |
| 3.6 claim letter via AI | deterministic verified English template; AI letter only for other languages and rejected if it contains any figure not in the input | "use only numbers supplied" is enforced, not hoped for |
| 3.7 follow-ups via AI | deterministic templates (<90 words, no legal threats) | fewer failure modes |
| Supabase Storage | local-disk storage behind a `Storage` protocol, HMAC-signed expiring URLs | adapter for Supabase Storage is the remaining integration |
| shadcn/ui | hand-written Tailwind components | no generator step needed; same look |

## Known limits
* Heavily abbreviated invoice text (e.g. `CKN BRST BNLS 40#`) scores < 0.4 trigram similarity and is **not** sent to AI (spec §3.3 band). Fix = add `aliases` on contract items; this is a recall limit, never a false-claim risk.
* Unit conversions (CS ↔ LB) are flagged `unit_mismatch` with amount 0 for a human; the system never claims across units automatically.
* Contract prices defined by formula (`needs_index`) are never audited.
* The success fee is **computed** (25 % of recovered) and stored; invoicing/payment collection is not built.
* Not code, still required by the spec: DPA with customers, lawyer review of the contingency-fee clause, pilots, the 90-day plan.
