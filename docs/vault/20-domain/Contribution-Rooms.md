---
tags: [domain-ca, verified-2026-08]
sources:
  - https://www.steadyhand.com/education/2026/01/30/important-tfsa-rrsp-and-fhsa-numbers-for-2026/
  - https://www.bnnbloomberg.ca/investing/opinion/2025/12/31/cra-sets-new-savings-and-pension-plan-limits-for-2026-dale-jackson/
  - https://www.canada.ca/en/revenue-agency/services/tax/registered-plans-administrators/whats-new.html
---

# Contribution Rooms — TFSA / RRSP / FHSA (CRA rules, 2026)

The engine treats room as a ledger ([[../10-mental-models/Contribution-Room-as-Ledger]]). Numbers below verified Aug 2026; the CRA limits table lives in one versioned config file so annual updates are a one-line PR.

## TFSA
- Room accrues each year you are **18+ AND a Canadian resident**, from 2009 onward. Immigrants accrue only from their arrival year (input: *year since in Canada*).
- Annual dollar limits: 2009–12: $5,000 · 2013–14: $5,500 · 2015: $10,000 · 2016–18: $5,500 · 2019–22: $6,000 · 2023: $6,500 · 2024–25: $7,000 · **2026: $7,000**. Cumulative since 2009: **$109,000**.
- Withdrawals are re-credited on **Jan 1 of the following year**. Over-contribution penalty: 1%/month on excess.

## RRSP
- Room = **18% of prior-year earned income**, capped at the annual limit (**2026: $33,810**, 2025: $32,490), minus pension adjustment (from T4 — the Canada Life RPP matters here), plus unused carry-forward (indefinite).
- Requires an income input the statements don't provide → user enters prior-year earned income or their Notice of Assessment room directly (NOA wins on conflict).
- Withdrawals do **not** restore room (except HBP/LLP repayment schemes — out of MVP scope, flagged in UI).

## FHSA
- Opens room only once the account is opened (input: *year FHSA opened*). **$8,000/year**, **$40,000 lifetime**, carry-forward of unused room capped at **one year's $8,000** (max $16,000 addable in any one year).
- 15-year max participation window from opening; contributions tax-deductible; qualifying first-home withdrawals tax-free and room is never restored.

## Engine contract
- Pure function: `(user_facts, room_events, limits_table) -> {tfsa, rrsp, fhsa}: {room_total, room_used, room_remaining, ledger[]}`.
- Unit tests: hand-computed cases incl. immigrant arriving 2019, FHSA opened 2024 with $0 year-one contributions (→ $16k in 2025), TFSA withdrawal re-credit timing.
- Reconciliation entry type `cra_override` lets the user pin CRA My Account numbers; the ledger then explains the delta.
