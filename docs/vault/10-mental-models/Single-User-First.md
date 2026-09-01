---
tags: [mental-model]
---

# Single-User-First, Multi-Tenant-Correct

Build for exactly one user (the owner) while keeping the invariants that make multi-tenancy possible later: every table keyed by `user_id`, Supabase RLS on from day one, no global mutable state, per-user blob prefixes.

**Why it matters:** the project's goals are (a) fundamentals practice, (b) auditing the owner's finances, (c) a blog-ready showcase. None require multi-user *features* (sharing, admin, billing), but a showcase project with sloppy tenancy invariants reads badly and is painful to retrofit. RLS-on-day-one is cheap; RLS-later is a migration project.

**Consequences:** no admin panel, no email flows beyond Supabase Auth defaults, no rate-limiting tiers in MVP — but every query goes through RLS-scoped clients and every test asserts `user_id` scoping. See [[../30-architecture/Security-Model]], Assumption A1 in [[../Assumptions]].
