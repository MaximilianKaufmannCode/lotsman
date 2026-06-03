# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Unit + property tests for password_policy.py (US-2, US-6, ADR-0003 §2 / R-2).

Covers:
- Min length enforcement (< 12 chars → PasswordPolicyViolationError)
- Max length enforcement (> 1024 chars → PasswordPolicyViolationError)
- HIBP breach detection
- No composition rules (unicode passes if length OK)
- No periodic rotation assumption (no expiry logic in validate_password)
- Property: any string 12..1024 that is NOT breached must pass
- Property: any string shorter than 12 OR longer than 1024 must fail
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from auth_service.application.policies.password_policy import validate_password
from auth_service.domain.errors import PasswordPolicyViolationError, WeakPasswordError

from ..use_cases.conftest import FakeBreachedPasswordChecker

_CLEAN_CHECKER = FakeBreachedPasswordChecker()
_BREACH_CHECKER = FakeBreachedPasswordChecker(breach_words=frozenset(["password123456"]))


# ---------------------------------------------------------------------------
# Min / max length
# ---------------------------------------------------------------------------


def test_password_below_min_length_raises() -> None:
    with pytest.raises(PasswordPolicyViolationError):
        validate_password("short", hibp_checker=_CLEAN_CHECKER)


def test_password_at_min_length_passes() -> None:
    validate_password("a" * 12, hibp_checker=_CLEAN_CHECKER)


def test_password_exceeds_max_length_raises() -> None:
    with pytest.raises(PasswordPolicyViolationError):
        validate_password("x" * 1025, hibp_checker=_CLEAN_CHECKER)


def test_password_at_max_length_passes() -> None:
    validate_password("a" * 1024, hibp_checker=_CLEAN_CHECKER)


# ---------------------------------------------------------------------------
# HIBP check
# ---------------------------------------------------------------------------


def test_breached_password_raises_weak_password_error() -> None:
    with pytest.raises(WeakPasswordError):
        validate_password("password123456", hibp_checker=_BREACH_CHECKER)


def test_non_breached_password_passes() -> None:
    # Identical length but not in breached list
    validate_password("password1234567", hibp_checker=_BREACH_CHECKER)


# ---------------------------------------------------------------------------
# No composition rules
# ---------------------------------------------------------------------------


def test_all_lowercase_no_digits_passes_if_long_enough() -> None:
    """NIST 800-63B: no composition rules. Lowercase-only is fine."""
    validate_password("correcthorsebatterystaple", hibp_checker=_CLEAN_CHECKER)


def test_unicode_cyrillic_password_passes() -> None:
    """Cyrillic characters are valid (no ASCII restriction in policy)."""
    validate_password("корректнаялошадьбатарея", hibp_checker=_CLEAN_CHECKER)


def test_emoji_password_passes_if_long_enough() -> None:
    """Emoji in passwords: allowed (paste-friendly)."""
    validate_password("correct-horse-battery-🔋", hibp_checker=_CLEAN_CHECKER)


# ---------------------------------------------------------------------------
# Property tests (hypothesis)
# ---------------------------------------------------------------------------


@settings(max_examples=200)
@given(st.text(min_size=12, max_size=1024))
def test_property_valid_length_non_breached_passes(password: str) -> None:
    """Any non-breached password between 12 and 1024 chars must pass validation."""
    # Ensure it's not in the breached list
    checker = FakeBreachedPasswordChecker(breach_words=frozenset())
    validate_password(password, hibp_checker=checker)


@settings(max_examples=200)
@given(st.text(min_size=0, max_size=11))
def test_property_too_short_always_raises(password: str) -> None:
    """Any password shorter than 12 chars raises PasswordPolicyViolationError."""
    with pytest.raises(PasswordPolicyViolationError):
        validate_password(password, hibp_checker=_CLEAN_CHECKER)


@settings(max_examples=50)
@given(st.text(min_size=1025, max_size=2048))
def test_property_too_long_always_raises(password: str) -> None:
    """Any password longer than 1024 chars raises PasswordPolicyViolationError."""
    with pytest.raises(PasswordPolicyViolationError):
        validate_password(password, hibp_checker=_CLEAN_CHECKER)
