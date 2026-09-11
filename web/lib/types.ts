// The wire shapes the window consumes. Mirrors src/api/schemas.py by hand: the API is Python,
// the window is TypeScript, and a generated client would be a build step for six routes.
// Cross-service IDs are plain strings, never foreign keys. Orphans are allowed on purpose.

export type Severity = "nominal" | "warning" | "critical";
export type IncidentStatus = "pending" | "opened" | "ongoing" | "resolved" | "dismissed";
export type AgentName = "water_feed" | "herd_health" | "infrastructure" | "compliance" | "chaos";
export type Decision = "approve" | "reject";

export const STATUSES: IncidentStatus[] = ["pending", "opened", "ongoing", "resolved", "dismissed"];
export const AGENTS: AgentName[] = ["water_feed", "herd_health", "infrastructure", "compliance", "chaos"];
export const SUBJECT_TYPES = ["sensor", "animal"] as const;

export interface Meta { count: number; limit: number; offset: number }
export interface Single<T> { data: T }
export interface Collection<T> { data: T[]; meta: Meta }
export interface ApiError { error: { code: string; message: string; details: Record<string, unknown> } }

export interface Incident {
  id: number | null; incident_key: string; subject_id: string; subject_type: string; location: string; category: string;
  severity: Severity; status: IncidentStatus; summary: string; last_value: string | null; unit: string; threshold: number | null;
  occurrences: number; first_seen_at: string; last_seen_at: string; resolved_at: string | null; tick_opened: number; tick_last_seen: number; run_id: string; owner: string | null;
}

export interface ShiftReport {
  id: number; run_id: string; tick: number; at: string; source: "model" | "code" | string; headline: string; situation: string; priorities: string[]; linked: string[];
  escalations: string[]; worlds: string[]; incident_keys: string[]; work_orders: number; violations: string[]; provider: string; model: string; finish_reason: string;
  latency_ms: number; input_tokens: number; output_tokens: number;
}

/** One row of sw_ops.ticks. `fields` is the whole tick line (docs/plan.md); the names the panels read are typed below. */
export interface Tick {
  id: number; run_id: string; tick: number; at: string; store: string; duration_ms: number; cost_usd: number; error: string | null; failed_stage: string | null;
  fields: TickFields;
}
export interface TickFields {
  input_tokens?: number; output_tokens?: number; cost_usd?: number;
  findings?: number; critical?: number; opened?: number; ongoing?: number; resolved?: number; pending?: number; dismissed?: number;
  work_orders?: number; work_orders_shipped?: number; work_orders_rejected?: number; escalated?: number;
  tier?: number; tier1_orders?: number; escalations?: number; escalation_reasons?: string[];
  shift_report?: string; shift_report_violations?: string[]; worlds?: string[];
  held?: number; held_unread?: number; skipped_upstreams?: string[]; herd_animals?: number; herd_errors?: number; herd_error?: string | null;
  writes_pending?: number; writes_proposed?: number;
  [key: string]: unknown;
}

export interface PendingWrite { audit_id: string; incident_key: string; agent: string; tool: string; args: Record<string, unknown>; proposed_at: string; tick: number; run_id: string; age_s: number }

export interface GateDecision {
  audit_id: string; incident_key: string; agent: string; tool: string; args: Record<string, unknown>; decision: string; decided_by: string; reason: string; decided_at: string;
  result: string; upstream: string; latency_to_decision_ms: number;
}

export function isApiError(x: unknown): x is ApiError {
  return typeof x === "object" && x !== null && "error" in x && typeof (x as ApiError).error?.code === "string";
}
