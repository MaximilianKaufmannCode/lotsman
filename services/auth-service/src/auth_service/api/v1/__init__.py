# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""auth-service API v1 router aggregator."""

from fastapi import APIRouter

from .admin import router as admin_router
from .auth import router as auth_router
from .internal import router as internal_router
from .key_rotations import router as key_rotations_router
from .me import router as me_router

router = APIRouter()
router.include_router(auth_router)  # prefix="/auth" declared in auth.py
router.include_router(me_router)  # prefix="/auth" declared in me.py (GET/PATCH /me)
router.include_router(admin_router)  # prefix="/admin" declared in admin.py
router.include_router(key_rotations_router)  # prefix="/system" declared in key_rotations.py
router.include_router(internal_router)  # prefix="/internal" declared in internal.py
