/** Display helpers. Money is stored in paise; India uses lakh/crore digit grouping. */

export function rupees(paise: number | null | undefined, digits = 0): string {
  if (paise === null || paise === undefined) return "—";
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: digits,
    minimumFractionDigits: digits,
  }).format(paise / 100);
}

export function inr(amount: number | null | undefined, digits = 2): string {
  if (amount === null || amount === undefined) return "—";
  return rupees(Math.round(amount * 100), digits);
}

export function pct(x: number | null | undefined): string {
  return x === null || x === undefined ? "—" : `${Math.round(x * 100)}%`;
}

export function when(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" });
}

export const PATH_NAMES: Record<string, string> = {
  B2B_COMPANY: "Companies & decision-makers",
  LOCAL_BUSINESS: "Local businesses",
  LOOKALIKE: "Lookalikes of your customers",
  SIGNAL_LED: "Buying signals",
  B2B2C: "Businesses that reach consumers",
  AUDIENCE_INSIGHT: "Audience report",
};

export const REASON_LABELS: Record<string, string> = {
  company_dead: "Website down",
  out_of_region: "Outside your region",
  excluded_seed: "Your existing customer",
  duplicate_delivered: "Already delivered to you",
  icp_mismatch: "Different business type",
  below_threshold: "Fit score too low",
  email_status: "Email can't be fully verified",
  reasons_supported: "Weak evidence",
  judge_agrees_with_fit: "Judge disagreed with fit",
  low_confidence: "Low confidence",
  core_reachable: "No verified contact",
  persona_match: "No decision-maker found",
  judge_unavailable: "AI judge unavailable",
  weak_reasoning: "Weak reasoning",
};
