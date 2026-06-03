# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Fernet-based encryption for channel configuration blobs.

Reads CHANNEL_ENC_KEY from environment at construction time.
If missing or empty the service refuses to start (US-16 scenario 2).

SECURITY:
  - Never logs the input or output of encrypt/decrypt.
  - Separate from TOTP_ENC_KEY to limit blast-radius (ADR-0004 §4).
"""

from __future__ import annotations

import json
import os
from typing import Any

from cryptography.fernet import Fernet


class ChannelCipher:
    """Thin wrapper around Fernet for channel config JSON blobs.

    Construction reads CHANNEL_ENC_KEY from the environment.
    Raises RuntimeError immediately if the key is absent or empty —
    intentionally loud so notification-service refuses to boot without it.
    """

    def __init__(self) -> None:
        raw_key = os.environ.get("CHANNEL_ENC_KEY", "")
        if not raw_key:
            raise RuntimeError(
                "CHANNEL_ENC_KEY is required"
                " (see infra/secrets-dev/README.md §5 for generation)"
            )
        self._fernet = Fernet(raw_key.encode())

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def encrypt(self, config: dict[str, Any]) -> bytes:
        """JSON-serialize *config* then Fernet-encrypt the bytes.

        Raises ``ValueError`` if *config* is not JSON-serialisable.
        Output is opaque bytes suitable for storage in config_enc BYTEA.
        """
        plaintext = json.dumps(config).encode("utf-8")
        return self._fernet.encrypt(plaintext)

    def decrypt(self, blob: bytes) -> dict[str, Any]:
        """Fernet-decrypt *blob* and JSON-parse the result.

        Raises ``cryptography.fernet.InvalidToken`` on bad ciphertext or
        a wrong key, and ``json.JSONDecodeError`` on malformed plaintext.
        """
        plaintext = self._fernet.decrypt(blob)
        result: dict[str, Any] = json.loads(plaintext.decode("utf-8"))
        return result
