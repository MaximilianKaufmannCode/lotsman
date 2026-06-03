# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""PyOTP-based TOTP service (ADR-0003 §3).

RFC 6238: SHA-1, 6-digit, 30-second period.
Secret: 160-bit from secrets.token_bytes(20) encoded as base32.
Verification: valid_window=1 (accept ±1 period, 90-second tolerance).
"""

from __future__ import annotations

import math
import time

import pyotp


class PyotpTotpService:
    """Concrete TotpService implementation using pyotp.

    Implements auth_service.application.ports.TotpService.
    """

    def generate_secret_b32(self) -> str:
        """Generate a 160-bit random TOTP secret encoded as base32."""
        return pyotp.random_base32()

    def make_otpauth_url(self, *, email: str, secret_b32: str, issuer: str) -> str:
        """Build the otpauth:// URL for QR rendering."""
        totp = pyotp.TOTP(secret_b32)
        return totp.provisioning_uri(name=email, issuer_name=issuer)

    def verify(self, secret_b32: str, code: str, *, valid_window: int = 1) -> bool:
        """Verify a TOTP code. Returns True iff code matches within ±valid_window steps."""
        totp = pyotp.TOTP(secret_b32)
        return totp.verify(code, valid_window=valid_window)

    def current_period_index(self) -> int:
        """Return floor(unix_time / 30) for anti-replay period tracking."""
        return math.floor(time.time() / 30)


# Singleton instance
totp_service = PyotpTotpService()
