# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""CheckLockout use case — US-11, US-12 helper.

Reads the failure count for an email from the login_attempts repository and
returns whether the email is currently locked (short or long lockout).

Called BEFORE argon2 verification to avoid timing attacks on locked accounts.
"""

from __future__ import annotations

from dataclasses import dataclass

from auth_service.application.policies.lockout_policy import (
    SHORT_LOCKOUT_WINDOW_SECONDS,
    is_long_locked,
    is_short_locked,
)
from auth_service.application.ports import LoginAttemptRepository


@dataclass(slots=True)
class LockoutStatus:
    is_locked: bool
    is_long_lockout: bool  # True → 24h lockout threshold breached


@dataclass(slots=True)
class CheckLockout:
    """Check whether an email address is currently locked out.

    Returns a LockoutStatus. Callers raise InvalidCredentialsError on lock.
    """

    attempts_repo: LoginAttemptRepository

    async def execute(self, *, email: str) -> LockoutStatus:
        """
        Returns LockoutStatus(is_locked=True) if the email is locked.
        The long lockout is checked first (stricter threshold).
        """
        email_lower = email.strip().lower()

        # Check 10/60-min first (more severe)
        count_60min = await self.attempts_repo.count_failures_since(
            email_lower,
            SHORT_LOCKOUT_WINDOW_SECONDS * 4,  # 60 min
        )
        if is_long_locked(count_60min):
            return LockoutStatus(is_locked=True, is_long_lockout=True)

        # Check 5/15-min
        count_15min = await self.attempts_repo.count_failures_since(
            email_lower, SHORT_LOCKOUT_WINDOW_SECONDS
        )
        if is_short_locked(count_15min):
            return LockoutStatus(is_locked=True, is_long_lockout=False)

        return LockoutStatus(is_locked=False, is_long_lockout=False)
