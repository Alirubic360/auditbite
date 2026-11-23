import uuid
from decimal import Decimal as Dc


def make_org(conn, name="Org", user=None, slug=None):
    user = user or uuid.uuid4()
    org = conn.execute("insert into organizations (name, inbox_slug) values (%s,%s) returning id", (name, slug or uuid.uuid4().hex[:8])).fetchone()["id"]
    conn.execute("insert into org_members (org_id,user_id,role) values (%s,%s,'owner')", (org, user))
    return org, user


def make_vendor(conn, org, name="Sysco", email="ar@sysco.test"):
    return conn.execute("insert into vendors (org_id,name,contact_email) values (%s,%s,%s) returning id", (org, name, email)).fetchone()["id"]


def make_contract(conn, org, vendor, items, rules=None, vf="2025-01-01", vt="2025-12-31"):
    from auditbite.db import J
    c = conn.execute("insert into contracts (org_id,vendor_id,reference,valid_from,valid_to,rebate_rules) values (%s,%s,'C-77',%s,%s,%s) returning id",
                     (org, vendor, vf, vt, J(rules or []))).fetchone()["id"]
    for sku, name, unit, price in items:
        conn.execute("insert into contract_items (org_id,contract_id,sku,canonical_name,unit,contract_price,source_clause) values (%s,%s,%s,%s,%s,%s,'Sec 3.1')",
                     (org, c, sku, name, unit, Dc(str(price))))
    return c


def upload(conn, storage, org, marker, media="application/pdf"):
    from fakes import pdf
    iid = uuid.uuid4()
    path = f"{org}/invoices/{iid}"
    storage.put(path, pdf(marker))
    conn.execute("insert into invoices (id,org_id,file_path,media_type) values (%s,%s,%s,%s)", (iid, org, path, media))
    return iid
