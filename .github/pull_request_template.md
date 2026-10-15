## What and why
<!-- The change and the problem it solves. Link the issue: Closes #NN. -->

## Verification
<!-- What ran, which tests were seen failing first, and what is still unverified. -->

## Liberated Capacity
<!-- Definitions and rubric: docs/metrics/liberated-capacity.md.
     The agent fills this in; the owner corrects the hours in review. -->

| Hours | Value | Basis |
|---|---|---|
| Baseline (no AI) | 0 | one-line rationale [REVIEW REQUIRED] |
| Owner time | 0 | review, questions answered, owner-only steps |
| **Liberated** | **0** | baseline − owner |

| Cost | USD | Source |
|---|---|---|
| AI (API list price) | $0.00 | `python -m tools.liberated_capacity cost --since <branch start>` on `<branch>` |
| Paid infra | $0.00 | zero-cost contract |
