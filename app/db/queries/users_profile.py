"""`users_profile` reads/writes (KCH-42 / AA-7).

Every query here runs on an RLS-scoped connection (`app.db.pool.rls_connection`),
same convention as `app.db.queries.bronze_files`/`etl_jobs`/`staged_rows` —
Postgres's own RLS policy (migration 0001) is what actually enforces the
tenant boundary. `users_profile` is a singleton per user: `id = auth.uid()`
directly (no separate `user_id` column), so every query here binds `user_id`
as `id`.
"""

from __future__ import annotations

from decimal import Decimal

import asyncpg

_GET_SQL = """
    select id, age, holdings_country, year_in_canada, fhsa_opened_year, risk_profile,
           prior_year_earned_income, deactivated_at, created_at, updated_at
    from public.users_profile
    where id = $1 and deactivated_at is null
"""

# Create and update collapse into one upsert — `users_profile` is a singleton
# per user, and the onboarding screen and the later "edit profile" screen
# submit the same full fact set (app.routes.profile). The `where ... is null`
# guard stops a plain upsert from silently reactivating a deactivated profile
# (AA-10's scope, not this one's) — a conflict against a deactivated row
# matches nothing and `RETURNING` comes back empty, same convention
# `bronze_files.insert_bronze_file`'s guarded `ON CONFLICT` uses.
_UPSERT_SQL = """
    insert into public.users_profile
        (id, age, holdings_country, year_in_canada, fhsa_opened_year, risk_profile,
         prior_year_earned_income)
    values ($1, $2, $3, $4, $5, $6, $7)
    on conflict (id) do update
        set age = excluded.age,
            holdings_country = excluded.holdings_country,
            year_in_canada = excluded.year_in_canada,
            fhsa_opened_year = excluded.fhsa_opened_year,
            risk_profile = excluded.risk_profile,
            prior_year_earned_income = excluded.prior_year_earned_income
        where public.users_profile.deactivated_at is null
    returning id, age, holdings_country, year_in_canada, fhsa_opened_year, risk_profile,
              prior_year_earned_income, deactivated_at, created_at, updated_at
"""


async def get_profile(conn: asyncpg.Connection, *, user_id: str) -> asyncpg.Record | None:
    return await conn.fetchrow(_GET_SQL, user_id)


async def upsert_profile(
    conn: asyncpg.Connection,
    *,
    user_id: str,
    age: int,
    holdings_country: str,
    year_in_canada: int,
    fhsa_opened_year: int | None,
    risk_profile: str,
    prior_year_earned_income: Decimal | None,
) -> asyncpg.Record | None:
    """Insert or replace the caller's profile row.

    Returns `None` when the row exists but is deactivated — the route turns
    that into a 409 rather than letting an upsert quietly resurrect it.
    """
    return await conn.fetchrow(
        _UPSERT_SQL,
        user_id,
        age,
        holdings_country,
        year_in_canada,
        fhsa_opened_year,
        risk_profile,
        prior_year_earned_income,
    )
