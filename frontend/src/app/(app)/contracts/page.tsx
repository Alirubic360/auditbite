"use client";
import { useState } from "react";
import { api, money } from "@/lib/api";
import { Empty, ErrorBox, Skeleton, useApi, useToast, Badge } from "@/components/ui";

type Item = { sku: string | null; canonical_name: string; unit: string; pack_size: string | null; contract_price: string; tolerance_pct: string; aliases: string[]; needs_index: boolean; source_clause: string | null };

export default function Contracts() {
  const list = useApi<any[]>("/contracts"); const settings = useApi<any>("/settings"); const toast = useToast();
  const [draft, setDraft] = useState<{ items: Item[]; vendor_id: string; reference: string; valid_from: string; valid_to: string; file_path?: string; rebate_rules: any[]; warnings: string[] } | null>(null);
  const [busy, setBusy] = useState(false);
  const start = async (kind: "extract" | "csv", f?: File) => {
    if (!f) return; setBusy(true);
    try {
      const fd = new FormData(); fd.append("file", f);
      const r = await api<any>(kind === "extract" ? "/contracts/extract" : "/contracts/parse-csv", { form: fd });
      const vendor = settings.data?.vendors.find((v: any) => v.name.toLowerCase() === (r.vendor_name || "").toLowerCase());
      setDraft({ items: r.items, vendor_id: vendor?.id || "", reference: r.reference || "", valid_from: r.valid_from || "", valid_to: r.valid_to || "", file_path: r.file_path, rebate_rules: r.rebate_rules || [], warnings: r.warnings || [] });
    } catch (e) { toast((e as Error).message, "err"); } setBusy(false);
  };
  const upd = (i: number, k: keyof Item, v: any) => setDraft((d) => d && { ...d, items: d.items.map((it, n) => n === i ? { ...it, [k]: v } : it) });
  const save = async () => {
    if (!draft) return; if (!draft.vendor_id) return toast("Choose the vendor first", "err");
    try { await api("/contracts", { json: { vendor_id: draft.vendor_id, reference: draft.reference || null, valid_from: draft.valid_from || null, valid_to: draft.valid_to || null, file_path: draft.file_path, rebate_rules: draft.rebate_rules, items: draft.items.map((i) => ({ ...i, pack_size: i.pack_size || null, sku: i.sku || null })) } });
      toast("Contract saved"); setDraft(null); list.reload(); } catch (e) { toast((e as Error).message, "err"); }
  };
  return (<div className="space-y-4"><h1 className="text-xl font-semibold">Contracts</h1>
    {!draft && <div className="card flex flex-wrap items-center gap-3"><label className="btn btn-primary cursor-pointer">Upload contract PDF (AI extraction)<input data-testid="contract-pdf" type="file" accept="application/pdf,image/*" className="hidden" disabled={busy} onChange={(e) => start("extract", e.target.files?.[0])} /></label>
      <label className="btn cursor-pointer">Import CSV<input data-testid="contract-csv" type="file" accept=".csv,text/csv" className="hidden" disabled={busy} onChange={(e) => start("csv", e.target.files?.[0])} /></label>
      <span className="text-xs text-slate-500">CSV headers: canonical_name, unit, contract_price (optional: sku, pack_size, tolerance_pct, aliases with “|”, source_clause)</span></div>}
    {draft && <section className="card space-y-3"><h2 className="font-semibold">Review before saving — nothing is audited against this until you save</h2>
      {draft.warnings.length > 0 && <ul className="ms-5 list-disc text-sm text-amber-700">{draft.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>}
      <div className="grid gap-2 sm:grid-cols-4"><label className="text-sm">Vendor<select className="input mt-1" value={draft.vendor_id} onChange={(e) => setDraft({ ...draft, vendor_id: e.target.value })}><option value="">Select…</option>{settings.data?.vendors.map((v: any) => <option key={v.id} value={v.id}>{v.name}</option>)}</select></label>
        <label className="text-sm">Reference<input className="input mt-1" value={draft.reference} onChange={(e) => setDraft({ ...draft, reference: e.target.value })} /></label>
        <label className="text-sm">Valid from<input type="date" className="input mt-1" value={draft.valid_from} onChange={(e) => setDraft({ ...draft, valid_from: e.target.value })} /></label>
        <label className="text-sm">Valid to<input type="date" className="input mt-1" value={draft.valid_to} onChange={(e) => setDraft({ ...draft, valid_to: e.target.value })} /></label></div>
      <div className="overflow-x-auto"><table className="w-full"><thead><tr><th className="th">SKU</th><th className="th">Item</th><th className="th">Unit</th><th className="th">Price</th><th className="th">Tol %</th><th className="th">Formula?</th><th className="th">Source clause</th></tr></thead>
        <tbody>{draft.items.map((it, i) => (<tr key={i} className={it.needs_index ? "bg-amber-50 dark:bg-amber-950/30" : ""}>
          <td className="td"><input aria-label="SKU" className="input" value={it.sku ?? ""} onChange={(e) => upd(i, "sku", e.target.value)} /></td>
          <td className="td"><input aria-label="Item name" className="input" value={it.canonical_name} onChange={(e) => upd(i, "canonical_name", e.target.value)} /></td>
          <td className="td"><input aria-label="Unit" className="input !w-20" value={it.unit} onChange={(e) => upd(i, "unit", e.target.value)} /></td>
          <td className="td"><input aria-label="Price" inputMode="decimal" className="input !w-24" value={it.contract_price} onChange={(e) => upd(i, "contract_price", e.target.value)} /></td>
          <td className="td"><input aria-label="Tolerance" inputMode="decimal" className="input !w-16" value={it.tolerance_pct} onChange={(e) => upd(i, "tolerance_pct", e.target.value)} /></td>
          <td className="td"><input aria-label="Needs index" type="checkbox" checked={it.needs_index} onChange={(e) => upd(i, "needs_index", e.target.checked)} /></td>
          <td className="td text-xs">{it.source_clause ?? "—"}</td></tr>))}</tbody></table></div>
      <div className="flex gap-2"><button className="btn btn-primary" onClick={save}>Save contract</button><button className="btn" onClick={() => setDraft(null)}>Discard</button></div></section>}
    {list.error ? <ErrorBox msg={list.error} /> : !list.data ? <Skeleton className="h-32" /> : list.data.length === 0 ? <Empty title="No contracts yet" hint="Upload a supplier contract or price list so invoices can be audited against it." /> :
      <div className="card overflow-x-auto p-0"><table className="w-full"><thead><tr><th className="th">Vendor</th><th className="th">Reference</th><th className="th">Valid</th><th className="th">Items</th></tr></thead><tbody>{list.data.map((c) => (
        <tr key={c.id}><td className="td">{c.vendor_name}</td><td className="td">{c.reference || "—"}</td><td className="td">{c.valid_from || "…"} → {c.valid_to || "…"}</td><td className="td">{c.item_count}</td></tr>))}</tbody></table></div>}</div>);
}
