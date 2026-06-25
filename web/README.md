# web — Лоцман SPA

React 19 single-page application. Runs behind `web-bff` in both dev and production. The SPA communicates exclusively with `web-bff` at `/api/...`; it never calls backend services directly.

---

## Stack

| Layer | Choice |
|---|---|
| Framework | React 19 |
| Language | TypeScript 5 (strict) |
| Build | Vite 6 |
| Routing | TanStack Router |
| Server state | TanStack Query |
| Table / virtual | TanStack Table + TanStack Virtual |
| Forms | react-hook-form + zod |
| Styling | Tailwind CSS 4 |
| Components | shadcn/ui (copy-paste, we own these — see `src/shared/ui/`) |
| Linting / formatting | Biome |
| Unit tests | Vitest + Testing Library + vitest-axe |
| E2E tests | Playwright |
| API types | openapi-typescript (generated from `docs/api/web-bff.yaml`) |
| i18n | react-i18next — RU is the default locale; EN is the fallback (`fallbackLng`) for untranslated strings |

---

## Local dev

```bash
cd web
pnpm install
pnpm dev
# SPA at http://localhost:5173
# /api proxied to http://localhost:8000 (web-bff must be running)
```

Hot module replacement is enabled. API proxy is configured in `vite.config.ts`.

---

## Scripts

| Script | Command | What it does |
|---|---|---|
| Dev server | `pnpm dev` | Vite HMR dev server on port 5173 |
| Build | `pnpm build` | `tsc --noEmit` then `vite build` → `dist/` |
| Preview | `pnpm preview` | Serve the production build locally |
| Type-check | `pnpm typecheck` | `tsc --noEmit` |
| Lint | `pnpm lint` | Biome check on `src/` |
| Lint + fix | `pnpm lint:fix` | Biome check --write |
| Format | `pnpm format` | Biome format --write |
| Unit tests | `pnpm test` | Vitest run (single pass) |
| Unit tests UI | `pnpm test:ui` | Vitest with browser UI |
| Unit tests watch | `pnpm test:watch` | Vitest watch mode |
| Generate API types | `pnpm gen:api` | openapi-typescript from `docs/api/web-bff.yaml` |

Run tests once (used by CI and `make test`):

```bash
pnpm test
```

### E2E tests

Specs live in `web/e2e/`. Playwright does **not** start a dev server for you — run `pnpm dev` in another terminal first (it tests against `http://localhost:5173`).

Install browsers on first run:

```bash
pnpm exec playwright install --with-deps
pnpm dev            # in a separate terminal
pnpm exec playwright test
```

### Generate API types

```bash
pnpm gen:api
```

This reads `../docs/api/web-bff.yaml` (relative to `web/`) and writes `src/shared/api/schema.gen.ts`. The `web-bff` OpenAPI spec does not exist yet, so `schema.gen.ts` ships as a stub (`paths = Record<string, never>`) that lets the typed client compile. Regenerate once the spec lands.

---

## Where things live

Code is split three ways: **`pages/`** are route-level screens, **`features/`** hold domain logic (API clients, hooks, components grouped by domain), and **`shared/`** is cross-cutting UI, layout, and the API client. A page composes hooks and components from one or more features.

