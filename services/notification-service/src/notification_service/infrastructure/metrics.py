# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Prometheus metrics for notification-service email reminders (Phase C).

Exposed at /metrics via lotsman_shared.metrics.make_metrics_router().
Scraped by prometheus at 127.0.0.1:8003 (notification-svc internal port).

Naming convention: `lotsman_<domain>_<unit>_total` (counters) or
`lotsman_<domain>_<unit>_seconds` (histograms).
"""

from __future__ import annotations

from prometheus_client import Counter, Histogram

# ── Email reminder send metrics ─────────────────────────────────────────────

EMAIL_REMINDERS_TOTAL = Counter(
    "lotsman_email_reminders_total",
    "Total email reminders processed by status, channel and template_code.",
    ["status", "channel", "template_code"],
)

EMAIL_SEND_DURATION = Histogram(
    "lotsman_email_send_duration_seconds",
    "Time spent in send_transactional (SMTP + EWS-fallback).",
    ["channel", "outcome"],
    buckets=(0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0),
)

EMAIL_EWS_FALLBACK_TOTAL = Counter(
    "lotsman_email_ews_fallback_total",
    "Times SMTP failed with policy-error and we fell back to EWS.",
    ["outcome"],  # 'succeeded' | 'failed'
)

# ── Scheduler metrics ───────────────────────────────────────────────────────

SCHEDULE_RUN_TOTAL = Counter(
    "lotsman_reminder_schedule_runs_total",
    "Times the daily scheduler executed (regardless of how many reminders enqueued).",
    ["outcome"],  # 'ok' | 'error'
)

SCHEDULE_ENQUEUED_TOTAL = Counter(
    "lotsman_reminder_enqueued_total",
    "Reminder jobs enqueued by the daily scheduler, by template_code.",
    ["template_code"],
)

SCHEDULE_SKIPPED_TOTAL = Counter(
    "lotsman_reminder_skipped_total",
    "Documents skipped by the daily scheduler.",
    ["reason"],  # 'no_user' | 'no_expiry' | 'archived'
)
