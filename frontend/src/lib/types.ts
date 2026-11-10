/** Shapes returned by the application tier (mirrors the backend module services). */

export type IcpBody = {
  seller_mode: "b2b" | "b2c";
  summary: string;
  industries: string[];
  industry_keywords: string[];
  negative_keywords: string[];
  target_size: string[];
  technologies: string[];
  personas: string[];
  pains: string[];
  triggers: string[];
  exclusions: string[];
  segments: { label: string; description: string }[];
  local_ratio: number;
  wants_contacts: boolean;
  place_categories: string[];
};

export type Icp = { id: string; lineage_id: string; version: number; body: IcpBody; source: Record<string, unknown> };

export type GeoScope = {
  level: string;
  label: string;
  country_iso2?: string;
  admin1_code?: string;
  city?: string;
  lat?: number;
  lon?: number;
  radius_km?: number;
};

export type GeoResolution = {
  status: "resolved" | "ambiguous" | "unresolved";
  scopes: GeoScope[];
  ambiguities: { text: string; options: GeoScope[] }[];
  unresolved: { text: string; did_you_mean: GeoScope[]; parent: GeoScope[] }[];
  forced_choice: boolean;
  allow_map_pin: boolean;
};

export type PlanPath = { path: string; share: number; label: string };

export type Search = {
  id: string;
  icp_id: string;
  status: string;
  stage: string | null;
  region_text: string;
  scopes: GeoScope[];
  target_count: number;
  delivered_count: number;
  held_count: number;
  rejected_count: number;
  budget_paise: number;
  spent_paise: number;
  plan: { paths: PlanPath[]; confidence: number; rationale: string[] } | null;
  estimate: { cost_paise: number; cost_per_lead_paise: number; web_searches: number; candidate_companies: number; ai_mode: string } | null;
  question: { text: string; options: { label: string; path: string }[] } | null;
  quality_report: QualityReport | null;
  created_at: string;
  completed_at: string | null;
  error: string | null;
};

export type Contact = { value: string; status: string; sources: string[]; type?: string | null };

export type Lead = {
  id: string;
  search_id: string;
  company: { id: string; name: string; domain: string; city: string; size_band: string; employees: number; industry: string };
  person: { name: string | null; title: string | null } | null;
  path: string;
  fit: string;
  final_score: number;
  confidence: number;
  decision: "pass" | "degrade" | "hold" | "reject";
  reason_codes: string[];
  reasons: { text: string; evidence_ids: string[] }[];
  opener: string | null;
  billable: boolean;
  accepted_from_hold: boolean;
  delivered_at: string | null;
  contacts?: Partial<Record<"email" | "phone" | "whatsapp" | "linkedin", Contact>>;
  checks?: { layer: string; check_code: string; passed: boolean; detail: Record<string, unknown> }[];
  evidence?: Record<string, { id: string; kind: string; url: string | null; snippet: string }>;
};

export type Usage = {
  plan: string;
  plan_name: string;
  status: string;
  monthly_leads: number;
  remaining: number;
  period: string;
  price_paise: number;
};

/** Written by the orchestrator when a search finishes (and partly while it runs). */
export type QualityReport = {
  discovery?: {
    found?: number;
    kept?: number;
    dropped?: Record<string, number>;
    injections_removed?: number;
    unsafe_links_blocked?: number;
  };
  dropped_before_judge?: Record<string, number>;
  decisions?: Record<string, number>;
  hold_and_reject_reasons?: Record<string, number>;
  avg_confidence_delivered?: number | null;
  verified_email_rate?: number | null;
  spent_paise?: number;
  cost_per_delivered_lead_paise?: number | null;
  ai?: { calls: number; cost_micro_usd: number; cached_tokens: number };
};
