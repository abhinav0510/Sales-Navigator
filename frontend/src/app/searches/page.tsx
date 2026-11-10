/** All searches in this workspace. */
"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { AppShell } from "@/components/AppShell";
import { Empty, ErrorBox, Spinner, StatusBadge } from "@/components/ui";
import { api } from "@/lib/api";
import { rupees, when } from "@/lib/format";
import type { Search } from "@/lib/types";

export default function SearchesPage() {
  const [items, setItems] = useState<Search[] | null>(null);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    api<Search[]>("/v1/searches").then(setItems).catch(setError);
  }, []);

  return (
    <AppShell title="Searches" subtitle="Every search, its results and what it cost."
      actions={<Link href="/searches/new" className="rounded-lg bg-brand px-3.5 py-2 text-sm font-medium text-brand-ink">New search</Link>}>
      <ErrorBox error={error} />
      {!items && !error && <div className="py-10 text-center text-muted"><Spinner /></div>}
      {items && items.length === 0 && (
        <Empty title="No searches yet" href="/searches/new" cta="Find your first leads">
          Tell us a region and we will find decision-makers who fit your customer profile.
        </Empty>
      )}
      {items && items.length > 0 && (
        <div className="overflow-x-auto rounded-xl border border-border bg-surface">
          <table className="w-full text-sm">
            <thead className="bg-surface-2 text-left text-xs uppercase tracking-wide text-muted">
              <tr>
                <th className="px-4 py-3">Region</th><th className="px-4 py-3">Status</th>
                <th className="px-4 py-3 text-right">Delivered</th><th className="px-4 py-3 text-right">Review</th>
                <th className="px-4 py-3 text-right">Cost</th><th className="px-4 py-3">Created</th>
              </tr>
            </thead>
            <tbody>
              {items.map((s) => (
                <tr key={s.id} className="border-t border-border hover:bg-surface-2">
                  <td className="px-4 py-3"><Link href={`/searches/${s.id}`} className="font-medium text-brand">{s.region_text}</Link></td>
                  <td className="px-4 py-3"><StatusBadge status={s.status} /></td>
                  <td className="px-4 py-3 text-right tabular-nums">{s.delivered_count} / {s.target_count}</td>
                  <td className="px-4 py-3 text-right tabular-nums">{s.held_count}</td>
                  <td className="px-4 py-3 text-right tabular-nums">{rupees(s.spent_paise, 2)}</td>
                  <td className="px-4 py-3 text-muted">{when(s.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </AppShell>
  );
}
