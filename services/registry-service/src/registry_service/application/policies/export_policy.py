# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Export job TTL policy — pure constant + helper.

Q8: export files expire 24 hours after the job completes.
The ARQ cron task purge_expired_exports runs hourly.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

EXPORT_TTL_HOURS: int = 24


def compute_expires_at(completed_at: datetime | None = None) -> datetime:
    """Return the expiry timestamp for an export file.

    Args:
        completed_at: When the export job completed. Defaults to now().

    Returns:
        completed_at + 24 hours in UTC.
    """
    base = completed_at or datetime.now(tz=UTC)
    return base + timedelta(hours=EXPORT_TTL_HOURS)
