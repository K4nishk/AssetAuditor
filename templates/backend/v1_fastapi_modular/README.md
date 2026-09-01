# Backend template v1 — FastAPI modular monolith + worker

The repo structure and API surface to scaffold when building starts. Feeds the coding agents directly; matches [ADR v1.1.0](../../../docs/adr/ADR_v1.1.0.md) (home-lab worker + LiteLLM router).

## Repo structure

```
assetauditor/
├── api/                        # Vercel Python entry
│   └── index.py                # mounts app.main:app
├── app/
│   ├── main.py                 # FastAPI app factory, middleware (auth, request-id, metrics)
│   ├── auth.py                 # Supabase JWT verification (JWKS cache), user_id dependency
│   ├── routes/                 # profile.py · uploads.py · staged.py · dashboards.py · lineage.py · rooms.py · internal.py
│   ├── domain/
│   │   ├── rooms/              # pure contribution-room engine + cra_limits.py (versioned table)
│   │   ├── buckets.py          # term-bucket rules
│   │   └── diversification.py  # cut computations + flag thresholds by risk profile
│   ├── db/
│   │   ├── pool.py             # asyncpg pool, RLS-scoped role
│   │   ├── queries/            # *.sql files + thin typed wrappers — parameterized only (CI-linted)
│   │   └── migrations/         # 0001_init.sql … applied via supabase CLI in CI
│   └── obs/                    # metrics.py (remote_write batch), logging.py (redaction filter)
├── worker/                     # runs on the owner's GPU box via docker-compose (ADR v1.1.0)
│   ├── Dockerfile
│   ├── docker-compose.yml      # services: worker · litellm · vllm (--profile gpu) · alloy
│   ├── bring-up.md             # box bring-up: env, compose, heartbeat check
│   ├── main.py                 # poll etl_jobs (FOR UPDATE SKIP LOCKED) loop, 30s heartbeat, /metrics endpoint
│   ├── adapters/               # scotiabank.py, questrade.py, wealthsimple.py, td.py, kraken.py, moomoo.py, canadalife.py, equateaccess.py — each: detect() + parse()
│   ├── extract/                # pdfplumber_tier.py · docling_tier.py · llm_tier.py (masked, JSON-schema, via LiteLLM)
│   ├── masking.py              # account-number + PII redaction (unit-tested against fixtures)
│   ├── lineage.py              # OpenLineage event emitter → lineage_events
│   └── gold.py                 # rebuild_gold(user_id) — snapshots, buckets, cuts, room ledger, CSV export
├── frontend/                   # Vite + React + Chakra + Recharts
│   └── src/{routes,components,charts,content(MDX)}
├── llm/
│   └── litellm.config.yaml     # model group "extractor": vllm primary ↔ groq fallback, RPM/TPM caps below Groq free tier
├── db/seeds/                   # cra_limits, etf_classification (XEQT/VEQT/VFV/XIC factsheet weights)
├── data/samples/               # fixtures = adapter contract (already authored)
├── tests/                      # unit/ (rooms golden numbers, masking, adapters vs fixtures) · api/ · e2e/
└── .github/workflows/          # ci.yml · deploy.yml · sweeper.yml (nightly) · prices.yml · llm-evals.yml
```

## Route table

| Method & path | Auth | Description |
|---|---|---|
| `GET/PUT /profile` | user | facts: age, country, year_in_canada, fhsa_opened_year, income, risk_profile |
| `POST /profile/deactivate` · `POST /profile/reactivate` | user | soft freeze / unfreeze (`deactivated_at`) |
| `DELETE /account` | user + re-auth | schedule hard purge (rows, blobs, auth identity ≤30d) |
| `POST /uploads` | user | register file, return signed Blob URL, enqueue job |
| `GET /uploads/{id}/status` | user | pending / parsing / needs_user / done / failed |
| `GET /staged/{job_id}/rows` · `POST .../confirm` | user | parse-confirm read + confirm/correct (corrections logged as `manual_correction`) |
| `GET /portfolio/holdings` · `POST /portfolio/manual` | user | manual form entry path (ticker, shares, avg cost; optional lots) |
| `GET /dashboards/networth` · `/terms` · `/diversification?cut=` | user | gold reads |
| `GET /lineage/slice?chart=&key=` | user | drill-down chain gold→runs→bronze tombstones |
| `GET /rooms` · `POST /rooms/override` | user | ledger + CRA reconciliation entry |
| `POST /internal/sweep` · `/internal/prices/refresh` | HMAC (GH Actions) | retention + price refresh |

## Model sketches (pydantic)

```python
class ProfileFacts(BaseModel):
    age: int; holdings_country: Literal["CA"]
    year_in_canada: int; fhsa_opened_year: int | None
    prior_year_earned_income: Decimal | None
    risk_profile: Literal["very_risky","high","medium","low","no_risk"]

class StagedRow(BaseModel):
    entity: Literal["transaction","holding","lot","liability","account"]
    payload: dict; confidence: float
    method: Literal["deterministic","llm","manual_entry","manual_correction"]

class RoomEvent(BaseModel):
    account_type: Literal["tfsa","rrsp","fhsa"]; year: int
    kind: Literal["grant","contribution","withdrawal","pension_adjustment","cra_override"]
    amount: Decimal; source_ref: UUID | None   # links to silver txn or NOA entry
```

**Conventions:** `Decimal` everywhere money/quantity appears (crypto needs 8dp); UTC timestamps; all amounts stored in native currency + converted-CAD snapshot columns with FX rate + date recorded.
