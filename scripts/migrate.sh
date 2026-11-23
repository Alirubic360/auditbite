#!/usr/bin/env bash
# Apply SQL migrations in order. Usage: DATABASE_URL=... scripts/migrate.sh
# Each file is applied once (tracked in schema_migrations). On Supabase, use the direct (non-pooled) connection string.
set -euo pipefail
: "${DATABASE_URL:?set DATABASE_URL}"
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -q -c "create table if not exists schema_migrations (name text primary key, applied_at timestamptz default now())"
for f in "$(dirname "$0")"/../db/migrations/*.sql; do
  n=$(basename "$f")
  if [ "$(psql "$DATABASE_URL" -Atq -c "select 1 from schema_migrations where name='$n'")" = "1" ]; then echo "skip $n"; continue; fi
  echo "apply $n"
  psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -q --single-transaction -f "$f" -c "insert into schema_migrations (name) values ('$n')"
done
