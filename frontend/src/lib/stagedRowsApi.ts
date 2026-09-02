import { apiFetch } from "./api";

// Mirrors app/routes/staged.py's response models (KCH-52 / AA-17).
export interface StagedRow {
  id: string;
  entity: "transaction" | "holding" | "lot" | "liability" | "account";
  payload: Record<string, unknown>;
  confidence: number | null;
  method: "deterministic" | "llm" | "manual_entry" | "manual_correction";
  is_low_confidence: boolean;
  confirmed_at: string | null;
  created_at: string;
}

export interface StagedRowsResponse {
  job_id: string;
  job_status: string;
  rows: StagedRow[];
}

export interface ConfirmResponse {
  job_id: string;
  status: string;
  confirmed_row_count: number;
  silver_write_summary: Record<string, number>;
}

export function fetchStagedRows(jobId: string): Promise<StagedRowsResponse> {
  return apiFetch<StagedRowsResponse>(`/staged/${jobId}/rows`);
}

export function editStagedRow(
  jobId: string,
  rowId: string,
  payload: Record<string, unknown>,
): Promise<StagedRow> {
  return apiFetch<StagedRow>(`/staged/${jobId}/rows/${rowId}`, {
    method: "PATCH",
    body: JSON.stringify({ payload }),
  });
}

export function confirmStagedRows(jobId: string): Promise<ConfirmResponse> {
  return apiFetch<ConfirmResponse>(`/staged/${jobId}/confirm`, { method: "POST" });
}
