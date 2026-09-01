---
tags: [mental-model]
---

# Provenance-First

Every number on a dashboard must be traceable back through gold → silver → bronze to the exact uploaded file, page, and extraction method (deterministic parser vs. LLM vs. manual entry), with the transformation recorded as an OpenLineage-format event.

**Why it matters:** the owner ranked *data provenance* above everything else. An auditor app whose numbers can't justify themselves is worse than a spreadsheet. This is also what powers the click-to-drill-down requirement: the drill-down view *is* the lineage query.

**Consequences:**
- Every silver/gold row carries `source_file_id`, `extraction_method`, `confidence`, `confirmed_by_user_at`.
- Deleting bronze at 14 days (retention) must not orphan lineage — lineage stores a content hash + metadata, not the file.
- See [[Medallion-Architecture]], [[../30-architecture/ETL-Pipeline]], [[../40-research/Lineage-OpenLineage]].
