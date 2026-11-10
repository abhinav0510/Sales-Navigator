/**
 * Small, reusable UI primitives. Every page builds from these so the app looks
 * consistent and a new developer only learns one set of components.
 */
"use client";

import Link from "next/link";
import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode, TextareaHTMLAttributes } from "react";

export function cx(...classes: (string | false | null | undefined)[]) {
  return classes.filter(Boolean).join(" ");
}

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "ghost" | "danger";
  loading?: boolean;
};

export function Button({ variant = "primary", loading, className, children, disabled, ...rest }: ButtonProps) {
  const styles = {
    primary: "bg-brand text-brand-ink hover:opacity-90",
    secondary: "bg-surface border border-border text-text hover:bg-surface-2",
    ghost: "text-muted hover:text-text hover:bg-surface-2",
    danger: "bg-bad-soft text-bad hover:opacity-90",
  }[variant];
  return (
    <button
      className={cx(
        "inline-flex items-center justify-center gap-2 rounded-lg px-3.5 py-2 text-sm font-medium transition",
        "disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-brand",
        styles,
        className,
      )}
      disabled={disabled || loading}
      {...rest}
    >
      {loading && <Spinner small />}
      {children}
    </button>
  );
}

export function Card({ title, actions, children, className }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={cx("rounded-xl border border-border bg-surface p-5", className)}>
      {(title || actions) && (
        <header className="mb-4 flex items-center justify-between gap-3">
          {title && <h2 className="text-base font-semibold">{title}</h2>}
          {actions}
        </header>
      )}
      {children}
    </section>
  );
}

export function Badge({ tone = "neutral", children }: { tone?: "neutral" | "good" | "warn" | "bad" | "brand"; children: ReactNode }) {
  const styles = {
    neutral: "bg-surface-2 text-muted",
    good: "bg-good-soft text-good",
    warn: "bg-warn-soft text-warn",
    bad: "bg-bad-soft text-bad",
    brand: "bg-brand-soft text-brand",
  }[tone];
  return <span className={cx("inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium", styles)}>{children}</span>;
}

export function DecisionBadge({ decision }: { decision: string }) {
  const map: Record<string, [string, "good" | "warn" | "bad" | "brand"]> = {
    pass: ["Delivered", "good"],
    degrade: ["Delivered (field removed)", "good"],
    hold: ["Needs review", "warn"],
    reject: ["Rejected", "bad"],
  };
  const [label, tone] = map[decision] ?? [decision, "brand"];
  return <Badge tone={tone}>{label}</Badge>;
}

export function StatusBadge({ status }: { status: string }) {
  const tone =
    status === "COMPLETED" ? "good" : status === "FAILED" || status === "CANCELLED" ? "bad"
      : status === "NEEDS_INPUT" || status === "PAUSED_BUDGET" ? "warn" : "brand";
  return <Badge tone={tone}>{status.replace("_", " ").toLowerCase()}</Badge>;
}

export function Stat({ label, value, hint }: { label: string; value: ReactNode; hint?: ReactNode }) {
  return (
    <div className="rounded-xl border border-border bg-surface p-4">
      <div className="text-xs font-medium uppercase tracking-wide text-muted">{label}</div>
      <div className="mt-1 text-2xl font-semibold tabular-nums">{value}</div>
      {hint && <div className="mt-1 text-xs text-muted">{hint}</div>}
    </div>
  );
}

export function ConfidenceBar({ value }: { value: number }) {
  const tone = value >= 0.8 ? "bg-good" : value >= 0.6 ? "bg-warn" : "bg-bad";
  return (
    <div className="flex items-center gap-2" title={`Confidence ${Math.round(value * 100)}%`}>
      <div className="h-1.5 w-16 overflow-hidden rounded-full bg-surface-2">
        <div className={cx("h-full", tone)} style={{ width: `${Math.round(value * 100)}%` }} />
      </div>
      <span className="text-xs tabular-nums text-muted">{Math.round(value * 100)}%</span>
    </div>
  );
}

export function Spinner({ small }: { small?: boolean }) {
  return (
    <span
      aria-label="Loading"
      className={cx("inline-block animate-spin rounded-full border-2 border-current border-r-transparent",
        small ? "h-3.5 w-3.5" : "h-5 w-5")}
    />
  );
}

export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-sm font-medium">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-xs text-muted">{hint}</span>}
    </label>
  );
}

const inputCls =
  "w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm outline-none placeholder:text-muted focus:border-brand focus:ring-2 focus:ring-brand-soft";

export function Input(props: InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={cx(inputCls, props.className)} />;
}

export function TextArea(props: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea {...props} className={cx(inputCls, "min-h-24", props.className)} />;
}

export function ErrorBox({ error }: { error: unknown }) {
  if (!error) return null;
  const e = error as { message?: string; requestId?: string };
  return (
    <div role="alert" className="rounded-lg bg-bad-soft px-3 py-2 text-sm text-bad">
      {e.message ?? String(error)}
      {e.requestId && <span className="ml-2 font-mono text-xs opacity-70">ref {e.requestId.slice(0, 8)}</span>}
    </div>
  );
}

export function Empty({ title, children, href, cta }: { title: string; children?: ReactNode; href?: string; cta?: string }) {
  return (
    <div className="rounded-xl border border-dashed border-border bg-surface px-6 py-12 text-center">
      <h3 className="text-base font-semibold">{title}</h3>
      {children && <p className="mx-auto mt-1 max-w-md text-sm text-muted">{children}</p>}
      {href && cta && (
        <Link href={href} className="mt-4 inline-flex rounded-lg bg-brand px-3.5 py-2 text-sm font-medium text-brand-ink">
          {cta}
        </Link>
      )}
    </div>
  );
}

export function Chips({ items, tone = "neutral" }: { items: string[]; tone?: "neutral" | "brand" }) {
  if (!items?.length) return <span className="text-sm text-muted">—</span>;
  return (
    <div className="flex flex-wrap gap-1.5">
      {items.map((i) => <Badge key={i} tone={tone}>{i}</Badge>)}
    </div>
  );
}
