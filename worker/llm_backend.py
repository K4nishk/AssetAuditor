"""Which backend actually served a LiteLLM call — AA-61 (#44), hard rule 1.

LiteLLM (v1.104.2, the pinned router) overwrites a response's `model` field
with the alias the caller requested (`extractor`, `commentary`), so deriving
provenance from it records the alias rather than the provider. The deployment
that served the call travels in the `x-litellm-model-name` response header,
on success and on error alike; that header is the only source read here.
The result is always one of `BACKENDS`, so it is also safe as a metric label.
"""

from __future__ import annotations

from typing import Any

BACKEND_HEADER = "x-litellm-model-name"
BACKENDS = ("vllm", "groq", "unknown")

_PREFIXES = {"vllm": "vllm", "hosted_vllm": "vllm", "groq": "groq"}


def backend_from_headers(headers: Any) -> str:
    """`vllm|groq|unknown` from a response's headers (case-insensitive lookup)."""
    value = headers.get(BACKEND_HEADER) if headers is not None else None
    if not isinstance(value, str):
        return "unknown"
    return _PREFIXES.get(value.split("/", 1)[0].strip().lower(), "unknown")


def backend_from_error(exc: BaseException) -> str:
    """The failing backend, when the error carries the router's response."""
    response = getattr(exc, "response", None)
    return backend_from_headers(getattr(response, "headers", None))


def clamp_backend(label: str) -> str:
    """Keep a backend metric label inside `BACKENDS`."""
    return label if label in BACKENDS else "unknown"
