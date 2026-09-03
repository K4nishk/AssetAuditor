"""Unit tests for `app.domain.audit_commentary` (KCH-62 / AA-25).

Pure module, no I/O — covers the prompt rendering and the advice-shaped
guardrail that backs mvp.md's AA-25 "never advice-shaped" requirement.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.domain.audit_commentary import (
    DISCLOSURE_TEXT,
    DiversificationSlice,
    build_gold_facts_snapshot,
    decode_observations,
    filter_advice_shaped,
    is_advice_shaped,
    render_facts_for_prompt,
)


def _facts():
    return build_gold_facts_snapshot(
        as_of=date(2026, 7, 31),
        total_assets_cad=Decimal("10000.00"),
        total_liabilities_cad=Decimal("2000.00"),
        net_worth_cad=Decimal("8000.00"),
        term_buckets={"short_term": Decimal("4000.00"), "long_term": Decimal("6000.00")},
        diversification_by_institution=[
            DiversificationSlice(label="questrade", amount_cad=Decimal("6000.00")),
            DiversificationSlice(label="scotia", amount_cad=Decimal("4000.00")),
        ],
    )


# --- render_facts_for_prompt -------------------------------------------------


def test_render_includes_every_top_level_kpi():
    text = render_facts_for_prompt(_facts())
    assert "Total assets: $10,000.00 CAD" in text
    assert "Total liabilities: $2,000.00 CAD" in text
    assert "Net worth: $8,000.00 CAD" in text
    assert "2026-07-31" in text


def test_render_includes_term_buckets_with_percentages():
    text = render_facts_for_prompt(_facts())
    assert "short_term: $4,000.00 CAD (40.0% of total assets)" in text
    assert "long_term: $6,000.00 CAD (60.0% of total assets)" in text


def test_render_includes_diversification_with_percentages():
    text = render_facts_for_prompt(_facts())
    assert "questrade: $6,000.00 CAD (60.0% of total assets)" in text
    assert "scotia: $4,000.00 CAD (40.0% of total assets)" in text


def test_render_handles_zero_total_assets_without_dividing_by_zero():
    facts = build_gold_facts_snapshot(
        as_of=date(2026, 7, 31),
        total_assets_cad=Decimal("0"),
        total_liabilities_cad=Decimal("0"),
        net_worth_cad=Decimal("0"),
        term_buckets={"short_term": Decimal("0")},
        diversification_by_institution=[],
    )
    text = render_facts_for_prompt(facts)
    assert "n/a" in text


def test_render_omits_empty_sections():
    facts = build_gold_facts_snapshot(
        as_of=date(2026, 7, 31),
        total_assets_cad=Decimal("100.00"),
        total_liabilities_cad=Decimal("0"),
        net_worth_cad=Decimal("100.00"),
        term_buckets={},
        diversification_by_institution=[],
    )
    text = render_facts_for_prompt(facts)
    assert "Term buckets" not in text
    assert "Diversification" not in text


# --- advice-shaped guardrail --------------------------------------------------


def test_is_advice_shaped_flags_directive_language():
    for observation in [
        "You should sell some of your tech holdings.",
        "Consider rebalancing toward bonds.",
        "We recommend moving your money to a TFSA.",
        "It's time to buy more Canadian equities.",
        "My advice is to diversify into international markets.",
    ]:
        assert is_advice_shaped(observation), observation


def test_is_advice_shaped_flags_negative_imperative_and_hedged_language():
    for observation in [
        "Avoid concentrating further in one institution.",
        "Do not add to your tech position this month.",
        "Don't let your emergency fund shrink further.",
        "You should not hold this much cash.",
        "You shouldn't ignore the liabilities side.",
        "It may be wise to spread holdings across institutions.",
        "It might be wise to review your allocation.",
    ]:
        assert is_advice_shaped(observation), observation


def test_is_advice_shaped_allows_factual_observations():
    for observation in [
        "Tech is 48% of your equity holdings.",
        "Your portfolio is split 60/40 between short-term and long-term assets.",
        "Net worth increased since the previous snapshot.",
        "Canada makes up 45.0% of your equity look-through.",
    ]:
        assert not is_advice_shaped(observation), observation


def test_filter_advice_shaped_drops_only_matching_lines():
    observations = [
        "Tech is 48% of your holdings.",
        "You should sell some of that.",
        "  ",
        "Net worth grew this month.",
    ]
    assert filter_advice_shaped(observations) == [
        "Tech is 48% of your holdings.",
        "Net worth grew this month.",
    ]


def test_filter_advice_shaped_can_return_empty_list():
    assert filter_advice_shaped(["You should buy more ETFs."]) == []


def test_disclosure_text_is_nonempty_and_names_ai():
    assert DISCLOSURE_TEXT
    assert "ai" in DISCLOSURE_TEXT.lower() or "generated" in DISCLOSURE_TEXT.lower()


# --- decode_observations -------------------------------------------------------


def test_decode_observations_from_json_string():
    assert decode_observations('["a", "b"]') == ["a", "b"]


def test_decode_observations_from_already_decoded_list():
    assert decode_observations(["a", "b"]) == ["a", "b"]
