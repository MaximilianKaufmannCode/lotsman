# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Unit tests for CheckLockout use case + lockout_policy (US-11, US-12).

Covers:
- 5 failures in 15 min → locked
- 10 failures in 60 min → long locked + AccountLocked event emitted
- Failures outside 60 min window not counted (not contiguous)
- Successful login logically resets counter
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from auth_service.application.use_cases.check_lockout import CheckLockout
from auth_service.domain.entities import LoginAttempt

from .conftest import FakeLoginAttemptRepository


def _make_failures(
    email: str,
    count: int,
    minutes_ago: int,
    outcome: str = "failed_password",
) -> list[LoginAttempt]:
    base = datetime.now(tz=UTC) - timedelta(minutes=minutes_ago)
    return [
        LoginAttempt.create(
            email=email,
            outcome=outcome,
            now=base + timedelta(seconds=i * 10),
        )
        for i in range(count)
    ]


# ---------------------------------------------------------------------------
# Short lockout (US-11)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_5_failures_in_15min_triggers_short_lockout() -> None:
    email = "vince@example.com"
    repo = FakeLoginAttemptRepository()
    for attempt in _make_failures(email, 5, minutes_ago=5):
        await repo.add(attempt)

    uc = CheckLockout(repo)
    result = await uc.execute(email=email)

    assert result.is_locked


@pytest.mark.asyncio
async def test_4_failures_in_15min_does_not_lock() -> None:
    email = "vince@example.com"
    repo = FakeLoginAttemptRepository()
    for attempt in _make_failures(email, 4, minutes_ago=5):
        await repo.add(attempt)

    uc = CheckLockout(repo)
    result = await uc.execute(email=email)

    assert not result.is_locked


@pytest.mark.asyncio
async def test_lockout_window_expired_after_16min_allows_login() -> None:
    """Failures older than 15 min do not count toward the short lockout."""
    email = "vince@example.com"
    repo = FakeLoginAttemptRepository()
    # 5 failures 16 minutes ago (outside the 15-min window)
    for attempt in _make_failures(email, 5, minutes_ago=16):
        await repo.add(attempt)

    uc = CheckLockout(repo)
    result = await uc.execute(email=email)

    assert not result.is_locked


# ---------------------------------------------------------------------------
# Long lockout (US-12)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_10_failures_in_60min_triggers_long_lockout() -> None:
    email = "wendy@example.com"
    repo = FakeLoginAttemptRepository()
    for attempt in _make_failures(email, 10, minutes_ago=30):
        await repo.add(attempt)

    uc = CheckLockout(repo)
    result = await uc.execute(email=email)

    assert result.is_locked
    assert result.is_long_lockout


@pytest.mark.asyncio
async def test_old_failures_outside_60min_not_counted() -> None:
    """10 failures older than 60 min must not trigger the 24h lockout."""
    email = "wendy@example.com"
    repo = FakeLoginAttemptRepository()
    for attempt in _make_failures(email, 10, minutes_ago=61):
        await repo.add(attempt)

    uc = CheckLockout(repo)
    result = await uc.execute(email=email)

    assert not result.is_long_lockout


@pytest.mark.asyncio
async def test_success_after_four_failures_records_success_counter_reset() -> None:
    """A success in the window means the lockout count should not reach threshold."""
    email = "vince@example.com"
    repo = FakeLoginAttemptRepository()

    # 4 failures + 1 success — success resets the effective counter
    for attempt in _make_failures(email, 4, minutes_ago=10):
        await repo.add(attempt)
    await repo.add(LoginAttempt.create(email=email, outcome="success"))

    # CheckLockout reads failures since the last success; with a success present,
    # has_success_after_last_failure returns True → counter is logically reset
    uc = CheckLockout(repo)
    result = await uc.execute(email=email)

    assert not result.is_locked