```
web/src/
├── app/
│   ├── router.tsx          TanStack Router root (route tree)
│   └── providers.tsx       Query client, i18n, theme + auth providers; wires the 401 interceptor
├── pages/                  Route-level screens
│   ├── login/              LoginPage
│   ├── first-login/        FirstLoginPage (forced password + TOTP enrolment)
│   ├── registry/           RegistryPage (the document grid) + its dialogs:
│   │                       DocumentCreateDialog, QuickTypeDialog, DocumentDetailDrawer,
│   │                       EditColumnDialog, ExportJobsModal, ImportXlsxDialog
│   ├── profile/            ProfilePage (personal settings: font size, notification matrix, email)
│   ├── settings/           SettingsPage
│   ├── admin/              Admin surfaces: assets, users, document-types, channels,
│   │                       calendar-subscriptions, notifications
│   └── system/             Service-control panel (admin-only): SystemHealth/Audit/Logs/
│                           Migrations/Queues/Keys/Maintenance pages + SystemLayout
├── features/               Domain logic, grouped by domain
│   ├── auth/               AuthProvider, AuthGuard, RoleGuard, token refresh, broadcast sync
│   ├── registry/           api.ts, hooks/ (useDocuments, useAssets, …), filters/,
│   │                       columnConfig.ts, computeStatus.ts, types.ts, __tests__/
│   ├── admin/              channels / document-types / calendar-subscriptions API clients
│   ├── notifications/      NotificationBell
│   └── system/             api.ts, types.ts
├── shared/                 Cross-cutting building blocks
│   ├── ui/                 shadcn/ui components (we own these), plus theme-toggle,
│   │                       status-badge, and font-scale.ts (the --app-font-scale setter)
│   ├── layout/             AppLayout, Header, Footer
│   ├── api/
│   │   ├── client.ts       openapi-fetch typed client
│   │   ├── interceptor.ts  Global 401 recovery (single refresh, then retry or kick to /login)
│   │   └── schema.gen.ts   Generated from the web-bff OpenAPI spec (currently a stub)
│   └── lib/
│       └── cn.ts           clsx + tailwind-merge helper
├── i18n/
│   ├── index.ts            i18next init (default RU, fallback EN)
│   ├── ru.json             Russian strings (default locale)
│   └── en.json             English strings (fallback)
├── styles/
│   └── globals.css         Tailwind base + CSS custom properties (design tokens)
└── test/
    ├── setup.ts            Vitest global setup (jest-dom, axe)
    └── vitest-axe.d.ts     Type declarations for the a11y matcher
```

The tree is illustrative, not exhaustive — new domains go under `src/features/<domain>/`, new screens under `src/pages/`.

---

## Conventions

- **shadcn/ui components** live in `src/shared/ui/`. We copy-paste them from shadcn and own them — do not `npm install` the originals. Customize freely.
- **No `any` or `unknown` without narrowing.** TypeScript strict mode is enforced; Biome lint catches unsafe casts.
- **URL is state** for filters, sort, and pagination. Use TanStack Router search params — do not store table state in React state that disappears on refresh.
- **Russian for UI text**, English for code and comments.
- **«Компания» is the canonical user-facing term** (renamed from «Контрагент» in 2.2.0; «Company» in EN). The internal identifier stays `asset` — so code, API paths, and i18n keys keep `asset` / `col_counterparty` while displayed strings say «Компания». Don't rename the identifier.
- **Interface font size** is a unitless multiplier on the `--app-font-scale` CSS custom property (`globals.css` does `font-size: calc(16px * var(--app-font-scale, 1))`). It's set by `shared/ui/font-scale.ts`, cached in `localStorage` (`lotsman-font-scale`), and — for signed-in users — backed by the server (`auth.users.ui_font_scale`). Size with `rem`/relative units so this scales; never hardcode `px`.
- **Design tokens** (status colors, radii, etc.) are CSS custom properties declared in `src/styles/globals.css` and consumed via Tailwind. Do not hardcode HSL values in components.
- **Accessibility**: WCAG 2.2 AA. All interactive elements must be keyboard-navigable with a visible focus ring. Run `vitest-axe` checks in component tests.

---

## Notable features

The document form supports inline creation so you never leave the registry to add a missing reference:

- **«Создать компанию»** (in `DocumentCreateDialog`) — available to **editor or admin**.
- **«Создать тип документа»** (`QuickTypeDialog`) — **admin-only**.

Other 2.1.0–2.4.0 user-facing work that touches the SPA: the personal interface font-size setting (`profile/`, see Conventions), the «Контрагент» → «Компания» rename, and the rebranded HTML notification emails (rendered backend-side). See the root `CHANGELOG.md` for the full history.

---

*Last updated: 2026-06-25*
