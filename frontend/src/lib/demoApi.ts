import { apiFetch } from "./api";

// Mirrors app/routes/demo.py's response models (KCH-69 / AA-32).

export interface DemoStatus {
  configured: boolean;
  is_demo_user: boolean;
}

export interface DemoSeedResult {
  fixtures_loaded: string[];
  fixtures_skipped: string[];
  silver_write_summary: Record<string, number>;
  room_events_written: number;
  total_assets_cad: string;
  total_liabilities_cad: string;
  net_worth_cad: string;
}

export function getDemoStatus(): Promise<DemoStatus> {
  return apiFetch<DemoStatus>("/demo/status");
}

export function seedDemoData(): Promise<DemoSeedResult> {
  return apiFetch<DemoSeedResult>("/demo/seed", { method: "POST" });
}
