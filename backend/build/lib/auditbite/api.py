from __future__ import annotations
import datetime as dt, json, os, re, uuid
from decimal import Decimal
from typing import Any
import jwt
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.encoders import ENCODERS_BY_TYPE
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from . import claims as claims_mod, contracts as contracts_mod, metrics
from .db import J, service_tx, user_tx
from .money import D, cents
from .storage import MAX_BYTES, sha256, sniff_media_type


ENCODERS_BY_TYPE[Decimal] = str   # money crosses the API as exact strings, never floats


def _default(o):
    if isinstance(o, (Decimal, uuid.UUID)):
        return str(o)
    if isinstance(o, (dt.date, dt.datetime)):
        return o.isoformat()
    raise TypeError(type(o))


class JSON(JSONResponse):
    def render(self, content: Any) -> bytes:
        return json.dumps(content, default=_default, ensure_ascii=False).encode()


class Deps:
    """Injectable collaborators so tests (and local demos) never need real AI/email."""
    def __init__(self, storage, ai=None, sender=None):
        self.storage, self.ai, self.sender = storage, ai, sender


# ---- request models ----
class OnboardIn(BaseModel):
    org_name: str = Field(min_length=1, max_length=120)

class VendorIn(BaseModel):
    name: str = Field(min_length=1); aliases: list[str] = []; contact_email: str | None = None; payment_terms_days: int = 30

class LocationIn(BaseModel):
    name: str = Field(min_length=1); account_number: str | None = None

class ItemIn(BaseModel):
    sku: str | None = None; canonical_name: str; unit: str; pack_size: Decimal | None = None
    contract_price: Decimal = Field(ge=0); tolerance_pct: Decimal = Field(default=Decimal("0.5"), ge=0)
    aliases: list[str] = []; needs_index: bool = False; source_clause: str | None = None

class ContractIn(BaseModel):
    vendor_id: uuid.UUID; reference: str | None = None; valid_from: dt.date | None = None; valid_to: dt.date | None = None
    rebate_rules: list[dict] = []; file_path: str | None = None; items: list[ItemIn]

class LineEdit(BaseModel):
    description: str | None = None; sku: str | None = None; qty: Decimal | None = None; unit: str | None = None
    unit_price: Decimal | None = None; line_total: Decimal | None = None; matched_item_id: uuid.UUID | None = None

class ReviewIn(BaseModel):
    review_status: str = Field(pattern="^(approved|rejected|pending)$"); amount: Decimal | None = Field(default=None, ge=0)

class ClaimIn(BaseModel):
    vendor_id: uuid.UUID; discrepancy_ids: list[uuid.UUID]; language: str = "en"

class CreditIn(BaseModel):
    amount: Decimal = Field(ge=0); status: str = "credited"

class Attachment(BaseModel):
    filename: str; content_base64: str

class InboundIn(BaseModel):
    to: str; attachments: list[Attachment]

class SettingsIn(BaseModel):
    name: str | None = None; success_fee_rate: Decimal | None = Field(default=None, ge=0, le=1)
    default_tolerance_pct: Decimal | None = Field(default=None, ge=0, le=100)


