"use client";
import { createContext, useCallback, useContext, useEffect, useRef, useState, ReactNode } from "react";
import { api, ApiError } from "@/lib/api";
import { useRouter } from "next/navigation";

const ToastCtx = createContext<(msg: string, kind?: "ok" | "err") => void>(() => {});
export const useToast = () => useContext(ToastCtx);
export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<{ id: number; msg: string; kind: string }[]>([]);
  const push = useCallback((msg: string, kind: "ok" | "err" = "ok") => {
    const id = Date.now() + Math.random();
    setItems((s) => [...s, { id, msg, kind }]);
    setTimeout(() => setItems((s) => s.filter((x) => x.id !== id)), 4000);
  }, []);
  return (<ToastCtx.Provider value={push}>{children}
    <div role="status" aria-live="polite" className="fixed bottom-4 end-4 z-50 flex flex-col gap-2">
      {items.map((i) => <div key={i.id} className={`rounded-lg px-4 py-2 text-sm text-white shadow-lg ${i.kind === "err" ? "bg-red-600" : "bg-emerald-600"}`}>{i.msg}</div>)}
    </div></ToastCtx.Provider>);
}

export function Skeleton({ className = "h-6 w-full" }: { className?: string }) { return <div className={`animate-pulse rounded bg-slate-200 dark:bg-slate-800 ${className}`} />; }
export function Empty({ title, hint, action }: { title: string; hint?: string; action?: ReactNode }) {
  return <div className="card flex flex-col items-center gap-2 py-10 text-center"><p className="font-medium">{title}</p>{hint && <p className="text-sm text-slate-500">{hint}</p>}{action}</div>;
}

const BADGE: Record<string, string> = {
  uploaded: "bg-slate-200 text-slate-800", extracting: "bg-blue-100 text-blue-800 animate-pulse", needs_review: "bg-amber-100 text-amber-800",
  audited: "bg-emerald-100 text-emerald-800", claimed: "bg-indigo-100 text-indigo-800", recovered: "bg-emerald-200 text-emerald-900", failed: "bg-red-100 text-red-800",
  draft: "bg-slate-200 text-slate-800", sent: "bg-blue-100 text-blue-800", disputed: "bg-amber-100 text-amber-800", credited: "bg-emerald-100 text-emerald-800",
  paid: "bg-emerald-200 text-emerald-900", written_off: "bg-slate-300 text-slate-700", pending: "bg-amber-100 text-amber-800", approved: "bg-emerald-100 text-emerald-800", rejected: "bg-slate-200 text-slate-700", open: "bg-amber-100 text-amber-800", processing: "bg-blue-100 text-blue-800 animate-pulse", void: "bg-slate-300 text-slate-700",
};
export const Badge = ({ s }: { s: string }) => <span className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${BADGE[s] || "bg-slate-200"}`}>{s.replace("_", " ")}</span>;

/** Minimal data hook: loads, exposes error/reload, optional polling (stands in for Supabase Realtime). */
export function useApi<T>(path: string | null, pollMs = 0) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const router = useRouter();
  const load = useCallback(async () => {
    if (!path) return;
    try { setData(await api<T>(path)); setError(null); }
    catch (e) {
      if (e instanceof ApiError && e.status === 401) router.push("/login");
      else if (e instanceof ApiError && e.status === 403) router.push("/onboarding");
      else setError((e as Error).message);
    }
  }, [path, router]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => { if (!pollMs) return; const t = setInterval(load, pollMs); return () => clearInterval(t); }, [load, pollMs]);
  return { data, error, reload: load, loading: data === null && !error };
}

export function ErrorBox({ msg }: { msg: string }) { return <div role="alert" className="card border-red-300 text-sm text-red-700 dark:text-red-300">{msg}</div>; }
