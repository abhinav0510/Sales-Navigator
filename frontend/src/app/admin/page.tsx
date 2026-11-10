/**
 * Observability console (OBSERVABILITY_AND_QA.md part F).
 * Workspace admins see their own workspace; the platform operator can switch to
 * "All workspaces". Every number comes from ledgers the pipeline already writes.
 */
"use client";

import { useEffect, useState } from "react";

import { AppShell } from "@/components/AppShell";
import { Badge, Card, ErrorBox, Spinner, Stat, cx } from "@/components/ui";
import { api } from "@/lib/api";
import { inr, pct, when } from "@/lib/format";
import { useSession } from "@/lib/session";

type Overview = {
  spend_inr: { providers: number; ai: number; total: number };
  provider_calls: number; cache_hit_rate: number | null; ai_calls: number; ai_failures: number;
  leads: { delivered: number; held: number; rejected: number };
  searches: { n: number; done: number; failed: number }; errors: number; cost_per_delivered_lead_inr: number | null;
};
type AiRow = { task: string; model: string; calls: number; success_rate: number; tokens_in: number; tokens_cached: number;
  tokens_out: number; cached_ratio: number | null; cost_inr: number; fallbacks: number; evidence_drops: number; p50: number; p95: number };
type Cost = { daily: { day: string; providers_inr: number; ai_inr: number }[]; by_provider: { provider: string; op: string; calls: number; cost_inr: number; cache_hits: number }[];
  platform_daily_cap_inr: number; today_inr: number; cap_used_pct: number };
type Provider = { provider: string; op: string; calls: number; error_rate: number; cache_hits: number; p95_ms: number; cost_inr: number };
type ErrorRow = { id: string; at: string; location: string; error_type: string; message: string; request_id: string | null };

const TABS = ["Overview", "AI calls", "Cost", "Providers", "Errors"] as const;

