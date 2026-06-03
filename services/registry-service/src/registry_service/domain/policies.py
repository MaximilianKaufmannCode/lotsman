# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Domain policies — pure functions, no I/O, no infrastructure imports.

compute_status: derives DocumentStatus from expiry_date + deleted_at + today.
is_urgent: returns True when a document is within the pre-notice window.
"""

from __future__ import annotations

from datetime import date

from registry_service.domain.value_objects import DocumentStatus

_SOON_THRESHOLD_DAYS = 30


def compute_status(
    expiry_date: date | None,
    deleted_at: object | None,
    today: date,
) -> DocumentStatus:
    """Compute the urgency status badge for a document (US-21).

    Rules (calendar days, Q4):
      1. archived   if deleted_at IS NOT NULL
      2. ok         if expiry_date IS NULL
      3. overdue    if expiry_date < today
      4. soon       if (expiry_date - today).days <= 30  (includes in-day)
      5. ok         otherwise

    Args:
        expiry_date: The document's expiry date, or None.
        deleted_at:  Any truthy value means archived (mirrors the ORM column).
        today:       The reference date (injected to keep this function pure/testable).

    Returns:
        A :class:`DocumentStatus` enum value.
    """
    if deleted_at is not None:
        return DocumentStatus.archived

    if expiry_date is None:
        return DocumentStatus.ok

    delta = (expiry_date - today).days
    if delta < 0:
        return DocumentStatus.overdue
    if delta <= _SOON_THRESHOLD_DAYS:
        return DocumentStatus.soon
    return DocumentStatus.ok


def is_urgent(
    expiry_date: date | None,
    today: date,
    type_pre_notice_days: list[int],
) -> bool:
    """Return True if today falls within any of the type's pre-notice windows.

    Used by notification-service to decide whether to schedule a pre-notice
    notification. The domain has no knowledge of delivery channels.
    """
    if expiry_date is None:
        return False
    delta = (expiry_date - today).days
    return any(delta == n for n in type_pre_notice_days)
