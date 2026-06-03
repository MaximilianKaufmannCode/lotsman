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
| i18n | react-i18next (RU primary, EN secondary) |

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

Install browsers on first run:

```bash
pnpm exec playwright install --with-deps
pnpm exec playwright test
```

### Generate API types

```bash
pnpm gen:api
```

This reads `docs/api/web-bff.yaml` and writes `src/shared/api/schema.gen.ts`. The OpenAPI spec for `web-bff` does not yet exist (feature endpoints land in subsequent sprints); the generated file is a stub until then.

---

## Where things live

```
web/src/
├── app/
│   ├── router.tsx          TanStack Router root (file-based route tree)
│   └── providers.tsx       Query client, i18n, theme providers
├── pages/
│   ├── login/              LoginPage.tsx
│   ├── registry/           RegistryPage.tsx (main document grid)
│   └── settings/           SettingsPage.tsx
├── shared/
│   ├── ui/                 shadcn/ui components (we own these — edit freely)
│   ├── layout/             AppLayout.tsx, Header.tsx
│   ├── api/
│   │   ├── client.ts       openapi-fetch typed client
│   │   └── schema.gen.ts   Generated from web-bff OpenAPI spec
│   └── lib/
│       └── cn.ts           clsx + tailwind-merge helper
├── i18n/
│   ├── index.ts            i18next init
│   ├── ru.json             Russian strings (primary)
│   └── en.json             English strings
├── styles/
│   └── globals.css         Tailwind base, CSS custom properties (design tokens)
└── test/
    └── setup.ts            Vitest global setup (jest-dom, axe)
```

Add new feature modules as `src/features/<feature-name>/` once the feature has a dedicated use case.

---

## Conventions

- **shadcn/ui components** live in `src/shared/ui/`. We copy-paste them from shadcn and own them — do not `npm install` the originals. Customize freely.
- **No `any` or `unknown` without narrowing.** TypeScript strict mode is enforced; Biome lint catches unsafe casts.
- **URL is state** for filters, sort, and pagination. Use TanStack Router search params — do not store table state in React state that disappears on refresh.
- **Russian for UI text**, English for code and comments.
- **Design tokens** (status colors, etc.) are declared in `the design spec` and materialized in `src/styles/globals.css`. Do not hardcode HSL values in components.
- **Accessibility**: WCAG 2.2 AA. All interactive elements must be keyboard-navigable with visible focus ring. Run `vitest-axe` checks in component tests.

---

*Last updated: 2026-05-06*
