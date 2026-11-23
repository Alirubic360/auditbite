"use client";
import { useI18n } from "@/lib/i18n";
import { money } from "@/lib/api";
import { useApi, Skeleton, ErrorBox, Empty } from "@/components/ui";
import Link from "next/link";

function Kpi({ label, value, tone }: { label: string; value: string; tone?: "good" | "bad" }) {
  return <div className="card"><p className="text-xs text-slate-500">{label}</p><p className={`num mt-1 text-2xl font-semibold ${tone === "good" ? "text-good" : tone === "bad" ? "text-bad" : ""}`}>{value}</p></div>;
}

function Line({ points }: { points: { month: string; recovered: string }[] }) {
  if (points.length === 0) return null;
  const vals = points.map((p) => Number(p.recovered)); const max = Math.max(...vals, 1);
  const W = 560, H = 160, pad = 24, step = points.length > 1 ? (W - pad * 2) / (points.length - 1) : 0;
  const xy = vals.map((v, i) => [pad + i * step, H - pad - (v / max) * (H - pad * 2)]);
  return (<svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Recovery by month" className="w-full">
    <polyline fill="none" stroke="#16a34a" strokeWidth="2" points={xy.map((p) => p.join(",")).join(" ")} />
    {xy.map(([x, y], i) => <g key={i}><circle cx={x} cy={y} r="3" fill="#16a34a" /><text x={x} y={H - 6} fontSize="10" textAnchor="middle" fill="currentColor">{points[i].month}</text></g>)}
  </svg>);
}

export default function Dashboard() {
  const { t } = useI18n(); const { data, error } = useApi<any>("/dashboard", 15000);
  if (error) return <ErrorBox msg={error} />;
  if (!data) return <div className="grid gap-3 md:grid-cols-3"><Skeleton className="h-24" /><Skeleton className="h-24" /><Skeleton className="h-24" /></div>;
  const none = data.invoices_audited === 0;
  return (<div className="space-y-4">
    <h1 className="text-xl font-semibold">{t("dashboard")}</h1>
    {none && <Empty title="No invoices audited yet" hint="Upload your first supplier invoices to start finding billing variances." action={<Link className="btn btn-primary" href="/invoices">{t("upload")}</Link>} />}
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
      <Kpi label={t("audited")} value={String(data.invoices_audited)} />
      <Kpi label={t("spend")} value={money(data.total_spend_audited)} />
      <Kpi label={t("found")} value={money(data.discrepancies_found)} tone="bad" />
      <Kpi label={t("recovered")} value={money(data.recovered_cash)} tone="good" />
      <Kpi label={t("success")} value={data.success_rate == null ? "—" : `${(Number(data.success_rate) * 100).toFixed(0)}%`} />
    </div>
    <div className="grid gap-4 lg:grid-cols-3">
      <section className="card lg:col-span-2"><h2 className="mb-2 text-sm font-semibold">{t("monthly")}</h2>
        {data.recovery_by_month.length ? <Line points={data.recovery_by_month} /> : <p className="text-sm text-slate-500">No claims yet.</p>}</section>
      <section className="card"><h2 className="mb-2 text-sm font-semibold">{t("topVendors")}</h2>
        {data.top_vendors.length ? <ol className="space-y-1 text-sm">{data.top_vendors.map((v: any) => <li key={v.id} className="flex justify-between"><span>{v.name}</span><span className="num text-bad">{money(v.overcharge)}</span></li>)}</ol>
          : <p className="text-sm text-slate-500">Approved discrepancies will appear here.</p>}</section>
    </div>
  </div>);
}
