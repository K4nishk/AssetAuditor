# Sample fixtures — mock user "Alex Mock"

Fabricated data only. Alex: age 29, `holdings_country=CA`, in Canada since **2019**, FHSA opened **2024**, risk profile **medium**, prior-year earned income $82,000. These files are the **adapter contract** (Assumption A15): real statement parsing must normalize into the same silver shapes documented per file below.

| File | Institution / product | Silver entities exercised |
|---|---|---|
| `scotiabank_chequing_savings.csv` | Scotiabank chequing + savings | accounts, transactions |
| `scotiabank_sample_statement.pdf` | Same data as a text-layer PDF | bronze→extract path (pdfplumber) |
| `questrade_activity.csv` | Questrade TFSA stocks/ETF + RRSP bonds, per-lot buys | holdings, lots, transactions |
| `wealthsimple.json` | WS HISA + FHSA ETFs + mortgage (+ linked home estimate) | accounts, holdings, liabilities, room_events (FHSA) |
| `td_loc_mutualfunds.csv` | TD line of credit + mutual fund (note the 2.18% MER) | liabilities, holdings |
| `kraken_ledger.csv` | Kraken BTC/ETH ledger (8-decimal precision → `Decimal`) | transactions, holdings |
| `moomoo_crypto.csv` | moomoo SOL/DOGE positions | holdings |
| `canadalife_rpp.json` | Canada Life DC pension incl. **pension adjustment $4,100** (feeds RRSP room) | accounts, holdings, room_events |
| `equateaccess_esop.csv` | ESOP: 45 vested / 30 unvested @ $38 FMV | holdings, lots (vested flag) |

## Reference totals (as of 2026-07-31, CAD — dashboards/wireframes use these)

**Assets** — chequing 4,200 · savings 8,500 · HISA 15,000 · FHSA ETFs 12,400 · Questrade ≈ 16,150 (AAPL 10 sh ≈ 3,110 + VFV 50 sh ≈ 6,500 + ZAG ≈ 2,500 + GOC-2029 bond ≈ 2,540 + cash 1,500) · TD mutual fund 7,199 · crypto ≈ 10,600 (Kraken BTC 0.085 + ETH 1.2 ≈ 8,800; moomoo 1,800) · RPP 22,500 · ESOP vested 1,710 · home (user estimate) 520,000 → **total ≈ 618,259** (liquid ≈ 96,549 excl. home + RPP + ESOP).

**Liabilities** — mortgage 412,000 · TD LOC 9,800 → **421,800**. **Net worth ≈ 196,459.**

**Contribution rooms (engine expectations, hand-computed):**
- TFSA: resident since 2019, 18+ → grants 2019–2026 = 6,000×4 + 6,500 + 7,000×3 = **51,500**; contributions to date (Questrade TFSA buys ≈ 10,300) → room ≈ **41,200**.
- RRSP: 18% × 82,000 = 14,760 (under 2026 cap 33,810) − PA 4,100 = **10,660** new room for 2026 + carry-forward (user enters NOA figure to reconcile).
- FHSA: opened 2024 → 8,000 (2024) + 8,000 (2025) + 8,000 (2026), contributed 8,000 + 4,000 → **room 12,000**, lifetime remaining 28,000.

These three results are the golden answers for the contribution-room engine's unit tests and the e2e skill.
