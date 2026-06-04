# Contributing to Лоцман

Thanks for your interest in improving Лоцман! This document describes how to set
up the project, the conventions we follow, and how to get a change merged.

By participating you agree to abide by our [Code of Conduct](CODE_OF_CONDUCT.md).

---

## Conventions

- **Naming.** The product is **«Лоцман»** in user-facing text and documentation;
  in code, identifiers, hosts, paths, and environment variables use the Latin
  transliteration **`lotsman`**.
- **Languages.** Code and technical documentation are in **English**; the
  end-user interface and the user guide are in **Russian**.
- **Architecture.** Each service follows clean architecture
  (`domain/` → `application/` → `infrastructure/` + `api/`), dependencies point
  inward only, and the rule is enforced by `import-linter`. Backend services do
  not call each other over HTTP — everything fans out through `web-bff`.
- **Data changes are audited.** Every state-changing operation must emit an event
  through the transactional outbox in the same database transaction.
- **No secrets in the repo.** Use `.env` (git-ignored) locally and a secrets
  manager in production. `.env.example` documents every variable.

---

## Development setup

Prerequisites: Docker 26+ with Compose v2, `uv`, `pnpm`.

```bash
git clone https://github.com/MaximilianKaufmannCode/lotsman.git
cd lotsman
cp .env.example .env       # fill in the values
make dev                   # build, start, migrate, seed
```

See the [README](README.md#quickstart-5-minutes) for service URLs and the
[deployment guide](docs/deployment/README.md) for production.

---

## Branch & commit conventions

Branches:

```
feat/<scope>   fix/<scope>   chore/<scope>   docs/<scope>
```

Commits use Conventional-Commits style, imperative present tense, one subject
line (≤ 72 chars):

```
feat(registry): add document archiving endpoint
fix(auth): reject expired refresh tokens at login
docs(deployment): document TLS termination at Nginx
```

An optional body (after a blank line) explains *why*, not *what* — the diff
already shows what changed.

---

## Local quality gate

Run this before every push — it is exactly what CI runs:

```bash
make ci-local        # = make lint + make typecheck + make test
```

- Backend: `ruff`, `mypy --strict`, `pytest` (with `testcontainers` for
  integration tests).
- Frontend: `biome`, `tsc --noEmit`, `vitest`, and Playwright for E2E.

A push that fails `ci-local` will fail CI.

### Tests — where they go

| What you wrote | Where the test goes |
|---|---|
| Domain entity / value object / use case | `services/<svc>/tests/unit/` |
| Infrastructure adapter (DB, Redis, HTTP) | `services/<svc>/tests/integration/` |
| FastAPI router | `services/<svc>/tests/unit/` |
| Shared kernel | `shared/tests/` |
| React component | `web/src/**/*.test.tsx` |
| E2E flow | `web/e2e/` (Playwright) |

Coverage targets: ≥ 80% lines overall, ≥ 90% branches in `domain/`.

---

## Versioning

The project uses **Semantic Versioning** (`vMAJOR.MINOR.PATCH`). The source of
truth is the `version` field in `web/package.json`, surfaced in the UI footer.

- **MAJOR** — architectural change (new/removed service, breaking inter-service
  contract, destructive migration).
- **MINOR** — new feature, endpoint, screen, or channel without breakage.
- **PATCH** — bug fix or security fix without public-contract change.

Every code change bumps the version in the same commit and adds a `CHANGELOG.md`
entry. The `scripts/check-version-bump.sh` pre-commit guard enforces this.

---

## Architecture Decision Records

Write an ADR when you make a non-trivial choice — adding/removing a service or
major dependency, changing an inter-service contract, changing how auth/secrets
work, or introducing new infrastructure.

1. Copy [docs/adr/TEMPLATE.md](docs/adr/TEMPLATE.md) to
   `docs/adr/NNNN-short-title.md` and fill it in.
2. Open it as a PR for discussion; merge it once `Status: Accepted`.
3. Never edit an accepted ADR — write a new one that supersedes it.

See the [ADR index](docs/adr/README.md).

---

## Pull requests

1. Branch off `main`, make your change, and run `make ci-local`.
2. Open a PR using the template; link the issue it closes.
3. A maintainer reviews it; address feedback and keep CI green.
4. A maintainer merges once approved.

## Reporting bugs & vulnerabilities

- **Bugs / features** — open a [GitHub issue](https://github.com/MaximilianKaufmannCode/lotsman/issues/new/choose) using the
  provided templates.
- **Security vulnerabilities** — do **not** open a public issue; follow
  [SECURITY.md](SECURITY.md).

## License

Лоцман is distributed under the **Business Source License 1.1** (BUSL-1.1) — a
**source-available** license, not an OSI-approved open-source license. Each
version converts to the **Mozilla Public License 2.0** four years after its
release. See [LICENSE](LICENSE) and [LICENSING.md](LICENSING.md).

By submitting a contribution (for example, a pull request) you certify that you
have the right to do so, and you agree that your contribution is licensed to the
project and its users under the **same terms as the Licensed Work** (BUSL-1.1,
converting to MPL-2.0 on the Change Date), and that the Licensor (Maximilian
Kaufmann) may set the Change Date and Change License for it. Contributions made
before v2.0.0 were accepted under MPL-2.0.
