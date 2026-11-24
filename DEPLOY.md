# Deploying AuditBite

Stack: **Supabase** (Postgres, Auth, Storage) · **Vercel** (Next.js) · **Fly.io** (API + worker) · **Resend** (email).

## 1. Supabase
1. Create a project. Storage → create a **private** bucket named `auditbite`.
2. Apply migrations with the **direct** connection string (not the pooler):
   `DATABASE_URL=... scripts/migrate.sh`  (do not run `db/test/auth_shim.sql`; Supabase has real `auth`).
3. Auth → enable email/password. Copy: project URL, anon key, service-role key. Tokens: the API accepts both legacy HS256 (set `SUPABASE_JWT_SECRET`) and the newer asymmetric ES256/RS256 signing keys (verified via `SUPABASE_URL`'s JWKS). Set both; whichever your project uses will work.
4. Make yourself platform admin (after signing up once in the app):
   `insert into platform_admins (user_id) values ('<your auth.users id>');`

## 2. API + worker (Fly.io)
```bash
fly launch --no-deploy --copy-config      # uses fly.toml; pick a unique app name
fly secrets set \
  DATABASE_URL='postgresql://…supabase direct connection…' \
  SUPABASE_JWT_SECRET=… SUPABASE_URL=https://<ref>.supabase.co SUPABASE_SERVICE_KEY=… SUPABASE_BUCKET=auditbite \
  ANTHROPIC_API_KEY=… RESEND_API_KEY=… AUDITBITE_FROM_EMAIL='AuditBite <claims@yourdomain>' \
  INBOUND_WEBHOOK_SECRET=$(openssl rand -hex 24) ALLOWED_ORIGINS=https://app.yourdomain
fly deploy
fly scale count web=1 worker=1
```
`AUDITBITE_ENV=production` is set in the image. With Supabase Storage configured, no local disk is used (machines stay stateless).
The worker without `ANTHROPIC_API_KEY` leaves invoices queued instead of failing them.

## 2b. Crypto payments (BTCPay Server)
Customers are billed monthly **in USD** (subscription per location + success fee on money actually recovered) and pay in crypto; the gateway locks the exchange rate for 60 minutes at checkout.
1. Run a BTCPay Server (self-hosted: docs.btcpayserver.org/Deployment, or a third-party host). Create a **Store** and connect **your own wallet** (xpub / derivation scheme) so funds go straight to you.
2. Store → Access tokens / API keys: create a key with `btcpay.store.cancreateinvoice` and `btcpay.store.canviewinvoices`.
3. Store → Webhooks → Create: URL `https://api.yourdomain/webhooks/btcpay`, secret = a long random string, events: Invoice settled / processing / expired / invalid.
4. `fly secrets set BTCPAY_URL=… BTCPAY_STORE_ID=… BTCPAY_API_KEY=… BTCPAY_WEBHOOK_SECRET=… APP_BASE_URL=https://app.yourdomain`
5. Which coins customers can pay with is decided in BTCPay (Bitcoin/Lightning built in; stablecoins and other coins need BTCPay plugins). Check the plugin before promising USDT.
6. Set prices per organization in the Persian admin console (subscription per location; success-fee %). Invoices are generated automatically by the worker after each month ends (first month has no subscription).
Payment is marked **paid only after the server re-fetches the invoice from BTCPay and the amount, currency and order id match exactly**. Mismatches, double payments, over/under/late payments are listed under "payment events needing review" in the admin console for manual handling (e.g. refunds).
Legal/tax: accepting crypto from US customers has tax, AML and sanctions implications that depend on where you operate. Get professional advice before taking real payments.

## 3. Frontend (Vercel)
Import the repo, **root directory `frontend`**, env vars:
`NEXT_PUBLIC_API_URL=https://api.yourdomain`, `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`.
(Do not set `NEXT_PUBLIC_DEV_JWT` in production.)

## 4. Inbound invoice email (optional)
Point your inbound-email provider's webhook at `POST https://api.yourdomain/webhooks/inbound-email` with header `x-webhook-secret: <INBOUND_WEBHOOK_SECRET>` and JSON `{"to": "invoices+<org-slug>@…", "attachments": [{"filename", "content_base64"}]}` (map your provider's payload to this shape).

## Before the first customer
* Run the quality eval (`python -m auditbite.evaluation`) on 50–100 labelled real invoices; targets in `README.md`.
* DPA with the customer, lawyer review of the success-fee clause (see spec).
* Set Anthropic spend limits; check current per-token pricing.

## Status of these deployment files
Verified: crypto billing (generation, webhook security, re-verification, admin review) through unit/API/browser tests against an in-process fake BTCPay; migrations script (applied twice on Postgres 16, idempotent), clean `pip install .` + API boot + `/healthz` + production secret guard, Supabase Storage request shapes (mock transport), CI definition written. **Not verified:** `docker build` (no Docker daemon in the dev sandbox), `fly deploy`, Vercel deploy, real Supabase/Resend/Anthropic calls, **a real BTCPay Server (API details follow BTCPay's Greenfield docs; confirm with a testnet/regtest payment before going live)**, the GitHub Actions run.
