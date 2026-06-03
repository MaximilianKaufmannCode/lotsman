# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Locust load-test scenarios for the Лоцман service.

Includes two user classes:

AuthUser — auth flow (50 VU):
  1. POST /api/v1/auth/login (email + password) → get totp session ticket
  2. POST /api/v1/auth/totp/verify (TOTP code) → get access JWT + refresh cookie
  3. GET /api/v1/auth/sessions (list own sessions) → 200
  4. POST /api/v1/auth/logout → 204

RegistryUser — registry CRUD (50 VU, US-2 through US-24):
  Task weights (per spec): 70% list/search, 20% patch, 5% create, 5% bulk
  - list_documents (weight 70)
  - patch_document (weight 20)
  - create_document (weight 5)
  - bulk_archive_documents (weight 5)

Target (from requirements §6 / ADR-0003):
  - Median response time < 300ms
  - p99 < 2000ms
  - 0% error rate at 50 concurrent users

Run auth-only:
  uv run locust -f tests/load/locustfile.py --host http://localhost:8000 \\
    --users 50 --spawn-rate 5 --run-time 60s --headless --class-picker

Run registry load shape:
  uv run locust -f tests/load/locustfile.py --host http://localhost:8000 \\
    --users 50 --spawn-rate 5 --run-time 120s --headless \\
    -u 50 -r 5 --class-picker

Environment variables:
  LOAD_TEST_EMAIL         — test user email (default: loadtest@example.com)
  LOAD_TEST_PASSWORD      — test user password (must be >= 12 chars)
  LOAD_TEST_TOTP_CODE     — valid 6-digit TOTP code (or '000000' if mock mode)
  LOAD_TEST_ASSET_ID      — a seeded asset UUID for create/patch tasks
  LOAD_TEST_TYPE_CODE     — document type code for create tasks (default: contract)
