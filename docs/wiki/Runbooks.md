# Runbooks (stubs — filled during build)
- **Overnight build orchestration** — full runbook in [`ops/README.md`](../../ops/README.md): first-run bootstrap, nightly `tmux` launch, the CodeRabbit gate and its escalation path, the morning PR approval order, and a failure-symptom table. Night reports land in `ops/logs/NIGHT_REPORT.md`.
- CodeRabbit gate fired but nothing merged: expected. Human review on `development` is the only merge gate; agents never merge.
- GPU box down — what still works: uploads accepted + queued (bronze lands in Blob), dashboards serve last gold, rooms engine unaffected; nothing parses until the box returns. Check `worker_heartbeat` + queued-jobs alert.
- LiteLLM config change checklist: edit `llm/litellm.config.yaml` in a PR (fallback order, RPM/TPM caps below Groq free tier), run golden-set evals against the changed config, then reload the compose service.
- Retention sweeper failed / stale alert → treat as privacy incident: verify last purge, run manually, audit window.
- ETL job stuck in `needs_user` — triage decision tree.
- Rebuild gold from silver (`rebuild_gold`) — safe anytime; document expected duration.
- Rotate Groq/Supabase/Grafana credentials.
- Account hard-purge verification checklist (mirrors e2e Flow 1.6).
