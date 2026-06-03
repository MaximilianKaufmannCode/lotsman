# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""system-control API v1 router aggregator."""

from fastapi import APIRouter

from system_control.api.v1.backup_ops import router as backup_ops_router
from system_control.api.v1.docker_ops import router as docker_ops_router
from system_control.api.v1.logs import router as logs_router
from system_control.api.v1.migrate_ops import router as migrate_ops_router
from system_control.api.v1.ps import router as ps_router

router = APIRouter(prefix="/v1")
router.include_router(docker_ops_router)
router.include_router(backup_ops_router)
router.include_router(migrate_ops_router)
router.include_router(logs_router)
router.include_router(ps_router)
