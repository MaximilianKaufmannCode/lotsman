// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * SystemLayout — shell for all /system/* pages.
 * Visible only to super_admin (enforced at route level via RoleGuard).
 *
 * Left sidebar (~240px) with 7 nav links.
 * Red super-admin banner above content (dismissible per-session).
 */

import { Link, Outlet, useLocation } from "@tanstack/react-router";
import {
  Activity,
  AlertTriangle,
  BookOpen,
  ClipboardList,
  Database,
  FileText,
  Key,
  Menu,
  X,
} from "lucide-react";
import * as React from "react";
import { useTranslation } from "react-i18next";
// NOTE: Footer is rendered ONCE at the root level (router.tsx RootShell).
import { Header } from "@/shared/layout/Header";
import { cn } from "@/shared/lib/cn";

// ── Sidebar nav items ─────────────────────────────────────────────────────────

interface NavItem {
  to: string;
  labelKey: string;
  Icon: React.FC<React.SVGProps<SVGSVGElement>>;
}

const NAV_ITEMS: NavItem[] = [
  { to: "/system/health", labelKey: "system.nav_health", Icon: Activity },
  { to: "/system/queues", labelKey: "system.nav_queues", Icon: Database },
  { to: "/system/migrations", labelKey: "system.nav_migrations", Icon: ClipboardList },
  { to: "/system/keys", labelKey: "system.nav_keys", Icon: Key },
  { to: "/system/logs", labelKey: "system.nav_logs", Icon: FileText },
  { to: "/system/audit", labelKey: "system.nav_audit", Icon: BookOpen },
  { to: "/system/maintenance", labelKey: "system.nav_maintenance", Icon: AlertTriangle },
];

// ── Super-admin banner ────────────────────────────────────────────────────────

const SUPER_ADMIN_BANNER_KEY = "superadmin-banner-dismissed";

function SuperAdminBanner() {
  const { t } = useTranslation();
  const [dismissed, setDismissed] = React.useState(() => {
    try {
      return sessionStorage.getItem(SUPER_ADMIN_BANNER_KEY) === "true";
    } catch {
      return false;
    }
  });

  if (dismissed) return null;

  const handleDismiss = () => {
    try {
      sessionStorage.setItem(SUPER_ADMIN_BANNER_KEY, "true");
    } catch {
      // ignore
    }
    setDismissed(true);
  };

  return (
    <div
      role="alert"
      className="flex items-center gap-3 border-b-2 border-destructive bg-destructive/10 px-4 py-2"
    >
      <AlertTriangle className="h-4 w-4 shrink-0 text-destructive" aria-hidden />
      <p className="flex-1 text-sm font-medium text-destructive">
        {t("system.super_admin_banner")}
      </p>
      <button
        type="button"
        aria-label={t("common.close")}
        onClick={handleDismiss}
        className="rounded p-0.5 text-destructive hover:text-destructive/80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <X className="h-4 w-4" aria-hidden />
      </button>
    </div>
  );
}

// ── Sidebar ───────────────────────────────────────────────────────────────────

interface SidebarProps {
  collapsed: boolean;
  onToggle: () => void;
}

function Sidebar({ collapsed, onToggle }: SidebarProps) {
  const { t } = useTranslation();
  const location = useLocation();

  return (
    <nav
      aria-label={t("system.sidebar_nav_label")}
      className={cn(
        "flex flex-col border-r border-border bg-muted/30 transition-all duration-200",
        collapsed ? "w-14" : "w-60",
      )}
    >
      {/* Toggle button */}
      <div className="flex h-12 items-center border-b border-border px-3">
        <button
          type="button"
          aria-label={collapsed ? t("system.sidebar_expand") : t("system.sidebar_collapse")}
          onClick={onToggle}
          className={cn(
            "inline-flex h-8 w-8 items-center justify-center rounded-md",
            "text-muted-foreground hover:bg-accent hover:text-foreground transition-colors",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
          )}
        >
          <Menu className="h-4 w-4" aria-hidden />
        </button>
        {!collapsed && (
          <span className="ml-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            {t("system.sidebar_title")}
          </span>
        )}
      </div>

      {/* Nav links */}
      <ul className="flex flex-col gap-0.5 p-2">
        {NAV_ITEMS.map(({ to, labelKey, Icon }) => {
          const isActive =
            location.pathname === to || (location.pathname.startsWith(to) && to !== "/system");
          return (
            <li key={to}>
              <Link
                to={to}
                aria-current={isActive ? "page" : undefined}
                className={cn(
                  "flex items-center gap-2 rounded-md px-2 py-2 text-sm transition-colors",
                  "hover:bg-accent hover:text-foreground",
                  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                  isActive ? "bg-primary/10 text-primary font-medium" : "text-muted-foreground",
                  collapsed && "justify-center",
                )}
                title={collapsed ? t(labelKey) : undefined}
              >
                <Icon className="h-4 w-4 shrink-0" aria-hidden />
                {!collapsed && <span>{t(labelKey)}</span>}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}

// ── SystemLayout ──────────────────────────────────────────────────────────────

export function SystemLayout() {
  const { t } = useTranslation();
  const [sidebarCollapsed, setSidebarCollapsed] = React.useState(false);

  // Collapse sidebar on narrow viewports by default
  React.useEffect(() => {
    const mq = window.matchMedia("(max-width: 1023px)");
    if (mq.matches) setSidebarCollapsed(true);
    const handler = (e: MediaQueryListEvent) => {
      setSidebarCollapsed(e.matches);
    };
    mq.addEventListener("change", handler);
    return () => mq.removeEventListener("change", handler);
  }, []);

  return (
    <div className="flex flex-1 flex-col">
      {/* Skip-to-content link — a11y */}
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-50 focus:rounded focus:bg-primary focus:px-3 focus:py-2 focus:text-sm focus:text-primary-foreground focus:shadow"
      >
        {t("a11y.skip_to_content")}
      </a>

      <Header />
      <SuperAdminBanner />

      <div className="flex flex-1 overflow-hidden">
        <Sidebar collapsed={sidebarCollapsed} onToggle={() => setSidebarCollapsed((c) => !c)} />

        <main id="main-content" className="flex-1 overflow-auto" tabIndex={-1}>
          <Outlet />
        </main>
      </div>

      {/* Global live region */}
      <div
        role="status"
        aria-live="polite"
        aria-atomic="true"
        aria-label={t("a11y.status_region")}
        className="sr-only"
        id="status-region"
      />
    </div>
  );
}
