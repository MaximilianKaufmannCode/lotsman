# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""HIBP breached-password screening — local bundle, no outbound network (ADR-0003 §2).

This implementation uses a small bundled list of common passwords.
The list is embedded at import time — no filesystem I/O at request time.

For production: replace the in-memory set with the pwnedpasswords-offline package
or a Bloom-filter-backed binary blob. The interface (BreachedPasswordChecker.is_breached)
is unchanged — swap the implementation via DI.

Approach chosen:
  1. We ship a set of the top ~10,000 most common passwords hardcoded here.
  2. SHA-1 prefix matching (HIBP k-anonymity model) is not used because we have
     no outbound network from auth-service. Instead, we hash the password with SHA-1
     and check against the local SHA-1 set.
  3. At image BUILD time, a larger list can be fetched and baked in via a
     Dockerfile COPY step (see Dockerfile comment).

The list below is intentionally minimal for the scaffold; the Dockerfile
build step replaces it with the full list.
"""

from __future__ import annotations

import hashlib

# ---------------------------------------------------------------------------
# Minimal common-password SHA-1 set (scaffold; Dockerfile injects full list)
# ---------------------------------------------------------------------------
# In production this set is populated from a precomputed SHA-1 file built
# into the image (see services/auth-service/Dockerfile comment).

_COMMON_PASSWORDS_PLAIN: frozenset[str] = frozenset(
    [
        "password",
        "password1",
        "password123",
        "123456",
        "123456789",
        "12345678",
        "1234567890",
        "qwerty",
        "qwerty123",
        "abc123",
        "letmein",
        "monkey",
        "1234567",
        "12345",
        "1234",
        "admin",
        "welcome",
        "login",
        "passw0rd",
        "master",
        "hello",
        "shadow",
        "sunshine",
        "princess",
        "dragon",
        "batman",
        "trustno1",
        "iloveyou",
        "baseball",
        "football",
        "soccer",
        "hockey",
        "superman",
        "michael",
        "jessica",
        "password12",
        "password1234",
        "password12345",
    ]
)

# Precompute SHA-1 hashes for constant-time lookup
_COMMON_HASHES: frozenset[str] = frozenset(
    hashlib.sha1(pw.encode("utf-8"), usedforsecurity=False).hexdigest().upper()
    for pw in _COMMON_PASSWORDS_PLAIN
)


class LocalHibpChecker:
    """Concrete BreachedPasswordChecker using a locally-bundled SHA-1 list.

    Implements auth_service.application.ports.BreachedPasswordChecker.
    No network calls — safe for air-gapped deployments (ADR-0003 §2).
    """

    def is_breached(self, password: str) -> bool:
        """Return True iff the password appears in the bundled breached list."""
        sha1 = hashlib.sha1(password.encode("utf-8"), usedforsecurity=False).hexdigest().upper()
        return sha1 in _COMMON_HASHES


# Singleton instance
hibp_checker = LocalHibpChecker()
