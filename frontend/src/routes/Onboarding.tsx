import {
  Alert,
  AlertIcon,
  Box,
  Button,
  FormControl,
  FormLabel,
  Heading,
  Input,
  Select,
  Text,
  VStack,
} from "@chakra-ui/react";
import { useState } from "react";

import { ApiError } from "../lib/api";
import {
  type ProfileOut,
  type ProfileUpsertRequest,
  type RiskProfile,
  upsertProfile,
} from "../lib/profileApi";

// Wireframe v1 screen 1 (mvp.md AA-7): the facts the contribution-room
// engine (AA-8) needs before it can compute anything — collected once here,
// editable later from the same form. `holdings_country` gates whether the
// TFSA/RRSP/FHSA room widgets ever render (CRA rules are Canada-only,
// docs/vault/Assumptions.md A2) — the backend computes that flag
// (`ProfileOut.shows_room_widgets`), this form just collects the country.

const RISK_PROFILES: { value: RiskProfile; label: string }[] = [
  { value: "no_risk", label: "No risk" },
  { value: "low", label: "Low" },
  { value: "medium", label: "Medium" },
  { value: "high", label: "High" },
  { value: "very_risky", label: "Very risky" },
];

function toFormState(profile: ProfileOut | null) {
  return {
    age: profile ? String(profile.age) : "",
    holdings_country: profile?.holdings_country ?? "CA",
    year_in_canada: profile ? String(profile.year_in_canada) : "",
    fhsa_opened_year: profile?.fhsa_opened_year != null ? String(profile.fhsa_opened_year) : "",
    risk_profile: profile?.risk_profile ?? "medium",
    prior_year_earned_income: profile?.prior_year_earned_income ?? "",
  };
}

export default function Onboarding({
  initialProfile = null,
  onSaved,
  onCancel,
}: {
  initialProfile?: ProfileOut | null;
  onSaved: (profile: ProfileOut) => void;
  onCancel?: () => void;
}) {
  const [form, setForm] = useState(() => toFormState(initialProfile));
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function field<K extends keyof typeof form>(key: K) {
    return (value: string) => setForm((prev) => ({ ...prev, [key]: value }));
  }

  async function submit() {
    setSubmitting(true);
    setError(null);
    try {
      const body: ProfileUpsertRequest = {
        age: Number(form.age),
        holdings_country: form.holdings_country.trim().toUpperCase(),
        year_in_canada: Number(form.year_in_canada),
        fhsa_opened_year: form.fhsa_opened_year ? Number(form.fhsa_opened_year) : null,
        risk_profile: form.risk_profile as RiskProfile,
        prior_year_earned_income: form.prior_year_earned_income || null,
      };
      const saved = await upsertProfile(body);
      onSaved(saved);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "failed to save profile");
    } finally {
      setSubmitting(false);
    }
  }

  const canSubmit =
    !submitting &&
    form.age !== "" &&
    form.holdings_country.trim().length === 2 &&
    form.year_in_canada !== "" &&
    form.risk_profile;

  return (
    <VStack align="stretch" spacing={6} p={8} maxW="480px" mx="auto">
      <Heading size="md">Tell us about you</Heading>
      <Text color="gray.500">
        These facts drive your contribution-room numbers. You can update them anytime.
      </Text>

      {error && (
        <Alert status="error">
          <AlertIcon />
          {error}
        </Alert>
      )}

      <Box borderWidth="1px" borderRadius="md" p={4}>
        <VStack spacing={3}>
          <FormControl isRequired>
            <FormLabel>Age</FormLabel>
            <Input
              type="number"
              value={form.age}
              onChange={(e) => field("age")(e.target.value)}
              placeholder="35"
            />
          </FormControl>
          <FormControl isRequired>
            <FormLabel>Country of tax residency (2-letter code)</FormLabel>
            <Input
              value={form.holdings_country}
              onChange={(e) => field("holdings_country")(e.target.value.toUpperCase())}
              placeholder="CA"
              maxLength={2}
            />
            <Text fontSize="sm" color="gray.500" mt={1}>
              Contribution-room tracking only supports Canada (CA) right now — other countries
              hide those widgets.
            </Text>
          </FormControl>
          <FormControl isRequired>
            <FormLabel>Year you became a Canadian tax resident</FormLabel>
            <Input
              type="number"
              value={form.year_in_canada}
              onChange={(e) => field("year_in_canada")(e.target.value)}
              placeholder="2009"
            />
          </FormControl>
          <FormControl>
            <FormLabel>Year you opened an FHSA (if any)</FormLabel>
            <Input
              type="number"
              value={form.fhsa_opened_year}
              onChange={(e) => field("fhsa_opened_year")(e.target.value)}
              placeholder="2024"
            />
          </FormControl>
          <FormControl isRequired>
            <FormLabel>Risk profile</FormLabel>
            <Select value={form.risk_profile} onChange={(e) => field("risk_profile")(e.target.value)}>
              {RISK_PROFILES.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </Select>
          </FormControl>
          <FormControl>
            <FormLabel>Prior-year earned income (for RRSP room)</FormLabel>
            <Input
              value={form.prior_year_earned_income ?? ""}
              onChange={(e) => field("prior_year_earned_income")(e.target.value)}
              placeholder="85000.00"
            />
          </FormControl>
        </VStack>

        <VStack mt={4} spacing={2} align="stretch">
          <Button colorScheme="teal" isDisabled={!canSubmit} isLoading={submitting} onClick={() => void submit()}>
            Save
          </Button>
          {onCancel && (
            <Button variant="ghost" onClick={onCancel} isDisabled={submitting}>
              Cancel
            </Button>
          )}
        </VStack>
      </Box>
    </VStack>
  );
}
