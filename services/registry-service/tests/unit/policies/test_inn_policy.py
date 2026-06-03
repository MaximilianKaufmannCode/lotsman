# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Tests for INN validation policy (Q6)."""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from registry_service.application.policies.inn_policy import validate_inn

# ---------------------------------------------------------------------------
# Known valid INNs (publicly available Russian company INNs)
# ---------------------------------------------------------------------------


class TestValidInns:
    def test_sberbank_10_digit(self) -> None:
        result = validate_inn("7707083893")
        assert result.valid is True
        assert result.error is None

    def test_gazprom_10_digit(self) -> None:
        result = validate_inn("7736050003")
        assert result.valid is True

    def test_valid_12_digit_ip(self) -> None:
        result = validate_inn("500100732259")
        assert result.valid is True

    def test_whitespace_stripped(self) -> None:
        result = validate_inn("  7707083893  ")
        assert result.valid is True


class TestInvalidInns:
    def test_empty_string(self) -> None:
        result = validate_inn("")
        assert result.valid is False
        assert "empty" in (result.error or "").lower()

    def test_letters(self) -> None:
        result = validate_inn("7707ABCDEF")
        assert result.valid is False

    def test_9_digits(self) -> None:
        result = validate_inn("123456789")
        assert result.valid is False
        assert "10 or 12" in (result.error or "")

    def test_11_digits(self) -> None:
        result = validate_inn("12345678901")
        assert result.valid is False

    def test_13_digits(self) -> None:
        result = validate_inn("1234567890123")
        assert result.valid is False

    def test_wrong_checksum_10_digit(self) -> None:
        # Sberbank INN with last digit changed
        result = validate_inn("7707083890")
        assert result.valid is False
        assert "checksum" in (result.error or "").lower()

    def test_wrong_11th_digit_12_digit(self) -> None:
        # Mutate 11th digit of a valid 12-digit INN
        result = validate_inn("500100732249")  # last 2 digits changed
        assert result.valid is False


# ---------------------------------------------------------------------------
# Property tests
# ---------------------------------------------------------------------------


@given(st.text(min_size=0, max_size=20))
def test_validate_inn_never_crashes(s: str) -> None:
    """Property: validate_inn never raises — always returns a result."""
    result = validate_inn(s)
    assert isinstance(result.valid, bool)


@given(st.integers(min_value=1, max_value=10**14))
@settings(max_examples=200)
def test_random_digit_strings_mostly_invalid(n: int) -> None:
    """Random digit strings should have very low probability of passing checksum."""
    s = str(n)
    result = validate_inn(s)
    # We don't assert invalid — some random strings could be valid by coincidence.
    # We assert the function returns without error.
    assert isinstance(result.valid, bool)
