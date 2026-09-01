---
tags: [mental-model, domain-ca]
---

# Contribution Room as a Ledger, not a Number

TFSA/RRSP/FHSA room is not a static figure — it is the running balance of a small ledger: annual grants (indexed limits), contributions (debits), withdrawals (TFSA: credited back *next* Jan 1; FHSA: never credited back; RRSP: never credited back), and carry-forwards with per-account rules (FHSA carries forward max one year's $8k).

**Why it matters:** modelling room as a ledger means each user fact (year turned 18, year became CA resident, year FHSA opened, prior contributions) is just an opening entry, and every parsed statement contribution automatically updates room. It also gives free provenance: "your TFSA room is $X *because* of these entries."

**Consequences:**
- Schema: `room_events(account_type, year, kind, amount, source_ref)` — grants generated from a CRA limits table, contributions linked to silver transactions.
- The engine is pure, deterministic, unit-tested against hand-computed CRA examples ([[../20-domain/Contribution-Rooms]]).
- CRA's My Account number wins on conflict: allow a manual "CRA says" reconciliation entry.
