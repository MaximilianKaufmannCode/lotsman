# ADR-0001: Tech Stack Selection

- **Status**: Accepted
- **Date**: 2026-05-06
- **Deciders**: architect (proposed), product owner (accepted via initial brief)

## Context

Лоцман is an internal corporate document registry with notifications, replacing a manually-maintained Excel file. Constraints from the requirement intake:

- **Users**: 2–4 specialists (single department), confidential data, no external access.
- **Deployment**: on-premise (corporate network), accessible from office, business trips, abroad — all via corporate VPN/proxy.
- **Auth**: own credential flow with email + TOTP (no corporate SSO available now).
- **Channels**: Email, Telegram, Dion (https://faq.dion.vc/ru/deployment-settings/api).
- **Excel parity**: UI must visually resemble Excel and export to .xlsx; .xlsx is NOT the source of truth.
- **UX**: ergonomic, search-friendly under high row counts, adaptive across monitor sizes.

We need a stack that:
1. Is reliable for a small ops team to operate on-prem (not cloud-managed).
2. Has first-class async I/O (notifications + scheduled work).
3. Yields a data-dense, accessible UI without bespoke component engineering.
4. Has a long support horizon and broad hire-ability.

## Decision

**Backend**: Python 3.12 + FastAPI + SQLAlchemy 2 (async) + Alembic + Pydantic v2. Background jobs via ARQ (Redis-backed). `uv` for environment/deps.

**Frontend**: React 19 + TypeScript (strict) + Vite. Data via TanStack Query, table via TanStack Table + TanStack Virtual, routing via TanStack Router. Forms: react-hook-form + zod. Styling: Tailwind CSS 4 + shadcn/ui (copy-paste components). Tooling: `pnpm`, `biome`.

**Storage**: PostgreSQL 16 (single instance, separate schemas per service). Redis 7 (cache, queues, rate limiters).

**Auth**: argon2id passwords, pyotp (TOTP), JWT (RS256, 15-min access) + opaque refresh in HttpOnly cookie.

**Infrastructure**: Docker Compose v2 on a single host. Nginx for TLS termination + security headers. Prometheus + Grafana + Loki for observability.

**Quality tooling**: ruff, mypy --strict, pytest + testcontainers + hypothesis, playwright, biome, semgrep, bandit, gitleaks, pip-audit.

## Consequences

### Positive
- Async-first end to end (FastAPI + SQLAlchemy async + ARQ + httpx) matches the notification-heavy workload.
- TanStack Table + Virtual is the proven choice for Excel-like data grids; covers virtualization, sticky cells, and inline edit primitives.
- shadcn/ui (copy-paste, not a dep) avoids vendor lock-in and runtime bloat; we own the components.
- Pydantic v2 + Zod give matching validation idioms on each side; OpenAPI bridges them.
- Docker Compose is "boring" — operable by a single sysadmin, no Kubernetes investment.
- All chosen tools are MIT/Apache and run fully on-prem with no SaaS dependency.

### Negative
- `uv` and `biome` are newer than pip/eslint; small risk of churn (mitigated: both have stable APIs and broad adoption in 2025).
- Tailwind 4 / React 19 / TanStack Router are recent majors; expect minor breaking changes in 12-month window.
- ARQ is less battle-tested than Celery at huge scale, but our scale is trivial.
- shadcn/ui copy-paste means we maintain the components — explicit cost but low (small surface).

### Neutral / Follow-ups
- Single-host deployment limits HA. Acceptable for 4 users; revisit if user base grows >50.
- Object storage for attachments is local filesystem under volume mount; ADR later if S3-compatible storage (MinIO) becomes desirable.
- mTLS between services is deferred (single-host, single-network); ADR-0005 will address if we move to multi-host.

## Alternatives considered

### Backend: Node.js (NestJS) + Prisma
- **Pro**: Same language as frontend; great Prisma DX.
- **Con**: Background-job ecosystem (BullMQ) is fine but Python's ARQ + APScheduler analogues are simpler; ASVS-grade auth libs in Python are more mature; team biases toward Python for backend per stated preference for "modern/effective".
- **Why rejected**: marginal frontend-shared-lang benefit doesn't outweigh Python's edge in scheduled work + simpler ops scripts.

### Backend: Go (Echo / Fiber + sqlc)
- **Pro**: single binary, fast, low memory.
- **Con**: longer time-to-MVP for CRUD-heavy app with rich validation/serialization; smaller pool of in-house Go expertise typical for this domain; OpenAPI generation less ergonomic than FastAPI's auto-gen.
- **Why rejected**: developer velocity matters more than runtime efficiency at this scale.

### Backend: .NET 9 + EF Core
- **Pro**: Strong typing, mature; great for Windows-corporate environments.
- **Con**: heavier ops footprint on Linux on-prem; less aligned with the open-source observability stack chosen.
- **Why rejected**: not preferred unless explicit corporate-Windows mandate.

### Frontend: AG Grid Enterprise
- **Pro**: most feature-rich data grid in the world.
- **Con**: commercial license (>$1k/dev/yr); overkill for 4 users.
- **Why rejected**: TanStack Table covers our needs at zero cost.

### Frontend: Inertia + Laravel-style monolith
- **Pro**: simpler than SPA + API split.
- **Con**: backend chosen as Python; Inertia's Python adapters are immature.
- **Why rejected**: ecosystem mismatch.

### Auth: Keycloak self-hosted
- **Pro**: turnkey IdP, OIDC, SAML, RBAC.
- **Con**: requires Java runtime + PostgreSQL of its own; one more thing to operate; user explicitly chose "own login + email + TOTP".
- **Why rejected**: per stakeholder choice; revisit if SSO becomes a corporate requirement.

### Deployment: Kubernetes (k3s)
- **Pro**: portable, declarative, future-proof.
- **Con**: operational overhead vastly exceeds the value at 4 users + single host.
- **Why rejected**: Compose first, k3s is a future ADR if scale demands.

## References

- Initial requirement brief (chat 2026-05-06)
- Dion API: https://faq.dion.vc/ru/deployment-settings/api
- FastAPI: https://fastapi.tiangolo.com
- TanStack Table virtualization: https://tanstack.com/virtual/v3
- OWASP ASVS 4.0
- NIST 800-63B (TOTP/AAL2)

## Implementation handoff

- `the data layer`: bootstrap Postgres schemas + Alembic projects per service.
- `ops`: write `infra/compose.dev.yml`, Makefile, CI skeleton.
- `backend`: scaffold the 4 services + web-bff per the layout in `the project conventions` §3.
- `frontend`: bootstrap Vite + Tailwind + shadcn baseline; set up `openapi-typescript` codegen.
- `security`: produce ADR-0002 threat model.