def create_app(deps: Deps) -> FastAPI:
    app = FastAPI(title="AuditBite API", default_response_class=JSON)
    app.add_middleware(CORSMiddleware, allow_origins=os.environ.get("ALLOWED_ORIGINS", "http://localhost:3000").split(","),
                       allow_methods=["*"], allow_headers=["*"])

    def uid(authorization: str = Header(default="")) -> str:
        if not authorization.lower().startswith("bearer "):
            raise HTTPException(401, "missing bearer token")
        try:
            claims = jwt.decode(authorization[7:], os.environ["SUPABASE_JWT_SECRET"], algorithms=["HS256"], audience="authenticated")
        except jwt.PyJWTError:
            raise HTTPException(401, "invalid token") from None
        return claims["sub"]

    def org_of(c) -> str:
        r = c.execute("select org_id from org_members limit 1").fetchone()   # RLS: only my memberships
        if not r:
            raise HTTPException(403, "no organization; call POST /onboarding")
        return str(r["org_id"])

    def one(c, sql, params, what="resource"):
        r = c.execute(sql, params).fetchone()
        if not r:
            raise HTTPException(404, f"{what} not found")
        return r

    @app.get("/healthz")
    def healthz():
        with service_tx() as c:
            c.execute("select 1")
        return {"ok": True}

    # ---- onboarding / me / settings ----
    @app.post("/onboarding", status_code=201)
    def onboarding(body: OnboardIn, user=Depends(uid)):
        with service_tx() as c:
            if c.execute("select 1 from org_members where user_id=%s", (user,)).fetchone():
                raise HTTPException(409, "user already belongs to an organization")
            slug = re.sub(r"[^a-z0-9]+", "-", body.org_name.lower()).strip("-")[:30] + "-" + uuid.uuid4().hex[:6]
            org = c.execute("insert into organizations (name, inbox_slug) values (%s,%s) returning *", (body.org_name, slug)).fetchone()
            c.execute("insert into org_members (org_id, user_id, role) values (%s,%s,'owner')", (org["id"], user))
            c.execute("insert into audit_log (org_id,user_id,action,entity,entity_id) values (%s,%s,'org.create','organization',%s)", (org["id"], user, org["id"]))
        return org

    @app.get("/me")
    def me(user=Depends(uid)):
        with user_tx(user) as c:
            return {"user_id": user, "organization": c.execute("select * from organizations limit 1").fetchone()}

    @app.get("/settings")
    def get_settings(user=Depends(uid)):
        with user_tx(user) as c:
            return {"organization": one(c, "select * from organizations limit 1", (), "organization"),
                    "locations": c.execute("select * from locations order by name").fetchall(),
                    "vendors": c.execute("select * from vendors order by name").fetchall()}

    @app.patch("/settings")
    def patch_settings(body: SettingsIn, user=Depends(uid)):
        with user_tx(user) as c:
            org = org_of(c)
            d = body.model_dump(exclude_none=True)
            if d:
                sets = ", ".join(f"{k}=%s" for k in d)
                c.execute(f"update organizations set {sets} where id=%s", (*d.values(), org))
            return c.execute("select * from organizations where id=%s", (org,)).fetchone()

    # ---- locations / vendors ----
    @app.post("/locations", status_code=201)
    def add_location(b: LocationIn, user=Depends(uid)):
        with user_tx(user) as c:
            return c.execute("insert into locations (org_id,name,account_number) values (%s,%s,%s) returning *", (org_of(c), b.name, b.account_number)).fetchone()

    @app.delete("/locations/{id}", status_code=204)
    def del_location(id: uuid.UUID, user=Depends(uid)):
        with user_tx(user) as c:
            c.execute("delete from locations where id=%s", (id,))
        return Response(status_code=204)

    @app.post("/vendors", status_code=201)
    def add_vendor(b: VendorIn, user=Depends(uid)):
        with user_tx(user) as c:
            return c.execute("insert into vendors (org_id,name,aliases,contact_email,payment_terms_days) values (%s,%s,%s,%s,%s) returning *",
                             (org_of(c), b.name, b.aliases, b.contact_email, b.payment_terms_days)).fetchone()

    @app.patch("/vendors/{id}")
    def patch_vendor(id: uuid.UUID, b: VendorIn, user=Depends(uid)):
        with user_tx(user) as c:
            return one(c, "update vendors set name=%s, aliases=%s, contact_email=%s, payment_terms_days=%s where id=%s returning *",
                       (b.name, b.aliases, b.contact_email, b.payment_terms_days, id), "vendor")

    # ---- contracts ----
    @app.post("/contracts/parse-csv")
    async def parse_csv(file: UploadFile = File(...), user=Depends(uid)):
        data = await file.read()
        if len(data) > 2_000_000:
            raise HTTPException(413, "CSV too large")
        with user_tx(user) as c:
            tol = one(c, "select default_tolerance_pct from organizations limit 1", (), "organization")["default_tolerance_pct"]
        try:
            return {"items": contracts_mod.parse_csv(data, tol)}
        except contracts_mod.ContractImportError as e:
            raise HTTPException(422, str(e)) from None

    @app.post("/contracts/extract")
    async def extract_contract(file: UploadFile = File(...), user=Depends(uid)):
        """AI reads the contract into an EDITABLE DRAFT; nothing is audited until a human saves it."""
        if deps.ai is None:
            raise HTTPException(503, "AI extraction not configured")
        data = await file.read()
        mt = sniff_media_type(data)
        if len(data) > MAX_BYTES or mt is None:
            raise HTTPException(422, "contract must be a PDF/PNG/JPEG under 20MB")
        with user_tx(user) as c:
            org = org_of(c)
            tol = c.execute("select default_tolerance_pct from organizations limit 1").fetchone()["default_tolerance_pct"]
        path = f"{org}/contracts/{uuid.uuid4()}"
        deps.storage.put(path, data)
        try:
            draft = contracts_mod.draft_from_extraction(deps.ai.extract_contract(data, mt), tol)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"contract extraction failed: {e}") from None
        draft["file_path"] = path
        return draft

    @app.post("/contracts", status_code=201)
    def create_contract(b: ContractIn, user=Depends(uid)):
        with user_tx(user) as c:
            org = org_of(c)
            if b.file_path and not b.file_path.startswith(f"{org}/"):
                raise HTTPException(403, "file does not belong to your organization")
            try:
                ct = c.execute("insert into contracts (org_id,vendor_id,reference,valid_from,valid_to,rebate_rules,file_path) values (%s,%s,%s,%s,%s,%s,%s) returning *",
                               (org, b.vendor_id, b.reference, b.valid_from, b.valid_to, J(b.rebate_rules), b.file_path)).fetchone()
                for it in b.items:
                    c.execute("""insert into contract_items (org_id,contract_id,sku,canonical_name,aliases,unit,pack_size,contract_price,tolerance_pct,needs_index,source_clause)
                                 values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", (org, ct["id"], it.sku, it.canonical_name, it.aliases, it.unit, it.pack_size,
                                 it.contract_price, it.tolerance_pct, it.needs_index, it.source_clause))
            except Exception as e:  # FK violations (vendor from another org) surface as 422, never as data
                if e.__class__.__name__ in ("ForeignKeyViolation", "CheckViolation"):
                    raise HTTPException(422, "invalid vendor or dates") from None
                raise
            return ct

    @app.get("/contracts")
    def list_contracts(user=Depends(uid)):
        with user_tx(user) as c:
            return c.execute("""select ct.*, v.name as vendor_name, (select count(*) from contract_items i where i.contract_id=ct.id) as item_count
                                from contracts ct join vendors v on v.id=ct.vendor_id order by ct.created_at desc""").fetchall()

    @app.get("/contracts/{id}")
    def get_contract(id: uuid.UUID, user=Depends(uid)):
        with user_tx(user) as c:
            ct = one(c, "select * from contracts where id=%s", (id,), "contract")
            return {**ct, "items": c.execute("select * from contract_items where contract_id=%s order by canonical_name", (id,)).fetchall()}

    @app.patch("/contract-items/{id}")
    def patch_item(id: uuid.UUID, b: ItemIn, user=Depends(uid)):
        with user_tx(user) as c:
            return one(c, """update contract_items set sku=%s,canonical_name=%s,aliases=%s,unit=%s,pack_size=%s,contract_price=%s,tolerance_pct=%s,needs_index=%s,source_clause=%s
                             where id=%s returning *""", (b.sku, b.canonical_name, b.aliases, b.unit, b.pack_size, b.contract_price, b.tolerance_pct, b.needs_index, b.source_clause, id), "item")

    # ---- invoices ----
    def _ingest(c, storage, org, filename, data, location_id=None) -> dict:
        mt = sniff_media_type(data)
        if mt is None:
            raise HTTPException(422, f"{filename}: only PDF, PNG and JPEG are accepted")
        if len(data) > MAX_BYTES or not data:
            raise HTTPException(422, f"{filename}: empty or larger than 20MB")
        inv_id = uuid.uuid4()
        path = f"{org}/invoices/{inv_id}"
        storage.put(path, data)
        return c.execute("""insert into invoices (id,org_id,location_id,file_path,media_type,original_filename,file_sha256) values (%s,%s,%s,%s,%s,%s,%s) returning *""",
                         (inv_id, org, location_id, path, mt, filename, sha256(data))).fetchone()

    @app.post("/invoices", status_code=201)
    async def upload_invoices(files: list[UploadFile] = File(...), location_id: uuid.UUID | None = Form(None), user=Depends(uid)):
        blobs = [(f.filename, await f.read()) for f in files]
        with user_tx(user) as c:
            org = org_of(c)
            return [_ingest(c, deps.storage, org, n, d, location_id) for n, d in blobs]

    @app.get("/invoices")
    def list_invoices(status: str | None = None, vendor_id: uuid.UUID | None = None, location_id: uuid.UUID | None = None,
                      date_from: dt.date | None = None, date_to: dt.date | None = None, limit: int = Query(100, le=500), user=Depends(uid)):
        q = """select i.*, v.name as vendor_name, l.name as location_name,
               (select count(*) from discrepancies d where d.invoice_id=i.id) as discrepancy_count,
               (select coalesce(sum(amount),0) from discrepancies d where d.invoice_id=i.id) as discrepancy_amount
               from invoices i left join vendors v on v.id=i.vendor_id left join locations l on l.id=i.location_id where true"""
        p: list = []
        for col, val, op in (("i.status", status, "="), ("i.vendor_id", vendor_id, "="), ("i.location_id", location_id, "="),
                             ("i.invoice_date", date_from, ">="), ("i.invoice_date", date_to, "<=")):
            if val is not None:
                q += f" and {col} {op} %s"; p.append(val)
        with user_tx(user) as c:
            return c.execute(q + " order by i.created_at desc limit %s", (*p, limit)).fetchall()

    @app.get("/invoices/{id}")
    def get_invoice(id: uuid.UUID, user=Depends(uid)):
        with user_tx(user) as c:
            inv = one(c, "select i.*, v.name as vendor_name from invoices i left join vendors v on v.id=i.vendor_id where i.id=%s", (id,), "invoice")
            inv.pop("raw_extraction", None)
            lines = c.execute("""select l.*, ci.canonical_name as item_name, ci.contract_price, ci.unit as contract_unit, ci.tolerance_pct
                                 from invoice_lines l left join contract_items ci on ci.id=l.matched_item_id where l.invoice_id=%s order by l.line_no""", (id,)).fetchall()
            discs = c.execute("select * from discrepancies where invoice_id=%s order by created_at", (id,)).fetchall()
            return {**inv, "lines": lines, "discrepancies": discs}

    @app.get("/invoices/{id}/raw")
    def invoice_raw(id: uuid.UUID, user=Depends(uid)):
        with user_tx(user) as c:
            return one(c, "select raw_extraction from invoices where id=%s", (id,), "invoice")["raw_extraction"]

    @app.get("/invoices/{id}/file-url")
    def invoice_file_url(id: uuid.UUID, user=Depends(uid)):
        with user_tx(user) as c:
            r = one(c, "select file_path, media_type, org_id from invoices where id=%s", (id,), "invoice")
            c.execute("insert into audit_log (org_id,user_id,action,entity,entity_id) values (%s,%s,'file.access','invoice',%s)", (r["org_id"], user, id))
        return {"url": deps.storage.signed_url(r["file_path"]), "media_type": r["media_type"]}

    @app.get("/files/{path:path}")
    def get_file(path: str, exp: int, sig: str):
        if not hasattr(deps.storage, "verify"):
            raise HTTPException(404, "not found")      # Supabase Storage serves its own signed URLs
        if not deps.storage.verify(path, exp, sig):
            raise HTTPException(403, "link expired or invalid")
        data = deps.storage.get(path)
        return Response(data, media_type=sniff_media_type(data) or "application/octet-stream", headers={"Cache-Control": "private, no-store"})

    @app.patch("/invoices/{id}/lines/{line_id}")
    def edit_line(id: uuid.UUID, line_id: uuid.UUID, b: LineEdit, user=Depends(uid)):
        d = b.model_dump(exclude_unset=True)
        if not d:
            raise HTTPException(422, "nothing to update")
        with user_tx(user) as c:
            sets = ", ".join(f"{k}=%s" for k in d)
            return one(c, f"update invoice_lines set {sets} where id=%s and invoice_id=%s returning *", (*d.values(), line_id, id), "line")

    @app.post("/invoices/{id}/reprocess")
    def reprocess(id: uuid.UUID, user=Depends(uid)):
        with user_tx(user) as c:
            one(c, "select 1 from invoices where id=%s", (id,), "invoice")
            if c.execute("select 1 from discrepancies where invoice_id=%s and review_status<>'pending'", (id,)).fetchone():
                raise HTTPException(409, "invoice has reviewed discrepancies; reprocessing would discard human decisions")
            c.execute("update invoices set status='uploaded', attempts=0, error=null where id=%s", (id,))
        return {"status": "uploaded"}

    # ---- review / discrepancies ----
    @app.get("/review")
    def review_queue(user=Depends(uid)):
        """Low-confidence (<0.8) or unclear findings go here first; they never reach a claim without a human."""
        with user_tx(user) as c:
            return c.execute("""select d.*, i.invoice_number, i.invoice_date, v.name as vendor_name, l.description
                                from discrepancies d join invoices i on i.id=d.invoice_id left join vendors v on v.id=i.vendor_id
                                left join invoice_lines l on l.id=d.line_id
                                where d.review_status='pending' and (d.confidence < 0.8 or d.type in ('unit_mismatch','substitution')
                                      or d.ai_verdict ->> 'verdict' = 'unclear')
                                order by d.created_at""").fetchall()

    @app.patch("/discrepancies/{id}")
    def review(id: uuid.UUID, b: ReviewIn, user=Depends(uid)):
        with user_tx(user) as c:
            d = one(c, "select * from discrepancies where id=%s", (id,), "discrepancy")
            if c.execute("select 1 from claim_items where discrepancy_id=%s", (id,)).fetchone():
                raise HTTPException(409, "discrepancy is already part of a claim")
            amount = cents(b.amount) if b.amount is not None else d["amount"]
            if b.review_status == "approved" and D(amount) <= 0:
                raise HTTPException(422, "set a positive amount before approving (this finding has no computed amount)")
            r = c.execute("""update discrepancies set review_status=%s, amount=%s, reviewed_by=%s, reviewed_at=case when %s='pending' then null else now() end
                             where id=%s returning *""", (b.review_status, amount, user, b.review_status, id)).fetchone()
            c.execute("insert into audit_log (org_id,user_id,action,entity,entity_id,detail) values (%s,%s,'discrepancy.review','discrepancy',%s,%s)",
                      (d["org_id"], user, id, J({"to": b.review_status, "amount": str(amount)})))
            return r

    @app.get("/discrepancies/claimable")
    def claimable(user=Depends(uid)):
        with user_tx(user) as c:
            return c.execute("""select d.*, i.invoice_number, i.invoice_date, i.vendor_id, v.name as vendor_name from discrepancies d
                join invoices i on i.id=d.invoice_id join vendors v on v.id=i.vendor_id
                where d.review_status='approved' and d.amount>0 and not exists (select 1 from claim_items ci where ci.discrepancy_id=d.id)
                order by v.name, i.invoice_date""").fetchall()

    # ---- claims ----
    def _claim_err(e):
        raise HTTPException(422, str(e)) from None

    @app.post("/claims", status_code=201)
    def make_claim(b: ClaimIn, user=Depends(uid)):
        with user_tx(user) as c:
            try:
                return claims_mod.create_claim(c, org_of(c), str(b.vendor_id), [str(x) for x in b.discrepancy_ids], deps.storage, deps.ai, b.language)
            except claims_mod.ClaimError as e:
                _claim_err(e)

    @app.get("/claims")
    def list_claims(user=Depends(uid)):
        with user_tx(user) as c:
            return c.execute("select cl.*, v.name as vendor_name from claims cl join vendors v on v.id=cl.vendor_id order by cl.created_at desc").fetchall()

    @app.get("/claims/{id}")
    def get_claim(id: uuid.UUID, user=Depends(uid)):
        with user_tx(user) as c:
            cl = one(c, "select cl.*, v.name as vendor_name, v.contact_email from claims cl join vendors v on v.id=cl.vendor_id where cl.id=%s", (id,), "claim")
            items = c.execute("""select ci.amount, d.type, d.evidence, i.invoice_number, i.invoice_date from claim_items ci
                                 join discrepancies d on d.id=ci.discrepancy_id join invoices i on i.id=d.invoice_id where ci.claim_id=%s""", (id,)).fetchall()
            return {**cl, "items": items,
                    "followups": c.execute("select day, due_at, status, sent_at from followups where claim_id=%s order by day", (id,)).fetchall(),
                    "emails": c.execute("select kind, to_email, subject, created_at from outbound_emails where claim_id=%s order by created_at", (id,)).fetchall()}

    @app.get("/claims/{id}/pdf-url")
    def claim_pdf(id: uuid.UUID, user=Depends(uid)):
        with user_tx(user) as c:
            return {"url": deps.storage.signed_url(one(c, "select pdf_path from claims where id=%s", (id,), "claim")["pdf_path"])}

    @app.post("/claims/{id}/send")
    def send(id: uuid.UUID, user=Depends(uid)):
        """Human click = approval. Nothing is ever emailed to a vendor without this call."""
        if deps.sender is None:
            raise HTTPException(503, "email not configured")
        with user_tx(user) as c:
            try:
                r = claims_mod.send_claim(c, org_of(c), id, deps.storage, deps.sender)
            except claims_mod.ClaimError as e:
                _claim_err(e)
            c.execute("insert into audit_log (org_id,user_id,action,entity,entity_id) values (%s,%s,'claim.send','claim',%s)", (r["org_id"], user, id))
            return r

    @app.post("/claims/{id}/credit")
    def credit(id: uuid.UUID, b: CreditIn, user=Depends(uid)):
        with user_tx(user) as c:
            try:
                return claims_mod.record_credit(c, org_of(c), id, b.amount, b.status)
            except claims_mod.ClaimError as e:
                _claim_err(e)

    # ---- dashboard / metrics ----
    @app.get("/dashboard")
    def dash(user=Depends(uid)):
        with user_tx(user) as c:
            return metrics.dashboard(c, org_of(c))

    @app.get("/metrics/precision")
    def precision(user=Depends(uid)):
        with user_tx(user) as c:
            return metrics.precision_by_month(c, org_of(c))

    # ---- inbound email webhook (shared-secret; maps invoices+<slug>@ to an org) ----
    @app.post("/webhooks/inbound-email", status_code=202)
    def inbound(b: InboundIn, x_webhook_secret: str = Header(default="")):
        import base64, binascii, hmac
        expected = os.environ.get("INBOUND_WEBHOOK_SECRET", "")
        if not expected or not hmac.compare_digest(x_webhook_secret, expected):
            raise HTTPException(401, "bad webhook secret")
        m = re.search(r"\+([a-z0-9-]+)@", b.to.lower())
        if not m:
            raise HTTPException(422, "recipient has no organization slug")
        accepted = rejected = 0
        with service_tx() as c:
            org = c.execute("select id from organizations where inbox_slug=%s", (m.group(1),)).fetchone()
            if not org:
                raise HTTPException(404, "unknown inbox")
            for a in b.attachments:
                try:
                    data = base64.b64decode(a.content_base64, validate=True)
                    _ingest(c, deps.storage, str(org["id"]), a.filename, data)
                    accepted += 1
                except (binascii.Error, HTTPException):
                    rejected += 1
        return {"accepted": accepted, "rejected": rejected}

    # ---- platform admin (Persian-language console; operator-only, cross-tenant by design) ----
    def admin_user(user=Depends(uid)) -> str:
        with service_tx() as c:
            if not c.execute("select 1 from platform_admins where user_id=%s", (user,)).fetchone():
                raise HTTPException(403, "platform admin only")
        return user

    @app.get("/admin/overview")
    def admin_overview(_=Depends(admin_user)):
        with service_tx() as c:
            return {
                "orgs": c.execute("select count(*) n from organizations").fetchone()["n"],
                "users": c.execute("select count(distinct user_id) n from org_members").fetchone()["n"],
                "invoices_by_status": c.execute("select status, count(*) n from invoices group by 1 order by 1").fetchall(),
                "queue_depth": c.execute("select count(*) n from invoices where status in ('uploaded','extracting')").fetchone()["n"],
                "failed": c.execute("select count(*) n from invoices where status='failed'").fetchone()["n"],
                "claimed": c.execute("select coalesce(sum(total_claimed),0) v from claims where status<>'draft'").fetchone()["v"],
                "recovered": c.execute("select coalesce(sum(recovered_amount),0) v from claims").fetchone()["v"],
                "fees": c.execute("select coalesce(sum(fee_amount),0) v from claims").fetchone()["v"],
            }

    @app.get("/admin/orgs")
    def admin_orgs(_=Depends(admin_user)):
        with service_tx() as c:
            return c.execute("""select o.id, o.name, o.success_fee_rate, o.created_at,
                (select count(*) from org_members m where m.org_id=o.id) as members,
                (select count(*) from invoices i where i.org_id=o.id) as invoices,
                (select count(*) from invoices i where i.org_id=o.id and i.status='failed') as failed,
                (select coalesce(sum(total_claimed),0) from claims c where c.org_id=o.id and c.status<>'draft') as claimed,
                (select coalesce(sum(recovered_amount),0) from claims c where c.org_id=o.id) as recovered,
                (select coalesce(sum(fee_amount),0) from claims c where c.org_id=o.id) as fees
                from organizations o order by o.created_at desc""").fetchall()

    @app.patch("/admin/orgs/{id}")
    def admin_set_fee(id: uuid.UUID, b: SettingsIn, admin=Depends(admin_user)):
        if b.success_fee_rate is None:
            raise HTTPException(422, "success_fee_rate required")
        with service_tx() as c:
            r = one(c, "update organizations set success_fee_rate=%s where id=%s returning id, name, success_fee_rate", (b.success_fee_rate, id), "organization")
            c.execute("insert into audit_log (org_id,user_id,action,entity,entity_id,detail) values (%s,%s,'admin.set_fee','organization',%s,%s)",
                      (id, admin, id, J({"rate": str(b.success_fee_rate)})))
            return r

    @app.get("/admin/failed-invoices")
    def admin_failed(_=Depends(admin_user)):
        with service_tx() as c:
            return c.execute("""select i.id, i.org_id, o.name as org_name, i.original_filename, i.error, i.attempts, i.created_at
                                from invoices i join organizations o on o.id=i.org_id where i.status='failed' order by i.created_at desc limit 200""").fetchall()

    @app.post("/admin/invoices/{id}/requeue")
    def admin_requeue(id: uuid.UUID, admin=Depends(admin_user)):
        with service_tx() as c:
            r = one(c, "update invoices set status='uploaded', attempts=0, error=null where id=%s and status='failed' returning id, org_id", (id,), "failed invoice")
            c.execute("insert into audit_log (org_id,user_id,action,entity,entity_id) values (%s,%s,'admin.requeue','invoice',%s)", (r["org_id"], admin, id))
            return {"status": "uploaded"}

    return app
