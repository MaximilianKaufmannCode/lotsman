# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Fernet-based TOTP secret encryption (ADR-0003 §6).

AES-128-CBC + HMAC-SHA256 via cryptography.fernet.Fernet.
Master key from TOTP_ENC_KEY env var (Fernet key format, 44 URL-safe base64 chars).

NEVER log the decrypted secret. Encryption/decryption is synchronous
(Fernet is CPU-bound and fast; ~1ms — no async wrapper needed).
"""

from __future__ import annotations

from cryptography.fernet import Fernet


class FernetEncryptionService:
    """Concrete EncryptionService for TOTP secrets.

    Implements auth_service.application.ports.EncryptionService.
    """

    def __init__(self, key: str | bytes) -> None:
        """
        Args:
            key: Fernet key (URL-safe base64, 44 chars). Loaded from settings.totp_enc_key.
        """
        if isinstance(key, str):
            key = key.encode()
        self._fernet = Fernet(key)

    def encrypt(self, plaintext: str) -> bytes:
        """Encrypt a TOTP secret_b32 string. Returns Fernet token bytes."""
        return self._fernet.encrypt(plaintext.encode("ascii"))

    def decrypt(self, ciphertext: bytes) -> str:
        """Decrypt Fernet token bytes to a TOTP secret_b32 string.

        Never log the return value.
        """
        return self._fernet.decrypt(ciphertext).decode("ascii")
