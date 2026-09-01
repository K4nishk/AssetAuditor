---
tags: [decision-log]
date: 2026-08-31
---

# 2026-08-31 — Planning session decisions

Decisions made by the owner during the planning-mode session:

1. **Vault location:** inside the repo (`docs/vault/`), versioned with git; open as a folder-vault in Obsidian.
2. **Linear method, markdown only:** `mvp.md` follows linear.app/method issue-writing; no push to Linear during planning.
3. **ADR posture:** honest versions, **best-solution-first** — ADR v1 is the recommended architecture (ETL worker off Vercel, Grafana Cloud hosted Prometheus); ADR v2 documents the strict listed-stack variant with workaround costs.
4. **Grafana/Prometheus question answered:** Grafana Cloud does host a Prometheus-compatible backend; serverless pushes via remote_write; worker is a normal scrape target. See [[../10-mental-models/Push-Dont-Scrape-Metrics]].
5. **Mock data:** structured CSV/JSON fixtures per institution + 1–2 text-layer sample PDFs; no full fake PDF statements.

Open items live in `../../CLARIFICATIONS.md` — that file is the owner's review queue.
