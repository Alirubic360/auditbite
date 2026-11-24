"use client";
import { useCallback, useEffect, useState } from "react";
import { api, ApiError, money } from "@/lib/api";

// Platform-admin console: Persian, right-to-left. Only for the SaaS operator's staff (platform_admins table).
const fa = new Intl.NumberFormat("fa-IR");
const STATUS_FA: Record<string, string> = { uploaded: "در صف", extracting: "در حال استخراج", needs_review: "نیازمند بازبینی", audited: "حسابرسی‌شده", claimed: "ادعا شده", recovered: "بازیابی‌شده", failed: "ناموفق" };

export default function Admin() {
  const [ov, setOv] = useState<any>(null); const [orgs, setOrgs] = useState<any[]>([]); const [failed, setFailed] = useState<any[]>([]);
  const [bill, setBill] = useState<any>({ invoices: [], flagged_events: [] });
  const [err, setErr] = useState(""); const [msg, setMsg] = useState("");
  const load = useCallback(async () => {
    try { const [a, b, c, d] = await Promise.all([api("/admin/overview"), api("/admin/orgs"), api("/admin/failed-invoices"), api("/admin/billing")]); setOv(a); setOrgs(b); setFailed(c); setBill(d); setErr(""); }
    catch (e) { setErr(e instanceof ApiError && e.status === 403 ? "شما دسترسی مدیر پلتفرم ندارید." : e instanceof ApiError && e.status === 401 ? "ابتدا وارد شوید." : (e as Error).message); }
  }, []);
  useEffect(() => { load(); }, [load]);
  const requeue = async (id: string) => { try { await api(`/admin/invoices/${id}/requeue`, { method: "POST" }); setMsg("فاکتور دوباره در صف قرار گرفت."); load(); } catch (e) { setErr((e as Error).message); } };
  const setFee = async (o: any) => {
    const v = window.prompt(`نرخ کارمزد موفقیت برای «${o.name}» (درصد، ۰ تا ۱۰۰):`, String(Number(o.success_fee_rate) * 100)); if (v === null) return;
    try { await api(`/admin/orgs/${o.id}`, { method: "PATCH", json: { success_fee_rate: String(Number(v) / 100) } }); setMsg("نرخ کارمزد ذخیره شد."); load(); } catch (e) { setErr((e as Error).message); }
  };
  const gen = async () => { try { const r = await api<any>("/admin/billing/generate", { method: "POST" }); setMsg(`${fa.format(r.created)} صورتحساب جدید ساخته شد.`); load(); } catch (e) { setErr((e as Error).message); } };
  const markPaid = async (b: any) => {
    const reason = window.prompt(`دلیل ثبت دستی پرداخت برای «${b.org_name}» (مثلاً شماره تراکنش):`); if (!reason) return;
    try { await api(`/admin/billing/${b.id}/mark-paid`, { json: { reason } }); setMsg("پرداخت ثبت شد."); load(); } catch (e) { setErr((e as Error).message); }
  };
  const setSub = async (o: any) => {
    const v = window.prompt(`اشتراک ماهانه به‌ازای هر شعبه برای «${o.name}» (دلار):`, String(o.subscription_per_location_usd)); if (v === null) return;
    try { await api(`/admin/orgs/${o.id}`, { method: "PATCH", json: { subscription_per_location_usd: v } }); setMsg("اشتراک ذخیره شد."); load(); } catch (e) { setErr((e as Error).message); }
  };
  return (
    <div dir="rtl" lang="fa" className="mx-auto max-w-6xl space-y-5 p-4 md:p-8">
      <header><h1 className="text-2xl font-bold">پنل مدیریت پلتفرم AuditBite</h1><p className="text-sm text-slate-500">نمای کلی همه سازمان‌ها (فقط برای مدیران پلتفرم)</p></header>
      {err && <div role="alert" className="card border-red-300 text-red-700 dark:text-red-300">{err}</div>}
      {msg && <div role="status" className="card border-emerald-300 text-emerald-700 dark:text-emerald-300">{msg}</div>}
      {ov && <>
        <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4" aria-label="آمار کلی">
          {[["سازمان‌ها", fa.format(ov.orgs)], ["کاربران", fa.format(ov.users)], ["صف پردازش", fa.format(ov.queue_depth)], ["فاکتورهای ناموفق", fa.format(ov.failed)],
            ["مبلغ ادعاشده", money(ov.claimed)], ["مبلغ بازیابی‌شده", money(ov.recovered)], ["کارمزد محاسبه‌شده", money(ov.fees)]].map(([k, v]) => (
            <div key={k} className="card"><p className="text-xs text-slate-500">{k}</p><p className="num mt-1 text-xl font-semibold" dir="ltr" style={{ textAlign: "right" }}>{v}</p></div>))}
        </section>
        <section className="card"><h2 className="mb-2 font-semibold">فاکتورها بر اساس وضعیت</h2><ul className="flex flex-wrap gap-3 text-sm">
          {ov.invoices_by_status.map((s: any) => <li key={s.status} className="rounded-full bg-slate-100 px-3 py-1 dark:bg-slate-800">{STATUS_FA[s.status] || s.status}: {fa.format(s.n)}</li>)}</ul></section>
        <section className="card overflow-x-auto p-0"><h2 className="p-4 pb-2 font-semibold">سازمان‌ها</h2><table className="w-full"><thead><tr>
          {["نام", "اعضا", "فاکتور", "ناموفق", "ادعاشده", "بازیابی‌شده", "کارمزد", "نرخ کارمزد", ""].map((h) => <th key={h} className="th">{h}</th>)}</tr></thead><tbody>
          {orgs.map((o) => <tr key={o.id} className="border-t border-slate-200 dark:border-slate-800"><td className="td" dir="auto">{o.name}</td><td className="td">{fa.format(o.members)}</td><td className="td">{fa.format(o.invoices)}</td>
            <td className="td">{o.failed > 0 ? <span className="text-bad">{fa.format(o.failed)}</span> : fa.format(0)}</td><td className="td num">{money(o.claimed)}</td><td className="td num">{money(o.recovered)}</td><td className="td num">{money(o.fees)}</td>
            <td className="td">{fa.format(Number(o.success_fee_rate) * 100)}٪</td><td className="td"><button className="btn" onClick={() => setFee(o)}>ویرایش نرخ</button> <button className="btn" onClick={() => setSub(o)}>اشتراک: <span dir="ltr">{money(o.subscription_per_location_usd)}</span></button></td></tr>)}</tbody></table></section>
        <section className="card overflow-x-auto p-0"><div className="flex items-center justify-between p-4 pb-2"><h2 className="font-semibold">صورتحساب‌های کریپتو</h2><button className="btn" onClick={gen}>ساخت صورتحساب‌های ماه</button></div>
          {bill.invoices.length === 0 ? <p className="p-4 text-sm text-slate-500">صورتحسابی وجود ندارد.</p> : <table className="w-full"><thead><tr>{["سازمان", "دوره", "مبلغ (دلار)", "وضعیت", "سررسید", ""].map((h) => <th key={h} className="th">{h}</th>)}</tr></thead><tbody>
            {bill.invoices.map((b: any) => <tr key={b.id} className="border-t border-slate-200 dark:border-slate-800"><td className="td" dir="auto">{b.org_name}</td><td className="td" dir="ltr">{b.period_start.slice(0, 7)}</td><td className="td num">{money(b.total_usd)}</td>
              <td className="td">{{ open: "باز", processing: "در حال پردازش", paid: "پرداخت‌شده", void: "باطل" }[b.status as string] || b.status}{b.overdue && <span className="ms-2 text-bad">(معوق)</span>}</td><td className="td" dir="ltr">{b.due_at.slice(0, 10)}</td>
              <td className="td">{b.status !== "paid" && b.status !== "void" && <button className="btn" onClick={() => markPaid(b)}>ثبت پرداخت دستی</button>}</td></tr>)}</tbody></table>}</section>
        <section className="card"><h2 className="mb-2 font-semibold">رویدادهای پرداخت نیازمند بررسی</h2>
          {bill.flagged_events.length === 0 ? <p className="text-sm text-slate-500">موردی نیست.</p> : <ul className="space-y-1 text-sm">{bill.flagged_events.map((e: any) => <li key={e.id}><b dir="ltr">{e.note}</b> · <span dir="auto">{e.org_name || "—"}</span> · <span dir="ltr">{e.provider_invoice_id}</span> · <span dir="ltr">{e.created_at.slice(0, 16).replace("T", " ")}</span></li>)}</ul>}
          <p className="mt-2 text-xs text-slate-500">مغایرت مبلغ، پرداخت دوباره، پرداخت اضافه یا دیرهنگام اینجا می‌آید و باید دستی رسیدگی شود (مثلاً بازپرداخت).</p></section>
        <section className="card overflow-x-auto p-0"><h2 className="p-4 pb-2 font-semibold">فاکتورهای ناموفق</h2>
          {failed.length === 0 ? <p className="p-4 text-sm text-slate-500">فاکتور ناموفقی وجود ندارد.</p> : <table className="w-full"><thead><tr>{["سازمان", "فایل", "خطا", "تلاش‌ها", ""].map((h) => <th key={h} className="th">{h}</th>)}</tr></thead><tbody>
            {failed.map((f) => <tr key={f.id} className="border-t border-slate-200 dark:border-slate-800"><td className="td" dir="auto">{f.org_name}</td><td className="td" dir="ltr">{f.original_filename || f.id.slice(0, 8)}</td><td className="td text-xs" dir="auto">{f.error}</td><td className="td">{fa.format(f.attempts)}</td>
              <td className="td"><button className="btn btn-primary" onClick={() => requeue(f.id)}>تلاش مجدد</button></td></tr>)}</tbody></table>}</section></>}
    </div>
  );
}
