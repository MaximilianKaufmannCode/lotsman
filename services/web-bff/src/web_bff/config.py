# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Settings for web-bff.

web-bff has no Postgres connection — it only talks to Redis (session store)
and to downstream services via HTTP (internal JWTs).
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field
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
            "Path to the RS256 public key PEM. Required in production. "
            "When absent, BFF decodes without signature verification (dev only)."
        ),
    )


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()  # type: ignore[call-arg]
    return _settings
