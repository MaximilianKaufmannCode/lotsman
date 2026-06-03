# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""argon2id PasswordHasher implementation (ADR-0003 §1).

Parameters per ADR-0003 §1:
- time_cost=3
- memory_cost=65536 (64 MiB)
- parallelism=4
- hash_len=32
- salt_len=16

The module-level singleton `hasher` is the single instance used throughout auth-service.
"""

from __future__ import annotations

from argon2 import PasswordHasher as _ArgonHasher
from argon2.exceptions import VerificationError, VerifyMismatchError

# Module-level singleton per ADR-0003 §1
_PH = _ArgonHasher(
    time_cost=3,
    memory_cost=65536,  # 64 MiB
    parallelism=4,
    hash_len=32,
    salt_len=16,
)


class Argon2PasswordHasher:
    """Concrete argon2id PasswordHasher for auth-service.

    Implements auth_service.application.ports.PasswordHasher.
    """

    def hash(self, password: str) -> str:
        """Hash a plaintext password. Returns an argon2id PHC string."""
        return _PH.hash(password)

    def verify(self, hash: str, password: str) -> bool:
        """Return True iff the password matches the stored hash.

        Short-circuit rejects:
        - hash == 'SYSTEM': always False (system actor sentinel)
        - Empty or non-PHC hash: always False
        """
        if hash == "SYSTEM" or not hash.startswith("$argon2"):
            return False
        try:
            return _PH.verify(hash, password)
        except (VerifyMismatchError, VerificationError):
            return False

    def check_needs_rehash(self, hash: str) -> bool:
        """Return True if the stored hash was made with outdated parameters."""
        if hash == "SYSTEM" or not hash.startswith("$argon2"):
            return False
        return _PH.check_needs_rehash(hash)


# Singleton instance for direct import (testing, DI wiring)
hasher = Argon2PasswordHasher()
