"use client";
import { useState } from "react";
import Link from "next/link";
import { api, money } from "@/lib/api";
import { Badge, Empty, ErrorBox, Skeleton, useApi, useToast } from "@/components/ui";

export default function Claims() {
  const claims = useApi<any[]>("/claims"); const avail = useApi<any[]>("/discrepancies/claimable"); const toast = useToast();
  const [status, setStatus] = useState(""); const [sel, setSel] = useState<Record<string, boolean>>({});
  const byVendor = (avail.data ?? []).reduce((m: Record<string, any[]>, d) => ((m[d.vendor_id] ||= []).push(d), m), {});
  const make = async (vendorId: string) => {
    const ids = byVendor[vendorId].filter((d) => sel[d.id]).map((d) => d.id); if (!ids.length) return toast("Select at least one discrepancy", "err");
    try { const c = await api<any>("/claims", { json: { vendor_id: vendorId, discrepancy_ids: ids } }); toast("Draft claim created"); location.href = `/claims/${c.id}`; } catch (e) { toast((e as Error).message, "err"); }
  };
  const shown = (claims.data ?? []).filter((c) => !status || c.status === status);
  return (<div className="space-y-4"><h1 className="text-xl font-semibold">Claims</h1>
    {avail.data && avail.data.length > 0 && <section className="card space-y-3"><h2 className="font-semibold">Approved discrepancies ready to claim</h2>
      {Object.entries(byVendor).map(([vid, rows]) => (<div key={vid}><div className="mb-1 flex items-center gap-3"><b>{rows[0].vendor_name}</b><button className="btn btn-primary ms-auto" onClick={() => make(vid)}>Create claim draft</button></div>
        <ul className="space-y-1 text-sm">{rows.map((d) => <li key={d.id}><label className="flex gap-2"><input type="checkbox" checked={!!sel[d.id]} onChange={(e) => setSel({ ...sel, [d.id]: e.target.checked })} />
          <span>{d.invoice_number} · {d.type.replace("_", " ")} · <span className="num text-bad">{money(d.amount)}</span></span></label></li>)}</ul></div>))}</section>}
    <select aria-label="Status" className="input !w-auto" value={status} onChange={(e) => setStatus(e.target.value)}><option value="">All statuses</option>{["draft", "sent", "disputed", "credited", "paid", "written_off"].map((s) => <option key={s}>{s}</option>)}</select>
    {claims.error ? <ErrorBox msg={claims.error} /> : !claims.data ? <Skeleton className="h-32" /> : shown.length === 0 ? <Empty title="No claims yet" hint="Approve discrepancies on an invoice, then create a claim for the vendor." /> :
      <div className="card overflow-x-auto p-0"><table className="w-full"><thead><tr><th className="th">Vendor</th><th className="th">Claimed</th><th className="th">Recovered</th><th className="th">Status</th><th className="th">Sent</th></tr></thead><tbody>{shown.map((c) => (
        <tr key={c.id}><td className="td"><Link className="text-emerald-700 underline dark:text-emerald-400" href={`/claims/${c.id}`}>{c.vendor_name}</Link></td><td className="td num">{money(c.total_claimed)}</td><td className="td num text-good">{money(c.recovered_amount)}</td><td className="td"><Badge s={c.status} /></td><td className="td">{c.sent_at?.slice(0, 10) || "—"}</td></tr>))}</tbody></table></div>}</div>);
}
