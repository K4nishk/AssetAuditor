# Liberated Capacity

What each PR cost to build, and how many human hours it freed. Every PR body
carries the section (`.github/pull_request_template.md`); every merged PR gets
a row in `docs/metrics/liberated-capacity.csv`, and
`python -m tools.liberated_capacity totals` adds the rows up.

## Definitions

| Term | Meaning |
|---|---|
| **Baseline hours** | What a mid-level developer who already knows this repo would need to land the same change without AI: reading, design, code, tests, PR write-up, the first CI pass. The agent estimates it with a one-line rationale, marked [REVIEW REQUIRED]; the owner corrects it in review. |
| **Owner hours** | The owner's own time on the PR: review, answering the agent's questions, owner-only steps (secrets, consoles, merging). The agent prefills it from the rubric below; the owner corrects it. |
| **Liberated hours** | Baseline hours − owner hours. Can be negative. |
| **AI cost** | Measured, not estimated: the Claude Code transcripts priced at Anthropic API list prices. |
| **Infra cost** | Paid services used for the PR. $0 under the zero-cost contract (hard rule 6); a non-zero value is an incident. |
| **Overhead** | AI cost not attributable to one PR (status updates, review-order questions, PR watching). Counted in USD, never in hours. |

## How AI cost is measured

1. `python -m tools.liberated_capacity cost [transcripts] [--since ISO] [--until ISO]`
   reads every transcript file (main session and fan-out sub-agents), counts each
   API response once and groups its cost by the git branch that was checked out.
2. Prices are list prices per million tokens for `claude-opus-5-5`, as of
   2026-10-06: input $4.00, output $20.00, cache read $0.20, cache write $5.00
   (5-minute) or $8.00 (1-hour). An unknown model is an error, never $0.
3. A PR's cost is its branch's cost while the branch was being built or fixed.
   Idle conversation that happened while the branch was checked out is split
   out with `--since/--until` (the PR-creation and commit times in the
   transcript are the boundaries) and booked as overhead.
4. This is the **API-equivalent** price. On a subscription plan the marginal
   cost is lower; the figure is what the same work would cost on the API.

To keep attribution clean, the agent switches back to `development` when it is
not working on a PR.

## Owner-hours rubric (the prefill)

| PR kind | Review |
|---|---|
| Config or docs, under 100 non-test lines | 0.25 h |
| Code, 100–300 non-test lines | 0.5 h |
| Migration, security-sensitive, or needs an owner decision | 0.75–1 h |

Add 0.1 h per question the agent asked and the actual time of any owner-only step.

## Ledger columns

`pr, title, branch, status, baseline_h, owner_h, ai_usd, infra_usd, cost_basis, estimate_basis`

- `status`: `estimate` (agent's numbers), `confirmed` (owner corrected or accepted),
  `overhead` (no hours), `not_measured` (no transcript available, e.g. another session).
- `cost_basis`: which branch runs or time windows were counted.
- `estimate_basis`: the one-line rationale for the baseline.

## Backfill, 2026-10-15

Rows for #36–#68 come from this session's transcripts. The rows plus overhead
total $126.02, which matches the transcript total of $126.03 once the cost of
building this metric is excluded. Notes:

- #36's $78.82 includes $52.75 from two planning fan-outs (sub-agent runs that
  re-read the repository on every turn). That is 41% of all spend so far, and
  the reason fan-outs are now limited to scoped sweeps (`docs/mvp1/model-policy.md`).
- #37 was written on #36's branch before the split, so its cost sits in #36.
- #61's figure includes about $5 of cache rebuild after a context compaction
  that happened while its branch was checked out.
- #54 came from another session whose transcript is not here.
