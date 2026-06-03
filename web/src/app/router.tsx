// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

import {
  createRootRoute,
  createRoute,
  createRouter,
  type NotFoundRouteComponent,
  Outlet,
  redirect,
} from "@tanstack/react-router";
import { AuthGuard } from "@/features/auth/AuthGuard";
import { RoleGuard } from "@/features/auth/RoleGuard";
import { AssetsPage } from "@/pages/admin/assets/AssetsPage";
import { CalendarSubscriptionsPage } from "@/pages/admin/calendar-subscriptions/CalendarSubscriptionsPage";
import { ChannelsPage } from "@/pages/admin/channels/ChannelsPage";
import { CustomFieldsPage } from "@/pages/admin/document-types/CustomFieldsPage";
import { DocumentTypesPage } from "@/pages/admin/document-types/DocumentTypesPage";
import { NotificationsHistoryPage } from "@/pages/admin/notifications/NotificationsHistoryPage";
import { UsersPage } from "@/pages/admin/users/UsersPage";
import { FirstLoginPage } from "@/pages/first-login/FirstLoginPage";
import { LoginPage } from "@/pages/login/LoginPage";
import { ProfilePage } from "@/pages/profile/ProfilePage";
import { RegistryPage } from "@/pages/registry/RegistryPage";
import { SettingsPage } from "@/pages/settings/SettingsPage";
import { SystemAuditPage } from "@/pages/system/SystemAuditPage";
import { SystemHealthPage } from "@/pages/system/SystemHealthPage";
import { SystemKeysPage } from "@/pages/system/SystemKeysPage";
import { SystemLayout } from "@/pages/system/SystemLayout";
import { SystemLogsPage } from "@/pages/system/SystemLogsPage";
import { SystemMaintenancePage } from "@/pages/system/SystemMaintenancePage";
import { SystemMigrationsPage } from "@/pages/system/SystemMigrationsPage";
import { SystemQueuesPage } from "@/pages/system/SystemQueuesPage";
import { AppLayout } from "@/shared/layout/AppLayout";
import { Footer } from "@/shared/layout/Footer";

// ── 404 ───────────────────────────────────────────────────────────────────────

const NotFoundPage: NotFoundRouteComponent = () => (
  <div className="flex flex-1 flex-col items-center justify-center gap-4 text-center px-4">
    <h1 className="text-4xl font-bold">404</h1>
    <p className="text-muted-foreground">Страница не найдена</p>
    <a
      href="/registry"
      className="text-primary underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded"
    >
      Вернуться в реестр
    </a>
  </div>
);

// ── Root shell ────────────────────────────────────────────────────────────────
//
// Wraps EVERY route (login, first-login, app, system, 404) in a flex column
// with Footer pinned to the bottom. This guarantees that the version string
// is always visible per the project conventions (versioning policy) — physically
// impossible to "forget" the footer when adding a new layout/page.

function RootShell() {
  return (
    <div className="flex min-h-screen flex-col bg-background">
      <div className="flex-1 flex flex-col">
        <Outlet />
      </div>
      <Footer />
    </div>
  );
}

const rootRoute = createRootRoute({
  component: RootShell,
  notFoundComponent: NotFoundPage,
});

// ── Public routes ─────────────────────────────────────────────────────────────

const loginRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/login",
  component: LoginPage,
  validateSearch: (search: Record<string, unknown>) => ({
    next: typeof search.next === "string" ? search.next : undefined,
  }),
});

const firstLoginRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/first-login",
  component: FirstLoginPage,
});

// ── Layout route (AppLayout — for non-super_admin pages) ──────────────────────

const layoutRoute = createRoute({
  getParentRoute: () => rootRoute,
  id: "_layout",
  component: AppLayout,
});

// ── System layout route (SystemLayout — for super_admin /system/* pages) ──────

const systemLayoutRoute = createRoute({
  getParentRoute: () => rootRoute,
  id: "_system_layout",
  component: SystemLayout,
});

// ── Index → redirect ──────────────────────────────────────────────────────────

const indexRoute = createRoute({
  getParentRoute: () => layoutRoute,
  path: "/",
  beforeLoad: () => {
    throw redirect({
      to: "/registry",
      search: {
        q: undefined,
        type_code: undefined,
        status: undefined,
        asset_id: undefined,
        show_archived: undefined,
        sort: undefined,
        dir: undefined,
        page: undefined,
      },
    });
  },
  component: () => null,
});

