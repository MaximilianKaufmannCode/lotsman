# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""web-bff API v1 router aggregator."""

from fastapi import APIRouter

from web_bff.api.v1.admin import router as admin_router
from web_bff.api.v1.auth import router as auth_router
from web_bff.api.v1.registry import router as registry_router
from web_bff.api.v1.system import router as system_router
from web_bff.api.v1.system_health import router as system_health_router

router = APIRouter()
# system_router: super-admin panel endpoints — registered FIRST so its /health route
# (which requires super_admin) takes precedence over the legacy unauthenticated scaffold.
router.include_router(system_router, prefix="/system")
# system_health_router: legacy scaffold GET /system/health (no auth).
# Still registered so existing tests and infra probes continue to work.
# Routes from system_router shadow it for identical paths.
router.include_router(system_health_router, prefix="/system", tags=["system-health"])
router.include_router(auth_router)
router.include_router(admin_router)
router.include_router(registry_router)
