/**
 * Customer profile (ICP) builder.
 * Step 1: the user describes their business (+ optional website and existing customers).
 * Step 2: the system drafts an ICP; every field is editable. Saving creates a new version.
 */
"use client";

import Link from "next/link";
import { useEffect, useState, type FormEvent } from "react";

import { AppShell } from "@/components/AppShell";
import { Badge, Button, Card, Chips, ErrorBox, Field, Input, TextArea } from "@/components/ui";
import { api } from "@/lib/api";
import type { Icp, IcpBody } from "@/lib/types";

const LIST_FIELDS: { key: keyof IcpBody; label: string; hint: string }[] = [
  { key: "industries", label: "Industries", hint: "Kinds of business you sell to" },
  { key: "industry_keywords", label: "Words that identify a fit", hint: "Used to find and match companies" },
  { key: "negative_keywords", label: "Words that rule a company out", hint: "e.g. wholesale, bulk contracts" },
  { key: "target_size", label: "Company sizes", hint: "micro, smb, mid, enterprise" },
  { key: "technologies", label: "Technologies", hint: "Platforms your customers use" },
  { key: "personas", label: "Decision-makers (in priority order)", hint: "Who we contact first" },
  { key: "pains", label: "Problems you solve", hint: "Evidence of these raises the fit score" },
  { key: "triggers", label: "Buying triggers", hint: "e.g. hiring, expansion" },
  { key: "exclusions", label: "Never target (domains)", hint: "Your existing customers are added automatically" },
];

export default function OnboardingPage() {
  const [icp, setIcp] = useState<Icp | null>(null);
  const [draft, setDraft] = useState({ business_name: "", description: "", website_url: "", seeds: "" });
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    api<Icp[]>("/v1/icp").then((list) => list[0] && setIcp(list[0])).catch(setError);
  }, []);

  async function build(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const seeds = draft.seeds.split(/[\n,]/).map((s) => s.trim()).filter(Boolean);
      setIcp(await api<Icp>("/v1/icp/draft", {
        body: { business_name: draft.business_name, description: draft.description,
                website_url: draft.website_url || null, seeds },
      }));
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  async function save() {
    if (!icp) return;
    setBusy(true);
    setError(null);
    try {
      setIcp(await api<Icp>(`/v1/icp/${icp.id}`, { method: "PUT", body: icp.body }));
      setSaved(true);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  const setList = (key: keyof IcpBody, value: string) =>
    icp && setIcp({ ...icp, body: { ...icp.body, [key]: value.split(",").map((s) => s.trim()).filter(Boolean) } });

  return (
    <AppShell title="Customer profile" subtitle="Who should we find for you? The better this profile, the better every search.">
      {!icp ? (
        <Card title="Describe your business">
          <form onSubmit={build} className="grid gap-4 md:grid-cols-2">
            <Field label="Business name"><Input required value={draft.business_name} onChange={(e) => setDraft({ ...draft, business_name: e.target.value })} /></Field>
            <Field label="Website (optional)" hint="We read a few pages to understand what you sell">
              <Input placeholder="https://yourcompany.com" value={draft.website_url} onChange={(e) => setDraft({ ...draft, website_url: e.target.value })} />
            </Field>
            <div className="md:col-span-2">
              <Field label="What do you sell, and to whom?" hint="One or two sentences is enough">
                <TextArea required minLength={10} value={draft.description}
                  placeholder="e.g. Inventory software for D2C brands selling on Shopify and Amazon"
                  onChange={(e) => setDraft({ ...draft, description: e.target.value })} />
              </Field>
            </div>
            <div className="md:col-span-2">
              <Field label="A few of your best existing customers (optional, recommended)"
                hint="Domains, comma or line separated. We find companies like them - and never send them back to you.">
                <TextArea value={draft.seeds} placeholder="pawpantry.example, chocotribe.example"
                  onChange={(e) => setDraft({ ...draft, seeds: e.target.value })} />
              </Field>
            </div>
            <div className="md:col-span-2 flex items-center gap-3">
              <Button type="submit" loading={busy}>Build my customer profile</Button>
              <ErrorBox error={error} />
            </div>
          </form>
        </Card>
      ) : (
        <div className="space-y-5">
          <Card title={<span className="flex items-center gap-2">Your ideal customer <Badge tone="brand">v{icp.version}</Badge></span>}
            actions={<Button variant="ghost" onClick={() => { setIcp(null); setSaved(false); }}>Start over</Button>}>
            <p className="mb-4 text-sm text-muted">{icp.body.summary}</p>
            <div className="grid gap-4 md:grid-cols-2">
              {LIST_FIELDS.map((f) => (
                <Field key={f.key} label={f.label} hint={f.hint}>
                  <Input defaultValue={(icp.body[f.key] as string[]).join(", ")} onBlur={(e) => setList(f.key, e.target.value)} />
                </Field>
              ))}
            </div>
          </Card>
          {icp.body.segments?.length > 0 && (
            <Card title="Suggested starting segments">
              <p className="mb-3 text-sm text-muted">Focused segments get better leads than one broad search. Start with one.</p>
              <div className="grid gap-3 md:grid-cols-2">
                {icp.body.segments.map((s) => (
                  <div key={s.label} className="rounded-lg border border-border p-3">
                    <div className="text-sm font-medium">{s.label}</div>
                    <div className="text-sm text-muted">{s.description}</div>
                  </div>
                ))}
              </div>
            </Card>
          )}
          <div className="flex flex-wrap items-center gap-3">
            <Button onClick={save} loading={busy}>Save profile</Button>
            {saved && <Badge tone="good">Saved as v{icp.version}</Badge>}
            <Link href={`/searches/new?icp=${icp.id}`} className="rounded-lg border border-border bg-surface px-3.5 py-2 text-sm font-medium hover:bg-surface-2">
              Find leads with this profile →
            </Link>
            <ErrorBox error={error} />
          </div>
          <Card title="Targeting at a glance">
            <div className="grid gap-3 text-sm md:grid-cols-3">
              <div><div className="mb-1 text-muted">Sells to</div><Chips items={icp.body.industries} tone="brand" /></div>
              <div><div className="mb-1 text-muted">Contacts</div><Chips items={icp.body.personas} /></div>
              <div><div className="mb-1 text-muted">Excludes</div><Chips items={icp.body.exclusions} /></div>
            </div>
          </Card>
        </div>
      )}
    </AppShell>
  );
}
