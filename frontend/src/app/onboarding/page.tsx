"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { useToast } from "@/components/ui";

export default function Onboarding() {
  const r = useRouter(); const toast = useToast(); const [name, setName] = useState("");
  return (
    <main className="mx-auto mt-24 max-w-sm p-4">
      <h1 className="mb-1 text-2xl font-bold">Set up your restaurant group</h1>
      <p className="mb-4 text-sm text-slate-500">This creates your private workspace. Your data is never visible to other organizations.</p>
      <form className="card space-y-3" onSubmit={async (e) => { e.preventDefault(); try { await api("/onboarding", { json: { org_name: name } }); r.push("/settings"); } catch (er) { toast((er as Error).message, "err"); } }}>
        <label className="block text-sm">Organization name<input className="input mt-1" required value={name} onChange={(e) => setName(e.target.value)} /></label>
        <button className="btn btn-primary w-full">Create workspace</button>
      </form>
    </main>
  );
}
