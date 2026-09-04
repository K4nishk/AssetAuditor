# AssetAuditor Wiki — Home

Public-facing distillation. Canonical sources live in `docs/vault/` (thinking) and
`docs/adr/` (decisions of record); these pages are the operational summary.

## Start here
- **[Local-Quickstart](Local-Quickstart.md)** — get tests, Postgres, the API and the frontend running on this machine
- **[Project-Status](Project-Status.md)** — where the build is, what runs by itself, cold-start reading order
- **[Environment-Gotchas](Environment-Gotchas.md)** — this machine's landmines, each one already paid for
- [Architecture](Architecture.md) — the $0/month stack and the code-review pipeline
- [Data-Contracts](Data-Contracts.md) — silver/gold shapes, lineage, retention
- [Runbooks](Runbooks.md) — operational procedures

## The showcase
- `/blog/architecture-story` (public, no login) — the same architecture story below,
  rendered in-app as MDX with live mermaid diagrams and mock-data-only screenshots
  (`frontend/src/content/architecture-story.mdx`, mvp.md AA-31).

## Elsewhere in the repo
- `ops/README.md` — the operator runbook for the autonomous build and review loops
- `CLAUDE.md` — binding conventions for every agent (CodeRabbit protocol, branching, hard rules)
- `docs/adr/ADR_v1.1.0.md` — architecture of record (v1.0.0 superseded, v2.0.0 rejected)
- `docs/vault/00-Home.md` — the Obsidian vault: mental models, domain notes, research, decision log
- `mvp.md` — issue specs · `data/samples/README.md` — fixtures and golden numbers
