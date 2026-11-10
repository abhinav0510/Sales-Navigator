/** Plan & usage, and the workspace do-not-contact (suppression) list. */
"use client";

import { useEffect, useState } from "react";

import { AppShell } from "@/components/AppShell";
import { Badge, Button, Card, ErrorBox, Field, TextArea } from "@/components/ui";
import { api } from "@/lib/api";
import { rupees, when } from "@/lib/format";
import type { Usage } from "@/lib/types";

type Plan = { code: string; name: string; price_paise: number; monthly_leads: number; seats: number; features: string[] };
type Suppression = { id: string; kind: string; reason: string; at: string; is_global: boolean };

export default function SettingsPage() {
  const [usage, setUsage] = useState<Usage | null>(null);
  const [plans, setPlans] = useState<Plan[]>([]);
  const [supp, setSupp] = useState<Suppression[]>([]);
  const [kind, setKind] = useState("email");
  const [values, setValues] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = () => {
    api<Usage>("/v1/billing/usage").then(setUsage).catch(setError);
    api<Plan[]>("/v1/billing/plans").then(setPlans).catch(setError);
    api<Suppression[]>("/v1/compliance/suppressions").then(setSupp).catch(() => setSupp([]));
  };
  useEffect(load, []);

  async function choose(code: string) {
    setBusy(code);
    try { setUsage(await api<Usage>("/v1/billing/plan", { body: { plan_code: code } })); } catch (e) { setError(e); } finally { setBusy(null); }
  }

  async function addSuppressions() {
    setBusy("supp");
    try {
      const list = values.split(/[\n,]/).map((v) => v.trim()).filter(Boolean);
      const r = await api<{ added: number }>("/v1/compliance/suppressions", { body: { kind, values: list } });
      setMsg(`${r.added} added. These contacts will never be delivered to you.`);
      setValues("");
      load();
    } catch (e) { setError(e); } finally { setBusy(null); }
  }

  return (
    <AppShell title="Plan & settings" subtitle="Prices exclude 18% GST. You are only charged for delivered, verified leads.">
      <ErrorBox error={error} />
      {usage && (
        <Card className="mb-5" title="This month">
          <div className="flex flex-wrap items-center gap-6 text-sm">
            <div><div className="text-muted">Plan</div><div className="font-semibold">{usage.plan_name} <Badge>{usage.status}</Badge></div></div>
            <div><div className="text-muted">Leads left</div><div className="font-semibold tabular-nums">{usage.remaining} / {usage.monthly_leads}</div></div>
            <div><div className="text-muted">Period</div><div className="font-semibold">{usage.period}</div></div>
          </div>
        </Card>
      )}
      <div className="mb-5 grid gap-4 md:grid-cols-4">
        {plans.map((p) => (
          <div key={p.code} className={`rounded-xl border bg-surface p-5 ${usage?.plan === p.code ? "border-brand" : "border-border"}`}>
            <div className="font-semibold">{p.name}</div>
            <div className="mt-1 text-2xl font-semibold">{p.price_paise ? rupees(p.price_paise) : "Free"}<span className="text-sm font-normal text-muted">{p.price_paise ? " /mo" : ""}</span></div>
            <ul className="mt-3 space-y-1 text-sm text-muted">
              <li>{p.monthly_leads} verified leads / month</li><li>{p.seats} seat{p.seats > 1 ? "s" : ""}</li>
              {p.features.includes("api") && <li>API access</li>}{p.features.includes("priority") && <li>Priority processing</li>}
            </ul>
            <Button className="mt-4 w-full" variant={usage?.plan === p.code ? "secondary" : "primary"} disabled={usage?.plan === p.code}
              loading={busy === p.code} onClick={() => choose(p.code)}>{usage?.plan === p.code ? "Current plan" : "Switch"}</Button>
          </div>
        ))}
      </div>
      <p className="mb-5 text-xs text-muted">Payment collection (Razorpay subscriptions + GST invoices) plugs in here; this build switches plans directly.</p>

      <Card title="Do-not-contact list">
        <p className="mb-3 text-sm text-muted">Contacts on this list are never delivered or exported. Erasure requests are added for every workspace automatically.</p>
        <div className="grid gap-3 md:grid-cols-[160px_1fr_auto]">
          <select className="rounded-lg border border-border bg-surface px-3 py-2 text-sm" value={kind} onChange={(e) => setKind(e.target.value)}>
            <option value="email">Emails</option><option value="phone">Phone numbers</option><option value="domain">Domains</option>
          </select>
          <Field label=""><TextArea placeholder="one per line or comma separated" value={values} onChange={(e) => setValues(e.target.value)} /></Field>
          <Button onClick={addSuppressions} disabled={!values.trim()} loading={busy === "supp"}>Add</Button>
        </div>
        {msg && <p className="mt-2 text-sm text-good">{msg}</p>}
        {supp.length > 0 && (
          <p className="mt-4 text-sm text-muted">{supp.length} entries ({supp.filter((s) => s.is_global).length} global erasure requests). Latest added {when(supp[0].at)}. Values are stored only as one-way hashes.</p>
        )}
      </Card>
    </AppShell>
  );
}
