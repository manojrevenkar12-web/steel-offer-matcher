export type CheckStatus = "pass" | "review" | "fail";

export interface Check { name: string; status: CheckStatus; detail: string }

export interface Lot {
  item_id: string; supplier_id: string; form: string; finish: string; grade_id: string | null; grade_text: string;
  coating: string | null; surface: string | null; thickness_mm: number; width_mm: number; length_mm: number | null;
  weight_t: number; price_eur_t: number; country: string; origin: string | null; certificate: string; quality: string;
}

export interface Offer {
  item_id: string; status: CheckStatus; landed_eur_t: number; usable_t: number; processing: string[];
  grade_match: string; partial: boolean; lead_days: number; checks: Check[]; item: Lot | null;
}

export interface Spec {
  line: number; form: string; finish: string; grade_id: string; grade_text: string; coating: string | null; surface: string | null;
  thickness_mm: number; width_mm: number; length_mm: number | null; quantity_t: number; max_price_eur_t: number | null;
  delivery_country: string; delivery_by: string | null; alternatives: Record<string, number>[];
}

export interface Line {
  spec: Spec; offers: Offer[]; review_offers: Offer[]; needs_review: boolean; reasons: string[]; agent_found: string[];
  stop_reason: string; text: string; steps: { tool: string; new?: number; items?: number }[];
}

export interface ReviewItem { line: number; field: string; reason: string; proposals: string[] }

export interface Result {
  status: string;
  extraction: {
    status: string; language: string; policy: Record<string, unknown>; review: ReviewItem[];
    evidence: Record<string, { quote: string; source: string }>; invalid_quotes: number;
  };
  lines: Line[]; cost_eur: number; model_calls: number; prompt_versions: Record<string, string>; trace_id: string | null;
}

export interface Rfq {
  message_id: string; received: string; sender: string; subject: string; status: string; body: string;
  attachments: string[]; result: Result | null;
}

export type DesignKey = "manual" | "model_decides" | "platform" | "platform_no_agent";
export type Metrics = Record<string, number>;

export interface Reports {
  dev?: { seeds: number[]; mean: Record<DesignKey, Metrics> };
  heldout?: { seeds: number[]; mean: Record<DesignKey, Metrics> };
  bakeoff?: { note: string; models: Record<string, Record<string, number>> };
  gate?: { passed: boolean; cases: number; exact: number; wrong_values: number; regressions: string[] };
}

export interface Desk {
  generated_from: string; today: string; rfqs: Rfq[]; reports: Reports;
  lists: { supplier_id: string; filename: string; status: string; items: number; used_model: boolean; problems: string[] }[];
  gateway: Record<string, { calls: number; errors: number; fallbacks: number; cost_eur: number; p50_ms: number; p95_ms: number }>;
  mcp: { tools: string[]; clients: { client_id: string; scopes: string[] }[] };
}
