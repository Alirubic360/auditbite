# Deploying AuditBite

Stack: **Supabase** (Postgres, Auth, Storage) · **Vercel** (Next.js) · **Fly.io** (API + worker) · **Resend** (email).

## 1. Supabase
1. Create a project. Storage → create a **private** bucket named `auditbite`.
2. Apply migrations with the **direct** connection string (not the pooler):
   `DATABASE_URL=... scripts/migrate.sh`  (do not run `db/test/auth_shim.sql`; Supabase has real `auth`).
3. Auth → enable email/password. Copy: project URL, anon key, service-role key, JWT secret (Settings → API).
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
Verified: migrations script (applied twice on Postgres 16, idempotent), clean `pip install .` + API boot + `/healthz` + production secret guard, Supabase Storage request shapes (mock transport), CI definition written. **Not verified:** `docker build` (no Docker daemon in the dev sandbox), `fly deploy`, Vercel deploy, real Supabase/Resend/Anthropic calls, the GitHub Actions run.
