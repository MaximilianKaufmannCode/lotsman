# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Async HTTP client for audit-service.

Stub — real endpoint methods added in the audit-history feature.
"""

from __future__ import annotations

from web_bff.infrastructure.clients.base import DownstreamClient


class AuditClient(DownstreamClient):
    """Client for audit-service endpoints.

    Audience: 'audit-service'

    Methods added during the audit-history feature:
        - list_events(actor_id, role, request_id, entity_type, entity_id, limit) -> list[EventView]
    """

    AUDIENCE = "audit-service"
