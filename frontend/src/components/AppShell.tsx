/**
 * Authenticated layout: sidebar navigation + quota meter + page header.
 * Redirects to /login when there is no session.
 */
"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";

import { api } from "@/lib/api";
import { clearSession, getSession, isAdmin, useSession } from "@/lib/session";
import type { Usage } from "@/lib/types";
import { cx } from "./ui";

const NAV = [
  { href: "/searches", label: "Searches", icon: "M4 6h16M4 12h10M4 18h7" },
  { href: "/searches/new", label: "New search", icon: "M12 5v14M5 12h14" },
  { href: "/onboarding", label: "Customer profile", icon: "M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zm-7 9a7 7 0 0 1 14 0" },
  { href: "/admin", label: "Observability", icon: "M4 19V9m6 10V5m6 14v-7m4 7H2", admin: true },
  { href: "/settings", label: "Plan & settings", icon: "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19 12l2-1-2-4-2 .5-1.5-1.5L15 4h-4l-.5 2L9 7.5 7 7 5 11l2 1v1l-2 1 2 4 2-.5 1.5 1.5.5 2h4l.5-2 1.5-1.5 2 .5 2-4-2-1z" },
];

export function AppShell({ title, subtitle, actions, children }: { title: string; subtitle?: ReactNode; actions?: ReactNode; children: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const session = useSession();
  const [usage, setUsage] = useState<Usage | null>(null);

  useEffect(() => {
    // Read storage directly (not the hook value) so the first hydration render,
    // which has no session yet, never triggers a false redirect.
    if (!getSession()) {
      router.replace("/login");
      return;
    }
    api<Usage>("/v1/billing/usage").then(setUsage).catch(() => setUsage(null));
  }, [router, pathname]);

  if (!session) return null;

  return (
    <div className="flex min-h-screen">
      <aside className="sticky top-0 hidden h-screen w-60 shrink-0 flex-col border-r border-border bg-surface px-3 py-5 md:flex">
        <Link href="/searches" className="mb-6 flex items-center gap-2 px-2">
          <span className="grid h-8 w-8 place-items-center rounded-lg bg-brand font-bold text-brand-ink">SN</span>
          <span className="font-semibold">Sales Navigator</span>
        </Link>
        <nav className="flex flex-1 flex-col gap-1">
          {NAV.filter((n) => !n.admin || isAdmin(session)).map((n) => {
            const active = pathname === n.href || (n.href !== "/searches/new" && n.href !== "/searches" && pathname.startsWith(n.href))
              || (n.href === "/searches" && pathname.startsWith("/searches/") && pathname !== "/searches/new")
              || (n.href === "/searches" && pathname.startsWith("/leads/"));
            return (
              <Link key={n.href} href={n.href}
                className={cx("flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm",
                  active ? "bg-brand-soft font-medium text-brand" : "text-muted hover:bg-surface-2 hover:text-text")}>
                <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d={n.icon} /></svg>
                {n.label}
              </Link>
            );
          })}
        </nav>
        {usage && (
          <div className="mb-3 rounded-lg bg-surface-2 p-3 text-xs">
            <div className="flex justify-between font-medium"><span>{usage.plan_name}</span><span className="tabular-nums">{usage.remaining} left</span></div>
            <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-border">
              <div className="h-full bg-brand" style={{ width: `${Math.min(100, (usage.remaining / Math.max(usage.monthly_leads, 1)) * 100)}%` }} />
            </div>
            <div className="mt-1 text-muted">of {usage.monthly_leads} leads this month</div>
          </div>
        )}
        <div className="border-t border-border px-2 pt-3 text-xs">
          <div className="truncate font-medium">{session.workspace_name}</div>
          <div className="truncate text-muted">{session.user.email}</div>
          <button className="mt-2 text-muted underline-offset-2 hover:underline" onClick={() => { clearSession(); router.replace("/login"); }}>
            Sign out
          </button>
        </div>
      </aside>
      <main className="min-w-0 flex-1">
        {/* Phones: the sidebar is hidden, so show a compact scrollable nav bar instead. */}
        <nav className="sticky top-0 z-10 flex gap-1 overflow-x-auto border-b border-border bg-surface px-3 py-2 md:hidden">
          {NAV.filter((n) => !n.admin || isAdmin(session)).map((n) => (
            <Link key={n.href} href={n.href}
              className={cx("shrink-0 rounded-lg px-3 py-1.5 text-sm", pathname === n.href ? "bg-brand-soft font-medium text-brand" : "text-muted")}>
              {n.label}
            </Link>
          ))}
        </nav>
        <div className="mx-auto max-w-6xl px-4 py-6 md:px-8 md:py-8">
          <div className="mb-6 flex flex-wrap items-start justify-between gap-3">
            <div>
              <h1 className="text-xl font-semibold md:text-2xl">{title}</h1>
              {subtitle && <p className="mt-1 text-sm text-muted">{subtitle}</p>}
            </div>
            {actions}
          </div>
          {children}
        </div>
      </main>
    </div>
  );
}
