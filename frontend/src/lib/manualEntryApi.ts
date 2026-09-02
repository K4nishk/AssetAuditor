import { apiFetch } from "./api";

// Mirrors app/routes/manual_entry.py's request/response models (KCH-55 / AA-20).

export interface AccountField {
  institution: string;
  account_type: string;
  account_number: string;
  currency: string;
}

export interface LotField {
  quantity: string;
  unit_cost?: string | null;
  currency?: string | null;
  acquired_at?: string | null;
  vested?: boolean | null;
}

export interface PortfolioEntryRequest {
  account: AccountField;
  ticker: string;
  quantity: string;
  avg_cost?: string | null;
  currency: string;
  lots: LotField[];
}

export interface YahooImportRequest {
  account: AccountField;
  csv_text: string;
  currency: string;
}

export interface AccountBalanceRequest {
  account: AccountField;
  balance: string;
  currency: string;
}

export interface ManualEntryResponse {
  job_id: string;
  status: string;
  row_count: number;
}

export function submitPortfolioEntry(body: PortfolioEntryRequest): Promise<ManualEntryResponse> {
  return apiFetch<ManualEntryResponse>("/manual-entry/portfolio", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function importYahooFinancePortfolio(
  body: YahooImportRequest,
): Promise<ManualEntryResponse> {
  return apiFetch<ManualEntryResponse>("/manual-entry/portfolio/yahoo-import", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function submitAccountBalance(body: AccountBalanceRequest): Promise<ManualEntryResponse> {
  return apiFetch<ManualEntryResponse>("/manual-entry/account-balance", {
    method: "POST",
    body: JSON.stringify(body),
  });
}
