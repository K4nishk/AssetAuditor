---
tags: [domain]
---

# Risk Profiles

User-declared enum: `very_risky | high | medium | low | no_risk`. It is a *lens*, not a gate — it changes what the dashboards flag, never what the user can hold.

| Profile | Reference equity/fixed-income lens | Dashboard behaviour |
|---|---|---|
| very_risky | ~95/5, crypto & concentrated bets tolerated | mutes concentration callouts, still shows them |
| high | ~80/20 | flags single-name >15% |
| **medium (mock user)** | ~60/40 (the classic mix from [[Diversification-Factors]]) | flags single-name >10%, sector >30%, crypto >10%, home bias |
| low | ~40/60 | flags equity drift above lens |
| no_risk | cash/GIC/HISA-centric | flags anything volatile at all |

Term-bucket pie (short/medium/long + liabilities) uses profile-independent, liquidity-based rules:
- **Short-term (<1y):** chequing, savings, HISA, cash
- **Medium (1–5y):** bonds, GICs, balanced funds, FHSA earmarked for a purchase
- **Long (5y+):** equities, ETFs, RPP/CPP, unvested ESOP, real estate
- **Liabilities:** LOC balance, credit cards, mortgage

Open question for owner: should term-bucketing be overridable per account (e.g., "this ETF is my house down-payment fund")? → `../../CLARIFICATIONS.md` Q9.
