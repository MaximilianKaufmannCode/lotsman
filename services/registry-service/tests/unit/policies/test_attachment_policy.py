# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Tests for attachment validation policy (Q1, Q7)."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from registry_service.application.policies.attachment_policy import (
    ALLOWED_MIME,
    MAX_BYTES,
    validate,
)
from registry_service.domain.errors import AttachmentMimeRejectedError, AttachmentTooLargeError


class TestValidate:
    def test_valid_pdf_within_limit(self) -> None:
        validate("application/pdf", 1024)  # no exception

    def test_valid_jpeg(self) -> None:
        validate("image/jpeg", MAX_BYTES)  # exactly at limit

    def test_exceeds_size_limit(self) -> None:
        with pytest.raises(AttachmentTooLargeError):
            validate("application/pdf", MAX_BYTES + 1)

    def test_rejected_mime(self) -> None:
        with pytest.raises(AttachmentMimeRejectedError):
            validate("application/x-dosexec", 1024)

    def test_generic_binary_rejected(self) -> None:
        with pytest.raises(AttachmentMimeRejectedError):
            validate("application/octet-stream", 1024)

    def test_text_rejected(self) -> None:
        with pytest.raises(AttachmentMimeRejectedError):
            validate("text/plain", 100)

    def test_max_bytes_is_25_mib(self) -> None:
        assert MAX_BYTES == 25 * 1024 * 1024

    def test_allowed_mime_set_contains_all_q7_types(self) -> None:
        assert "application/pdf" in ALLOWED_MIME
        assert "image/jpeg" in ALLOWED_MIME
        assert "image/png" in ALLOWED_MIME
        assert "image/tiff" in ALLOWED_MIME
        assert (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            in ALLOWED_MIME
        )
        assert "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" in ALLOWED_MIME


@given(size=st.integers(min_value=MAX_BYTES + 1, max_value=MAX_BYTES * 10))
def test_always_rejects_oversized(size: int) -> None:
    """Property: any file exceeding MAX_BYTES always raises AttachmentTooLargeError."""
    with pytest.raises(AttachmentTooLargeError):
        validate("application/pdf", size)


@given(mime=st.text(min_size=1, max_size=100))
@settings(max_examples=200)
def test_only_allowed_mimes_pass(mime: str) -> None:
    """Property: only MIME types in ALLOWED_MIME can pass size=0 validation."""
    if mime not in ALLOWED_MIME:
        with pytest.raises(AttachmentMimeRejectedError):
            validate(mime, 0)
