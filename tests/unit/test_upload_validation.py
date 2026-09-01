"""Unit tests for `app.uploads.validation` (KCH-46 / AA-11).

Pure functions, no DB/Blob/network — exercises sha256 format checks, size
caps, and the magic-byte sniff that rejects a mislabeled Content-Type before
any bronze row or Blob write happens.
"""

from __future__ import annotations

import pytest

from app.uploads.validation import (
    MAX_UPLOAD_SIZE_BYTES,
    UploadDeclaration,
    UploadRejected,
    is_valid_sha256_hex,
    sniff_content_type,
    validate_declaration,
    validate_received_bytes,
)

VALID_SHA256 = "a" * 64


def test_valid_sha256_hex_accepts_64_lowercase_hex_chars():
    assert is_valid_sha256_hex(VALID_SHA256) is True


@pytest.mark.parametrize(
    "value",
    ["A" * 64, "a" * 63, "a" * 65, "g" * 64, ""],
)
def test_valid_sha256_hex_rejects_malformed_values(value):
    assert is_valid_sha256_hex(value) is False


def test_sniff_content_type_detects_pdf_magic_bytes():
    assert sniff_content_type(b"%PDF-1.4\n...") == "application/pdf"


def test_sniff_content_type_treats_utf8_text_as_text_plain():
    assert sniff_content_type(b"date,amount\n2026-01-01,10.00\n") == "text/plain"


def test_sniff_content_type_rejects_binary_control_bytes():
    assert sniff_content_type(b"\x00\x01\x02not text") == "application/octet-stream"


def test_sniff_content_type_rejects_invalid_utf8():
    assert sniff_content_type(b"\xff\xfe\x00\x01") == "application/octet-stream"


def _declaration(**overrides):
    fields = {
        "sha256_hex": VALID_SHA256,
        "size_bytes": 1024,
        "content_type": "application/pdf",
    }
    fields.update(overrides)
    return UploadDeclaration(**fields)


def test_validate_declaration_accepts_a_well_formed_declaration():
    validate_declaration(_declaration())  # must not raise


def test_validate_declaration_rejects_bad_sha256():
    with pytest.raises(UploadRejected, match="sha256"):
        validate_declaration(_declaration(sha256_hex="not-a-hash"))


@pytest.mark.parametrize("size_bytes", [0, -1])
def test_validate_declaration_rejects_non_positive_size(size_bytes):
    with pytest.raises(UploadRejected, match="positive"):
        validate_declaration(_declaration(size_bytes=size_bytes))


def test_validate_declaration_rejects_size_over_cap():
    with pytest.raises(UploadRejected, match="exceeds cap"):
        validate_declaration(_declaration(size_bytes=MAX_UPLOAD_SIZE_BYTES + 1))


def test_validate_declaration_rejects_unsupported_content_type():
    with pytest.raises(UploadRejected, match="content_type"):
        validate_declaration(_declaration(content_type="application/zip"))


def test_validate_received_bytes_accepts_matching_pdf():
    validate_received_bytes(declared_content_type="application/pdf", data=b"%PDF-1.4\n...")


def test_validate_received_bytes_accepts_matching_csv():
    validate_received_bytes(declared_content_type="text/csv", data=b"a,b\n1,2\n")


def test_validate_received_bytes_rejects_content_type_mismatch():
    with pytest.raises(UploadRejected, match="do not look like"):
        validate_received_bytes(declared_content_type="application/pdf", data=b"a,b\n1,2\n")


def test_validate_received_bytes_rejects_binary_disguised_as_csv():
    with pytest.raises(UploadRejected, match="do not look like"):
        validate_received_bytes(declared_content_type="text/csv", data=b"\x00\x01MZ\x02")


def test_validate_received_bytes_rejects_empty_body():
    with pytest.raises(UploadRejected, match="empty"):
        validate_received_bytes(declared_content_type="application/pdf", data=b"")


def test_validate_received_bytes_rejects_over_cap():
    oversized = b"%PDF-" + b"0" * MAX_UPLOAD_SIZE_BYTES
    with pytest.raises(UploadRejected, match="exceeds cap"):
        validate_received_bytes(declared_content_type="application/pdf", data=oversized)
