import { apiFetch } from "./api";

// Mirrors app/routes/lineage.py's response models (KCH-60 / AA-23).

export type SliceKind = "term_bucket" | "net_worth" | "diversification";

export interface SourceFile {
  bronze_file_id: string;
  institution: string | null;
  period: string | null;
  is_purged: boolean;
  blob_url: string | null;
  purged_at: string | null;
}

export interface UnderlyingRow {
  id: string;
  entity: string;
  payload: Record<string, unknown>;
  method: string;
  confirmed_at: string | null;
}

export interface LineageSlice {
  kind: SliceKind;
  run_id: string;
  job_id: string | null;
  source_file: SourceFile | null;
  rows: UnderlyingRow[];
}

export interface SliceSelector {
  kind: SliceKind;
  snapshotDate: string;
  bucket?: string;
  cut?: string;
  label?: string;
}

export function getLineageSlice(selector: SliceSelector): Promise<LineageSlice> {
  const params = new URLSearchParams({ kind: selector.kind, snapshot_date: selector.snapshotDate });
  if (selector.bucket) params.set("bucket", selector.bucket);
  if (selector.cut) params.set("cut", selector.cut);
  if (selector.label) params.set("label", selector.label);
  return apiFetch<LineageSlice>(`/lineage/slice?${params.toString()}`);
}
