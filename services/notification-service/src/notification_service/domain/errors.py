# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Typed domain errors for notification-service."""

from __future__ import annotations

from lotsman_shared.errors import DomainError


class NotificationDomainError(DomainError):
    """Base for all notification-service domain errors."""


class DeliveryAttemptNotFoundError(NotificationDomainError):
    status_code = 404
    default_message = "Delivery attempt not found"


class TemplateNotFoundError(NotificationDomainError):
    status_code = 404
    default_message = "Message template not found"


class ProviderError(NotificationDomainError):
    status_code = 502
    default_message = "Notification provider returned an error"


class DuplicateDeliveryError(NotificationDomainError):
    status_code = 409
    default_message = "Duplicate delivery attempt detected"


# ---------------------------------------------------------------------------
# Channel management errors (ADR-0004 Phase 2)
# ---------------------------------------------------------------------------


class ChannelNotConfiguredError(NotificationDomainError):
    """Channel has no config_enc row in the database."""

    status_code = 404
    default_message = "Канал не настроен"


class ChannelValidationError(NotificationDomainError):
    """Submitted channel config fails field-level validation."""

    status_code = 422
    code = "CHANNEL_VALIDATION"
    default_message = "Ошибка валидации конфигурации канала"


class ChannelDecryptError(NotificationDomainError):
    """config_enc cannot be decrypted — wrong CHANNEL_ENC_KEY."""

    status_code = 503
    code = "CHANNEL_DECRYPT_ERROR"
    default_message = (
        "Не удалось расшифровать конфиг канала — "
        "обратитесь к super-admin (см. runbook §6.4)"
    )


class PendingInvitationsError(NotificationDomainError):
    """Cannot disable the only enabled channel while invitations are pending."""

    status_code = 409
    code = "PENDING_INVITES"
    default_message = (
        "Невозможно отключить — есть неподтверждённые приглашения через этот канал"
    )


class ChannelNotImplementedError(NotificationDomainError):
    """Test / send not yet implemented for this channel."""

    status_code = 501
    default_message = "Not implemented for this channel"
