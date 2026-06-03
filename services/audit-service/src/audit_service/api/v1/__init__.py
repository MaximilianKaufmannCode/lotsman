# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""audit-service API v1 router aggregator."""

from fastapi import APIRouter

from audit_service.api.v1.audit import router as audit_router

router = APIRouter()
router.include_router(audit_router)
