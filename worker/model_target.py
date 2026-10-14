"""The closed set of LiteLLM model groups the worker may call (AA-60, #43; ADR v1.2.0 §3).

Each model use has today's group (`extractor`, `commentary`: Groq) and a
`-local` group that reaches only the Box's vLLM. A call names its use and may
name a target; a target that belongs to the other use is rejected here, before
any request is built. With no target a call keeps today's group, so production
routing is unchanged until AA-67 wires in the worker's backend decision.

The values must match `llm/litellm.config.yaml`'s `model_name` entries exactly;
tests/unit/test_local_stack_config.py checks both directions.
"""

from __future__ import annotations

from enum import StrEnum


class ModelUse(StrEnum):
    EXTRACTOR = "extractor"
    COMMENTARY = "commentary"


class ModelTarget(StrEnum):
    EXTRACTOR = "extractor"
    EXTRACTOR_LOCAL = "extractor-local"
    COMMENTARY = "commentary"
    COMMENTARY_LOCAL = "commentary-local"


_DEFAULT_TARGET = {
    ModelUse.EXTRACTOR: ModelTarget.EXTRACTOR,
    ModelUse.COMMENTARY: ModelTarget.COMMENTARY,
}

_TARGETS_BY_USE = {
    ModelUse.EXTRACTOR: frozenset({ModelTarget.EXTRACTOR, ModelTarget.EXTRACTOR_LOCAL}),
    ModelUse.COMMENTARY: frozenset({ModelTarget.COMMENTARY, ModelTarget.COMMENTARY_LOCAL}),
}


def model_group(use: ModelUse | str, target: ModelTarget | str | None = None) -> str:
    """The LiteLLM `model` for a call: today's group, or a target checked against the use.

    Raises `ValueError` for an unknown use or target, or a target from the other use.
    """
    use = ModelUse(use)
    if target is None:
        return _DEFAULT_TARGET[use].value
    target = ModelTarget(target)
    if target not in _TARGETS_BY_USE[use]:
        raise ValueError(f"model target {target.value!r} does not serve {use.value!r}")
    return target.value
