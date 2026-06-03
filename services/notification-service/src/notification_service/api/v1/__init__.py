# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""notification-service API v1 router aggregator."""

from fastapi import APIRouter

from notification_service.api.v1.admin_calendar_subscriptions import (
    router as admin_calendar_subscriptions_router,
)
from notification_service.api.v1.admin_channels import router as admin_channels_router
from notification_service.api.v1.admin_notifications_history import (
    router as admin_notifications_history_router,
)
from notification_service.api.v1.calendar_feed import router as calendar_feed_router
from notification_service.api.v1.internal_email import router as internal_email_router
from notification_service.api.v1.me_notifications import router as me_notifications_router

router = APIRouter()
router.include_router(admin_channels_router)
router.include_router(admin_calendar_subscriptions_router)
router.include_router(admin_notifications_history_router)
router.include_router(calendar_feed_router)
router.include_router(internal_email_router)
router.include_router(me_notifications_router)
