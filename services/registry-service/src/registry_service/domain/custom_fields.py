# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Value objects and validators for per-type custom document fields.

Pure domain module — no SQLAlchemy, no FastAPI, no HTTP codes.
Only stdlib + domain errors are imported (Iron Rule 1).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from math import isfinite
from typing import Any

_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

# ---------------------------------------------------------------------------
# Field type enum
# ---------------------------------------------------------------------------


class FieldType(StrEnum):
    TEXT = "text"
    NUMBER = "number"
    DATE = "date"
    ENUM = "enum"


# ---------------------------------------------------------------------------
# Domain error (defined here to avoid circular imports with domain/errors.py)
# ---------------------------------------------------------------------------


class CustomFieldValidationError(Exception):
    """Raised when CustomField construction or value validation fails."""

    code = "CUSTOM_FIELD_VALIDATION"
    status_code = 422

    def __init__(self, message: str, loc: str | None = None) -> None:
        super().__init__(message)
        self.loc = loc  # optional field key for per-field error context


# ---------------------------------------------------------------------------
# CustomField value object
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CustomField:
    """Descriptor for a single custom field on a document type.

    Invariants (enforced in __post_init__):
      - key matches ^[a-z][a-z0-9_]{0,63}$
      - display_name is 1..100 chars
      - options is required (non-empty list) for ENUM; must be None for others
    """

    key: str
    display_name: str
    type: FieldType
    required: bool = False
    options: list[str] | None = field(default=None, hash=False)

    def __post_init__(self) -> None:
        if not _KEY_RE.match(self.key):
            raise CustomFieldValidationError(
                f"Field key '{self.key}' must match ^[a-z][a-z0-9_]{{0,63}}$",
                loc=self.key,
            )
        if not (1 <= len(self.display_name) <= 100):
            raise CustomFieldValidationError(
                f"display_name must be 1..100 characters (got {len(self.display_name)})",
                loc=self.key,
            )
        if self.type == FieldType.ENUM:
            if not self.options:
                raise CustomFieldValidationError(
                    f"Field '{self.key}' of type 'enum' must have at least one option",
                    loc=self.key,
                )
        else:
            if self.options is not None:
                raise CustomFieldValidationError(
                    f"Field '{self.key}' of type '{self.type}' must not have options",
                    loc=self.key,
                )

    # Serialise to / from plain dicts (stored in JSONB)
    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "key": self.key,
            "display_name": self.display_name,
            "type": str(self.type),
            "required": self.required,
        }
        if self.options is not None:
            d["options"] = self.options
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CustomField:
        return cls(
            key=str(data["key"]),
            display_name=str(data["display_name"]),
            type=FieldType(data["type"]),
            required=bool(data.get("required", False)),
            options=(
                list(data["options"]) if "options" in data and data["options"] is not None else None
            ),
        )


# ---------------------------------------------------------------------------
# Value validators
# ---------------------------------------------------------------------------


def validate_value(field_def: CustomField, value: Any) -> Any:
    """Coerce and validate a single value against its CustomField descriptor.

    Returns the coerced value on success.
    Raises CustomFieldValidationError on failure.
    """
    ft = field_def.type

    if ft == FieldType.TEXT:
        coerced = value if isinstance(value, str) else str(value)
        if len(coerced) > 10_000:
            raise CustomFieldValidationError(
                f"Field '{field_def.key}': text value exceeds 10,000 characters",
                loc=field_def.key,
            )
        return coerced

    if ft == FieldType.NUMBER:
        if isinstance(value, bool):
            raise CustomFieldValidationError(
                f"Field '{field_def.key}': boolean is not a valid number",
                loc=field_def.key,
            )
        try:
            coerced_num: int | float
            if isinstance(value, (int, float)):
                coerced_num = value
            else:
                s = str(value).strip()
                coerced_num = float(s)
                if coerced_num == int(coerced_num):
                    coerced_num = int(coerced_num)
        except (ValueError, TypeError) as exc:
            raise CustomFieldValidationError(
                f"Field '{field_def.key}': cannot convert {value!r} to number",
                loc=field_def.key,
            ) from exc
        if isinstance(coerced_num, float) and not isfinite(coerced_num):
            raise CustomFieldValidationError(
                f"Field '{field_def.key}': NaN and Inf are not permitted",
                loc=field_def.key,
            )
        return coerced_num

    if ft == FieldType.DATE:
        # Stored as ISO-string in JSONB so json.dumps doesn't trip over
        # native date/datetime objects (openpyxl returns datetime for date
        # cells; datetime IS a subclass of date so a naive isinstance(date)
        # check would let it through unchanged).
        from datetime import datetime as _dt

        if isinstance(value, _dt):
            return value.date().isoformat()
        if isinstance(value, date):
            return value.isoformat()
        try:
            return date.fromisoformat(str(value).strip()[:10]).isoformat()
        except (ValueError, TypeError) as exc:
            raise CustomFieldValidationError(
                f"Field '{field_def.key}': cannot parse {value!r} as ISO date (YYYY-MM-DD)",
                loc=field_def.key,
            ) from exc

    if ft == FieldType.ENUM:
        str_val = str(value).strip()
        opts = field_def.options or []
        if str_val not in opts:
            raise CustomFieldValidationError(
                f"Field '{field_def.key}': value {str_val!r} not in options {opts}",
                loc=field_def.key,
            )
        return str_val

    raise CustomFieldValidationError(f"Unknown field type: {ft}", loc=field_def.key)


def validate_values_against_schema(
    schema: list[CustomField],
    values: dict[str, Any],
) -> dict[str, Any]:
    """Validate a flat values dict against a list of CustomField descriptors.

    Rules:
    - Unknown keys (not in schema) are silently dropped (per US-2 last scenario).
    - Present keys are validated and coerced.
    - Required fields that are absent (or None) raise CustomFieldValidationError.

    Returns the cleaned (coerced, unknown-keys-stripped) dict.
    """
    schema_by_key: dict[str, CustomField] = {f.key: f for f in schema}
    result: dict[str, Any] = {}

    for field_def in schema:
        raw = values.get(field_def.key)

        if raw is None or raw == "":
            if field_def.required:
                raise CustomFieldValidationError(
                    f"Required field '{field_def.key}' is missing or empty",
                    loc=field_def.key,
                )
            # Optional + absent → omit from result
            continue

        result[field_def.key] = validate_value(field_def, raw)

    # Validate any values that ARE present, even if field is optional
    # (already handled above for schema fields; just make sure extra keys are dropped)
    for key in values:
        if key not in schema_by_key:
            # Unknown key — silently drop, already excluded from result
            continue

    return result
