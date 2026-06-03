# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Value objects for auth-service domain.

Pure Python — no framework imports. Only stdlib + pydantic allowed here.
"""

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel, field_validator


class Role(StrEnum):
    """RBAC roles for Лоцман users."""

    ADMIN = "admin"
    EDITOR = "editor"
    VIEWER = "viewer"


class TicketScope(StrEnum):
    """Scope discriminator for pending-login tickets (ADR-0008 D1 / MF-1).

    Both enrollment and TOTP-login tickets live in the same Redis key family
    (totp:login:pending:<ticket>).  The scope field in the JSON value is the
    *only* enforced discriminator preventing cross-scope ticket replay.

    ENROLL — issued by start_login for users without TOTP (enrollment lane).
    LOGIN  — issued by start_login for TOTP-enrolled users (verify_totp lane).
    """

    ENROLL = "enroll"
    LOGIN = "login"


class LoginOutcome(StrEnum):
    """Possible outcomes recorded in auth.login_attempts."""

    SUCCESS = "success"
    FAILED_PASSWORD = "failed_password"
    FAILED_TOTP = "failed_totp"
    LOCKED = "locked"
    DEACTIVATED = "deactivated"
    NO_USER = "no_user"


class Email(BaseModel):
    """Validated, normalised (lowercased) email address value object.

    Only ASCII-domain emails are accepted in v1 (per US-17 acceptance criteria).
    """

    model_config = {"frozen": True}

    value: str

    @field_validator("value", mode="before")
    @classmethod
    def normalise_and_validate(cls, v: object) -> str:
        if not isinstance(v, str):
            raise ValueError("Email must be a string")
        normalised = v.strip().lower()
        # Basic structural check: local@domain.tld, ASCII domain only
        if not re.match(
            r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$",
            normalised,
        ):
            raise ValueError(f"Invalid email address: {v!r}")
        # Reject non-ASCII domain parts
        domain = normalised.split("@", 1)[1]
        if not domain.isascii():
            raise ValueError("Non-ASCII domain email addresses are not accepted in v1")
        return normalised

    def __str__(self) -> str:
        return self.value


class BackupCodeFormat:
    """4-4 hex backup code helper.

    Format: ``XXXX-XXXX`` where X is an uppercase hex digit.
    Total of 8 hex digits = 32 bits of entropy, suitable for hand-reading.
    """

    PATTERN = re.compile(r"^[0-9A-F]{4}-[0-9A-F]{4}$")

    @staticmethod
    def generate(secrets_token_bytes: bytes) -> str:
        """Convert 4 random bytes into a formatted backup code."""
        hex_str = secrets_token_bytes.hex().upper()
        return f"{hex_str[:4]}-{hex_str[4:8]}"

    @staticmethod
    def is_valid(code: str) -> bool:
        """Return True iff the code matches the expected 4-4 hex format."""
        return bool(BackupCodeFormat.PATTERN.match(code.upper()))

    @staticmethod
    def normalise(code: str) -> str:
        """Normalise a user-supplied backup code to uppercase."""
        return code.strip().upper()
