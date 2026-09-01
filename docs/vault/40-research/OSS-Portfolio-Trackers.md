---
tags: [research]
verified: 2026-08-31
---

# Open-source portfolio / finance tools — survey

Verdict scale: **leverage** (use directly), **borrow-ideas** (steal patterns/schemas), **skip**.

| Tool | What it is | Verdict | Why |
|---|---|---|---|
| [Ghostfolio](https://github.com/ghostfolio/ghostfolio) | Wealth management webapp — net worth, multi-asset, performance. Angular + NestJS + Prisma, AGPL-3.0 | **borrow-ideas** | Closest feature match (allocations by class/sector/region ≈ our diversification pies). Stack mismatch (TS/Angular vs our React/FastAPI) and AGPL makes embedding code unattractive for a showcase repo — but its **activity/holding data model and allocation math are the best reference to study** |
| [Wealthfolio](https://wealthfolio.app/) | Local-first tracker, Tauri/Rust, CSV import, addons | **borrow-ideas** | Its CSV import-mapping UX (map arbitrary broker CSV → canonical schema) is exactly our parse-confirm screen's shape |
| [Firefly III](https://www.firefly-iii.org/) | Self-hosted personal finance manager (PHP) | **borrow-ideas** | Mature transaction/budget model + its separate **data importer** (CSV/JSON mapping engine) validates our adapter design; too budget-centric to reuse |
| [Actual Budget](https://actualbudget.org/) | Privacy-first budgeting (local-first sync) | **skip** | Budgeting envelope model, not portfolio auditing |
| [OpenBB](https://openbb.co/) | Python financial data/analytics platform | **leverage (selectively)** | Python-native price/fundamentals fetching and sector/country metadata for tickers — a candidate for our market-data layer instead of raw yfinance |
| Maybe Finance | Formerly OSS personal-finance app | **skip** | Project wound down / repo effectively unmaintained; ideas superseded by Ghostfolio |
| Beancount / plain-text accounting | Double-entry ledger + ecosystem | **borrow-ideas** | Gold layer could optionally export a Beancount file — free audit trail + huge tooling ecosystem; noted as a post-MVP issue |

## Net conclusion
Nothing replaces AssetAuditor (no OSS tool does statement-PDF ingestion + Canadian contribution-room auditing + lineage). Build the core; **study Ghostfolio's allocation model, Wealthfolio/Firefly's import-mapping UX, and use OpenBB/yfinance for market data.**

Sources: [openalternative.co/wealthfolio](https://openalternative.co/alternatives/wealthfolio) · [Ghostfolio GitHub](https://github.com/ghostfolio/ghostfolio) · [wealthfolio.app](https://wealthfolio.app/) · [Firefly III vs Actual comparison](https://beancount.io/blog/2026/07/26/firefly-iii-vs-actual-budget-self-hosted-open-source-budgeting-guide)
