/** Shared login / signup form. */
"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { api } from "@/lib/api";
import { saveSession, type Session } from "@/lib/session";
import { Button, ErrorBox, Field, Input } from "./ui";

export function AuthForm({ mode }: { mode: "login" | "signup" }) {
  const router = useRouter();
  const [form, setForm] = useState({ email: "", password: "", full_name: "", company_name: "" });
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const set = (k: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [k]: e.target.value });

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const body = mode === "login" ? { email: form.email, password: form.password } : form;
      const session = await api<Session>(`/v1/auth/${mode}`, { body });
      saveSession(session);
      // New customers start by describing their business (builds the ICP).
      router.replace(mode === "signup" ? "/onboarding" : "/searches");
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid min-h-screen place-items-center px-4">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex items-center gap-2">
          <span className="grid h-9 w-9 place-items-center rounded-lg bg-brand font-bold text-brand-ink">SN</span>
          <span className="text-lg font-semibold">Sales Navigator</span>
        </div>
        <form onSubmit={submit} className="space-y-4 rounded-xl border border-border bg-surface p-6">
          <h1 className="text-lg font-semibold">{mode === "login" ? "Sign in" : "Create your account"}</h1>
          {mode === "signup" && (
            <>
              <Field label="Your name"><Input required value={form.full_name} onChange={set("full_name")} autoComplete="name" /></Field>
              <Field label="Company"><Input required value={form.company_name} onChange={set("company_name")} autoComplete="organization" /></Field>
            </>
          )}
          <Field label="Work email"><Input required type="email" value={form.email} onChange={set("email")} autoComplete="email" /></Field>
          <Field label="Password" hint={mode === "signup" ? "At least 8 characters" : undefined}>
            <Input required type="password" minLength={mode === "signup" ? 8 : undefined} value={form.password}
              onChange={set("password")} autoComplete={mode === "login" ? "current-password" : "new-password"} />
          </Field>
          <ErrorBox error={error} />
          <Button type="submit" loading={busy} className="w-full">{mode === "login" ? "Sign in" : "Start free trial"}</Button>
          <p className="text-center text-sm text-muted">
            {mode === "login" ? <>New here? <Link className="text-brand" href="/signup">Create an account</Link></>
              : <>Have an account? <Link className="text-brand" href="/login">Sign in</Link></>}
          </p>
        </form>
        {mode === "signup" && <p className="mt-3 text-center text-xs text-muted">Free trial: 25 verified leads. No card needed.</p>}
      </div>
    </div>
  );
}
