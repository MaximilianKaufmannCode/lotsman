# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Attachment validation policy — pure function, no I/O.

MIME allowlist (Q7) and max size (Q1) are the two guardrails for file uploads.
"""

from __future__ import annotations

from registry_service.domain.errors import (
    AttachmentMimeRejectedError,
    AttachmentTooLargeError,
)

# Q1: 25 MiB per file
MAX_BYTES: int = 25 * 1024 * 1024  # 26_214_400 bytes

# Q7: allowed MIME types (server-side sniff, not extension)
ALLOWED_MIME: frozenset[str] = frozenset(
    {
        "application/pdf",
        "image/jpeg",
        "image/png",
        "image/tiff",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",  # docx
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",  # xlsx
    }
)


def validate(mime_type: str, size_bytes: int) -> None:
    """Validate attachment MIME type and size.

    Raises:
        AttachmentTooLargeError: if size_bytes > MAX_BYTES (25 MiB)
        AttachmentMimeRejectedError: if mime_type not in ALLOWED_MIME

    This function must be called BEFORE writing any bytes to disk.
    """
    if size_bytes > MAX_BYTES:
        raise AttachmentTooLargeError("Файл превышает допустимый размер 25 МиБ")

    if mime_type not in ALLOWED_MIME:
        allowed_list = ", ".join(sorted(ALLOWED_MIME))
        raise AttachmentMimeRejectedError(
            f"Unsupported media type '{mime_type}'. Allowed: {allowed_list}"
        )