// ── 403 component ─────────────────────────────────────────────────────────────

const superAdminFallback = (
  <div className="flex min-h-[50vh] items-center justify-center">
    <p className="text-muted-foreground">Только для super-admin.</p>
  </div>
);

const adminFallback = (
  <div className="flex min-h-[50vh] items-center justify-center">
    <p className="text-muted-foreground">Недостаточно прав доступа.</p>
  </div>
);

// ── Guards ────────────────────────────────────────────────────────────────────

function GuardedRegistry() {
  return (
    <AuthGuard>
      <RegistryPage />
    </AuthGuard>
  );
}

function GuardedSettings() {
  return (
    <AuthGuard>
      <SettingsPage />
    </AuthGuard>
  );
}

function GuardedProfile() {
  return (
    <AuthGuard>
      <ProfilePage />
    </AuthGuard>
  );
}

function GuardedAdminUsers() {
  return (
    <AuthGuard>
      {/* biome-ignore lint/a11y/useValidAriaRole: role is a RoleGuard custom prop, not an HTML attribute */}
      <RoleGuard role="admin" fallback={adminFallback}>
        <UsersPage />
      </RoleGuard>
    </AuthGuard>
  );
}

function GuardedAdminAssets() {
  return (
    <AuthGuard>
      {/* biome-ignore lint/a11y/useValidAriaRole: role is a RoleGuard custom prop, not an HTML attribute */}
      <RoleGuard role="admin" fallback={adminFallback}>
        <AssetsPage />
      </RoleGuard>
    </AuthGuard>
  );
}

function GuardedAdminDocumentTypes() {
  return (
    <AuthGuard>
      {/* biome-ignore lint/a11y/useValidAriaRole: role is a RoleGuard custom prop, not an HTML attribute */}
      <RoleGuard role="admin" fallback={adminFallback}>
        <DocumentTypesPage />
      </RoleGuard>
    </AuthGuard>
  );
}

function GuardedAdminCustomFields({ typeCode }: { typeCode: string }) {
  return (
    <AuthGuard>
      {/* biome-ignore lint/a11y/useValidAriaRole: role is a RoleGuard custom prop, not an HTML attribute */}
      <RoleGuard role="admin" fallback={adminFallback}>
        <CustomFieldsPage typeCode={typeCode} />
      </RoleGuard>
    </AuthGuard>
  );
}

function GuardedAdminChannels() {
  return (
    <AuthGuard>
      {/* biome-ignore lint/a11y/useValidAriaRole: role is a RoleGuard custom prop, not an HTML attribute */}
      <RoleGuard role="admin" fallback={adminFallback}>
        <ChannelsPage />
      </RoleGuard>
    </AuthGuard>
  );
}

function GuardedAdminCalendarSubscriptions() {
  return (
    <AuthGuard>
      {/* biome-ignore lint/a11y/useValidAriaRole: role is a RoleGuard custom prop, not an HTML attribute */}
      <RoleGuard role="admin" fallback={adminFallback}>
        <CalendarSubscriptionsPage />
      </RoleGuard>
    </AuthGuard>
  );
}

function GuardedAdminNotificationsHistory() {
  return (
    <AuthGuard>
      {/* biome-ignore lint/a11y/useValidAriaRole: role is a RoleGuard custom prop, not an HTML attribute */}
      <RoleGuard role="admin" fallback={adminFallback}>
        <NotificationsHistoryPage />
      </RoleGuard>
    </AuthGuard>
  );
}

// ── System page guards ─────────────────────────────────────────────────────────

function GuardedSystemHealth() {
  return (
    <AuthGuard>
      {/* biome-ignore lint/a11y/useValidAriaRole: role is a RoleGuard custom prop, not an HTML attribute */}
      <RoleGuard role="super_admin" fallback={superAdminFallback}>
        <SystemHealthPage />
      </RoleGuard>
    </AuthGuard>
  );
}

function GuardedSystemQueues() {
  return (
    <AuthGuard>
      {/* biome-ignore lint/a11y/useValidAriaRole: role is a RoleGuard custom prop, not an HTML attribute */}
      <RoleGuard role="super_admin" fallback={superAdminFallback}>
        <SystemQueuesPage />
      </RoleGuard>
    </AuthGuard>
  );
}

function GuardedSystemMigrations() {
  return (
    <AuthGuard>
      {/* biome-ignore lint/a11y/useValidAriaRole: role is a RoleGuard custom prop, not an HTML attribute */}
      <RoleGuard role="super_admin" fallback={superAdminFallback}>
        <SystemMigrationsPage />
      </RoleGuard>
    </AuthGuard>
  );
}

