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

See the [README quickstart](README.md#быстрый-старт-5-минут) for service URLs
and the [deployment guide](docs/deployment/README.md) for production.

---

## Branch & commit conventions

Branch off `main` using a `<type>/<scope>` name. Use the short, single-word
type that matches the change — the same set we use for commits:

```
feat/<scope>   fix/<scope>   refactor/<scope>   chore/<scope>
docs/<scope>   ci/<scope>    ops/<scope>        security/<scope>
```

Prefer the short form (`feat/`, not `feature/`) for consistency.

Commits use Conventional-Commits style, imperative present tense, one subject
line (≤ 72 chars). The accepted types are **`feat`, `fix`, `refactor`,
`chore`, `docs`, `ci`, `ops`, `test`**, with a scope in parentheses:

```
feat(registry):    add document archiving endpoint
fix(auth):         reject expired refresh tokens at login
refactor(web-bff): collapse duplicate fan-out helpers
docs(deployment):  document TLS termination at Nginx
ci(security):      pin trivy to a fixed version in the PR gate
chore(release):    2.4.0
```

An optional body (after a blank line) explains *why*, not *what* — the diff
already shows what changed.

---

## Local quality gate

Run this before every push — it mirrors the core checks CI runs:

```bash
make ci-local        # = make lint + make typecheck + make test
```

- Backend: `ruff`, `mypy --strict`, `pytest` (with `testcontainers` for
  integration tests).
- Frontend: `biome`, `tsc --noEmit`, `vitest`, and Playwright for E2E.

A push that fails `ci-local` will fail CI. The per-PR security gate
(`security-pr.yml`: dependency CVEs, IaC, SAST) runs only in CI, not in
`ci-local` — so a green `ci-local` is necessary but not sufficient.

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
- **MINOR** — new feature, endpoint, screen, or channel without breakage. This
  also covers a user-facing terminology rename that leaves the public
  contract and schema unchanged — for example **2.2.0**, which renamed
  «Контрагент» → «Компания» in the UI while the code/DB identifier stayed
  `asset`. Treat such renames as MINOR, not PATCH.
- **PATCH** — bug fix or security fix without public-contract change.

Every code change bumps the version in the same commit and adds a `CHANGELOG.md`
entry. A pre-commit guard for this lives in `scripts/check-version-bump.sh`,
but it is **opt-in** — install it once with `bash scripts/check-version-bump.sh
--hook`. It is not wired into CI, so the bump remains your responsibility on
every code commit.

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

Лоцман is distributed under the [Business Source License 1.1](LICENSE) (BUSL-1.1),
a source-available license that converts to the Mozilla Public License 2.0 on the
Change Date; see [LICENSING.md](LICENSING.md).

By contributing, you certify that you have the right to do so and agree that your
contribution is licensed under the project's license, and that the Licensor
(Maximilian Kaufmann) may set the Change Date and Change License for it.
