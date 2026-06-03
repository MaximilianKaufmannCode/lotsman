# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""registry-service API v1 router aggregator."""

from fastapi import APIRouter

from registry_service.api.v1.admin_import import router as admin_import_router
from registry_service.api.v1.assets import router as assets_router
from registry_service.api.v1.attachments import router as attachments_router
from registry_service.api.v1.document_types import router as document_types_router
from registry_service.api.v1.documents import router as documents_router
from registry_service.api.v1.exports import router as exports_router
from registry_service.api.v1.history import router as history_router
from registry_service.api.v1.imports import router as imports_router
from registry_service.api.v1.preferences import router as preferences_router

router = APIRouter()
router.include_router(assets_router)
router.include_router(document_types_router)
router.include_router(documents_router)
router.include_router(attachments_router)
router.include_router(exports_router)
router.include_router(history_router)
router.include_router(imports_router)
router.include_router(admin_import_router)
router.include_router(preferences_router)
