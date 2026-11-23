"use client";
import { use, useEffect, useState } from "react";
import { api, fileUrl, money } from "@/lib/api";
import { Badge, ErrorBox, Skeleton, useApi, useToast } from "@/components/ui";

export default function ClaimDetail({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params); const toast = useToast(); const { data: c, error, reload } = useApi<any>(`/claims/${id}`);
  const [pdf, setPdf] = useState(""); const [amt, setAmt] = useState(""); const [st, setSt] = useState("credited");
  useEffect(() => { api<{ url: string }>(`/claims/${id}/pdf-url`).then((r) => setPdf(fileUrl(r.url))).catch(() => {}); }, [id]);
  if (error) return <ErrorBox msg={error} />; if (!c) return <Skeleton className="h-96" />;
  const send = async () => { if (!confirm(`Send this claim to ${c.vendor_name} <${c.contact_email}>? This emails the vendor.`)) return;
    try { await api(`/claims/${id}/send`, { method: "POST" }); toast("Claim sent"); reload(); } catch (e) { toast((e as Error).message, "err"); } };
  const credit = async () => { try { await api(`/claims/${id}/credit`, { json: { amount: amt, status: st } }); toast("Recorded"); reload(); } catch (e) { toast((e as Error).message, "err"); } };
  return (<div className="space-y-4"><div className="flex flex-wrap items-center gap-3"><h1 className="text-xl font-semibold">Claim — {c.vendor_name}</h1><Badge s={c.status} /><span className="num text-lg">{money(c.total_claimed)}</span>
      {c.status === "draft" && <button className="btn btn-primary ms-auto" onClick={send}>Send to vendor</button>}</div>
    <div className="grid gap-4 lg:grid-cols-2">
      <section className="card"><h2 className="mb-2 text-sm font-semibold">PDF preview</h2>{pdf ? <iframe title="Claim PDF" src={pdf} className="h-[32rem] w-full rounded" /> : <Skeleton className="h-96" />}</section>
      <section className="space-y-4">
        <div className="card"><h2 className="mb-2 text-sm font-semibold">Letter</h2><p className="text-sm font-medium">{c.letter_subject}</p><pre className="mt-2 whitespace-pre-wrap font-sans text-sm">{c.letter_body}</pre></div>
        <div className="card"><h2 className="mb-2 text-sm font-semibold">Timeline</h2><ol className="space-y-1 text-sm">
          <li>Created {c.created_at.slice(0, 10)}</li>{c.sent_at && <li>Sent {c.sent_at.slice(0, 10)} (response due {c.due_at.slice(0, 10)})</li>}
          {c.followups.map((f: any) => <li key={f.day}>Day-{f.day} follow-up: {f.status}{f.sent_at ? ` (${f.sent_at.slice(0, 10)})` : ` · due ${f.due_at.slice(0, 10)}`}</li>)}
          {c.status !== "draft" && Number(c.recovered_amount) > 0 && <li className="text-good">Recovered {money(c.recovered_amount)} · fee {money(c.fee_amount)}</li>}</ol></div>
        {c.status !== "draft" && <div className="card"><h2 className="mb-2 text-sm font-semibold">Log credit memo / refund</h2><div className="flex flex-wrap items-end gap-2">
          <label className="text-sm">Amount received<input aria-label="Amount received" inputMode="decimal" className="input mt-1 !w-32" value={amt} onChange={(e) => setAmt(e.target.value)} /></label>
          <select aria-label="Outcome" className="input !w-auto" value={st} onChange={(e) => setSt(e.target.value)}><option value="credited">Credit memo</option><option value="paid">Refund paid</option><option value="disputed">Vendor disputes</option><option value="written_off">Write off</option></select>
          <button className="btn btn-primary" onClick={credit}>Record</button></div></div>}
        <div className="card"><h2 className="mb-2 text-sm font-semibold">Lines</h2><ul className="space-y-1 text-sm">{c.items.map((i: any, n: number) => <li key={n}>{i.invoice_number} · {i.type.replace("_", " ")} · <span className="num">{money(i.amount)}</span></li>)}</ul></div></section></div></div>);
}
