# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Domain value objects for registry-service.

Pure Python — no SQLAlchemy, no FastAPI, no infrastructure imports.
Stdlib + pydantic only (Iron Rule 1).
"""

from __future__ import annotations

import re
from enum import StrEnum

# ---------------------------------------------------------------------------
# DocumentStatus — computed urgency badge (US-21, Q4)
# ---------------------------------------------------------------------------


class DocumentStatus(StrEnum):
    """Computed urgency classification for a document.

    Never persisted — always derived at read time from expiry_date and deleted_at.

    Thresholds (calendar days, per Q4):
      - ok:       expiry_date IS NULL or expiry_date > today + 30 days
      - soon:     0 <= (expiry_date - today) <= 30 days (includes in-day per US-21)
      - overdue:  expiry_date < today
      - archived: deleted_at IS NOT NULL (regardless of expiry_date)
    """

    ok = "ok"
    soon = "soon"
    overdue = "overdue"
    archived = "archived"

    @property
    def display_label_ru(self) -> str:
        return {
            DocumentStatus.ok: "ОК",
            DocumentStatus.soon: "Скоро",
            DocumentStatus.overdue: "Просрочено",
            DocumentStatus.archived: "В архиве",
        }[self]


# ---------------------------------------------------------------------------
# ExportFormat
# ---------------------------------------------------------------------------


class ExportFormat(StrEnum):
    xlsx = "xlsx"


# ---------------------------------------------------------------------------
# AttachmentMime — MIME allowlist per Q7
# ---------------------------------------------------------------------------


class AttachmentMime(StrEnum):
    """Allowed MIME types for attachments (Q7 acceptance decision)."""

    pdf = "application/pdf"
    jpeg = "image/jpeg"
    png = "image/png"
    tiff = "image/tiff"
    docx = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    xlsx = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


# ---------------------------------------------------------------------------
# INN  — Russian tax identifier value object
# ---------------------------------------------------------------------------

_INN_10_RE = re.compile(r"^\d{10}$")
_INN_12_RE = re.compile(r"^\d{12}$")

# ФНС checksum weights
_INN10_W1 = (2, 4, 10, 3, 5, 9, 4, 6, 8)  # first 9 digits → check digit at index 9
_INN12_W1 = (7, 2, 4, 10, 3, 5, 9, 4, 6, 8)  # for 11th digit
_INN12_W2 = (3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8)  # for 12th digit


def _checksum(digits: str, weights: tuple[int, ...]) -> int:
    total = sum(int(d) * w for d, w in zip(digits[: len(weights)], weights, strict=True))
    return total % 11 % 10


class INN(str):
    """Russian tax identifier, validated per ФНС checksum algorithm.

    Accepts 10-digit (юрлицо) or 12-digit (ИП) strings.
    Raises ValueError on invalid format or checksum.
    """

    def __new__(cls, value: str) -> INN:
        value = value.strip()
        if _INN_10_RE.match(value):
            if _checksum(value, _INN10_W1) != int(value[9]):
                raise ValueError(f"INN checksum invalid for 10-digit INN: {value}")
        elif _INN_12_RE.match(value):
            if _checksum(value, _INN12_W1) != int(value[10]):
                raise ValueError(f"INN checksum invalid (11th digit) for 12-digit INN: {value}")
            if _checksum(value, _INN12_W2) != int(value[11]):
                raise ValueError(f"INN checksum invalid (12th digit) for 12-digit INN: {value}")
        else:
            raise ValueError(
                f"INN must be exactly 10 or 12 digits; got {len(value)} characters: {value!r}"
            )
        return str.__new__(cls, value)


# ---------------------------------------------------------------------------
# AssetName — trimmed, non-empty, max 500 chars
# ---------------------------------------------------------------------------

_ASSET_NAME_MAX = 500


class AssetName(str):
    """Trimmed, non-empty partner-company display name."""

    def __new__(cls, value: str) -> AssetName:
        value = value.strip()
        if not value:
            raise ValueError("Asset name must not be empty or whitespace-only")
        if len(value) > _ASSET_NAME_MAX:
            raise ValueError(
                f"Asset name must not exceed {_ASSET_NAME_MAX} characters; got {len(value)}"
            )
        return str.__new__(cls, value)


# ---------------------------------------------------------------------------
# DocumentNumber — optional, but if provided must not exceed 500 chars
# ---------------------------------------------------------------------------

_DOC_NUMBER_MAX = 500


class DocumentNumber(str):
    """Document number / identifier (free text)."""

    def __new__(cls, value: str) -> DocumentNumber:
        value = value.strip()
        if len(value) > _DOC_NUMBER_MAX:
            raise ValueError(f"Document number must not exceed {_DOC_NUMBER_MAX} characters")
        return str.__new__(cls, value)
