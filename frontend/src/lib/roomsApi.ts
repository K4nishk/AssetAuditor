import { apiFetch } from "./api";

// Mirrors app/routes/rooms.py's response/request models (KCH-44 / AA-9).

export type AccountType = "tfsa" | "rrsp" | "fhsa";

export interface LedgerEntry {
  year: number;
  kind: "grant" | "contribution" | "withdrawal" | "pension_adjustment" | "cra_override";
  amount: string;
  note: string;
  room_event_id: string | null;
  source_ref: string | null;
}

export interface RoomBreakdown {
  room_total: string;
  room_used: string;
  room_remaining: string;
  ledger: LedgerEntry[];
}

export interface RoomsOut {
  as_of_year: number;
  tfsa: RoomBreakdown;
  rrsp: RoomBreakdown;
  fhsa: RoomBreakdown;
}

export interface CraOverrideRequest {
  account_type: AccountType;
  year: number;
  amount: string;
}

export function getRooms(): Promise<RoomsOut> {
  return apiFetch<RoomsOut>("/rooms");
}

export function submitCraOverride(body: CraOverrideRequest): Promise<RoomsOut> {
  return apiFetch<RoomsOut>("/rooms/override", {
    method: "POST",
    body: JSON.stringify(body),
  });
}
