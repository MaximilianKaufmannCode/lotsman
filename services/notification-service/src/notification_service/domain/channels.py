# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Channel value objects and per-channel config models.

Validation lives here so use cases can validate before encrypting.
Secrets are identified in SECRET_FIELDS and redacted via redact_secrets()
before inclusion in audit payloads (US-14).

Domain layer — no infrastructure imports.
"""

from __future__ import annotations

import re
import secrets
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

# RFC 5322-compatible email regex (permissive: accepts .local / .corp TLDs).
# We do NOT use pydantic.EmailStr because email-validator rejects private-use
# domains (e.g. example.com) which are common in on-premise deployments.
_EMAIL_RE = re.compile(
    r"^[a-zA-Z0-9!#$%&'*+/=?^_`{|}~.-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
)

# ---------------------------------------------------------------------------
# Type alias
# ---------------------------------------------------------------------------

Channel = Literal["email", "telegram", "dion", "exchange_calendar", "ics_feed"]

# ---------------------------------------------------------------------------
# Per-channel config Pydantic models (validated at boundary)
# ---------------------------------------------------------------------------


class EmailConfig(BaseModel):
    """SMTP channel configuration."""

    smtp_host: str
    smtp_port: int = Field(..., ge=1, le=65535, description="Port must be in range 1..65535")
    smtp_user: str
    smtp_password: str
    from_address: str
    from_name: str

    @field_validator("from_address")
    @classmethod
    def validate_from_address(cls, v: str) -> str:
        if not _EMAIL_RE.match(v):
            raise ValueError(
                "from_address must be a valid email address (RFC 5322 format)"
            )
        return v


_BOT_TOKEN_RE = re.compile(r"^\d+:[A-Za-z0-9_-]{30,}$")


class TelegramConfig(BaseModel):
    """Telegram Bot API channel configuration."""

    bot_token: str
    default_parse_mode: Literal["HTML", "MarkdownV2"]

    @field_validator("bot_token")
    @classmethod
    def validate_bot_token(cls, v: str) -> str:
        if not _BOT_TOKEN_RE.match(v):
            raise ValueError("bot_token must match Telegram format <int>:<base64>")
        return v


class DionConfig(BaseModel):
    """Dion API channel configuration."""

    api_base: str
    api_token: str
    workspace_id: str | None = None

    @field_validator("api_base")
    @classmethod
    def validate_api_base(cls, v: str) -> str:
        if not v.startswith("https://"):
            raise ValueError("api_base must use https:// scheme")
        return v


class ExchangeCalendarConfig(BaseModel):
    """Exchange Calendar (EWS) channel configuration.

    Stored encrypted in provider_credentials.config_enc for channel='exchange_calendar'.
    See ADR-0005 §2.
    """

    ews_url: str
    service_account_login: str
    service_account_password: str
    target_mailbox: str
    auth_type: Literal["NTLM", "Basic"] = "NTLM"
    verify_ssl: bool = True
    default_notice_days: int = Field(default=14, ge=1, le=90)

    @field_validator("ews_url")
    @classmethod
    def validate_ews_url(cls, v: str) -> str:
        if not v.startswith("https://"):
            raise ValueError("ews_url must use https:// scheme")
        return v

    @field_validator("target_mailbox")
    @classmethod
    def validate_target_mailbox(cls, v: str) -> str:
        # Same permissive regex as elsewhere — accepts .local / .corp TLDs.
        if not _EMAIL_RE.match(v):
            raise ValueError(
                "target_mailbox must be a valid email address (RFC 5322 format)"
            )
        return v

    @field_validator("service_account_login")
    @classmethod
    def validate_service_account_login(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("service_account_login must not be blank")
        return v


_MIN_ICS_TOKEN_BYTES = 32


class IcsFeedConfig(BaseModel):
    """ICS feed channel configuration.

    token is the shared secret embedded in the feed URL.  If the caller does
    not supply one (empty string or None) we auto-generate a secure token.
    Min length of 32 characters enforced to resist brute-force enumeration.

    See ADR-0005 §10.
    """

    token: str = Field(default="")
    cache_ttl_seconds: int = Field(default=300, ge=60, le=86400)

    @field_validator("token", mode="before")
    @classmethod
    def ensure_token(cls, v: str | None) -> str:
        if not v:
            return secrets.token_urlsafe(_MIN_ICS_TOKEN_BYTES)
        if len(v) < _MIN_ICS_TOKEN_BYTES:
            raise ValueError(
                f"token must be at least {_MIN_ICS_TOKEN_BYTES} characters (URL-safe)"
            )
        return v


# ---------------------------------------------------------------------------
# Secret fields per channel (used for audit-payload redaction)
# ---------------------------------------------------------------------------

SECRET_FIELDS: dict[Channel, set[str]] = {
    "email": {"smtp_password"},
    "telegram": {"bot_token"},
    "dion": {"api_token"},
    "exchange_calendar": {"service_account_password"},
    "ics_feed": {"token"},
}


def redact_secrets(channel: Channel, config: dict[str, Any]) -> dict[str, Any]:
    """Return a shallow copy of *config* with secret fields replaced by '[REDACTED]'.

    Safe to include in audit events (US-14).
    """
    secret_fields = SECRET_FIELDS.get(channel, set())
    return {k: "[REDACTED]" if k in secret_fields else v for k, v in config.items()}
