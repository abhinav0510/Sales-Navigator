/**
 * One search: live progress, results by Quality Judge decision, the Quality Report
 * (why each company was or wasn't delivered), CSV export and the event timeline.
 * Polls every 2 seconds while the search is running.
 */
"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { AppShell } from "@/components/AppShell";
import { Badge, Button, Card, ConfidenceBar, DecisionBadge, Empty, ErrorBox, Spinner, Stat, StatusBadge, cx } from "@/components/ui";
import { api, downloadCsv } from "@/lib/api";
import { PATH_NAMES, REASON_LABELS, pct, rupees, when } from "@/lib/format";
import type { Lead, Search } from "@/lib/types";

const STAGES = [["discovery", "Finding companies"], ["scoring", "Scoring fit"], ["enrich_and_judge", "Contacts & quality checks"], ["done", "Done"]];
type Tab = "delivered" | "hold" | "reject" | "timeline";

export default function SearchDetailPage() {
  const { id } = useParams<{ id: string }>();
  const [search, setSearch] = useState<Search | null>(null);
  const [leads, setLeads] = useState<Lead[]>([]);
  const [timeline, setTimeline] = useState<{ at: string; type: string; message: string }[]>([]);
  const [tab, setTab] = useState<Tab>("delivered");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState<string | null>(null);

  // State is only set inside the promise callbacks, never synchronously in an effect.
  const load = useCallback(
    () =>
      Promise.all([api<Search>(`/v1/searches/${id}`), api<Lead[]>(`/v1/searches/${id}/leads`)])
        .then(([s, l]) => {
          setSearch(s);
          setLeads(l);
        })
        .catch(setError),
    [id],
  );

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (!search || !["RUNNING", "PLANNING"].includes(search.status)) return;
    const t = setInterval(load, 2000);
    return () => clearInterval(t);
  }, [search, load]);

  useEffect(() => {
    if (tab === "timeline") api<typeof timeline>(`/v1/searches/${id}/timeline`).then(setTimeline).catch(setError);
  }, [tab, id]);

  async function accept(leadId: string) {
    setBusy(leadId);
    try {
      await api(`/v1/leads/${leadId}/accept`, { body: {} });
      await load();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(null);
    }
  }

  if (!search) return <AppShell title="Search">{error ? <ErrorBox error={error} /> : <Spinner />}</AppShell>;

  const groups: Record<Exclude<Tab, "timeline">, Lead[]> = {
    delivered: leads.filter((l) => l.decision === "pass" || l.decision === "degrade"),
    hold: leads.filter((l) => l.decision === "hold"),
    reject: leads.filter((l) => l.decision === "reject"),
  };
  const report = search.quality_report ?? {};
  const running = search.status === "RUNNING";
  const stageIdx = STAGES.findIndex(([k]) => k === (search.stage ?? "")) ;

  return (
    <AppShell
      title={search.region_text}
      subtitle={<span className="flex flex-wrap items-center gap-2"><StatusBadge status={search.status} /> {search.plan?.paths.map((p) => PATH_NAMES[p.path]).join(" + ")} · started {when(search.created_at)}</span>}
      actions={groups.delivered.length > 0 && (
        <Button variant="secondary" loading={busy === "csv"} onClick={async () => {
          setBusy("csv");
          try { await downloadCsv(`/v1/searches/${id}/export.csv`, `leads-${search.region_text}.csv`); } catch (e) { setError(e); } finally { setBusy(null); }
        }}>Export CSV</Button>
      )}>
      <ErrorBox error={error} />

      {(running || search.status === "PAUSED_BUDGET") && (
        <Card className="mb-5">
          <div className="flex flex-wrap items-center gap-4">
            {STAGES.map(([key, label], i) => (
              <div key={key} className="flex items-center gap-2 text-sm">
                <span className={cx("grid h-6 w-6 place-items-center rounded-full text-xs font-semibold",
                  i < stageIdx ? "bg-good-soft text-good" : i === stageIdx ? "bg-brand text-brand-ink" : "bg-surface-2 text-muted")}>
                  {i < stageIdx ? "✓" : i + 1}
                </span>
                <span className={i === stageIdx ? "font-medium" : "text-muted"}>{label}</span>
                {i === stageIdx && running && <Spinner small />}
              </div>
            ))}
          </div>
          {search.status === "PAUSED_BUDGET" && (
            <div className="mt-4 flex items-center gap-3 rounded-lg bg-warn-soft p-3 text-sm text-warn">
              Paused: this search reached its budget of {rupees(search.budget_paise)}.
              <Button variant="secondary" onClick={async () => {
                await api(`/v1/searches/${id}/budget`, { body: { budget_paise: search.budget_paise * 2 } });
                load();
              }}>Double the budget and continue</Button>
            </div>
          )}
        </Card>
      )}

      <div className="mb-5 grid grid-cols-2 gap-3 md:grid-cols-5">
        <Stat label="Delivered" value={`${search.delivered_count} / ${search.target_count}`} hint="verified & billed" />
        <Stat label="Needs review" value={search.held_count} hint="not billed unless accepted" />
        <Stat label="Rejected" value={search.rejected_count} hint="never billed" />
        <Stat label="Spent" value={rupees(search.spent_paise, 2)} hint={report.cost_per_delivered_lead_paise ? `${rupees(report.cost_per_delivered_lead_paise, 2)} per lead` : `budget ${rupees(search.budget_paise)}`} />
        <Stat label="Verified emails" value={pct(report.verified_email_rate)} hint={report.avg_confidence_delivered ? `avg confidence ${pct(report.avg_confidence_delivered)}` : undefined} />
      </div>

      <div className="mb-4 flex gap-1 border-b border-border">
        {([["delivered", `Delivered (${groups.delivered.length})`], ["hold", `Needs review (${groups.hold.length})`],
          ["reject", `Rejected (${groups.reject.length})`], ["timeline", "Timeline"]] as [Tab, string][]).map(([k, label]) => (
          <button key={k} onClick={() => setTab(k)}
            className={cx("-mb-px border-b-2 px-3 py-2 text-sm", tab === k ? "border-brand font-medium text-brand" : "border-transparent text-muted hover:text-text")}>
            {label}
          </button>
        ))}
      </div>

      {tab === "timeline" ? (
        <Card>
          <ol className="space-y-2 text-sm">
            {timeline.map((t, i) => (
              <li key={i} className="flex gap-3">
                <span className="w-20 shrink-0 font-mono text-xs text-muted">{new Date(t.at).toLocaleTimeString("en-IN")}</span>
                <Badge tone={t.type === "guardrail" || t.type === "compliance" ? "warn" : t.type === "judge" ? "brand" : "neutral"}>{t.type}</Badge>
                <span>{t.message}</span>
              </li>
            ))}
          </ol>
        </Card>
      ) : groups[tab].length === 0 ? (
        <Empty title={running ? "Working on it…" : "Nothing here"}>
          {tab === "hold" ? "Rows the Quality Judge was not fully confident about appear here. You only pay if you accept one." : running ? "Leads appear as soon as they pass the quality checks." : undefined}
        </Empty>
      ) : (
        <div className="overflow-x-auto rounded-xl border border-border bg-surface">
          <table className="w-full text-sm">
            <thead className="bg-surface-2 text-left text-xs uppercase tracking-wide text-muted">
              <tr><th className="px-4 py-3">Company</th><th className="px-4 py-3">Decision-maker</th><th className="px-4 py-3">Contact</th>
                <th className="px-4 py-3">Fit</th><th className="px-4 py-3">Confidence</th><th className="px-4 py-3"></th></tr>
            </thead>
            <tbody>
              {groups[tab].map((l) => (
                <tr key={l.id} className="border-t border-border align-top hover:bg-surface-2">
                  <td className="px-4 py-3">
                    <Link href={`/leads/${l.id}`} className="font-medium text-brand">{l.company.name}</Link>
                    <div className="text-xs text-muted">{l.company.city} · {l.company.size_band ?? "?"} · {l.company.employees ?? "?"} people</div>
                  </td>
                  <td className="px-4 py-3">{l.person?.name ?? "—"}<div className="text-xs text-muted">{l.person?.title}</div></td>
                  <td className="px-4 py-3 text-xs">
                    {l.contacts?.email && <div className="flex items-center gap-1.5"><span className="font-mono">{l.contacts.email.value}</span>
                      <Badge tone={l.contacts.email.status === "valid" ? "good" : "warn"}>{l.contacts.email.status}</Badge></div>}
                    {l.contacts?.phone && <div className="mt-1 font-mono text-muted">{l.contacts.phone.value}</div>}
                    {!l.contacts && <span className="text-muted">hidden</span>}
                  </td>
                  <td className="px-4 py-3"><Badge tone={l.fit === "strong" ? "good" : "neutral"}>{l.fit}</Badge><div className="mt-1 text-xs text-muted">score {Math.round(l.final_score)}</div></td>
                  <td className="px-4 py-3"><ConfidenceBar value={l.confidence} />
                    {l.reason_codes.length > 0 && <div className="mt-1 text-xs text-muted">{l.reason_codes.map((c) => REASON_LABELS[c] ?? c).join(", ")}</div>}
                  </td>
                  <td className="px-4 py-3 text-right">
                    {l.decision === "hold" ? <Button variant="secondary" loading={busy === l.id} onClick={() => accept(l.id)}>Accept (1 lead)</Button>
                      : <DecisionBadge decision={l.decision} />}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {search.status === "COMPLETED" && (
        <Card title="Quality report" className="mt-5">
          <div className="grid gap-5 text-sm md:grid-cols-3">
            <div>
              <div className="mb-2 font-medium">Filtered out before any spend</div>
              <ul className="space-y-1">{Object.entries(report.dropped_before_judge ?? {}).map(([k, v]) => (
                <li key={k} className="flex justify-between"><span className="text-muted">{REASON_LABELS[k] ?? k}</span><span className="tabular-nums">{String(v)}</span></li>))}
              </ul>
            </div>
            <div>
              <div className="mb-2 font-medium">Quality Judge decisions</div>
              <ul className="space-y-1">{Object.entries(report.decisions ?? {}).map(([k, v]) => (
                <li key={k} className="flex justify-between"><DecisionBadge decision={k} /><span className="tabular-nums">{String(v)}</span></li>))}
              </ul>
            </div>
            <div>
              <div className="mb-2 font-medium">Security guardrails</div>
              <ul className="space-y-1 text-muted">
                <li className="flex justify-between"><span>Injection attempts removed</span><span className="tabular-nums text-text">{report.discovery?.injections_removed ?? 0}</span></li>
                <li className="flex justify-between"><span>Unsafe links blocked</span><span className="tabular-nums text-text">{report.discovery?.unsafe_links_blocked ?? 0}</span></li>
                <li className="flex justify-between"><span>AI calls</span><span className="tabular-nums text-text">{report.ai?.calls ?? 0}</span></li>
              </ul>
            </div>
          </div>
          {search.delivered_count < search.target_count && (
            <p className="mt-4 text-sm text-muted">Delivered {search.delivered_count} of {search.target_count}: every remaining candidate was filtered or judged not confident enough. Unused leads went back to your plan.</p>
          )}
        </Card>
      )}
    </AppShell>
  );
}
