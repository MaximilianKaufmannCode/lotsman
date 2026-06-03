# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Real clock implementation. Tests inject FakeClock."""

from __future__ import annotations

from datetime import UTC, datetime


class RealClock:
    """Concrete Clock implementation.

    Implements auth_service.application.ports.Clock.
    """

    def now(self) -> datetime:
        return datetime.now(tz=UTC)


class FakeClock:
    """Fake clock for testing. Allows freezing time."""

    def __init__(self, fixed_time: datetime) -> None:
        self._time = fixed_time

    def now(self) -> datetime:
        return self._time

    def advance(self, **kwargs: int) -> None:
        """Advance the clock by the given timedelta kwargs."""
        from datetime import timedelta

        self._time = self._time + timedelta(**kwargs)


# Singleton for production use
clock = RealClock()
