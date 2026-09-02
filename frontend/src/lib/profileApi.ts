import { apiFetch } from "./api";

// Mirrors app/routes/profile.py's request/response models (KCH-42 / AA-7).

export type RiskProfile = "very_risky" | "high" | "medium" | "low" | "no_risk";

export interface ProfileUpsertRequest {
  age: number;
  holdings_country: string;
  year_in_canada: number;
  fhsa_opened_year?: number | null;
  risk_profile: RiskProfile;
  prior_year_earned_income?: string | null;
}

export interface ProfileOut {
  age: number;
  holdings_country: string;
  year_in_canada: number;
  fhsa_opened_year: number | null;
  risk_profile: RiskProfile;
  prior_year_earned_income: string | null;
  shows_room_widgets: boolean;
}

export function getProfile(): Promise<ProfileOut> {
  return apiFetch<ProfileOut>("/profile");
}

export function upsertProfile(body: ProfileUpsertRequest): Promise<ProfileOut> {
  return apiFetch<ProfileOut>("/profile", {
    method: "PUT",
    body: JSON.stringify(body),
  });
}
