"""Term-bucket classification (KCH-53 / AA-18)."""

import pytest

from app.domain.buckets import UnknownAccountType, classify_term_bucket


@pytest.mark.parametrize(
    "account_type,expected",
    [
        ("chequing", "short_term"),
        ("savings", "short_term"),
        ("hisa", "short_term"),
        ("HISA", "short_term"),
        (" Savings ", "short_term"),
        ("fhsa", "medium_term"),
        ("fhsa_invest", "medium_term"),
        ("mutual_fund", "medium_term"),
        ("tfsa", "long_term"),
        ("rrsp", "long_term"),
        ("esop", "long_term"),
        ("crypto_exchange", "long_term"),
        ("crypto_brokerage", "long_term"),
    ],
)
def test_classify_term_bucket(account_type: str, expected: str) -> None:
    assert classify_term_bucket(account_type) == expected


def test_unknown_account_type_raises() -> None:
    with pytest.raises(UnknownAccountType):
        classify_term_bucket("line_of_credit")


def test_unknown_account_type_never_silently_misclassifies() -> None:
    with pytest.raises(UnknownAccountType):
        classify_term_bucket("some_future_institution_product")
