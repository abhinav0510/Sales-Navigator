/**
 * One lead: who, how to reach them, WHY they fit (each reason links to its evidence),
 * every Quality Judge check, the suggested opener, and feedback buttons.
 */
"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import { AppShell } from "@/components/AppShell";
import { Badge, Button, Card, ConfidenceBar, DecisionBadge, ErrorBox, Spinner } from "@/components/ui";
import { api } from "@/lib/api";
import { PATH_NAMES } from "@/lib/format";
import type { Lead } from "@/lib/types";

export default function LeadPage() {
  const { id } = useParams<{ id: string }>();
  const [lead, setLead] = useState<Lead | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    api<Lead>(`/v1/leads/${id}`).then(setLead).catch(setError);
  }, [id]);

  async function rate(verdict: "up" | "down") {
    await api(`/v1/leads/${id}/feedback`, { body: { verdict } });
    setFeedback(verdict);
  }

  if (!lead) return <AppShell title="Lead">{error ? <ErrorBox error={error} /> : <Spinner />}</AppShell>;
  const c = lead.contacts ?? {};

  return (
    <AppShell title={lead.company.name}
      subtitle={<span className="flex flex-wrap items-center gap-2"><DecisionBadge decision={lead.decision} /> {PATH_NAMES[lead.path]} · {lead.company.city}</span>}
      actions={<Link href={`/searches/${lead.search_id}`} className="text-sm text-brand">← Back to search</Link>}>
      <div className="grid gap-5 lg:grid-cols-[1fr_340px]">
        <div className="space-y-5">
          <Card title="Why they fit">
            <ol className="space-y-4">
              {lead.reasons.map((r, i) => (
                <li key={i} className="text-sm">
                  <div className="font-medium">{r.text}</div>
                  {r.evidence_ids.map((eid) => {
                    const ev = lead.evidence?.[eid];
                    return ev ? (
                      <blockquote key={eid} className="mt-2 border-l-2 border-brand pl-3 text-muted">
                        &ldquo;{ev.snippet.slice(0, 220)}{ev.snippet.length > 220 ? "…" : ""}&rdquo;
                        {ev.url && <a href={ev.url} target="_blank" rel="noreferrer" className="ml-2 text-xs text-brand">source ↗</a>}
                      </blockquote>
                    ) : null;
                  })}
                </li>
              ))}
            </ol>
          </Card>
          {lead.opener && (
            <Card title="Suggested opening line" actions={
              <Button variant="ghost" onClick={() => { navigator.clipboard.writeText(lead.opener!); setCopied(true); }}>{copied ? "Copied" : "Copy"}</Button>}>
              <p className="text-sm">{lead.opener}</p>
            </Card>
          )}
          <Card title="Quality Judge checks">
            <ul className="grid gap-2 sm:grid-cols-2">
              {lead.checks?.map((ch) => (
                <li key={ch.check_code} className="flex items-center gap-2 text-sm">
                  <span className={ch.passed ? "text-good" : "text-bad"}>{ch.passed ? "✓" : "✕"}</span>
                  <span>{ch.check_code.replaceAll("_", " ")}</span>
                  <Badge>{ch.layer}</Badge>
                </li>
              ))}
            </ul>
          </Card>
        </div>
        <div className="space-y-5">
          <Card title="Decision-maker">
            <div className="text-base font-semibold">{lead.person?.name ?? "—"}</div>
            <div className="text-sm text-muted">{lead.person?.title}</div>
            <dl className="mt-4 space-y-3 text-sm">
              {c.email && <ContactRow label="Email" value={c.email.value} status={c.email.status} href={`mailto:${c.email.value}`} />}
              {c.phone && <ContactRow label="Phone" value={c.phone.value} status={c.phone.type ?? c.phone.status} href={`tel:${c.phone.value}`} note="Check the DNC registry before calling" />}
              {c.whatsapp && <ContactRow label="WhatsApp" value={c.whatsapp.value} status="listed on site" href={`https://wa.me/${c.whatsapp.value.replace("+", "")}`} />}
              {c.linkedin && <ContactRow label="LinkedIn" value={c.linkedin.value.replace("https://www.", "")} status="matched" href={c.linkedin.value} />}
              {!lead.contacts && <p className="text-muted">Contact details are only shown for delivered or in-review leads.</p>}
            </dl>
          </Card>
          <Card title="Scores">
            <div className="space-y-3 text-sm">
              <div className="flex justify-between"><span className="text-muted">Fit</span><Badge tone={lead.fit === "strong" ? "good" : "neutral"}>{lead.fit}</Badge></div>
              <div className="flex justify-between"><span className="text-muted">Fit score</span><span className="tabular-nums">{Math.round(lead.final_score)} / 100</span></div>
              <div className="flex justify-between"><span className="text-muted">Confidence</span><ConfidenceBar value={lead.confidence} /></div>
              <div className="flex justify-between"><span className="text-muted">Company</span><span>{lead.company.size_band} · {lead.company.employees ?? "?"} people</span></div>
            </div>
          </Card>
          <Card title="Was this a good lead?">
            {feedback ? <p className="text-sm text-muted">Thanks - this improves future scoring.</p> : (
              <div className="flex gap-2"><Button variant="secondary" onClick={() => rate("up")}>👍 Good fit</Button><Button variant="secondary" onClick={() => rate("down")}>👎 Not a fit</Button></div>
            )}
          </Card>
        </div>
      </div>
    </AppShell>
  );
}

function ContactRow({ label, value, status, href, note }: { label: string; value: string; status: string; href: string; note?: string }) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-muted">{label}</dt>
      <dd className="mt-0.5 flex flex-wrap items-center gap-2">
        <a href={href} target="_blank" rel="noreferrer" className="font-mono text-brand">{value}</a>
        <Badge tone={status === "valid" || status === "matched" ? "good" : "neutral"}>{status}</Badge>
      </dd>
      {note && <dd className="mt-0.5 text-xs text-muted">{note}</dd>}
    </div>
  );
}
