"use client";
import { useCallback, useRef, useState } from "react";
import Link from "next/link";
import { api, money } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { Badge, Empty, ErrorBox, Skeleton, useApi, useToast } from "@/components/ui";

type Up = { name: string; state: "uploading" | "done" | "error"; msg?: string };

export default function Invoices() {
  const { t } = useI18n(); const toast = useToast();
  const [status, setStatus] = useState(""); const [vendor, setVendor] = useState(""); const [from, setFrom] = useState(""); const [to, setTo] = useState(""); const [loc, setLoc] = useState("");
  const qs = new URLSearchParams(Object.entries({ status, vendor_id: vendor, location_id: loc, date_from: from, date_to: to }).filter(([, v]) => v) as [string, string][]).toString();
  const { data, error, reload } = useApi<any[]>(`/invoices${qs ? "?" + qs : ""}`, 4000);   // polling = live status
  const settings = useApi<any>("/settings");
  const [ups, setUps] = useState<Up[]>([]); const [over, setOver] = useState(false); const input = useRef<HTMLInputElement>(null);

  const upload = useCallback(async (files: File[]) => {
    for (const f of files) {
      setUps((u) => [...u, { name: f.name, state: "uploading" }]);
      const fd = new FormData(); fd.append("files", f); if (loc) fd.append("location_id", loc);
      try { await api("/invoices", { form: fd }); setUps((u) => u.map((x) => x.name === f.name ? { ...x, state: "done" } : x)); }
      catch (e) { setUps((u) => u.map((x) => x.name === f.name ? { ...x, state: "error", msg: (e as Error).message } : x)); toast((e as Error).message, "err"); }
    }
    reload();
  }, [loc, reload, toast]);

  return (<div className="space-y-4">
    <h1 className="text-xl font-semibold">{t("invoices")}</h1>
    <div role="button" tabIndex={0} aria-label={t("upload")} onClick={() => input.current?.click()} onKeyDown={(e) => e.key === "Enter" && input.current?.click()}
      onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)}
      onDrop={(e) => { e.preventDefault(); setOver(false); upload(Array.from(e.dataTransfer.files)); }}
      className={`card cursor-pointer border-2 border-dashed py-8 text-center text-sm ${over ? "border-emerald-500 bg-emerald-50 dark:bg-emerald-950" : ""}`}>
      {t("drop")}<input ref={input} data-testid="file-input" type="file" multiple accept="application/pdf,image/jpeg,image/png" className="hidden" onChange={(e) => upload(Array.from(e.target.files || []))} />
    </div>
    {ups.length > 0 && <ul className="card space-y-1 text-sm">{ups.map((u, i) => <li key={i} className="flex items-center justify-between"><span>{u.name}</span>
      <span className={u.state === "error" ? "text-bad" : u.state === "done" ? "text-good" : "animate-pulse"}>{u.state === "uploading" ? "uploading…" : u.state === "done" ? "queued ✓" : u.msg}</span></li>)}</ul>}
    <div className="flex flex-wrap gap-2">
      <select aria-label="Status" className="input !w-auto" value={status} onChange={(e) => setStatus(e.target.value)}><option value="">All statuses</option>
        {["uploaded", "extracting", "needs_review", "audited", "claimed", "recovered", "failed"].map((s) => <option key={s}>{s}</option>)}</select>
      <select aria-label="Vendor" className="input !w-auto" value={vendor} onChange={(e) => setVendor(e.target.value)}><option value="">All vendors</option>{settings.data?.vendors.map((v: any) => <option key={v.id} value={v.id}>{v.name}</option>)}</select>
      <select aria-label="Location" className="input !w-auto" value={loc} onChange={(e) => setLoc(e.target.value)}><option value="">All locations</option>{settings.data?.locations.map((v: any) => <option key={v.id} value={v.id}>{v.name}</option>)}</select>
      <input aria-label="From date" type="date" className="input !w-auto" value={from} onChange={(e) => setFrom(e.target.value)} />
      <input aria-label="To date" type="date" className="input !w-auto" value={to} onChange={(e) => setTo(e.target.value)} />
    </div>
    {error ? <ErrorBox msg={error} /> : !data ? <Skeleton className="h-40" /> : data.length === 0 ? <Empty title={t("empty")} hint="Upload invoices above, or forward them to your inbox address (Settings)." /> :
      <div className="card overflow-x-auto p-0"><table className="w-full"><thead><tr><th className="th">File / invoice</th><th className="th">Vendor</th><th className="th">Date</th><th className="th">Total</th><th className="th">Discrepancies</th><th className="th">Status</th></tr></thead>
        <tbody className="divide-y divide-slate-200 dark:divide-slate-800">{data.map((i) => (
          <tr key={i.id} className="hover:bg-slate-50 dark:hover:bg-slate-800/50"><td className="td"><Link className="text-emerald-700 underline dark:text-emerald-400" href={`/invoices/${i.id}`}>{i.invoice_number || i.original_filename || i.id.slice(0, 8)}</Link></td>
            <td className="td">{i.vendor_name || "—"}</td><td className="td">{i.invoice_date || "—"}</td><td className="td num">{money(i.total, i.currency || "USD")}</td>
            <td className="td num">{i.discrepancy_count > 0 ? <span className="text-bad">{i.discrepancy_count} · {money(i.discrepancy_amount)}</span> : "—"}</td>
            <td className="td"><Badge s={i.status} />{i.status === "failed" && <span className="ms-2 text-xs text-bad">{i.error}</span>}</td></tr>))}</tbody></table></div>}
  </div>);
}
