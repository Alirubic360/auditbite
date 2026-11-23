import uuid
import psycopg, pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from auditbite.api import Deps, create_app
from auditbite.mailer import OutboxSender
from conftest import token
from seed import make_org, upload


@pytest.fixture
def world(dburl, storage):
    client = TestClient(create_app(Deps(storage, None, OutboxSender())))
    with psycopg.connect(dburl, row_factory=dict_row) as c:
        a, ua = make_org(c, "A"); b, ub = make_org(c, "B")
        bad = upload(c, storage, a, "x"); c.execute("update invoices set status='failed', error='boom' where id=%s", (bad,))
        admin = uuid.uuid4(); c.execute("insert into platform_admins (user_id) values (%s)", (admin,)); c.commit()
    return client, admin, ua, a, b, bad, dburl


H = lambda u: {"Authorization": f"Bearer {token(u)}"}


def test_tenant_users_cannot_use_admin_api_or_table(world):
    client, admin, ua, a, b, bad, dburl = world
    for path in ("/admin/overview", "/admin/orgs", "/admin/failed-invoices"):
        assert client.get(path, headers=H(ua)).status_code == 403
        assert client.get(path).status_code == 401
    assert client.post(f"/admin/invoices/{bad}/requeue", headers=H(ua)).status_code == 403
    from auditbite.db import user_tx
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with user_tx(str(ua)) as c:
            c.execute("select * from platform_admins")


def test_admin_sees_all_orgs_and_can_requeue_and_set_fee(world):
    client, admin, ua, a, b, bad, dburl = world
    ov = client.get("/admin/overview", headers=H(admin)).json()
    assert ov["orgs"] == 2 and ov["failed"] == 1 and {"status": "failed", "n": 1} in ov["invoices_by_status"]
    orgs = client.get("/admin/orgs", headers=H(admin)).json()
    assert {o["name"] for o in orgs} == {"A", "B"} and next(o for o in orgs if o["name"] == "A")["failed"] == 1
    f = client.get("/admin/failed-invoices", headers=H(admin)).json()
    assert f[0]["org_name"] == "A" and f[0]["error"] == "boom"
    assert client.post(f"/admin/invoices/{bad}/requeue", headers=H(admin)).json() == {"status": "uploaded"}
    assert client.post(f"/admin/invoices/{bad}/requeue", headers=H(admin)).status_code == 404   # only failed ones
    assert client.patch(f"/admin/orgs/{a}", json={"success_fee_rate": "0.2"}, headers=H(admin)).json()["success_fee_rate"] == "0.200"
    assert client.patch(f"/admin/orgs/{a}", json={"success_fee_rate": "1.5"}, headers=H(admin)).status_code == 422
    with psycopg.connect(dburl, row_factory=dict_row) as c:
        assert {r["action"] for r in c.execute("select action from audit_log").fetchall()} >= {"admin.set_fee", "admin.requeue"}
