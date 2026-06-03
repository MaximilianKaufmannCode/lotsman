# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Settings for auth-service, loaded from environment via Pydantic BaseSettings."""

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

    # Service identity
    service_name: str = Field(default="auth-service")
    log_level: str = Field(default="info")

    # Postgres — injected via DATABASE_URL in compose (overrides AUTH_DATABASE_URL alias)
    database_url: str = Field(
        ...,
        description="Async SQLAlchemy DSN, e.g. postgresql+asyncpg://auth_app:pw@postgres/lotsman",
    )

    # Redis
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        description="Redis DSN used by ARQ and session store.",
    )

    # Per-service internal JWT key (HS256, per ADR-0003 §10 / F-001, F-002)
    # auth-service holds ONLY its own key; BFF holds all four.
    internal_jwt_key_auth: str = Field(
        ...,
        min_length=32,
        description=(
            "HS256 key for verifying internal JWTs addressed to auth-service. "
            'Generate: python -c "import secrets; print(secrets.token_hex(32))"'
        ),
    )

    # RS256 access-JWT key paths (ADR-0003 §7)
    jwt_private_key_path: str = Field(
        default="infra/secrets-dev/jwt_private_key.pem",
        description="Path to the RS256 private key PEM for signing access JWTs.",
    )
    jwt_public_key_path: str = Field(
        default="infra/secrets-dev/jwt_public_key.pem",
        description="Path to the RS256 public key PEM.",
    )
    jwt_current_kid: str = Field(default="v1", description="Key ID for the current signing key.")

    # TOTP secret encryption key (Fernet, ADR-0003 §6)
    totp_enc_key: str = Field(
        ...,
        description=(
            "Fernet master key for TOTP secret encryption. "
            'Generate: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
        ),
    )

    # Token TTL configuration (ADR-0003 §7, §8; amended 2026-05-12)
    access_token_ttl_seconds: int = Field(
        default=900,
        ge=60,
        le=3600,
        description="Access JWT TTL in seconds. Default 15 min per ADR-0003 §7.",
    )
    refresh_token_ttl_seconds: int = Field(
        default=43200,
        ge=3600,
        le=2592000,
        description="Refresh cookie TTL in seconds. Default 12h per user request 2026-05-12.",
    )

    # Refresh-cookie security attributes — environment-tunable (ADR-0003 §9 amended 2026-05-12)
    refresh_cookie_secure: bool = Field(
        default=True,
        description="Set Secure flag on refresh cookie. Set False ONLY for local HTTP dev.",
    )
    refresh_cookie_samesite: Literal["strict", "lax", "none"] = Field(
        default="strict",
        description=(
            "SameSite attribute for refresh cookie. "
            "'strict' for prod, 'lax' for dev cross-port, "
            "'none' for cross-site (requires secure=true)."
        ),
    )

    # Outbox dispatcher polling interval in seconds
    outbox_poll_interval_seconds: float = Field(default=1.0)

    # notification-service URL and key (used by InviteUser to check enabled channels)
    notification_svc_url: str = Field(
        default="http://notification-svc:8000",
        description="Base URL for notification-service (internal).",
    )
    internal_jwt_key_notification: str = Field(
        default="",
        min_length=0,
        description=(
            "HS256 key for minting internal JWTs addressed to notification-service. "
            "Required when invite_user uses delivery='auto'. "
            "Defaults to empty; ChannelDirectoryHttpAdapter will return [] gracefully."
        ),
    )


_settings: Settings | None = None


def get_settings() -> Settings:
    """Return the singleton settings instance (lazy-loaded)."""
    global _settings
    if _settings is None:
        _settings = Settings()  # type: ignore[call-arg]
    return _settings
