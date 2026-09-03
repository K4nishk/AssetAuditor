import { apiFetch } from "./api";

// Mirrors app/routes/dashboard.py's response models (KCH-59 / AA-22).

export type DiversificationCut = "institution" | "account_type" | "currency";

export interface DashboardKpis {
  total_assets_cad: string;
  total_liabilities_cad: string;
  net_worth_cad: string;
}

export interface TermBucketSlice {
  bucket: string;
  amount_cad: string;
}

export interface DiversificationSlice {
  label: string;
  amount_cad: string;
}

export interface DashboardOut {
  as_of: string;
  kpis: DashboardKpis;
  term_buckets: TermBucketSlice[];
  diversification_cut: DiversificationCut;
  available_cuts: DiversificationCut[];
  diversification: DiversificationSlice[];
}

export interface NetWorthPoint {
  snapshot_date: string;
  total_assets_cad: string;
  total_liabilities_cad: string;
  net_worth_cad: string;
}

export interface NetWorthHistoryOut {
  points: NetWorthPoint[];
}

export function getDashboard(cut?: DiversificationCut): Promise<DashboardOut> {
  const query = cut ? `?cut=${encodeURIComponent(cut)}` : "";
  return apiFetch<DashboardOut>(`/dashboard${query}`);
}

export function getNetWorthHistory(): Promise<NetWorthHistoryOut> {
  return apiFetch<NetWorthHistoryOut>("/dashboard/history");
}
