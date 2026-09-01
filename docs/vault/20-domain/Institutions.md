---
tags: [domain, etl, fixtures]
---

# Institutions — mock user's 8 sources

The mock user (medium [[Risk-Profiles|risk profile]]) submits these. Each row defines the silver entities it feeds and its fixture in `data/samples/`. Fixtures are the adapter contract (Assumption A15 in [[../Assumptions]]).

| Institution | Products | Statement reality | Silver entities | Fixture |
|---|---|---|---|---|
| Scotiabank | Chequing + savings | Monthly PDF, tabular, text layer | accounts, transactions | `scotiabank_chequing_savings.csv` (+ sample PDF) |
| Questrade | Stocks + bonds | PDF + CSV activity export w/ per-lot buys | accounts, holdings, lots, transactions | `questrade_activity.csv` |
| Wealthsimple | HISA + mortgage + ETFs | App exports (CSV/JSON), clean | accounts, holdings, liabilities | `wealthsimple.json` |
| TD | Line of credit + mutual funds | PDF statements, LOC shows limit/balance/interest | accounts, liabilities, holdings | `td_loc_mutualfunds.csv` |
| Kraken | Crypto wallet | CSV ledger export, precise decimals, per-asset | accounts, holdings, transactions | `kraken_ledger.csv` |
| moomoo | Crypto | CSV/PDF | accounts, holdings | `moomoo_crypto.csv` |
| Canada Life | Employer RPP (**covers the "CPP" requirement** — owner confirmed the CPP mention means these insurer-managed pension contributions, e.g. Canada Life Index ETF Portfolio; government CPP itself is out of scope) | Portal PDF; contributions + employer match + pension adjustment | accounts, holdings, room_events (RRSP PA) | `canadalife_rpp.json` |
| EquateAccess | ESOP (employer shares) | Portal export; vested/unvested lots | holdings, lots | `equateaccess_esop.csv` |

## Adapter pattern
One adapter per institution: `detect(file) -> confidence`, `parse(bronze) -> silver_rows + lineage_event`. Deterministic first (pdfplumber/CSV), LLM fallback for unknown layouts ([[../10-mental-models/LLM-as-Parser-not-Oracle]]).

## Gotchas
- Kraken decimals: use `Decimal`, never float — satoshi-level rounding errors destroy trust.
- Canada Life's **pension adjustment** reduces RRSP room → feeds [[Contribution-Rooms]], not just net worth.
- No separate CPP card anywhere: the RPP row above is the whole pension story (decision 2026-08-31, CLARIFICATIONS Q12).
- ESOP vested vs. unvested: unvested shown as "conditional asset", excluded from liquid net worth, included in concentration warnings.
- TD LOC: a liability with an available-credit ceiling — don't count available credit as an asset.
- Mortgage (Wealthsimple): liability + implied real-estate asset; MVP asks the user for a home-value estimate rather than inferring.
