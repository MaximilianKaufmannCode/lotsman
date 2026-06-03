# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Bulk operation limits — pure constants + validator.

Q3: maximum 100 rows per bulk-archive request.
"""

from __future__ import annotations

from registry_service.domain.errors import BulkLimitExceededError

BULK_MAX_ROWS: int = 100


def validate_bulk_count(count: int) -> None:
    """Raise BulkLimitExceededError if count exceeds BULK_MAX_ROWS (100, per Q3)."""
    if count > BULK_MAX_ROWS:
        raise BulkLimitExceededError(f"Bulk operation limited to {BULK_MAX_ROWS} rows; got {count}")
