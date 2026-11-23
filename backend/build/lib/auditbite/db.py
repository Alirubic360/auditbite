from __future__ import annotations
import os
from contextlib import contextmanager
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


def dsn() -> str:
    return os.environ.get("DATABASE_URL", "postgresql://postgres@localhost:5432/auditbite")


def service_conn():
    """Owner/service connection: bypasses RLS. Worker code MUST filter by org_id explicitly."""
    return psycopg.connect(dsn(), row_factory=dict_row)


@contextmanager
def user_tx(user_id: str):
    """Transaction as an authenticated end-user: RLS is enforced by Postgres, not by app code."""
    with psycopg.connect(dsn(), row_factory=dict_row) as conn:
        conn.execute("set local role authenticated")
        conn.execute("select set_config('request.jwt.claim.sub', %s, true), set_config('request.jwt.claims', %s, true)",
                     (user_id, '{"sub":"%s","role":"authenticated"}' % user_id))
        yield conn


@contextmanager
def service_tx():
    with service_conn() as conn:
        yield conn


J = Jsonb
