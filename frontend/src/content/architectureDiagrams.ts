// Mermaid diagram source for the AA-31 blog page. `ZERO_COST_SPLIT_DIAGRAM`
// is reused verbatim from `docs/adr/ADR_v1.1.0.md` §2's fenced mermaid
// block (which GitHub already renders) so the ADR and the in-app story
// never drift into two independently hand-drawn copies of the same
// picture. `INGESTION_SEQUENCE_DIAGRAM` merges ADR v1.0.0 §5's upload→gold
// sequence with v1.1.0 §3's LiteLLM routing — v1.1.0 is delta-style ("chart
// stays" applies to the flowchart, not the sequence diagram, which still
// named the superseded Vercel AI Gateway), so this is the current picture,
// not a copy of either ADR section alone.

export const ZERO_COST_SPLIT_DIAGRAM = `flowchart LR
    subgraph CLOUD["Free-tier cloud"]
      FE[React+Chakra · Vercel Hobby] --> API[FastAPI · Vercel fns]
      API --> DB[(Supabase free\\njobs · gold · lineage · heartbeat)]
      API --> BLOB[(Vercel Blob\\nbronze/silver/gold)]
      GC[Grafana Cloud free]
    end
    subgraph HOME["Owner's GPU box (home-lab, outbound-only)"]
      W[worker] -->|poll jobs, heartbeat| DB
      W --> BLOB
      W --> LL[LiteLLM proxy]
      LL -->|primary once live| V[vLLM]
      LL -->|fallback, free tier| GQ[Groq]
      AL[Grafana Alloy] -->|scrape worker+litellm+vllm metrics| GC
    end`;

export const INGESTION_SEQUENCE_DIAGRAM = `sequenceDiagram
    actor U as Owner
    participant FE as Frontend (Vercel)
    participant API as FastAPI (Vercel fns)
    participant B as Vercel Blob
    participant DB as Supabase (jobs, lineage, gold)
    participant W as ETL worker (home-lab)
    participant LL as LiteLLM (home-lab)

    U->>FE: drop a bank statement PDF
    FE->>API: POST /uploads
    API->>DB: bronze_files + etl_jobs (pending)
    API-->>FE: signed Blob URL
    FE->>B: PUT file
    W->>DB: claim job (FOR UPDATE SKIP LOCKED)
    W->>B: fetch bronze file
    W->>W: mask account numbers and PII
    alt pdfplumber plus adapter confident
        W->>DB: staged_rows (method=deterministic)
    else layout unrecognized
        W->>LL: masked text, JSON schema
        LL-->>W: rows, per-field confidence, backend tag
        W->>DB: staged_rows (method=llm)
    end
    W->>DB: lineage_events (START to COMPLETE)
    FE->>U: parse-confirm screen, low-confidence rows highlighted
    U->>API: confirm or correct rows
    API->>DB: promote to silver
    W->>B: write silver parquet
    W->>DB: rebuild_gold(user) writes snapshots, buckets, cuts, room ledger`;