function GuardedSystemKeys() {
  return (
    <AuthGuard>
      {/* biome-ignore lint/a11y/useValidAriaRole: role is a RoleGuard custom prop, not an HTML attribute */}
      <RoleGuard role="super_admin" fallback={superAdminFallback}>
        <SystemKeysPage />
      </RoleGuard>
    </AuthGuard>
  );
}

function GuardedSystemLogs() {
  return (
    <AuthGuard>
      {/* biome-ignore lint/a11y/useValidAriaRole: role is a RoleGuard custom prop, not an HTML attribute */}
      <RoleGuard role="super_admin" fallback={superAdminFallback}>
        <SystemLogsPage />
      </RoleGuard>
    </AuthGuard>
  );
}

function GuardedSystemAudit() {
  return (
    <AuthGuard>
      {/* biome-ignore lint/a11y/useValidAriaRole: role is a RoleGuard custom prop, not an HTML attribute */}
      <RoleGuard role="super_admin" fallback={superAdminFallback}>
        <SystemAuditPage />
      </RoleGuard>
    </AuthGuard>
  );
}

function GuardedSystemMaintenance() {
  return (
    <AuthGuard>
      {/* biome-ignore lint/a11y/useValidAriaRole: role is a RoleGuard custom prop, not an HTML attribute */}
      <RoleGuard role="super_admin" fallback={superAdminFallback}>
        <SystemMaintenancePage />
      </RoleGuard>
    </AuthGuard>
  );
}

// ── Route declarations (AppLayout children) ───────────────────────────────────

const registryRoute = createRoute({
  getParentRoute: () => layoutRoute,
  path: "/registry",
  component: GuardedRegistry,
  // v1.24.3: permissive passthrough — Zod-validation теперь живёт в
  // useUrlState (`registrySearchSchema`), а не здесь. Прежний whitelist
  // из 7 ключей выкидывал из URL ВСЁ что было добавлено в v1.23/v1.24
  // (cf_*, asset_activity, doc_status, responsible, jurisdiction, и пр.),
  // отчего ни sidebar, ни per-column-фильтры на самом деле не применялись —
  // navigate({search}) переписывал URL уже без них.
  // Пропускаем любые скаляры; типизация — в Zod-схеме на стороне страницы.
  // v1.24.11 — IMPORTANT: keep arrays! Previously the check was `typeof v in
  // {string, number, boolean}`, which silently DROPPED any array value
  // (asset_ids, type_codes, expiry_dates, doc_status…) because typeof []
  // === "object". navigate({search: {asset_ids:[…]}}) сначала проходит через
  // validateSearch, потом через stringifySearch — массивы исчезали ДО
  // стрингифика, поэтому в URL никогда не попадали, и SPA реально звонил в
  // BFF БЕЗ этих параметров. Custom-fields работали т.к. они уже сплющены в
  // плоские строки в buildSearchObject. Pass arrays через — stringifySearch
  // их корректно сериализует в CSV.
  validateSearch: (search: Record<string, unknown>): Record<string, unknown> => {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(search)) {
      if (v === undefined || v === null) continue;
      if (
        typeof v === "string" ||
        typeof v === "number" ||
        typeof v === "boolean" ||
        Array.isArray(v)
      ) {
        out[k] = v;
      }
    }
    return out;
  },
});

const settingsRoute = createRoute({
  getParentRoute: () => layoutRoute,
  path: "/settings",
  component: GuardedSettings,
});

const profileRoute = createRoute({
  getParentRoute: () => layoutRoute,
  path: "/profile",
  component: GuardedProfile,
});

const adminUsersRoute = createRoute({
  getParentRoute: () => layoutRoute,
  path: "/admin/users",
  component: GuardedAdminUsers,
});

const adminAssetsRoute = createRoute({
  getParentRoute: () => layoutRoute,
  path: "/admin/assets",
  component: GuardedAdminAssets,
});

const adminDocumentTypesRoute = createRoute({
  getParentRoute: () => layoutRoute,
  path: "/admin/document-types",
  component: GuardedAdminDocumentTypes,
});

const adminCustomFieldsRoute = createRoute({
  getParentRoute: () => layoutRoute,
  path: "/admin/document-types/$typeCode/fields",
  component: function AdminCustomFieldsPage() {
    const { typeCode } = adminCustomFieldsRoute.useParams();
    return <GuardedAdminCustomFields typeCode={typeCode} />;
  },
});

