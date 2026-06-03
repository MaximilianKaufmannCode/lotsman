# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""INN validation policy — pure function, no I/O.

Russian tax identifier (ИНН) validation per ФНС official algorithm:

  10-digit INN (юрлицо / legal entity):
    Check digit (index 9) = (sum of digits[0..8] * weights[0..8]) mod 11 mod 10
    Weights: [2, 4, 10, 3, 5, 9, 4, 6, 8]

  12-digit INN (ИП / individual entrepreneur):
    Check digit 11 (index 10) = (sum of digits[0..9] * weights1[0..9]) mod 11 mod 10
    Check digit 12 (index 11) = (sum of digits[0..10] * weights2[0..10]) mod 11 mod 10
    weights1: [7, 2, 4, 10, 3, 5, 9, 4, 6, 8]
    weights2: [3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8]

References:
  https://www.nalog.gov.ru/rn77/related_activities/statistics_and_analytics/statistika/3861869/
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_DIGITS_ONLY = re.compile(r"^\d+$")
_INN10_W = (2, 4, 10, 3, 5, 9, 4, 6, 8)
_INN12_W1 = (7, 2, 4, 10, 3, 5, 9, 4, 6, 8)
_INN12_W2 = (3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8)


@dataclass(frozen=True)
class InnValidationResult:
    valid: bool
    error: str | None = None


def _weighted_check(digits: str, weights: tuple[int, ...]) -> int:
    total = sum(int(d) * w for d, w in zip(digits[: len(weights)], weights, strict=True))
    return total % 11 % 10


def validate_inn(value: str) -> InnValidationResult:
    """Validate a Russian INN string.

    Returns:
        InnValidationResult with valid=True on success, or valid=False with error
        describing the problem.

    This function is pure — no side effects, no I/O.
    """
    value = value.strip()

    if not value:
        return InnValidationResult(valid=False, error="INN must not be empty")

    if not _DIGITS_ONLY.match(value):
        return InnValidationResult(valid=False, error="INN must contain digits only")

    length = len(value)

    if length == 10:
        expected = _weighted_check(value, _INN10_W)
        if expected != int(value[9]):
            return InnValidationResult(
                valid=False,
                error=f"INN checksum invalid: expected last digit {expected}, got {value[9]}",
            )
        return InnValidationResult(valid=True)

    if length == 12:
        c11 = _weighted_check(value, _INN12_W1)
        if c11 != int(value[10]):
            return InnValidationResult(
                valid=False,
                error=f"INN 11th digit checksum invalid: expected {c11}, got {value[10]}",
            )
        c12 = _weighted_check(value, _INN12_W2)
        if c12 != int(value[11]):
            return InnValidationResult(
                valid=False,
                error=f"INN 12th digit checksum invalid: expected {c12}, got {value[11]}",
            )
        return InnValidationResult(valid=True)

    return InnValidationResult(
        valid=False,
        error=f"INN must be 10 or 12 digits; got {length}",
    )
