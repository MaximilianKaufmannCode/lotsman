# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Unit + property tests for lockout_policy.py (US-11, US-12 / ADR-0003 §12 / R-9 / F-005).

Covers:
- is_short_locked: threshold at 5
- is_long_locked: threshold at 10
- Property: counts below threshold never lock
- Property: counts at or above threshold always lock
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from auth_service.application.policies.lockout_policy import (
    LONG_LOCKOUT_FAILURES,
    SHORT_LOCKOUT_FAILURES,
    is_long_locked,
    is_short_locked,
)

# ---------------------------------------------------------------------------
# Short lockout
# ---------------------------------------------------------------------------


def test_4_failures_not_short_locked() -> None:
    assert not is_short_locked(4)


def test_5_failures_is_short_locked() -> None:
    assert is_short_locked(5)


def test_6_failures_is_short_locked() -> None:
    assert is_short_locked(6)


# ---------------------------------------------------------------------------
# Long lockout
# ---------------------------------------------------------------------------


def test_9_failures_not_long_locked() -> None:
    assert not is_long_locked(9)


def test_10_failures_is_long_locked() -> None:
    assert is_long_locked(10)


def test_11_failures_is_long_locked() -> None:
    assert is_long_locked(11)


# ---------------------------------------------------------------------------
# Property tests
# ---------------------------------------------------------------------------


@settings(max_examples=200)
@given(st.integers(min_value=0, max_value=SHORT_LOCKOUT_FAILURES - 1))
def test_property_below_short_threshold_never_locked(count: int) -> None:
    assert not is_short_locked(count)


@settings(max_examples=200)
@given(st.integers(min_value=SHORT_LOCKOUT_FAILURES, max_value=1000))
def test_property_at_or_above_short_threshold_always_locked(count: int) -> None:
    assert is_short_locked(count)


@settings(max_examples=200)
@given(st.integers(min_value=0, max_value=LONG_LOCKOUT_FAILURES - 1))
def test_property_below_long_threshold_never_long_locked(count: int) -> None:
    assert not is_long_locked(count)


@settings(max_examples=200)
@given(st.integers(min_value=LONG_LOCKOUT_FAILURES, max_value=1000))
def test_property_at_or_above_long_threshold_always_long_locked(count: int) -> None:
    assert is_long_locked(count)
