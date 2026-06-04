# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Settings for web-bff.

web-bff has no Postgres connection — it only talks to Redis (session store)
and to downstream services via HTTP (internal JWTs).
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    service_name: str = Field(default="web-bff")
    log_level: str = Field(default="info")

    # Security: permit the no-signature JWT fallback (dev only). Default False =
    # fail-closed everywhere; the RS256 public key is then mandatory (see validator
    # + deps.py). Deliberately NOT tied to LOTSMAN_ENV (which is mislabeled 'dev' in
    # some deployments) — an explicit opt-in cannot be triggered by misconfiguration.
    jwt_allow_unverified: bool = Field(
        default=False,
        description=(
            "Dev-only opt-in. When False (default), web-bff REQUIRES jwt_public_key_path "
            "and always verifies JWT signatures. Set True ONLY for local development."
        ),
    )

    # Redis for session keys
    redis_url: str = Field(default="redis://localhost:6379/0")

    # Per-service internal JWT keys (HS256, per ADR-0003 §10 / F-001, F-002).
    # BFF holds ALL FOUR; each backend holds only its own.
    # WARNING: each key MUST be unique per service. Reusing values defeats audience isolation.
    internal_jwt_key_auth: str = Field(
        ...,
        min_length=32,
        description="HS256 key for minting JWTs addressed to auth-service.",
    )
    internal_jwt_key_registry: str = Field(
        ...,
        min_length=32,
        description="HS256 key for minting JWTs addressed to registry-service.",
    )
    internal_jwt_key_notification: str = Field(
        ...,
        min_length=32,
        description="HS256 key for minting JWTs addressed to notification-service.",
    )
    internal_jwt_key_audit: str = Field(
        ...,
        min_length=32,
        description="HS256 key for minting JWTs addressed to audit-service.",
    )
    internal_jwt_key_system_control: str | None = Field(
        default=None,
        min_length=32,
        description=(
            "HS256 key for minting JWTs addressed to system-control sidecar. "
            "Optional: if absent, all /api/v1/system/sidecar-backed endpoints return 503. "
            'Generate: python -c "import secrets; print(secrets.token_hex(32))"'
        ),
    )

    # Downstream service base URLs (injected by compose)
    auth_svc_url: str = Field(default="http://auth-svc:8000")
    registry_svc_url: str = Field(default="http://registry-svc:8000")
    notification_svc_url: str = Field(default="http://notification-svc:8000")
    audit_svc_url: str = Field(default="http://audit-svc:8000")
    system_control_url: str = Field(
        default="http://system-control:8000",
        description="Base URL for the system-control sidecar (internal network only).",
    )

    # HTTP client settings
    http_timeout_seconds: float = Field(default=10.0)

    # Internal JWT TTL for downstream calls
    internal_jwt_ttl_seconds: int = Field(default=60)

    # Refresh-cookie attributes — must match auth-service settings (ADR-0003 §9 amended 2026-05-12)
    refresh_token_ttl_seconds: int = Field(
        default=43200,
        ge=3600,
        le=2592000,
        description=(
            "Refresh cookie Max-Age in seconds. Must match AUTH_SERVICE REFRESH_TOKEN_TTL_SECONDS."
        ),
    )
    refresh_cookie_secure: bool = Field(
        default=True,
        description="Set Secure flag on refresh cookie. Set False ONLY for local HTTP dev.",
    )
    refresh_cookie_samesite: Literal["strict", "lax", "none"] = Field(
        default="strict",
        description="SameSite attribute. 'strict' for prod, 'lax' for dev cross-port.",
    )

    # RS256 public key path for verifying access JWTs from auth-service (ADR-0003 §7)
    jwt_public_key_path: str | None = Field(
        default=None,
        description=(
            "Path to the RS256 public key PEM. Required outside dev. "
            "When absent in dev ONLY, BFF decodes without signature verification."
        ),
    )

    @model_validator(mode="after")
    def _require_jwt_key_unless_dev_optin(self) -> Settings:
        """Fail-closed: refuse to start without an RS256 public key unless the
        explicit dev opt-in (jwt_allow_unverified=True) is set.

        Prevents the unverified-JWT fallback in deps.py from ever activating due
        to a missing/misconfigured key (which would be an authentication bypass).
        """
        if not self.jwt_allow_unverified and not self.jwt_public_key_path:
            raise ValueError(
                "jwt_public_key_path is required (fail-closed). Mount the RS256 "
                "public key, or set JWT_ALLOW_UNVERIFIED=true for local development."
            )
        return self


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()  # type: ignore[call-arg]
    return _settings
