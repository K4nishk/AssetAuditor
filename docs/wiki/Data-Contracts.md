# Data Contracts
- Silver entities: accounts, holdings, lots, transactions, liabilities — canonical shapes defined by `data/samples/README.md` (fixtures are the contract).
- Gold: networth_snapshots, term_buckets, diversification_cuts, room_ledger (+ CSV exports).
- Lineage: OpenLineage-format JSON in `lineage_events`; every gold number resolves to runs → bronze hashes.
- Retention: bronze 14 days, logs 4 days — see vault note `20-domain/Data-Retention-and-Privacy`.
