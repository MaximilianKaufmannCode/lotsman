# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""System clock adapter."""

from __future__ import annotations

from datetime import UTC, date, datetime


class SystemClock:
    """Production clock — returns real UTC time."""

    def now(self) -> datetime:
        return datetime.now(tz=UTC)

    def today(self) -> date:
        return datetime.now(tz=UTC).date()
