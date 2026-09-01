---
tags: [moc, assetauditor]
created: 2026-08-31
---

# AssetAuditor — Home (Map of Content)

> A Finances + Portfolio auditor: upload Canadian institution statements → provenance-tracked ETL → net-worth, contribution-room, and diversification dashboards, with an LLM agent doing extraction and audit commentary.

**Priority order (fixed by owner):** data provenance > end-user satisfaction > code maintainability + quality > testing > documentation > delivery timelines.

## Start here
- [[Assumptions]] — ranked by criticality; contest the 5s first
- `AssetAuditor-Map.canvas` — visual map of requirements → assumptions → components
- [[../adr/ADR_v1.1.0|ADR v1.1.0 (current)]] — zero-cost home-lab + LiteLLM · supersedes [[../adr/ADR_v1.0.0|v1.0.0]] · [[../adr/ADR_v2.0.0|v2.0.0]] rejected/archived
- `../../CLARIFICATIONS.md` — open questions awaiting your answers
- `../../mvp.md` — Linear-method issues, dependency ordered

## Mental models (Karpathy-wiki style: atomic, linked, "why it matters")
- [[10-mental-models/Provenance-First]]
- [[10-mental-models/Medallion-Architecture]]
- [[10-mental-models/LLM-as-Parser-not-Oracle]]
- [[10-mental-models/Contribution-Room-as-Ledger]]
- [[10-mental-models/Single-User-First]]
- [[10-mental-models/Push-Dont-Scrape-Metrics]]

## Domain knowledge
- [[20-domain/Contribution-Rooms]] — TFSA / RRSP / FHSA math with 2026 numbers
- [[20-domain/Diversification-Factors]] — distilled from Wealthsimple's guide
- [[20-domain/Institutions]] — the 8 mock-user institutions and their data shapes
- [[20-domain/Risk-Profiles]] — very risky → no-risk mapping
- [[20-domain/Data-Retention-and-Privacy]] — 14d raw / 4d logs, masking, encryption

## Architecture
- [[30-architecture/System-Overview]]
- [[30-architecture/ETL-Pipeline]]
- [[30-architecture/Security-Model]]
- [[30-architecture/Observability]]
- [[30-architecture/LLM-Strategy]]

## Research (verdicts: leverage / borrow-ideas / skip)
- [[40-research/OSS-Portfolio-Trackers]]
- [[40-research/PDF-Statement-Parsing]]
- [[40-research/Lineage-OpenLineage]]
- [[40-research/LLM-Routing-LiteLLM]]

## Decisions log
- [[50-decisions-log/2026-08-31-planning-session]]
- [[50-decisions-log/2026-08-31-clarifications-round]] — CLARIFICATIONS resolved → ADR v1.1.0, planning wrap-up ready
