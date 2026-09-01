---
tags: [domain, dashboards]
sources:
  - https://www.wealthsimple.com/en-ca/learn/what-is-diversification
---

# Diversification Factors

Distilled from Wealthsimple's guide; these become the cut-dimensions of the diversification pie chart(s) and the "potential diversification avenues" callouts.

## Dimensions (each is a gold-table grouping column)
1. **Asset class** — stocks, bonds, real estate (incl. mortgage/home equity), commodities, cash/GIC/HISA, crypto, private assets, pension (RPP/CPP entitlements shown separately).
2. **Sector** — GICS-style: tech, energy, financials, consumer, utilities, telecom, healthcare, industrials, materials.
3. **Geography** — Canada, U.S., international developed, emerging markets. Explicitly compute **home bias** (CA weight vs. CA's ~3% of world market cap).
4. **Market cap / style** — large/mid/small; defensive vs. growth.
5. **Currency exposure** — CAD/USD/other (crypto counted as its own exposure).
6. **Correlation clusters** — MVP proxy: flag when >X% of equity sits in one sector or one ticker; true correlation math is post-MVP.

## Rules of thumb the article gives (surface as advisory callouts, never advice)
- Individual stocks: aim for roughly 20–30 names across industries.
- Classic reference mix: 60/40 stocks/bonds (adjust by [[Risk-Profiles|risk profile]]).
- A single broad-market ETF is a legitimate diversification shortcut — detect "already diversified via XEQT-style ETF" by looking through fund holdings (post-MVP: fund look-through; MVP: classify the ETF itself by its factsheet category).

## Gotchas
- **ETF look-through** is the hard part: an "all-equity ETF" line item hides geography/sector spread. MVP: map well-known Canadian ETFs (XEQT, VEQT, VFV, XIC…) to static factsheet weights in a seed table; unknown funds classified as "fund — unclassified" rather than guessed.
- Employer plans concentrate risk twice (income + equity in same company) — EquateAccess ESOP gets a dedicated concentration callout.
- Compliance posture: outputs are *observations* ("Tech is 48% of equities") + *avenues* ("consider international exposure"), never personalized directives. See `../../CLARIFICATIONS.md` Q10.