const adminChannelsRoute = createRoute({
  getParentRoute: () => layoutRoute,
  path: "/admin/channels",
  component: GuardedAdminChannels,
});

const adminCalendarSubscriptionsRoute = createRoute({
  getParentRoute: () => layoutRoute,
  path: "/admin/calendar-subscriptions",
  component: GuardedAdminCalendarSubscriptions,
});

const adminNotificationsHistoryRoute = createRoute({
  getParentRoute: () => layoutRoute,
  path: "/admin/notifications/history",
  component: GuardedAdminNotificationsHistory,
});

// ── System routes (SystemLayout children) ─────────────────────────────────────

const systemHealthRoute = createRoute({
  getParentRoute: () => systemLayoutRoute,
  path: "/system/health",
  component: GuardedSystemHealth,
});

const systemQueuesRoute = createRoute({
  getParentRoute: () => systemLayoutRoute,
  path: "/system/queues",
  component: GuardedSystemQueues,
});

const systemMigrationsRoute = createRoute({
  getParentRoute: () => systemLayoutRoute,
  path: "/system/migrations",
  component: GuardedSystemMigrations,
});

const systemKeysRoute = createRoute({
  getParentRoute: () => systemLayoutRoute,
  path: "/system/keys",
  component: GuardedSystemKeys,
});

const systemLogsRoute = createRoute({
  getParentRoute: () => systemLayoutRoute,
  path: "/system/logs",
  component: GuardedSystemLogs,
});

const systemAuditRoute = createRoute({
  getParentRoute: () => systemLayoutRoute,
  path: "/system/audit",
  component: GuardedSystemAudit,
});

const systemMaintenanceRoute = createRoute({
  getParentRoute: () => systemLayoutRoute,
  path: "/system/maintenance",
  component: GuardedSystemMaintenance,
});

// ── Route tree ────────────────────────────────────────────────────────────────

const routeTree = rootRoute.addChildren([
  loginRoute,
  firstLoginRoute,
  layoutRoute.addChildren([
    indexRoute,
    registryRoute,
    settingsRoute,
    profileRoute,
    adminUsersRoute,
    adminAssetsRoute,
    adminDocumentTypesRoute,
    adminCustomFieldsRoute,
    adminChannelsRoute,
    adminCalendarSubscriptionsRoute,
    adminNotificationsHistoryRoute,
  ]),
  systemLayoutRoute.addChildren([
    systemHealthRoute,
    systemQueuesRoute,
    systemMigrationsRoute,
    systemKeysRoute,
    systemLogsRoute,
    systemAuditRoute,
    systemMaintenanceRoute,
  ]),
]);

// v1.24.8 — Plain-string search serializer.
//
// TanStack Router's default parseSearch / stringifySearch wraps every value in
// JSON.stringify; strings end up as `?page="1"`, booleans get JSON-encoded too,
// and arrays serialize as `["a","b"]`. Our backend and the registry filter
// chain expect plain URL params: `?page=1&expiry_null=true&type_codes=a,b`.
//
// We override at the router level so EVERY route (registry + admin + system)
// gets consistent search params, regardless of how individual code paths build
// the URL (navigate({search:…}) or navigate({to:"/x?qs"}) — both end up here).
//
// Reading: values come out as strings; zod schemas in feature modules
// (e.g. registrySearchSchema in useUrlState) coerce them to typed values
// (z.coerce.number, boolParam union, csvArray transform).
function stringifySearch(search: Record<string, unknown>): string {
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(search)) {
    if (v === undefined || v === null || v === "" || v === false) continue;
    if (Array.isArray(v)) {
      if (v.length > 0) params.set(k, v.join(","));
    } else {
      params.set(k, String(v));
    }
  }
  const s = params.toString();
  return s ? `?${s}` : "";
}

function parseSearch(searchStr: string): Record<string, unknown> {
  const s = searchStr.startsWith("?") ? searchStr.slice(1) : searchStr;
  const out: Record<string, unknown> = {};
  for (const [k, v] of new URLSearchParams(s).entries()) {
    out[k] = v;
  }
  return out;
}

export const router = createRouter({
  routeTree,
  defaultPreload: "intent",
  defaultPreloadDelay: 100,
  parseSearch,
  stringifySearch,
});

// Type registration for TanStack Router
declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
