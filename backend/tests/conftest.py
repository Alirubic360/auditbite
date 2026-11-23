import os, pathlib, subprocess, uuid
import jwt, psycopg, pytest
from psycopg.rows import dict_row

ROOT = pathlib.Path(__file__).resolve().parents[2]
ADMIN = os.environ.get("TEST_PG_ADMIN", "postgresql://postgres@/postgres?host=/tmp&port=54329")


def _with_db(name: str) -> str:
    """Same server/credentials as ADMIN, different database (works for URL and key=value DSNs)."""
    from psycopg.conninfo import conninfo_to_dict, make_conninfo
    return make_conninfo(**{**conninfo_to_dict(ADMIN), "dbname": name})
SECRET = "test-secret-test-secret-test-secret-1234"


@pytest.fixture(scope="session")
def template_db():
    name = f"abt_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(ADMIN, autocommit=True) as a:
        a.execute(f'create database "{name}"')
    with psycopg.connect(_with_db(name), autocommit=True) as c:
        for f in [ROOT / "db/test/auth_shim.sql", *sorted((ROOT / "db/migrations").glob("*.sql"))]:
            c.execute(f.read_text())
    yield name
    with psycopg.connect(ADMIN, autocommit=True) as a:
        a.execute(f'drop database "{name}" with (force)')


@pytest.fixture
def dburl(template_db):
    """A fresh, fully migrated database per test: the job queue is global, so tests must not share one."""
    name = f"ab_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(ADMIN, autocommit=True) as a:
        a.execute(f'create database "{name}" template "{template_db}"')
    url = _with_db(name)
    os.environ["DATABASE_URL"] = url
    os.environ["SUPABASE_JWT_SECRET"] = SECRET
    os.environ["INBOUND_WEBHOOK_SECRET"] = "hook-secret"
    yield url
    with psycopg.connect(ADMIN, autocommit=True) as a:
        a.execute(f'drop database "{name}" with (force)')


@pytest.fixture
def conn(dburl):
    with psycopg.connect(dburl, row_factory=dict_row) as c:
        yield c
        c.rollback()


def token(user_id):
    return jwt.encode({"sub": str(user_id), "aud": "authenticated", "role": "authenticated"}, SECRET, algorithm="HS256")


@pytest.fixture
def storage(tmp_path):
    from auditbite.storage import LocalStorage
    return LocalStorage(str(tmp_path / "store"), "sign-secret")
