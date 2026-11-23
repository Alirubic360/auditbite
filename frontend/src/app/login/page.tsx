"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { signIn } from "@/lib/api";

export default function Login() {
  const r = useRouter(); const [email, setEmail] = useState(""); const [pw, setPw] = useState(""); const [err, setErr] = useState(""); const [busy, setBusy] = useState(false);
  const go = async (signUp: boolean) => { setBusy(true); setErr(""); try { await signIn(email, pw, signUp); r.push("/dashboard"); } catch (e) { setErr((e as Error).message); } setBusy(false); };
  return (
    <main className="mx-auto mt-24 max-w-sm p-4">
      <h1 className="mb-4 text-2xl font-bold text-emerald-600">AuditBite</h1>
      <form className="card space-y-3" onSubmit={(e) => { e.preventDefault(); go(false); }}>
        <label className="block text-sm">Email<input className="input mt-1" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} /></label>
        <label className="block text-sm">Password<input className="input mt-1" type="password" required minLength={8} value={pw} onChange={(e) => setPw(e.target.value)} /></label>
        {err && <p role="alert" className="text-sm text-red-600">{err}</p>}
        <div className="flex gap-2"><button className="btn btn-primary flex-1" disabled={busy}>Sign in</button><button type="button" className="btn flex-1" disabled={busy} onClick={() => go(true)}>Create account</button></div>
      </form>
    </main>
  );
}
