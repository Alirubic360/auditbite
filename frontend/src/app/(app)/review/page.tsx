"use client";
import { useEffect, useState } from "react";
import { api, money } from "@/lib/api";
import { Empty, ErrorBox, Skeleton, useApi, useToast } from "@/components/ui";
import Link from "next/link";

export default function Review() {
  const { data, error, reload } = useApi<any[]>("/review"); const toast = useToast(); const [i, setI] = useState(0);
  const cur = data?.[Math.min(i, (data?.length ?? 1) - 1)];
  const act = async (s: "approved" | "rejected") => {
    if (!cur) return; let amount: string | undefined;
    if (s === "approved" && Number(cur.amount) <= 0) { const a = window.prompt("Enter the claimable amount for this finding:"); if (!a) return; amount = a; }
    try { await api(`/discrepancies/${cur.id}`, { method: "PATCH", json: { review_status: s, amount } }); toast(s === "approved" ? "Approved" : "Rejected"); await reload(); } catch (e) { toast((e as Error).message, "err"); }
  };
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement).closest("input,textarea,select")) return;
      const k = e.key.toLowerCase();
      if (k === "a") act("approved"); else if (k === "r") act("rejected");
      else if (k === "j") setI((x) => Math.min(x + 1, (data?.length ?? 1) - 1)); else if (k === "k") setI((x) => Math.max(x - 1, 0));
    };
    window.addEventListener("keydown", h); return () => window.removeEventListener("keydown", h);
  });
  if (error) return <ErrorBox msg={error} />;
  if (!data) return <Skeleton className="h-48" />;
  return (<div className="space-y-3"><h1 className="text-xl font-semibold">Review queue <span className="text-sm font-normal text-slate-500">({data.length})</span></h1>
    <p className="text-sm text-slate-500">Low-confidence or unclear findings. Shortcuts: <kbd>A</kbd> approve · <kbd>R</kbd> reject · <kbd>J</kbd>/<kbd>K</kbd> next/previous.</p>
    {data.length === 0 ? <Empty title="Queue is clear" hint="Nothing needs a second look right now." /> :
      <div className="grid gap-4 lg:grid-cols-3"><ul className="card space-y-1 p-2 lg:col-span-1" aria-label="Queue">{data.map((d, n) => (
        <li key={d.id}><button onClick={() => setI(n)} className={`w-full rounded-lg px-2 py-1.5 text-start text-sm ${cur?.id === d.id ? "bg-emerald-100 dark:bg-emerald-900" : "hover:bg-slate-100 dark:hover:bg-slate-800"}`}>
          {d.type.replace("_", " ")} · {d.vendor_name} · {d.invoice_number}</button></li>))}</ul>
        {cur && <section className="card lg:col-span-2" data-testid="review-card"><h2 className="font-semibold">{cur.type.replace("_", " ")} — {money(cur.amount)}</h2>
          <p className="text-sm text-slate-500">{cur.vendor_name} · invoice {cur.invoice_number} · {cur.invoice_date} · confidence {cur.confidence}</p>
          <p className="mt-2 text-sm">{cur.evidence}</p>{cur.ai_verdict && <p className="mt-1 text-sm text-slate-500">AI triage: {cur.ai_verdict.verdict} ({cur.ai_verdict.defensibility}). {cur.ai_verdict.recommended_action}</p>}
          <div className="mt-3 flex gap-2"><button className="btn btn-primary" onClick={() => act("approved")}>Approve (A)</button><button className="btn" onClick={() => act("rejected")}>Reject (R)</button>
            <Link className="btn ms-auto" href={`/invoices/${cur.invoice_id}`}>Open invoice</Link></div></section>}</div>}</div>);
}
