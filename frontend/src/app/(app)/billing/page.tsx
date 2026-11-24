"use client";
import { useState } from "react";
import { api, money } from "@/lib/api";
import { Badge, Empty, ErrorBox, Skeleton, useApi, useToast } from "@/components/ui";

export default function Billing() {
  const { data, error } = useApi<any>("/billing", 5000);   // polling: status flips to paid after the on-chain confirmation
  const toast = useToast(); const [busy, setBusy] = useState("");
  const pay = async (id: string) => {
    setBusy(id);
    try { const r = await api<{ checkout_url: string }>(`/billing/invoices/${id}/checkout`, { method: "POST" }); window.location.href = r.checkout_url; }
    catch (e) { toast((e as Error).message, "err"); setBusy(""); }
  };
  if (error) return <ErrorBox msg={error} />;
  if (!data) return <Skeleton className="h-48" />;
  return (<div className="space-y-4"><h1 className="text-xl font-semibold">Billing</h1>
    <section className="card text-sm"><p>Invoices are issued monthly in <b>USD</b> and paid in cryptocurrency. Your plan: <b>{money(data.rates.subscription_per_location_usd)}</b> per location per month (first month free)
      + <b>{(Number(data.rates.success_fee_rate) * 100).toFixed(0)}%</b> of money actually recovered for you. We never charge a fee on money that was claimed but not recovered.</p>
      <p className="mt-1 text-slate-500">When you click Pay, the crypto amount is fixed for 60 minutes at the current exchange rate. Payments are confirmed on-chain; this page updates automatically.</p>
      {!data.payments_enabled && <p className="mt-2 text-amber-700">Crypto checkout is not enabled yet on this installation.</p>}</section>
    {data.invoices.length === 0 ? <Empty title="No invoices yet" hint="An invoice is created at the end of a month in which something is owed." /> :
      <div className="card overflow-x-auto p-0"><table className="w-full"><thead><tr><th className="th">Period</th><th className="th">Subscription</th><th className="th">Success fee</th><th className="th">Total (USD)</th><th className="th">Due</th><th className="th">Status</th><th className="th"></th></tr></thead>
        <tbody className="divide-y divide-slate-200 dark:divide-slate-800">{data.invoices.map((b: any) => (
          <tr key={b.id} data-testid="billing-row"><td className="td">{b.period_start.slice(0, 7)}</td><td className="td num">{money(b.subscription_amount)} <span className="text-xs text-slate-500">({b.location_count} loc.)</span></td>
            <td className="td num">{money(b.success_fee_amount)} <span className="text-xs text-slate-500">on {money(b.recovered_base)} recovered</span></td>
            <td className="td num font-semibold">{money(b.total_usd)}</td><td className="td">{b.due_at.slice(0, 10)}</td><td className="td"><Badge s={b.status} />{b.paid_at && <span className="ms-2 text-xs text-slate-500">{b.paid_at.slice(0, 10)}</span>}</td>
            <td className="td">{(b.status === "open" || b.status === "processing") && data.payments_enabled && <button className="btn btn-primary" disabled={busy === b.id} onClick={() => pay(b.id)}>{b.status === "processing" ? "Continue payment" : "Pay with crypto"}</button>}</td></tr>))}</tbody></table></div>}</div>);
}
