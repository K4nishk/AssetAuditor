# AssetAuditor

A single-user Canadian finances and portfolio auditor: statements in, provenance-tracked
numbers out. This glossary covers the terms the code, plans and specs must use consistently.

## Inference

**Box**:
The owner's own GPU machine that runs the worker, the model router and local inference.
_Avoid_: home-lab, GPU server, the server

**Local backend**:
A model served on the Box.
_Avoid_: self-hosted provider, on-prem model

**Egress backend**:
A third-party model provider. It may receive only coded payloads.
_Avoid_: fallback provider, cloud model

**Egress**:
Any model request that carries user-derived data to hardware the owner does not control.
_Avoid_: external call, fallback (a fallback is a reason to route; egress is a boundary crossing)

## Data protection

**Masking**:
Irreversible removal of identifiers (account numbers cut to a last-4 token; names, addresses
and contact details redacted) before anything is staged or sent to any model.
_Avoid_: anonymisation, coding

**Coding**:
Reversible replacement of sensitive values, such as amounts and payee names, with opaque codes
whose key never leaves the worker. It is applied only to Egress, on top of Masking.
_Avoid_: tokenisation (clashes with model tokens), encryption, masking

## Evaluation

**Golden set**:
The fabricated fixtures with known-correct rows that every extraction backend is scored
against.
_Avoid_: benchmark, test data

**Observation**:
One sentence of audit commentary stating what the user's numbers show.
_Avoid_: insight, recommendation, comment

**Advice-shaped**:
An Observation that directs the user to act (buy, sell, move money, "consider"). It is never
shown to the user.
_Avoid_: recommendation

**Shadow guard**:
A check that records a verdict on a model output and never changes or blocks that output.
_Avoid_: guardrail, filter
