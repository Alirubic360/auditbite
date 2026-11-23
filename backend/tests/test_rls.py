"""Tenant isolation is enforced by Postgres RLS, tested as the real `authenticated` role."""
import uuid
import psycopg, pytest
from auditbite.db import user_tx
from seed import make_org, make_vendor, make_contract

TABLES = ["locations", "vendors", "contracts", "contract_items", "invoices", "invoice_lines", "discrepancies", "claims",
          "claim_items", "followups", "outbound_emails", "audit_log", "organizations", "org_members"]


@pytest.fixture
def two_orgs(dburl):
    with psycopg.connect(dburl, row_factory=psycopg.rows.dict_row) as c:
        a, ua = make_org(c, "A"); b, ub = make_org(c, "B")
        va, vb = make_vendor(c, a, "VA"), make_vendor(c, b, "VB")
        make_contract(c, a, va, [("S1", "Item A", "LB", 1)]); make_contract(c, b, vb, [("S2", "Item B", "LB", 2)])
        for o, v in ((a, va), (b, vb)):
            c.execute("insert into locations (org_id,name) values (%s,'L')", (o,))
            i = c.execute("insert into invoices (org_id,vendor_id,file_path,media_type) values (%s,%s,'p','application/pdf') returning id", (o, v)).fetchone()["id"]
            l = c.execute("insert into invoice_lines (org_id,invoice_id,line_no) values (%s,%s,1) returning id", (o, i)).fetchone()["id"]
            d = c.execute("insert into discrepancies (org_id,invoice_id,line_id,type,amount) values (%s,%s,%s,'price_variance',5) returning id", (o, i, l)).fetchone()["id"]
            cl = c.execute("insert into claims (org_id,vendor_id,total_claimed) values (%s,%s,5) returning id", (o, v)).fetchone()["id"]
            c.execute("insert into claim_items (claim_id,discrepancy_id,org_id,amount) values (%s,%s,%s,5)", (cl, d, o))
            c.execute("insert into followups (org_id,claim_id,day,due_at) values (%s,%s,7,now())", (o, cl))
            c.execute("insert into outbound_emails (org_id,kind,to_email,subject,body,provider) values (%s,'claim','x@y','s','b','t')", (o,))
            c.execute("insert into audit_log (org_id,action) values (%s,'t')", (o,))
        c.commit()
    return a, ua, b, ub


def test_user_sees_only_own_org_on_every_table(two_orgs):
    a, ua, b, ub = two_orgs
    with user_tx(str(ua)) as c:
        for t in TABLES:
            n = c.execute(f"select count(*) n from {t}").fetchone()["n"]
            assert n >= 1, f"{t}: user sees nothing of own org"
            org_col = "id" if t == "organizations" else "org_id" if t != "org_members" else "org_id"
            if t != "claim_items":
                foreign = c.execute(f"select count(*) n from {t} where {org_col} = %s", (b,)).fetchone()["n"]
                assert foreign == 0, f"LEAK on {t}"
        assert c.execute("select count(*) n from claim_items").fetchone()["n"] == 1


def test_cannot_write_into_other_org(two_orgs):
    a, ua, b, ub = two_orgs
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with user_tx(str(ua)) as c:
            c.execute("insert into vendors (org_id,name) values (%s,'evil')", (b,))
    with user_tx(str(ua)) as c:                       # update/delete of foreign rows silently affect 0 rows
        assert c.execute("update vendors set name='hacked' where org_id=%s", (b,)).rowcount == 0
        assert c.execute("delete from invoices where org_id=%s", (b,)).rowcount == 0


def test_cannot_reference_foreign_vendor_even_with_own_org_id(two_orgs, dburl):
    a, ua, b, ub = two_orgs
    with user_tx(str(ua)) as c:
        vb = None
    with psycopg.connect(dburl, row_factory=psycopg.rows.dict_row) as s:
        vb = s.execute("select id from vendors where org_id=%s", (b,)).fetchone()["id"]
    with pytest.raises(psycopg.errors.ForeignKeyViolation):          # composite FK blocks cross-tenant links
        with user_tx(str(ua)) as c:
            c.execute("insert into contracts (org_id,vendor_id) values (%s,%s)", (a, vb))


def test_unauthenticated_and_anon_see_nothing(two_orgs, dburl):
    with psycopg.connect(dburl, row_factory=psycopg.rows.dict_row) as c:
        c.execute("set local role authenticated")           # authenticated but no sub claim
        assert c.execute("select count(*) n from invoices").fetchone()["n"] == 0
        c.rollback()
        c.execute("set local role anon")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("select * from invoices")


def test_user_cannot_tamper_with_audit_log_or_membership(two_orgs):
    a, ua, b, ub = two_orgs
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with user_tx(str(ua)) as c:
            c.execute("delete from audit_log")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with user_tx(str(ua)) as c:
            c.execute("insert into org_members (org_id,user_id) values (%s,%s)", (b, ua))
