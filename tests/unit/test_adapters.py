"""CSV/JSON adapter tests (KCH-49 / AA-14) — each adapter validated against
its own fixture in `data/samples/`, per the issue's acceptance bar.

Covers: `detect()` true-positive on its own fixture + false-positive
cross-checks against every other fixture; `parse()` entity counts and the
golden numbers `data/samples/README.md` documents (Questrade lot/holding
math, Kraken's Decimal ledger balances, ESOP vested/unvested split, etc.);
that every produced payload uses `Decimal` for money/quantity fields (never
`float`, per CLAUDE.md hard rule #4); and that no adapter ever emits a raw,
unmasked account identifier.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from worker.adapters import equateaccess, kraken, moomoo, questrade, td, wealthsimple
from worker.adapters.base import StagedRowDraft
from worker.masking import is_masked_account_token

SAMPLES_DIR = Path(__file__).resolve().parents[2] / "data" / "samples"

ADAPTERS = {
    "questrade": (questrade, "questrade_activity.csv"),
    "wealthsimple": (wealthsimple, "wealthsimple.json"),
    "td": (td, "td_loc_mutualfunds.csv"),
    "kraken": (kraken, "kraken_ledger.csv"),
    "moomoo": (moomoo, "moomoo_crypto.csv"),
    "equateaccess": (equateaccess, "equateaccess_esop.csv"),
}

ALL_FIXTURE_NAMES = sorted(p.name for p in SAMPLES_DIR.glob("*") if p.suffix in {".csv", ".json"})


def _raw(name: str) -> bytes:
    return (SAMPLES_DIR / name).read_bytes()


def _by_entity(drafts: list[StagedRowDraft], entity: str) -> list[StagedRowDraft]:
    return [d for d in drafts if d.entity == entity]


# --- detect(): true on its own fixture, false on every other institution's ---


@pytest.mark.parametrize("name", ADAPTERS.keys())
def test_detect_true_on_own_fixture(name: str) -> None:
    adapter, fixture_name = ADAPTERS[name]
    assert adapter.detect(_raw(fixture_name)) is True


@pytest.mark.parametrize("name", ADAPTERS.keys())
def test_detect_false_on_every_other_fixture(name: str) -> None:
    adapter, own_fixture = ADAPTERS[name]
    for fixture_name in ALL_FIXTURE_NAMES:
        if fixture_name == own_fixture:
            continue
        assert adapter.detect(_raw(fixture_name)) is False, (
            f"{name}.detect() false-positived on {fixture_name}"
        )


# --- every produced payload is Decimal, never float, for money/quantity ------

_DECIMAL_ISH_KEYS = {
    "quantity",
    "avg_cost",
    "unit_cost",
    "amount",
    "balance",
    "interest_rate",
    "commission",
    "credit_limit",
    "min_payment",
    "market_value",
    "market_value_cad",
    "mer_pct",
    "fee",
    "monthly_payment_cad",
}


@pytest.mark.parametrize("name", ADAPTERS.keys())
def test_parse_never_uses_float_for_money_or_quantity(name: str) -> None:
    adapter, fixture_name = ADAPTERS[name]
    for draft in adapter.parse(_raw(fixture_name)):
        for key, value in draft.payload.items():
            if key in _DECIMAL_ISH_KEYS and value is not None:
                assert isinstance(value, Decimal), f"{name}: {key}={value!r} is {type(value)}"
                assert not isinstance(value, float)


@pytest.mark.parametrize("name", ADAPTERS.keys())
def test_parse_masked_identifiers_are_never_raw(name: str) -> None:
    adapter, fixture_name = ADAPTERS[name]
    for draft in adapter.parse(_raw(fixture_name)):
        mask = draft.payload.get("masked_identifier") or draft.payload.get("account_mask")
        if mask is None:
            continue
        assert is_masked_account_token(mask) or mask.startswith(
            ("kraken-default", "equateaccess-")
        ), f"{name}: {mask!r} is not a safe natural-key identifier"


@pytest.mark.parametrize("name", ADAPTERS.keys())
def test_parse_is_deterministic_and_full_confidence(name: str) -> None:
    adapter, fixture_name = ADAPTERS[name]
    for draft in adapter.parse(_raw(fixture_name)):
        assert draft.method == "deterministic"
        assert draft.confidence == 1.0


# --- Questrade: per-lot buys -> holdings, lots, transactions -----------------


def test_questrade_parse_shapes() -> None:
    drafts = questrade.parse(_raw("questrade_activity.csv"))

    accounts = _by_entity(drafts, "account")
    holdings = _by_entity(drafts, "holding")
    lots = _by_entity(drafts, "lot")
    txns = _by_entity(drafts, "transaction")

    assert {a.payload["account_type"] for a in accounts} == {"tfsa", "rrsp"}
    assert len(lots) == 6
    assert len(txns) == 6

    aapl = next(h for h in holdings if h.payload["ticker"] == "AAPL")
    assert aapl.payload["quantity"] == Decimal("10")
    expected_avg_cost = (
        Decimal("4") * Decimal("171.20") + Decimal("6") * Decimal("186.10")
    ) / Decimal("10")
    assert aapl.payload["avg_cost"] == expected_avg_cost

    vfv = next(h for h in holdings if h.payload["ticker"] == "VFV.TO")
    assert vfv.payload["quantity"] == Decimal("50")


# --- Wealthsimple: HISA + FHSA holdings + mortgage liability + contributions -


def test_wealthsimple_parse_shapes() -> None:
    drafts = wealthsimple.parse(_raw("wealthsimple.json"))

    accounts = _by_entity(drafts, "account")
    holdings = _by_entity(drafts, "holding")
    liabilities = _by_entity(drafts, "liability")
    txns = _by_entity(drafts, "transaction")

    assert {a.payload["account_type"] for a in accounts} == {"hisa", "fhsa_invest", "mortgage"}
    assert len(holdings) == 2
    assert sum(h.payload["quantity"] for h in holdings) == Decimal("380")

    assert len(liabilities) == 1
    mortgage = liabilities[0]
    assert mortgage.payload["balance"] == Decimal("412000.00")
    assert mortgage.payload["linked_asset"]["user_estimated_value_cad"] == Decimal("520000.00")

    contributions = [t for t in txns if t.payload["kind"] == "contribution"]
    assert len(contributions) == 2
    assert sum(c.payload["amount"] for c in contributions) == Decimal("12000.00")
    assert {c.payload["room_account_type"] for c in contributions} == {"fhsa"}


# --- TD: pivoted metric rows -> one liability + one holding ------------------


def test_td_parse_shapes() -> None:
    drafts = td.parse(_raw("td_loc_mutualfunds.csv"))

    liabilities = _by_entity(drafts, "liability")
    holdings = _by_entity(drafts, "holding")

    assert len(liabilities) == 1
    assert liabilities[0].payload["balance"] == Decimal("9800.00")
    assert liabilities[0].payload["interest_rate"] == Decimal("7.20")

    assert len(holdings) == 1
    fund = holdings[0]
    assert fund.payload["quantity"] == Decimal("412.335")
    assert fund.payload["mer_pct"] == Decimal("2.18")


# --- Kraken: 8dp Decimal ledger -> transactions + running-balance holdings ---


def test_kraken_parse_decimal_precision_and_balances() -> None:
    drafts = kraken.parse(_raw("kraken_ledger.csv"))

    txns = _by_entity(drafts, "transaction")
    holdings = _by_entity(drafts, "holding")

    assert len(txns) == 5
    btc_trade = next(t for t in txns if t.payload["ticker"] == "BTC" and t.payload["fee"])
    assert btc_trade.payload["fee"] in {Decimal("0.00011000"), Decimal("0.00009000")}

    btc = next(h for h in holdings if h.payload["ticker"] == "BTC")
    eth = next(h for h in holdings if h.payload["ticker"] == "ETH")
    assert btc.payload["quantity"] == Decimal("0.08500000")
    assert eth.payload["quantity"] == Decimal("1.19760000")

    # CAD is cash flow, not a crypto holding.
    assert {h.payload["ticker"] for h in holdings} == {"BTC", "ETH"}


# --- moomoo: pre-aggregated positions -----------------------------------------


def test_moomoo_parse_shapes() -> None:
    drafts = moomoo.parse(_raw("moomoo_crypto.csv"))
    holdings = _by_entity(drafts, "holding")

    assert {h.payload["ticker"] for h in holdings} == {"SOL", "DOGE"}
    sol = next(h for h in holdings if h.payload["ticker"] == "SOL")
    assert sol.payload["quantity"] == Decimal("9.500000")
    assert sol.payload["market_value_cad"] == Decimal("1520.00")


# --- equateaccess: vested/unvested ESOP tranches ------------------------------


def test_equateaccess_parse_vested_split() -> None:
    drafts = equateaccess.parse(_raw("equateaccess_esop.csv"))

    lots = _by_entity(drafts, "lot")
    holdings = _by_entity(drafts, "holding")

    vested_total = sum(lot.payload["quantity"] for lot in lots if lot.payload["vested"])
    unvested_total = sum(lot.payload["quantity"] for lot in lots if not lot.payload["vested"])
    assert vested_total == Decimal("45")
    assert unvested_total == Decimal("30")

    assert len(holdings) == 1
    assert holdings[0].payload["quantity"] == Decimal("75")
    assert holdings[0].payload["avg_cost"] == Decimal("38.00")
