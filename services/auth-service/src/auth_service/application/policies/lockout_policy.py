# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Lockout policy thresholds (ADR-0003 §12, closes F-005).

Per-email lockout rules:
- 5 failures in 15 minutes → 15-minute lockout (short lockout)
- 10 failures in 60 minutes → 24-hour lockout + admin alert (long lockout)

Failures COMBINE across factors: failed_password + failed_totp both count.
Successful login resets the counter logically (next query anchors from last success).
"""

from __future__ import annotations

# Threshold 1: short lockout
SHORT_LOCKOUT_FAILURES = 5
SHORT_LOCKOUT_WINDOW_SECONDS = 15 * 60  # 15 minutes

# Threshold 2: long lockout + admin alert
LONG_LOCKOUT_FAILURES = 10
LONG_LOCKOUT_WINDOW_SECONDS = 60 * 60  # 60 minutes
LONG_LOCKOUT_DURATION_HOURS = 24


def is_short_locked(failure_count_15min: int) -> bool:
    """Return True if the 5/15-min lockout threshold is exceeded."""
    return failure_count_15min >= SHORT_LOCKOUT_FAILURES


def is_long_locked(failure_count_60min: int) -> bool:
    """Return True if the 10/60-min lockout threshold is exceeded."""
    return failure_count_60min >= LONG_LOCKOUT_FAILURES
