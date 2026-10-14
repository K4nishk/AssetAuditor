"""The worker's closed model-target set (AA-60, #43). Pure."""

import pytest

from worker.model_target import ModelTarget, ModelUse, model_group


def test_no_target_keeps_todays_groups() -> None:
    assert model_group(ModelUse.EXTRACTOR) == "extractor"
    assert model_group(ModelUse.COMMENTARY) == "commentary"


def test_a_local_target_serves_its_own_use() -> None:
    assert model_group(ModelUse.EXTRACTOR, ModelTarget.EXTRACTOR_LOCAL) == "extractor-local"
    assert model_group("commentary", "commentary-local") == "commentary-local"


@pytest.mark.parametrize(
    ("use", "target"),
    [
        (ModelUse.EXTRACTOR, ModelTarget.COMMENTARY_LOCAL),
        (ModelUse.EXTRACTOR, ModelTarget.COMMENTARY),
        (ModelUse.COMMENTARY, ModelTarget.EXTRACTOR_LOCAL),
        (ModelUse.COMMENTARY, ModelTarget.EXTRACTOR),
    ],
)
def test_a_target_from_the_other_use_is_rejected(use, target) -> None:
    with pytest.raises(ValueError, match="does not serve"):
        model_group(use, target)


def test_unknown_uses_and_targets_are_rejected() -> None:
    with pytest.raises(ValueError):
        model_group("summariser")
    with pytest.raises(ValueError):
        model_group(ModelUse.EXTRACTOR, "extractor-egress")
