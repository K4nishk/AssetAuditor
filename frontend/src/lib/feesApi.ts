import { apiFetch } from "./api";

// Mirrors app/routes/fees.py's response models (KCH-63 / AA-26).

export interface FeeDragRow {
  ticker: string;
  mer_pct: string;
  benchmark_mer_pct: string;
  annual_cost_cad: string;
  benchmark_cost_cad: string;
  excess_cost_cad: string;
}

export interface FeeDragOut {
  benchmark_mer_pct: string;
  rows: FeeDragRow[];
  total_annual_cost_cad: string;
  total_benchmark_cost_cad: string;
}

export function getFeeDrag(): Promise<FeeDragOut> {
  return apiFetch<FeeDragOut>("/fees/drag");
}
