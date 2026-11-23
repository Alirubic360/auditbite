"use client";
import { use, useEffect, useState } from "react";
import { api, fileUrl, money } from "@/lib/api";
import { Badge, ErrorBox, Skeleton, useApi, useToast } from "@/components/ui";

export default function InvoiceDetail({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params); const toast = useToast();
  const { data: inv, error, reload } = useApi<any>(`/invoices/${id}`, 5000);
  const [file, setFile] = useState<{ url: string; media_type: string } | null>(null);
  const [edit, setEdit] = useState<{ line: string; field: string } | null>(null); const [val, setVal] = useState("");
  useEffect(() => { api(`/invoices/${id}/file-url`).then(setFile).catch(() => {}); }, [id]);
  if (error) return <ErrorBox msg={error} />;
  if (!inv) return <Skeleton className="h-96" />;
  const bad = new Set(inv.discrepancies.filter((d: any) => d.review_status !== "rejected").map((d: any) => d.line_id).filter(Boolean));
  const review = async (d: any, s: "approved" | "rejected" | "pending") => {
    let amount: string | undefined;
    if (s === "approved" && Number(d.amount) <= 0) { const a = window.prompt("This finding has no computed amount. Enter the claimable amount (or cancel):"); if (!a) return; amount = a; }
    try { await api(`/discrepancies/${d.id}`, { method: "PATCH", json: { review_status: s, amount } }); toast(`Marked ${s}`); reload(); } catch (e) { toast((e as Error).message, "err"); }
  };
  const save = async (line: any) => {
    if (!edit) return;
    try { await api(`/invoices/${id}/lines/${line.id}`, { method: "PATCH", json: { [edit.field]: val === "" ? null : val } }); setEdit(null); toast("Saved"); reload(); } catch (e) { toast((e as Error).message, "err"); }
  };
  // plain function (not a component): a component defined here would remount on every keystroke and drop input focus
  const cell = (l: any, f: string, fmt?: (v: any) => string) => edit && edit.line === l.id && edit.field === f
    ? <input autoFocus aria-label={`Edit ${f}`} className="input !w-24 !py-0.5" value={val} onChange={(e) => setVal(e.target.value)} onBlur={() => save(l)} onKeyDown={(e) => { if (e.key === "Enter") save(l); if (e.key === "Escape") setEdit(null); }} />
    : <button className="num text-start underline decoration-dotted" onClick={() => { setEdit({ line: l.id, field: f }); setVal(l[f] ?? ""); }}>{fmt ? fmt(l[f]) : l[f] ?? "—"}</button>;
  return (<div className="space-y-3">
    <div className="flex flex-wrap items-center gap-3"><h1 className="text-xl font-semibold">Invoice {inv.invoice_number || inv.id.slice(0, 8)}</h1><Badge s={inv.status} />
      <span className="text-sm text-slate-500">{inv.vendor_name || "Unknown vendor"} · {inv.invoice_date || inv.date_raw || "no date"} · confidence {inv.extraction_confidence ?? "—"}</span>
      <button className="btn ms-auto" onClick={async () => { try { await api(`/invoices/${id}/reprocess`, { method: "POST" }); toast("Queued for reprocessing"); reload(); } catch (e) { toast((e as Error).message, "err"); } }}>Reprocess</button></div>
    {inv.status === "failed" && <ErrorBox msg={`Extraction failed: ${inv.error}`} />}
    {inv.warnings?.length > 0 && <div className="card border-amber-300 text-sm"><b>Warnings</b><ul className="ms-5 list-disc">{inv.warnings.map((w: string, i: number) => <li key={i}>{w}</li>)}</ul></div>}
    <div className="grid gap-4 2xl:grid-cols-2">
      <section className="card min-h-[24rem]" aria-label="Invoice file">{file ? (file.media_type === "application/pdf"
        ? <iframe title="Invoice PDF" src={fileUrl(file.url)} className="h-[36rem] w-full rounded" /> : <img alt="Invoice" src={fileUrl(file.url)} className="max-h-[36rem] w-full object-contain" />) : <Skeleton className="h-96" />}</section>
      <section className="space-y-3">
        <div className="card overflow-x-auto p-0"><table className="w-full"><thead><tr><th className="th">#</th><th className="th">Description</th><th className="th">Qty</th><th className="th">Unit</th><th className="th">Price</th><th className="th">Contract</th><th className="th">Total</th></tr></thead>
          <tbody className="divide-y divide-slate-200 dark:divide-slate-800">{inv.lines.map((l: any) => (
            <tr key={l.id} data-problem={bad.has(l.id) ? "true" : "false"} className={bad.has(l.id) ? "bg-red-50 dark:bg-red-950/40" : l.confidence < 0.8 ? "bg-amber-50 dark:bg-amber-950/30" : ""}>
              <td className="td">{l.line_no}</td>
              <td className="td"><div>{l.description}</div><div className="text-xs text-slate-500">{l.match_type && l.match_type !== "none" ? `↔ ${l.item_name} (${l.match_type}, ${l.match_confidence})` : l.line_type === "product" ? "no contract match" : l.line_type}</div></td>
              <td className="td">{cell(l, "qty")}</td><td className="td">{cell(l, "unit")}</td><td className="td">{cell(l, "unit_price", (v) => money(v))}</td>
              <td className="td num">{l.contract_price ? <>{money(l.contract_price)}<span className="text-xs text-slate-500"> /{l.contract_unit}</span></> : "—"}</td>
              <td className="td">{cell(l, "line_total", (v) => money(v))}</td></tr>))}</tbody></table></div>
        <div className="card"><h2 className="mb-2 text-sm font-semibold">Discrepancies</h2>
          {inv.discrepancies.length === 0 ? <p className="text-sm text-slate-500">None found.</p> : <ul className="space-y-2">{inv.discrepancies.map((d: any) => (
            <li key={d.id} data-testid="discrepancy" className="rounded-lg border border-slate-200 p-2 text-sm dark:border-slate-800">
              <div className="flex flex-wrap items-center gap-2"><b>{d.type.replace("_", " ")}</b><span className="num text-bad">{money(d.amount)}</span><Badge s={d.review_status} /><span className="text-xs text-slate-500">conf {d.confidence}</span></div>
              <p className="mt-1 text-slate-600 dark:text-slate-300">{d.evidence}</p>{d.source_clause && <p className="text-xs italic text-slate-500">Clause: “{d.source_clause}”</p>}
              {d.ai_verdict && <p className="text-xs text-slate-500">AI triage: {d.ai_verdict.verdict} · defensibility {d.ai_verdict.defensibility} · likely objection: {d.ai_verdict.likely_vendor_objection}</p>}
              <div className="mt-2 flex gap-2"><button className="btn btn-primary" onClick={() => review(d, "approved")}>Approve</button><button className="btn" onClick={() => review(d, "rejected")}>Reject</button>
                {d.review_status !== "pending" && <button className="btn" onClick={() => review(d, "pending")}>Undo</button>}</div></li>))}</ul>}</div>
      </section></div></div>);
}
