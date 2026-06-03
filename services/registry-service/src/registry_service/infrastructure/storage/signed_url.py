# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""HMAC-SHA256 signed URL generation and verification.

Per spec §6 Security:
  signature = HMAC-SHA256(key, f"{resource_id}:{expires_at_unix}")
  URL       = /internal/files/{storage_path}?expires={unix}&sig={hex_sig}

The nginx or file-serving layer validates the signature and TTL before
streaming bytes. The HMAC key is injected via Docker secrets (env var
LOTSMAN_SIGNED_URL_KEY). Never hardcode the key.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import quote, urlencode

_SIGNED_URL_KEY: str | None = None
_MIN_KEY_LENGTH = 32  # F-019: HMAC key must be ≥256 bits per RFC 7518


def _get_key() -> bytes:
    """Return the HMAC key bytes. Fails fast if the key is missing or too short.

    Closes F-019 (CWE-798) — no insecure-default fallback. Production and dev
    must both supply LOTSMAN_SIGNED_URL_KEY of at least 32 chars (generate via
    `python -c "import secrets; print(secrets.token_hex(32))"`).
    """
    global _SIGNED_URL_KEY
    if _SIGNED_URL_KEY is None:
        key = os.environ.get("LOTSMAN_SIGNED_URL_KEY", "")
        if not key:
            raise RuntimeError(
                "LOTSMAN_SIGNED_URL_KEY env var is required and must not be empty. "
                "Generate one with: "
                'python -c "import secrets; print(secrets.token_hex(32))"'
            )
        if len(key) < _MIN_KEY_LENGTH:
            raise RuntimeError(
                f"LOTSMAN_SIGNED_URL_KEY must be at least {_MIN_KEY_LENGTH} chars; "
                f"got {len(key)}. This length is required for HMAC-SHA256 security "
                "(RFC 7518 §3.2)."
            )
        _SIGNED_URL_KEY = key
    return _SIGNED_URL_KEY.encode()


def make_signed_url(
    *,
    storage_path: str,
    resource_id: uuid.UUID,
    ttl_seconds: int,
    resource_type: str = "attachment",
) -> str:
    """Generate a short-lived signed URL for serving a stored file.

    The URL format is:
      /internal/files/<storage_path>?expires=<unix_ts>&sig=<hmac_hex>&type=<type>

    Args:
        storage_path: Relative path under the volume root (stored in DB).
        resource_id:  The attachment or export job UUID.
        ttl_seconds:  Expiry in seconds from now.
        resource_type: "attachment" or "export".

    Returns:
        A URL string. Callers should redirect (302) to this URL.
    """
    expires_at = datetime.now(tz=UTC) + timedelta(seconds=ttl_seconds)
    expires_unix = int(expires_at.timestamp())

    message = f"{resource_id}:{expires_unix}".encode()
    sig = hmac.new(_get_key(), message, hashlib.sha256).hexdigest()

    params = urlencode(
        {
            "expires": expires_unix,
            "sig": sig,
            "type": resource_type,
        }
    )
    encoded_path = quote(storage_path, safe="/")
    return f"/internal/files/{encoded_path}?{params}"


def verify_signed_url(
    *,
    resource_id: str,
    expires_unix: int,
    sig: str,
) -> bool:
    """Verify a signed URL signature and expiry.

    Called by the nginx auth subrequest or file-serving middleware.

    Returns:
        True if signature is valid and URL has not expired.
    """
    now_unix = int(datetime.now(tz=UTC).timestamp())
    if now_unix > expires_unix:
        return False

    message = f"{resource_id}:{expires_unix}".encode()
    expected = hmac.new(_get_key(), message, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig)
