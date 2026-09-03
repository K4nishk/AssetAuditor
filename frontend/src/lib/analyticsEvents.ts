// Single source of truth for every Amplitude event this app emits (mvp.md
// AA-28, docs/vault/30-architecture/Observability.md's "Product analytics"
// row). Behaviour only — CLAUDE.md rule #2 / Assumption A13: no amount,
// ticker, or account identifier may ever appear in an event payload.
//
// scripts/check-analytics-schema.mjs parses this file's AST in CI and fails
// the build if a forbidden field name (or event name) appears below, and
// separately scans every analytics.track(...) call site in src/ for the same
// forbidden names in inline payloads — so a bypass at the call site fails the
// gate too, not just a schema edit. Keep every list here a flat array of
// string literals so that script can parse it without executing this file.
export const ANALYTICS_EVENT_SCHEMAS = {
  statement_uploaded: ["institution", "file_type"],
  parse_confirmed: ["row_count", "corrections"],
  dashboard_drilldown: ["chart"],
} as const;

export type AnalyticsEventName = keyof typeof ANALYTICS_EVENT_SCHEMAS;

export interface AnalyticsEventPayloads {
  statement_uploaded: { institution: string; file_type: string };
  parse_confirmed: { row_count: number; corrections: number };
  dashboard_drilldown: { chart: string };
}