"""

from __future__ import annotations

import os
import json
import uuid
import random
import string

from locust import HttpUser, between, task, events

_EMAIL = os.environ.get("LOAD_TEST_EMAIL", "loadtest@example.com")
_PASSWORD = os.environ.get("LOAD_TEST_PASSWORD", "LoadTest#Secure99")
_TOTP_CODE = os.environ.get("LOAD_TEST_TOTP_CODE", "000000")
_ASSET_ID = os.environ.get("LOAD_TEST_ASSET_ID", "")
_TYPE_CODE = os.environ.get("LOAD_TEST_TYPE_CODE", "contract")


class AuthUser(HttpUser):
    """Simulates a single user performing the full auth cycle."""

    wait_time = between(0.5, 2.0)  # think time between tasks

    def on_start(self) -> None:
        """Called once per user at spawn time — perform login to get initial tokens."""
        self.access_token: str | None = None
        self.refresh_cookie: str | None = None
        self._do_login()

    def _do_login(self) -> None:
        """Phase 1: password → Phase 2: TOTP."""
        # Phase 1: password
        resp = self.client.post(
            "/api/v1/auth/login",
            json={"email": _EMAIL, "password": _PASSWORD},
            name="/auth/login [phase1]",
        )
        if resp.status_code != 200:
            return

        body = resp.json()
        ticket = body.get("session_ticket") or body.get("ticket_id")
        if not ticket:
            return

        # Phase 2: TOTP
        resp2 = self.client.post(
            "/api/v1/auth/totp/verify",
            json={"ticket_id": ticket, "totp_code": _TOTP_CODE},
            name="/auth/totp/verify",
        )
        if resp2.status_code == 200:
            self.access_token = resp2.json().get("access_token")
            # Refresh cookie is set automatically by the HTTP client

    @task(5)
    def list_sessions(self) -> None:
        """GET /auth/sessions — authenticated with access JWT."""
        if not self.access_token:
            return
        self.client.get(
            "/api/v1/auth/sessions",
            headers={"Authorization": f"Bearer {self.access_token}"},
            name="/auth/sessions",
        )

    @task(2)
    def refresh_token(self) -> None:
        """POST /auth/refresh — rotate refresh cookie."""
        resp = self.client.post(
            "/api/v1/auth/refresh",
            name="/auth/refresh",
        )
        if resp.status_code == 200:
            self.access_token = resp.json().get("access_token")

    @task(1)
    def logout_and_relogin(self) -> None:
        """POST /auth/logout → re-login (simulates session expiry)."""
        self.client.post(
            "/api/v1/auth/logout",
            headers={"Authorization": f"Bearer {self.access_token}"},
            name="/auth/logout",
        )
        self.access_token = None
        self._do_login()


class RegistryUser(HttpUser):
    """Simulates a registry operator: 70% read, 20% patch, 5% create, 5% bulk.

    Task weight ratio is calibrated to match real-world usage observed in similar
    document-management systems: reads dominate, writes are infrequent, bulk ops rare.

    Targets (US-2 through US-24 load requirement):
      - Median response time < 300ms
      - p99 < 2000ms
      - 0% error rate at 50 concurrent users
    """

    wait_time = between(0.5, 2.0)

    def on_start(self) -> None:
        self.access_token: str | None = None
        self._created_doc_ids: list[str] = []
        self._do_login()

    def _do_login(self) -> None:
        resp = self.client.post(
            "/api/v1/auth/login",
            json={"email": _EMAIL, "password": _PASSWORD},
            name="/auth/login [registry-user]",
        )
        if resp.status_code != 200:
            return
        body = resp.json()
        ticket = body.get("session_ticket") or body.get("ticket_id")
        if not ticket:
            return
        resp2 = self.client.post(
            "/api/v1/auth/totp/verify",
            json={"ticket_id": ticket, "totp_code": _TOTP_CODE},
            name="/auth/totp/verify [registry-user]",
        )
        if resp2.status_code == 200:
            self.access_token = resp2.json().get("access_token")

    def _auth_headers(self) -> dict[str, str]:
        if self.access_token:
            return {"Authorization": f"Bearer {self.access_token}"}
        return {}

    # ------------------------------------------------------------------
    # Task: list / search documents (weight 70 = 70%)
    # ------------------------------------------------------------------

    @task(70)
    def list_documents(self) -> None:
        """GET /api/v1/registry/documents with varying filter combinations."""
        if not self.access_token:
            self._do_login()
            return

        # Rotate through common filter patterns to exercise pg_trgm search paths
        filter_variants = [
            {},
            {"status": "ok"},
            {"status": "soon"},
            {"status": "overdue"},
            {"sort": "expiry_date", "dir": "asc"},
            {"sort": "expiry_date", "dir": "desc"},
            {"sort": "number", "dir": "asc"},
            {"q": "договор"},
            {"q": "лицензия"},
            {"type_code": _TYPE_CODE},
            {"page": "2", "per_page": "50"},
        ]
        params = random.choice(filter_variants)

        self.client.get(
            "/api/v1/registry/documents",
            params=params,
            headers=self._auth_headers(),
            name="/registry/documents [list]",
        )

    # ------------------------------------------------------------------
    # Task: patch (inline edit) a document (weight 20 = 20%)
    # ------------------------------------------------------------------

    @task(20)
    def patch_document(self) -> None:
        """PATCH /api/v1/registry/documents/{id} — inline edit simulation."""
        if not self.access_token:
            self._do_login()
            return

        # Use a previously created doc if available, otherwise list first
        if self._created_doc_ids:
            doc_id = random.choice(self._created_doc_ids)
        else:
            # Fetch first page to get a real ID
            resp = self.client.get(
                "/api/v1/registry/documents",
                params={"per_page": "1"},
                headers=self._auth_headers(),
                name="/registry/documents [list for patch]",
            )
            if resp.status_code != 200:
                return
            items = resp.json().get("items", [])
            if not items:
                return
            doc_id = items[0]["id"]

        # Simulate inline-edit of the notes field (low-impact mutation)
        suffix = "".join(random.choices(string.ascii_letters, k=6))
        self.client.patch(
            f"/api/v1/registry/documents/{doc_id}",
            json={"notes": f"load-test-{suffix}"},
            headers=self._auth_headers(),
            name="/registry/documents/{id} [patch]",
        )

    # ------------------------------------------------------------------
    # Task: create a document (weight 5 = 5%)
    # ------------------------------------------------------------------

    @task(5)
    def create_document(self) -> None:
        """POST /api/v1/registry/documents — new document creation."""
        if not self.access_token:
            self._do_login()
            return

        if not _ASSET_ID:
            # No asset configured — list assets and pick first
            resp = self.client.get(
                "/api/v1/registry/assets",
                params={"per_page": "1", "status": "active"},
                headers=self._auth_headers(),
                name="/registry/assets [list for create]",
            )
            if resp.status_code != 200:
                return
            items = resp.json().get("items", [])
            if not items:
                return
            asset_id = items[0]["id"]
        else:
            asset_id = _ASSET_ID

        suffix = "".join(random.choices(string.digits, k=8))
        resp = self.client.post(
            "/api/v1/registry/documents",
            json={
                "asset_id": asset_id,
                "type_code": _TYPE_CODE,
                "number": f"ЛТ-{suffix}",
                "issue_date": "2026-01-01",
                "expiry_date": "2027-01-01",
                "notes": "locust load-test document",
            },
            headers=self._auth_headers(),
            name="/registry/documents [create]",
        )
        if resp.status_code == 201:
            doc_id = resp.json().get("id")
            if doc_id:
                # Track for subsequent patch/bulk tasks
                self._created_doc_ids.append(doc_id)
                # Keep list bounded to avoid unbounded growth
                if len(self._created_doc_ids) > 20:
                    self._created_doc_ids = self._created_doc_ids[-20:]

    # ------------------------------------------------------------------
    # Task: bulk archive (weight 5 = 5%)
    # ------------------------------------------------------------------

    @task(5)
    def bulk_archive_documents(self) -> None:
        """POST /api/v1/registry/documents/bulk-archive — bulk archive created docs."""
        if not self.access_token:
            self._do_login()
            return

        # Only archive our own load-test documents to avoid polluting the DB
        if len(self._created_doc_ids) < 2:
            # Not enough created docs yet — skip this iteration
            return

        # Pick up to 5 of our created docs
        ids_to_archive = random.sample(self._created_doc_ids, min(5, len(self._created_doc_ids)))

        resp = self.client.post(
            "/api/v1/registry/documents/bulk-archive",
            json={"ids": ids_to_archive},
            headers=self._auth_headers(),
            name="/registry/documents/bulk-archive [bulk]",
        )
        if resp.status_code in (200, 207):
            # Remove archived IDs from our tracking list
            archived_set = set(ids_to_archive)
            self._created_doc_ids = [d for d in self._created_doc_ids if d not in archived_set]


@events.quitting.add_listener
def _assert_targets(environment, **kwargs) -> None:  # type: ignore[type-arg]
    """Fail the load test if performance targets are not met."""
    stats = environment.runner.stats.total if environment.runner else None
    if stats is None:
        return

    median_ms = stats.get_response_time_percentile(0.50)
    p99_ms = stats.get_response_time_percentile(0.99)
    error_rate = stats.fail_ratio

    failures = []
    if median_ms and median_ms > 300:
        failures.append(f"Median {median_ms:.0f}ms > 300ms target")
    if p99_ms and p99_ms > 2000:
        failures.append(f"p99 {p99_ms:.0f}ms > 2000ms target")
    if error_rate > 0.0:
        failures.append(f"Error rate {error_rate:.1%} > 0% target")

    if failures:
        print("\nLOAD TEST TARGETS MISSED:")
        for f in failures:
            print(f"  - {f}")
        environment.process_exit_code = 1
