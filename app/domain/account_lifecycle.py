"""Pure account-lifecycle domain logic (KCH-45 / AA-10).

No I/O — `app.routes.account`/`app.routes.profile` do the DB and JWT work and
call into this module just to gate on the result. Mirrors the split every
other `app.domain.*` module already uses (e.g. `app.domain.profile`,
`app.domain.retention_status`).

`is_reauth_fresh` is the "requires re-auth" gate on `DELETE /account`
(templates/backend/v1_fastapi_modular/README.md's route table). There is no
Supabase password-reconfirmation round trip available here (no network in
this sandbox to verify one against a live project, and it would need a
second, separate credential flow the frontend doesn't have yet) — instead
this checks that the bearer token's own `iat` (issued-at) claim is recent.
Supabase mints a fresh `iat` whenever a session is (re)established, so
requiring the frontend to force a fresh sign-in (or `reauthenticate()`)
immediately before calling `DELETE /account` and checking `iat` here is a
network-free, still-meaningful re-auth freshness check: a token stolen or
replayed from an old session won't pass once it ages past `REAUTH_MAX_AGE`.
"""

from __future__ import annotations

from datetime import timedelta

REAUTH_MAX_AGE = timedelta(minutes=5)


def is_reauth_fresh(
    issued_at_epoch_seconds: float, *, now_epoch_seconds: float, max_age: timedelta = REAUTH_MAX_AGE
) -> bool:
    """True when the JWT's `iat` claim is within `max_age` of `now`.

    Also false for an `iat` in the future beyond a small skew tolerance — a
    clock-skewed or forged `iat` must not count as "fresh".
    """
    age = now_epoch_seconds - issued_at_epoch_seconds
    skew_tolerance_seconds = 5.0
    return -skew_tolerance_seconds <= age <= max_age.total_seconds()
