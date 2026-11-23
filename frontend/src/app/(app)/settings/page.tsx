"use client";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { ErrorBox, Skeleton, useApi, useToast } from "@/components/ui";

export default function Settings() {
  const { data, error, reload } = useApi<any>("/settings"); const toast = useToast();
  const [fee, setFee] = useState(""); const [tol, setTol] = useState(""); const [loc, setLoc] = useState(""); const [acct, setAcct] = useState(""); const [v, setV] = useState({ name: "", contact_email: "", payment_terms_days: "30", aliases: "" });
  useEffect(() => { if (data) { setFee(String(Number(data.organization.success_fee_rate) * 100)); setTol(data.organization.default_tolerance_pct); } }, [data]);
  if (error) return <ErrorBox msg={error} />; if (!data) return <Skeleton className="h-64" />;
  const wrap = (fn: () => Promise<any>, ok: string) => async () => { try { await fn(); toast(ok); reload(); } catch (e) { toast((e as Error).message, "err"); } };
  return (<div className="space-y-4"><h1 className="text-xl font-semibold">Settings</h1>
    <section className="card space-y-2"><h2 className="font-semibold">Billing & audit defaults</h2><div className="flex flex-wrap items-end gap-3">
      <label className="text-sm">Success fee %<input className="input mt-1 !w-28" inputMode="decimal" value={fee} onChange={(e) => setFee(e.target.value)} /></label>
      <label className="text-sm">Default tolerance %<input className="input mt-1 !w-28" inputMode="decimal" value={tol} onChange={(e) => setTol(e.target.value)} /></label>
      <button className="btn btn-primary" onClick={wrap(() => api("/settings", { method: "PATCH", json: { success_fee_rate: String(Number(fee) / 100), default_tolerance_pct: tol } }), "Saved")}>Save</button></div>
      <p className="text-xs text-slate-500">The success fee applies only to money actually recovered (credit memo / refund logged), never to the amount claimed.</p></section>
    <section className="card"><h2 className="font-semibold">Invoice forwarding address</h2><p className="mt-1 text-sm">Forward supplier invoices to <code data-testid="inbox">invoices+{data.organization.inbox_slug}@&lt;your-inbound-domain&gt;</code></p></section>
    <section className="card space-y-2"><h2 className="font-semibold">Locations</h2><ul className="text-sm">{data.locations.map((l: any) => <li key={l.id} className="flex justify-between border-b py-1 dark:border-slate-800">{l.name} {l.account_number && <span className="text-slate-500">#{l.account_number}</span>}
        <button className="text-xs text-red-600 underline" onClick={wrap(() => api(`/locations/${l.id}`, { method: "DELETE" }), "Deleted")}>delete</button></li>)}</ul>
      <div className="flex gap-2"><input aria-label="Location name" className="input" placeholder="Location name" value={loc} onChange={(e) => setLoc(e.target.value)} /><input aria-label="Account number" className="input" placeholder="Account #" value={acct} onChange={(e) => setAcct(e.target.value)} />
        <button className="btn" onClick={wrap(async () => { await api("/locations", { json: { name: loc, account_number: acct || null } }); setLoc(""); setAcct(""); }, "Location added")}>Add</button></div></section>
    <section className="card space-y-2"><h2 className="font-semibold">Vendors</h2><ul className="text-sm">{data.vendors.map((x: any) => <li key={x.id} className="border-b py-1 dark:border-slate-800">{x.name} <span className="text-slate-500">· {x.contact_email || "no email"} · net {x.payment_terms_days}</span></li>)}</ul>
      <div className="grid gap-2 sm:grid-cols-5"><input aria-label="Vendor name" className="input" placeholder="Name" value={v.name} onChange={(e) => setV({ ...v, name: e.target.value })} />
        <input aria-label="Vendor email" className="input" placeholder="AR email" value={v.contact_email} onChange={(e) => setV({ ...v, contact_email: e.target.value })} />
        <input aria-label="Aliases" className="input" placeholder="Aliases, comma separated" value={v.aliases} onChange={(e) => setV({ ...v, aliases: e.target.value })} />
        <input aria-label="Payment terms days" className="input" value={v.payment_terms_days} onChange={(e) => setV({ ...v, payment_terms_days: e.target.value })} />
        <button className="btn" onClick={wrap(async () => { await api("/vendors", { json: { name: v.name, contact_email: v.contact_email || null, payment_terms_days: Number(v.payment_terms_days) || 30, aliases: v.aliases.split(",").map((s) => s.trim()).filter(Boolean) } }); setV({ name: "", contact_email: "", payment_terms_days: "30", aliases: "" }); }, "Vendor added")}>Add</button></div></section></div>);
}
