---
tags: [research, lineage]
verified: 2026-08-31
---

# Lineage — OpenLineage without the ops burden

[OpenLineage](https://openlineage.io/) is the open spec for run/job/dataset lineage events; [Marquez](https://marquezproject.ai/) is its reference server (API + lineage graph UI, needs its own Postgres + service).

## Options
| Option | Cost | Verdict |
|---|---|---|
| Full Marquez deployment | Another always-on service + DB to run and secure | **defer** — real ops burden for a single-user app |
| **OpenLineage-format events into a `lineage_events` table in Supabase** | One table + a tiny Python emitter; events are spec-compliant JSON | **leverage (MVP choice)** — keeps the spec (so Marquez can be pointed at the history later), zero extra infra, and the dashboard drill-down queries this table directly |
| No lineage, just FK columns | Cheapest | **skip** — violates provenance-first priority |

## MVP event contract
Every ETL run emits START/COMPLETE/FAIL events: `job` (adapter name + version), `run_id`, `inputs` (bronze file: name, sha256, institution, period), `outputs` (silver/gold datasets + row counts), plus facets for `extraction_method`, `masking_applied`, `user_confirmed`. Bronze purge at 14 days emits its own lineage event so the chain shows *why* the file is gone ([[../20-domain/Data-Retention-and-Privacy]]).

**Drill-down = lineage query:** clicking a pie slice → gold rows → their `run_id`s → inputs. One code path serves both the provenance requirement and the UX requirement. See [[../10-mental-models/Provenance-First]].