export default function AdminPage() {
  const [tab, setTab] = useState<(typeof TABS)[number]>("Overview");
  const [platform, setPlatform] = useState(false);
  const [data, setData] = useState<Record<string, unknown>>({});
  const [error, setError] = useState<unknown>(null);
  const isPlatform = useSession()?.role === "platform_admin";

  useEffect(() => {
    const q = `?platform=${platform}`;
    const paths: Record<string, string> = { Overview: "overview", "AI calls": "ai", Cost: "cost", Providers: "providers", Errors: "errors" };
    api(`/v1/admin/${paths[tab]}${q}`).then((d) => setData({ [tab]: d })).catch(setError);
  }, [tab, platform]);

  const ov = data.Overview as Overview | undefined;
  const ai = data["AI calls"] as { by_task: AiRow[] } | undefined;
  const cost = data.Cost as Cost | undefined;
  const providers = data.Providers as Provider[] | undefined;
  const errors = data.Errors as ErrorRow[] | undefined;
  const maxDay = cost ? Math.max(0.01, ...cost.daily.map((d) => d.providers_inr + d.ai_inr)) : 1;

  return (
    <AppShell title="Observability" subtitle="AI calls, spend, data sources and errors - last 30 days."
      actions={isPlatform && (
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={platform} onChange={(e) => setPlatform(e.target.checked)} /> All workspaces</label>
      )}>
      <div className="mb-5 flex gap-1 border-b border-border">
        {TABS.map((t) => (
          <button key={t} onClick={() => setTab(t)} className={cx("-mb-px border-b-2 px-3 py-2 text-sm",
            tab === t ? "border-brand font-medium text-brand" : "border-transparent text-muted hover:text-text")}>{t}</button>
        ))}
      </div>
      <ErrorBox error={error} />
      {!data[tab] && !error && <div className="py-10 text-center text-muted"><Spinner /></div>}

      {ov && (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <Stat label="Total spend" value={inr(ov.spend_inr.total)} hint={`providers ${inr(ov.spend_inr.providers)} · AI ${inr(ov.spend_inr.ai)}`} />
          <Stat label="Cost per delivered lead" value={inr(ov.cost_per_delivered_lead_inr)} hint="target ≤ ₹5.00" />
          <Stat label="Cache hit rate" value={pct(ov.cache_hit_rate)} hint={`${ov.provider_calls} provider calls`} />
          <Stat label="AI calls" value={ov.ai_calls} hint={`${ov.ai_failures} failed`} />
          <Stat label="Delivered leads" value={ov.leads.delivered} hint={`${ov.leads.held} in review · ${ov.leads.rejected} rejected`} />
          <Stat label="Searches" value={ov.searches.n} hint={`${ov.searches.done} completed · ${ov.searches.failed} failed`} />
          <Stat label="Errors" value={ov.errors} hint="see the Errors tab" />
        </div>
      )}

      {ai && (
        <Card>
          <Table head={["Task", "Model", "Calls", "Success", "Tokens in", "Cached", "Out", "Cache ratio", "Cost", "Fallbacks", "Evidence drops", "p95"]}>
            {ai.by_task.map((r) => (
              <tr key={r.task + r.model} className="border-t border-border">
                <td className="px-3 py-2 font-medium">{r.task}</td><td className="px-3 py-2 font-mono text-xs">{r.model}</td>
                <Num>{r.calls}</Num><Num>{pct(r.success_rate)}</Num><Num>{r.tokens_in}</Num><Num>{r.tokens_cached}</Num><Num>{r.tokens_out}</Num>
                <Num>{pct(r.cached_ratio)}</Num><Num>{inr(r.cost_inr)}</Num><Num>{r.fallbacks}</Num>
                <Num>{r.evidence_drops > 0 ? <Badge tone="warn">{r.evidence_drops}</Badge> : 0}</Num><Num>{Math.round(r.p95)} ms</Num>
              </tr>
            ))}
          </Table>
          <p className="mt-3 text-xs text-muted">&ldquo;Evidence drops&rdquo; = AI reasons removed because they cited evidence we never gave the model (hallucination or injection). A rising number is an alert.</p>
        </Card>
      )}

      {cost && (
        <div className="space-y-5">
          <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
            <Stat label="Today" value={inr(cost.today_inr)} />
            <Stat label="Platform daily cap" value={inr(cost.platform_daily_cap_inr, 0)} hint="kill-switch: paid calls stop above this" />
            <Stat label="Cap used" value={`${cost.cap_used_pct}%`} />
          </div>
          <Card title="Daily spend">
            <div className="flex h-40 items-end gap-1">
              {cost.daily.map((d) => {
                const total = d.providers_inr + d.ai_inr;
                return (
                  <div key={d.day} className="group flex flex-1 flex-col justify-end" title={`${d.day}: ${inr(total)}`}>
                    <div className="rounded-t bg-brand" style={{ height: `${(total / maxDay) * 100}%`, minHeight: total ? 2 : 0 }} />
                  </div>
                );
              })}
            </div>
            <div className="mt-2 flex justify-between text-xs text-muted"><span>{cost.daily[0]?.day}</span><span>{cost.daily.at(-1)?.day}</span></div>
          </Card>
          <Card title="By provider and operation">
            <Table head={["Provider", "Operation", "Calls", "Cache hits", "Cost"]}>
              {cost.by_provider.map((p) => (
                <tr key={p.provider + p.op} className="border-t border-border"><td className="px-3 py-2">{p.provider}</td><td className="px-3 py-2">{p.op}</td>
                  <Num>{p.calls}</Num><Num>{p.cache_hits}</Num><Num>{inr(p.cost_inr)}</Num></tr>
              ))}
            </Table>
          </Card>
        </div>
      )}

      {providers && (
        <Card>
          <Table head={["Provider", "Operation", "Calls", "Error rate", "Cache hits", "p95", "Cost"]}>
            {providers.map((p) => (
              <tr key={p.provider + p.op} className="border-t border-border"><td className="px-3 py-2">{p.provider}</td><td className="px-3 py-2">{p.op}</td>
                <Num>{p.calls}</Num><Num>{p.error_rate > 0.2 ? <Badge tone="bad">{pct(p.error_rate)}</Badge> : pct(p.error_rate)}</Num>
                <Num>{p.cache_hits}</Num><Num>{Math.round(p.p95_ms)} ms</Num><Num>{inr(p.cost_inr)}</Num></tr>
            ))}
          </Table>
        </Card>
      )}

      {errors && (errors.length === 0 ? <Card><p className="text-sm text-muted">No errors recorded. 🎉</p></Card> : (
        <Card>
          <Table head={["When", "Where", "Type", "Message", "Request"]}>
            {errors.map((e) => (
              <tr key={e.id} className="border-t border-border align-top"><td className="px-3 py-2 text-xs text-muted">{when(e.at)}</td>
                <td className="px-3 py-2 font-mono text-xs">{e.location}</td><td className="px-3 py-2"><Badge tone="bad">{e.error_type}</Badge></td>
                <td className="px-3 py-2 text-xs">{e.message.slice(0, 200)}</td><td className="px-3 py-2 font-mono text-xs">{e.request_id?.slice(0, 8)}</td></tr>
            ))}
          </Table>
        </Card>
      ))}
    </AppShell>
  );
}

function Table({ head, children }: { head: string[]; children: React.ReactNode }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="text-left text-xs uppercase tracking-wide text-muted"><tr>{head.map((h) => <th key={h} className="px-3 py-2">{h}</th>)}</tr></thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}

function Num({ children }: { children: React.ReactNode }) {
  return <td className="px-3 py-2 text-right tabular-nums">{children}</td>;
}
