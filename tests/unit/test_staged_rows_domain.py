"""Unit tests for the pure parse-confirm helpers (KCH-52 / AA-17)."""

from __future__ import annotations

from decimal import Decimal

from app.domain.staged_rows import (
    LOW_CONFIDENCE_THRESHOLD,
    decode_payload,
    encode_payload,
    is_low_confidence,
)


def test_is_low_confidence_flags_none():
    assert is_low_confidence(None) is True


def test_is_low_confidence_flags_below_threshold():
    assert is_low_confidence(LOW_CONFIDENCE_THRESHOLD - 0.01) is True


def test_is_low_confidence_does_not_flag_at_or_above_threshold():
    assert is_low_confidence(LOW_CONFIDENCE_THRESHOLD) is False
    assert is_low_confidence(1.0) is False


def test_encode_payload_serializes_decimal_as_exact_string():
    encoded = encode_payload({"quantity": Decimal("0.085"), "ticker": "BTC"})

    assert '"quantity": "0.085"' in encoded
    assert decode_payload(encoded) == {"quantity": "0.085", "ticker": "BTC"}


def test_decode_payload_passes_through_an_already_decoded_dict():
    payload = {"ticker": "AAPL"}
    assert decode_payload(payload) is payload


def test_encode_decode_payload_roundtrips_nested_structures():
    payload = {
        "linked_asset": {"user_estimated_value_cad": Decimal("520000.00")},
        "vested": True,
        "amount": None,
    }

    assert decode_payload(encode_payload(payload)) == {
        "linked_asset": {"user_estimated_value_cad": "520000.00"},
        "vested": True,
        "amount": None,
    }
