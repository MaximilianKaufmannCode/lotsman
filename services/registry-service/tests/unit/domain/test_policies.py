# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Tests for domain policies — compute_status and is_urgent."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from hypothesis import given, settings
from hypothesis import strategies as st

from registry_service.domain.policies import compute_status, is_urgent
from registry_service.domain.value_objects import DocumentStatus

# ---------------------------------------------------------------------------
# compute_status
# ---------------------------------------------------------------------------


class TestComputeStatus:
    today = date(2026, 5, 7)

    def test_archived_overrides_expiry(self) -> None:
        result = compute_status(
            expiry_date=date(2025, 1, 1),  # in the past
            deleted_at=datetime.now(tz=UTC),
            today=self.today,
        )
        assert result == DocumentStatus.archived

    def test_no_expiry_is_ok(self) -> None:
        result = compute_status(expiry_date=None, deleted_at=None, today=self.today)
        assert result == DocumentStatus.ok

    def test_expiry_more_than_30_days_is_ok(self) -> None:
        future = self.today + timedelta(days=31)
        result = compute_status(expiry_date=future, deleted_at=None, today=self.today)
        assert result == DocumentStatus.ok

    def test_expiry_exactly_30_days_is_soon(self) -> None:
        future = self.today + timedelta(days=30)
        result = compute_status(expiry_date=future, deleted_at=None, today=self.today)
        assert result == DocumentStatus.soon

    def test_expiry_today_is_soon(self) -> None:
        """Boundary: in-day displays as 'Скоро' not 'Просрочено' per US-21 AC."""
        result = compute_status(expiry_date=self.today, deleted_at=None, today=self.today)
        assert result == DocumentStatus.soon

    def test_expiry_yesterday_is_overdue(self) -> None:
        yesterday = self.today - timedelta(days=1)
        result = compute_status(expiry_date=yesterday, deleted_at=None, today=self.today)
        assert result == DocumentStatus.overdue

    def test_expiry_far_past_is_overdue(self) -> None:
        long_ago = date(2020, 1, 1)
        result = compute_status(expiry_date=long_ago, deleted_at=None, today=self.today)
        assert result == DocumentStatus.overdue

    def test_deleted_at_string_truthy_counts_as_archived(self) -> None:
        result = compute_status(
            expiry_date=None, deleted_at="2026-01-01T00:00:00Z", today=self.today
        )
        assert result == DocumentStatus.archived


@given(
    days_from_today=st.integers(min_value=-3650, max_value=3650),
    is_archived=st.booleans(),
)
@settings(max_examples=500)
def test_compute_status_always_returns_valid_status(
    days_from_today: int, is_archived: bool
) -> None:
    """Property: compute_status always returns one of the four DocumentStatus values."""
    today = date(2026, 5, 7)
    expiry = today + timedelta(days=days_from_today)
    deleted_at = datetime.now(tz=UTC) if is_archived else None

    result = compute_status(expiry_date=expiry, deleted_at=deleted_at, today=today)
    assert result in (
        DocumentStatus.ok,
        DocumentStatus.soon,
        DocumentStatus.overdue,
        DocumentStatus.archived,
    )


@given(days_from_today=st.integers(min_value=1, max_value=3650))
def test_archived_always_wins(days_from_today: int) -> None:
    """Property: if deleted_at is set, status is always 'archived'."""
    today = date(2026, 5, 7)
    expiry = today + timedelta(days=days_from_today)
    result = compute_status(
        expiry_date=expiry,
        deleted_at=datetime.now(tz=UTC),
        today=today,
    )
    assert result == DocumentStatus.archived


# ---------------------------------------------------------------------------
# is_urgent
# ---------------------------------------------------------------------------


class TestIsUrgent:
    today = date(2026, 5, 7)

    def test_none_expiry_is_never_urgent(self) -> None:
        assert not is_urgent(None, self.today, [30, 7, 1])

    def test_exact_pre_notice_day_is_urgent(self) -> None:
        expiry = self.today + timedelta(days=30)
        assert is_urgent(expiry, self.today, [30, 7, 1])

    def test_not_on_pre_notice_day_is_not_urgent(self) -> None:
        expiry = self.today + timedelta(days=15)
        assert not is_urgent(expiry, self.today, [30, 7, 1])

    def test_overdue_is_not_urgent(self) -> None:
        expiry = self.today - timedelta(days=1)
        assert not is_urgent(expiry, self.today, [30, 7, 1])
