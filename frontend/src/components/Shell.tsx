"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { signOut } from "@/lib/api";
import { useI18n } from "@/lib/i18n";

const NAV = [["dashboard", "/dashboard"], ["invoices", "/invoices"], ["review", "/review"], ["contracts", "/contracts"], ["claims", "/claims"], ["billing", "/billing"], ["settings", "/settings"]];
export default function Shell({ children }: { children: React.ReactNode }) {
  const path = usePathname(); const router = useRouter(); const { t } = useI18n();
  return (
    <div className="flex min-h-screen flex-col md:flex-row">
      <aside className="border-b border-slate-200 bg-white p-3 dark:border-slate-800 dark:bg-slate-900 md:w-56 md:border-b-0 md:border-e">
        <div className="mb-3 px-2 text-lg font-bold text-emerald-600">AuditBite</div>
        <nav aria-label="Main" className="flex gap-1 overflow-x-auto md:flex-col">
          {NAV.map(([k, href]) => (
            <Link key={k} href={href} aria-current={path.startsWith(href) ? "page" : undefined}
              className={`whitespace-nowrap rounded-lg px-3 py-2 text-sm ${path.startsWith(href) ? "bg-emerald-600 text-white" : "hover:bg-slate-100 dark:hover:bg-slate-800"}`}>{t(k)}</Link>))}
        </nav>
        <div className="mt-3 flex items-center gap-2 px-2 text-xs">
          <button className="underline" onClick={async () => { await signOut(); router.push("/login"); }}>{t("signout")}</button>
        </div>
      </aside>
      <main className="min-w-0 flex-1 p-4 md:p-6">{children}</main>
    </div>
  );
}
