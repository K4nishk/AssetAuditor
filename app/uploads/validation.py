"""Pure upload validation: size caps, sha256 format, magic-byte sniffing (AA-11).

No I/O here — every function takes bytes/values already in hand so this is
runnable against arbitrary input in a unit test, with no DB/Blob/network in
the loop. `app/routes/uploads.py` is the only caller that turns a rejection
into an HTTP response.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Bank/brokerage statements are small; this is a generous ceiling that still
# stops a mistaken/abusive multi-GB upload from ever reaching Blob storage.
MAX_UPLOAD_SIZE_BYTES = 20 * 1024 * 1024

_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")

# The only shapes data/samples/README.md's adapter contract recognizes today.
# Mapping declared MIME -> the sniffed family it must match (see
# `sniff_content_type`) so a client can't smuggle an arbitrary binary past the
# API by mislabeling its Content-Type.
ALLOWED_DECLARED_CONTENT_TYPES = {
    "application/pdf": "application/pdf",
    "text/csv": "text/plain",
    "application/json": "text/plain",
}

_PDF_MAGIC = b"%PDF-"
# Control characters other than tab/newline/carriage-return never appear in
# well-formed CSV/JSON text; their presence is the cheapest signal that a
# file's real bytes don't match a declared text-ish Content-Type.
_BINARY_CONTROL_CHAR_RE = re.compile(rb"[\x00-\x08\x0b\x0c\x0e-\x1f]")


class UploadRejected(Exception):
    """A declared or observed upload property fails a hard rule (AA-11)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def is_valid_sha256_hex(value: str) -> bool:
    return bool(_SHA256_HEX_RE.match(value))


def sniff_content_type(header: bytes) -> str:
    """Classify a file by its leading bytes, independent of any declared type.

    Returns one of `"application/pdf"`, `"text/plain"` (covers CSV/JSON —
    neither format has a reliable magic number, so any plausible UTF-8 text
    without binary control characters counts), or `"application/octet-stream"`
    for anything else.
    """
    if header.startswith(_PDF_MAGIC):
        return "application/pdf"

    if _BINARY_CONTROL_CHAR_RE.search(header):
        return "application/octet-stream"

    try:
        header.decode("utf-8")
    except UnicodeDecodeError:
        return "application/octet-stream"

    return "text/plain"


@dataclass(frozen=True)
class UploadDeclaration:
    sha256_hex: str
    size_bytes: int
    content_type: str


def validate_declaration(declaration: UploadDeclaration) -> None:
    """Reject a `POST /uploads` request before any Blob/DB work happens."""
    if not is_valid_sha256_hex(declaration.sha256_hex):
        raise UploadRejected("sha256 must be 64 lowercase hex characters")

    if declaration.size_bytes <= 0:
        raise UploadRejected("size_bytes must be positive")

    if declaration.size_bytes > MAX_UPLOAD_SIZE_BYTES:
        raise UploadRejected(f"size_bytes exceeds cap of {MAX_UPLOAD_SIZE_BYTES} bytes")

    if declaration.content_type not in ALLOWED_DECLARED_CONTENT_TYPES:
        allowed = ", ".join(sorted(ALLOWED_DECLARED_CONTENT_TYPES))
        raise UploadRejected(f"content_type must be one of: {allowed}")


def validate_received_bytes(*, declared_content_type: str, data: bytes) -> None:
    """Reject the actual bytes received at `PUT /uploads/blob` before storing them."""
    if len(data) > MAX_UPLOAD_SIZE_BYTES:
        raise UploadRejected(f"upload exceeds cap of {MAX_UPLOAD_SIZE_BYTES} bytes")

    if not data:
        raise UploadRejected("upload body is empty")

    expected_family = ALLOWED_DECLARED_CONTENT_TYPES.get(declared_content_type)
    if expected_family is None:
        allowed = ", ".join(sorted(ALLOWED_DECLARED_CONTENT_TYPES))
        raise UploadRejected(f"content_type must be one of: {allowed}")

    sniffed = sniff_content_type(data[:64])
    if sniffed != expected_family:
        raise UploadRejected(
            f"upload bytes do not look like {declared_content_type} "
            f"(sniffed {sniffed}, declared family {expected_family})"
        )
