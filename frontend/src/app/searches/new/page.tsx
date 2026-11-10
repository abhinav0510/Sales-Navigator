/**
 * New search, in three steps on one screen:
 *   1. Region   - resolved against the built-in place table. Ambiguous names ask the
 *                 user to pick; unknown names show suggestions, and after 2 tries the
 *                 free-text box is replaced by options that always resolve (no dead end).
 *   2. Plan     - the Search Planner's paths, rationale and (if unsure) ONE question.
 *   3. Estimate - rupee cost + quota impact, then Run.
 */
"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { AppShell } from "@/components/AppShell";
import { Badge, Button, Card, ErrorBox, Field, Input } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import { PATH_NAMES, rupees } from "@/lib/format";
import type { GeoResolution, GeoScope, Icp, Search } from "@/lib/types";

export default function NewSearchPage() {
  const router = useRouter();
  const [icps, setIcps] = useState<Icp[]>([]);
  const [icpId, setIcpId] = useState("");
  const [region, setRegion] = useState("");
  const [attempt, setAttempt] = useState(1);
  const [resolution, setResolution] = useState<GeoResolution | null>(null);
  const [scopes, setScopes] = useState<GeoScope[]>([]);
  const [target, setTarget] = useState(20);
  const [includePrev, setIncludePrev] = useState(false);
  const [search, setSearch] = useState<Search | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    // ?icp=<id> preselects a profile (read directly: no Suspense boundary needed).
    const wanted = new URLSearchParams(window.location.search).get("icp");
    api<Icp[]>("/v1/icp").then((list) => {
      setIcps(list);
      setIcpId(wanted && list.some((i) => i.id === wanted) ? wanted : list[0]?.id ?? "");
    }).catch(setError);
  }, []);

  async function checkRegion() {
    setBusy("region");
    setError(null);
    try {
      const r = await api<GeoResolution>("/v1/geo/resolve", { body: { text: region, attempt } });
      setResolution(r);
      if (r.status === "resolved") setScopes(r.scopes);
      else setAttempt((a) => a + 1);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(null);
    }
  }

  function pick(scope: GeoScope) {
    setScopes([scope]);
    setRegion(scope.label);
    setResolution({ status: "resolved", scopes: [scope], ambiguities: [], unresolved: [], forced_choice: false, allow_map_pin: true });
  }

  async function plan() {
    setBusy("plan");
    setError(null);
    try {
      setSearch(await api<Search>("/v1/searches", {
        body: { icp_id: icpId, region_text: region, scopes, target_count: target, include_previously_delivered: includePrev },
      }));
    } catch (err) {
      setError(err);
    } finally {
      setBusy(null);
    }
  }

  async function act(path: string, body?: unknown, method = "POST") {
    if (!search) return;
    setBusy(path);
    setError(null);
    try {
      const s = await api<Search>(`/v1/searches/${search.id}/${path}`, { method, body: body ?? {} });
      if (path === "run") router.push(`/searches/${s.id}`);
      else setSearch(s);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(null);
    }
  }

  const unresolved = resolution?.status === "unresolved";
  const forced = unresolved && resolution?.forced_choice;
  const quotaError = error instanceof ApiError && error.code === "quota_exceeded";

  return (
    <AppShell title="New search" subtitle="Pick a region and how many leads you want. You see the plan and cost before anything is spent.">
      {icps.length === 0 ? (
        <Card><p className="text-sm">First, <a className="text-brand" href="/onboarding">build your customer profile</a> - every search uses it.</p></Card>
      ) : (
        <div className="grid gap-5 lg:grid-cols-[1fr_380px]">
          <div className="space-y-5">
            <Card title="1. Where should we look?">
              <div className="space-y-4">
                <Field label="Customer profile">
                  <select className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm" value={icpId} onChange={(e) => setIcpId(e.target.value)}>
                    {icps.map((i) => <option key={i.id} value={i.id}>{i.body.industries.join(", ") || "Profile"} (v{i.version})</option>)}
                  </select>
                </Field>
                {!forced && (
                  <Field label="Region" hint='A country, state, city, list or radius - e.g. "Maharashtra", "Pune, Mumbai", "within 25 km of Pune", "global"'>
                    <div className="flex gap-2">
                      <Input value={region} placeholder="Maharashtra" onChange={(e) => { setRegion(e.target.value); setScopes([]); setResolution(null); }}
                        onKeyDown={(e) => e.key === "Enter" && region && checkRegion()} />
                      <Button variant="secondary" onClick={checkRegion} disabled={!region} loading={busy === "region"}>Check</Button>
                    </div>
                  </Field>
                )}
                {resolution?.status === "resolved" && (
                  <div className="flex flex-wrap items-center gap-2 text-sm">
                    <span className="text-muted">Searching in</span>
                    {scopes.map((s) => <Badge key={s.label} tone="good">{s.label}</Badge>)}
                  </div>
                )}
                {resolution?.status === "ambiguous" && resolution.ambiguities.map((a) => (
                  <div key={a.text} className="rounded-lg bg-warn-soft p-3 text-sm">
                    <div className="mb-2 font-medium text-warn">&ldquo;{a.text}&rdquo; matches more than one place. Which one?</div>
                    <div className="flex flex-wrap gap-2">{a.options.map((o) => <Button key={o.label} variant="secondary" onClick={() => pick(o)}>{o.label}</Button>)}</div>
                  </div>
                ))}
                {unresolved && resolution.unresolved.map((u) => (
                  <div key={u.text} className="rounded-lg bg-warn-soft p-3 text-sm">
                    <div className="mb-2 font-medium text-warn">
                      We don&apos;t know &ldquo;{u.text}&rdquo; yet.{forced ? " Please choose one of these instead:" : " Did you mean:"}
                    </div>
                    <div className="flex flex-wrap gap-2">
                      {[...u.parent, ...u.did_you_mean].map((o) => <Button key={o.label} variant="secondary" onClick={() => pick(o)}>{o.label}</Button>)}
                      {forced && ["Maharashtra", "Karnataka", "Delhi", "India"].map((name) => (
                        <Button key={name} variant="ghost" onClick={async () => {
                          const r = await api<GeoResolution>("/v1/geo/resolve", { body: { text: name } });
                          if (r.scopes[0]) pick(r.scopes[0]);
                        }}>{name}</Button>
                      ))}
                    </div>
                    {forced && <p className="mt-2 text-xs text-muted">We&apos;ve logged &ldquo;{u.text}&rdquo; so it can be added. Tip: search the nearest city with a radius, e.g. &ldquo;within 10 km of Pune&rdquo;.</p>}
                    {forced && (
                      <div className="mt-3 flex gap-2">
                        <Input placeholder="within 10 km of Pune" onKeyDown={async (e) => {
                          if (e.key !== "Enter") return;
                          const r = await api<GeoResolution>("/v1/geo/resolve", { body: { text: (e.target as HTMLInputElement).value } });
                          if (r.status === "resolved" && r.scopes[0]) pick(r.scopes[0]);
                        }} />
                      </div>
                    )}
                  </div>
                ))}
                <div className="grid gap-4 sm:grid-cols-2">
                  <Field label="How many leads?" hint="Only delivered, verified leads count against your plan">
                    <Input type="number" min={1} max={1000} value={target} onChange={(e) => setTarget(Number(e.target.value))} />
                  </Field>
                  <label className="mt-6 flex items-start gap-2 text-sm">
                    <input type="checkbox" className="mt-0.5" checked={includePrev} onChange={(e) => setIncludePrev(e.target.checked)} />
                    <span>Include leads already delivered to me<span className="block text-xs text-muted">Off by default - you never pay twice for the same lead.</span></span>
                  </label>
                </div>
                <Button onClick={plan} disabled={!icpId || scopes.length === 0 || !!search} loading={busy === "plan"}>Plan this search</Button>
              </div>
            </Card>

            {search && (
              <Card title="2. How we'll search" actions={<Badge tone={search.plan && search.plan.confidence >= 0.6 ? "good" : "warn"}>
                confidence {Math.round((search.plan?.confidence ?? 0) * 100)}%</Badge>}>
                {search.question && (
                  <div className="mb-4 rounded-lg bg-brand-soft p-3">
                    <div className="mb-2 text-sm font-medium">{search.question.text}</div>
                    <div className="flex flex-wrap gap-2">
                      {search.question.options.map((o, i) => (
                        <Button key={o.label} variant="secondary" loading={busy === "clarify"} onClick={() => act("clarify", { option_index: i })}>{o.label}</Button>
                      ))}
                    </div>
                  </div>
                )}
                <div className="space-y-2">
                  {search.plan?.paths.map((p) => (
                    <div key={p.path} className="flex items-center gap-3">
                      <div className="w-56 text-sm font-medium">{PATH_NAMES[p.path] ?? p.path}</div>
                      <div className="h-2 flex-1 overflow-hidden rounded-full bg-surface-2"><div className="h-full bg-brand" style={{ width: `${p.share * 100}%` }} /></div>
                      <div className="w-10 text-right text-sm tabular-nums">{Math.round(p.share * 100)}%</div>
                    </div>
                  ))}
                </div>
                <ul className="mt-4 list-disc space-y-1 pl-5 text-sm text-muted">{search.plan?.rationale.map((r) => <li key={r}>{r}</li>)}</ul>
                <div className="mt-4 flex flex-wrap items-center gap-2 text-sm">
                  <span className="text-muted">Change approach:</span>
                  {["B2B_COMPANY", "LOCAL_BUSINESS", "LOOKALIKE"].map((p) => (
                    <Button key={p} variant="ghost" onClick={() => act("plan", { path: p }, "PUT")}>{PATH_NAMES[p]} only</Button>
                  ))}
                </div>
              </Card>
            )}
          </div>

          <div className="space-y-5">
            {search?.estimate && (
              <Card title="3. Cost before you run">
                <dl className="space-y-2 text-sm">
                  <Row k="Estimated cost" v={rupees(search.estimate.cost_paise, 2)} />
                  <Row k="Per delivered lead" v={rupees(search.estimate.cost_per_lead_paise, 2)} />
                  <Row k="Budget cap" v={rupees(search.budget_paise, 2)} />
                  <Row k="Companies to check" v={search.estimate.candidate_companies} />
                  <Row k="Web searches" v={search.estimate.web_searches} />
                  <Row k="Leads reserved from plan" v={search.target_count} />
                </dl>
                <p className="mt-3 text-xs text-muted">Unused leads are returned to your plan when the search ends. Rows that fail our quality checks are never charged.</p>
                <Button className="mt-4 w-full" disabled={search.status !== "ESTIMATED"} loading={busy === "run"} onClick={() => act("run")}>
                  {search.status === "NEEDS_INPUT" ? "Answer the question above first" : "Run search"}
                </Button>
              </Card>
            )}
            <ErrorBox error={error} />
            {quotaError && <a href="/settings" className="block text-sm text-brand">Upgrade your plan →</a>}
          </div>
        </div>
      )}
    </AppShell>
  );
}

function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return <div className="flex justify-between"><dt className="text-muted">{k}</dt><dd className="font-medium tabular-nums">{v}</dd></div>;
}
