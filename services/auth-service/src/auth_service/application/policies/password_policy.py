# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Password policy enforcement (NIST 800-63B, ADR-0003 §2).

Minimum 12 chars, maximum 1024 chars (DoS guard for argon2 input).
No composition rules, no rotation. Paste allowed.
HIBP breached-password screening runs server-side only.

Closes F-006.
"""

from __future__ import annotations

from auth_service.application.ports import BreachedPasswordChecker
from auth_service.domain.errors import PasswordPolicyViolationError, WeakPasswordError

_MIN_LENGTH = 12
_MAX_LENGTH = 1024


def validate_password(
    password: str,
    *,
    hibp_checker: BreachedPasswordChecker,
) -> None:
    """Validate password against NIST 800-63B policy.

    Raises:
        PasswordPolicyViolationError: if length requirements are violated.
        WeakPasswordError: if the password appears in the HIBP breached list.
    """
    if len(password) < _MIN_LENGTH:
        raise PasswordPolicyViolationError(
            f"Password must be at least {_MIN_LENGTH} characters long"
        )
    if len(password) > _MAX_LENGTH:
        raise PasswordPolicyViolationError(
            f"Password must be at most {_MAX_LENGTH} characters long"
        )
    if hibp_checker.is_breached(password):
        raise WeakPasswordError()
