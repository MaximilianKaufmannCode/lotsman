# ADR-0011: Notifications expansion — lifecycle events, all-users reminders, per-user preferences, in-app center

- **Status**: Proposed
- **Date**: 2026-06-01
- **Deciders**: architect (proposed), venawaziwoco83@gmail.com (approved scope — all 3 phases)
- **Depends on**: ADR-0002 (service boundaries / internal JWT), ADR-0005 (Exchange calendar — outbox→stream→consumer pipeline this builds on)
- **References**:
  - `registry-service/.../domain/events.py` (document lifecycle events)
  - `notification-service/.../infrastructure/consumers/registry_document_consumer.py` (existing consumer, group `notification-calendar-sync`)
  - `notification-service/.../application/use_cases/schedule_daily_reminders.py` (existing reminder scheduler)

## Context

Reminders go only to `responsible_user_id`; there are no event notifications and no per-user preferences. The
registry already publishes rich lifecycle events to the `registry.documents` Redis stream, and notification-service
already consumes that stream (only for calendar sync). We want to add event notifications, fan deadline reminders
out to all active users, and give users a preferences screen — **without** destabilising the live system or
existing data.

## Decision

### D1 — Build on the existing outbox→stream→consumer pipeline; add an isolated consumer group
A **new** Redis consumer group `notification-events` reads the **same** `registry.documents` stream, independently
of the existing `notification-calendar-sync` group. Consumer groups have independent cursors, so this cannot
disturb calendar sync. No new microservice.

### D2 — Per-user preferences as one additive table
`notification.user_notification_prefs` (one row/user): `enabled`, `suppress_own`, `email_mode` (`instant|digest|off`),
and a `categories` JSONB (`{category: {in_app, email}}`). JSONB chosen over a normalized matrix because the user
count is tiny (2–4) and categories evolve; matches the project's "JSONB for flexible fields" stance. Absent row →
code defaults (everything sensible-on), so the feature is safe before any user touches settings.

### D3 — Recipient resolution via a new auth internal endpoint
Add `GET /api/v1/internal/users?active=true` to auth-service (internal-JWT, additive; mirrors existing
`POST /internal/users/lookup`). notification-service's `HttpAuthGateway` gains `list_active_users()`.

### D4 — Deadline reminders → all active users (extend, don't rewrite)
`schedule_daily_reminders` keeps its template-selection logic; the change is **who** it enqueues for: instead of
just `responsible_user_id`, it iterates active users (minus opt-outs). Idempotency key stays
`reminder:{doc}:{template}:{date}` but becomes per-user: `reminder:{doc}:{user}:{template}:{date}`. `delivery_attempts`
dedup `(doc, user, template, date, status='sent')` already supports per-user rows — no schema change in Phase 1.

### D5 — Event spam control: coalescing + digest
`document.updated.v1` fires per field. The event consumer **coalesces** edits to one document within a Redis-buffered
window (~10 min) into a single notification. Email for events defaults to a **daily digest**; `instant` opt-in
available. New `template_code`s (`doc_created`, `doc_updated`, `doc_archived`, `digest`) require **widening** the
`delivery_attempts.template_code` CHECK (Phase 2, additive).

### D6 — In-app center (Phase 3)
`notification.user_notifications` feed table (additive) + BFF feed/unread endpoints + a bell UI. In-app is the
default channel so email volume stays low.

## Phasing
1. **Foundation**: D2, D3, D4 + minimal profile section (master + deadline email). MINOR bump.
2. **Events**: D1, D5 + category matrix in profile. MINOR bump.
3. **In-app center**: D6. MINOR bump.

## Consequences
- **Positive**: no new service; calendar sync untouched (separate group); all migrations additive; feature-safe
  with no prefs row; per-user control; EWS protected by digest/coalescing.
- **Negative / risks**: per-user fan-out multiplies `delivery_attempts` rows (bounded — 2–4 users); coalescing adds
  Redis state (ephemeral, acceptable); widening a CHECK requires a migration before new codes are written (sequenced
  in Phase 2). Telegram/Dion remain stubs.

## Alternatives considered
- *Reuse the calendar-sync consumer group* — rejected: coupling event-notify with calendar sync risks the live ICS
  feature; independent group is safer.
- *Normalized prefs matrix table* — rejected for now: overkill for 2–4 users; JSONB is simpler and matches house style.
- *Send every event immediately by email to everyone* — rejected: EWS rate-limit + inbox spam (per-field edits).
