export const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

let supabase: any = null;
async function client() {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL, key = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  if (!url || !key) return null;
  if (!supabase) { const { createClient } = await import("@supabase/supabase-js"); supabase = createClient(url, key); }
  return supabase;
}

export async function getToken(): Promise<string | null> {
  const sb = await client();
  if (sb) { const { data } = await sb.auth.getSession(); return data.session?.access_token ?? null; }
  return process.env.NEXT_PUBLIC_DEV_JWT || (typeof window !== "undefined" ? localStorage.getItem("ab_dev_jwt") : null);
}

export async function signIn(email: string, password: string, signUp = false) {
  const sb = await client();
  if (!sb) throw new Error("Supabase is not configured (set NEXT_PUBLIC_SUPABASE_URL / ANON_KEY)");
  const { error } = signUp ? await sb.auth.signUp({ email, password }) : await sb.auth.signInWithPassword({ email, password });
  if (error) throw new Error(error.message);
}
export async function signOut() { const sb = await client(); if (sb) await sb.auth.signOut(); localStorage.removeItem("ab_dev_jwt"); }

export class ApiError extends Error { constructor(public status: number, msg: string) { super(msg); } }

export async function api<T = any>(path: string, opts: { method?: string; json?: unknown; form?: FormData } = {}): Promise<T> {
  const token = await getToken();
  const res = await fetch(API + path, {
    method: opts.method || (opts.json || opts.form ? "POST" : "GET"),
    headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}), ...(opts.json ? { "Content-Type": "application/json" } : {}) },
    body: opts.form ?? (opts.json ? JSON.stringify(opts.json) : undefined),
  });
  if (res.status === 204) return undefined as T;
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(res.status, typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? body));
  return body;
}

export const fileUrl = (u: string) => (u.startsWith("http") ? u : API + u);

/** Display-only money formatting. Amounts are exact decimal strings from the API; no arithmetic is done in the browser. */
export function money(v: string | null | undefined, cur = "USD") {
  if (v == null || v === "") return "—";
  const n = Number(v);
  return Number.isFinite(n) ? new Intl.NumberFormat(undefined, { style: "currency", currency: cur }).format(n) : v;
}
