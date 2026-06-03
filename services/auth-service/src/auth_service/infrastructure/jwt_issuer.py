# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""RS256 access JWT issuer (ADR-0003 §7).

Reads PEM private key from JWT_PRIVATE_KEY_PATH.
Includes kid header for rotation support.
Claims: iss, aud, sub, email, role, sid, jti, iat, nbf, exp.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

import jwt

_ISSUER = "lotsman-auth"
_AUDIENCE = "lotsman-spa"
_ALGORITHM = "RS256"
_DEFAULT_TTL_SECONDS = 900  # 15 minutes — kept as a fallback constant


class RS256JwtIssuer:
    """Concrete JwtIssuer using RS256 with kid-based rotation.

    Implements auth_service.application.ports.JwtIssuer.
    """

    def __init__(
        self,
        private_key_path: str | Path,
        *,
        kid: str = "v1",
        ttl_seconds: int = _DEFAULT_TTL_SECONDS,
    ) -> None:
        pem = Path(private_key_path).read_bytes()
        self._private_key = jwt.algorithms.RSAAlgorithm.from_jwk  # type: ignore[attr-defined]
        # Use raw PEM bytes directly
        self._pem = pem
        self._kid = kid
        self._ttl_seconds = ttl_seconds

    def issue(
        self,
        *,
        user_id: uuid.UUID,
        email: str,
        role: str,
        session_id: uuid.UUID,
    ) -> str:
        now = datetime.now(tz=UTC)
        iat = int(now.timestamp())
        exp = iat + self._ttl_seconds
        jti = str(uuid.uuid4())

        payload: dict[str, object] = {
            "iss": _ISSUER,
            "aud": _AUDIENCE,
            "sub": str(user_id),
            "email": email,
            "role": role,
            "sid": str(session_id),
            "iat": iat,
            "nbf": iat,
            "exp": exp,
            "jti": jti,
        }

        return jwt.encode(
            payload,
            self._pem,
            algorithm=_ALGORITHM,
            headers={"kid": self._kid},
        )
