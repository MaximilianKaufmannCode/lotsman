// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

import { Link } from "@tanstack/react-router";
import {
  Calendar,
  ChevronDown,
  Compass,
  LogOut,
  Radio,
  Settings,
  Shield,
  User,
} from "lucide-react";
import * as React from "react";
import { useTranslation } from "react-i18next";
import { useAuth } from "@/features/auth/AuthProvider";
import { NotificationBell } from "@/features/notifications/NotificationBell";
import { cn } from "@/shared/lib/cn";
import { ThemeToggle } from "@/shared/ui/theme-toggle";

interface HeaderProps {
  className?: string;
}

// ── Role badge ────────────────────────────────────────────────────────────────

const ROLE_BADGE_CONFIG: Record<string, { label: string; cls: string; bold?: boolean }> = {
  super_admin: {
    label: "SUPER-ADMIN",
    cls: "bg-destructive text-destructive-foreground",
    bold: true,
  },
  admin: {
    label: "ADMIN",
    cls: "bg-primary text-primary-foreground",
    bold: true,
  },
  editor: {
    label: "EDITOR",
    cls: "bg-muted text-muted-foreground",
  },
  viewer: {
    label: "VIEWER",
    cls: "bg-muted/60 text-muted-foreground",
  },
};

function RoleBadge({ role }: { role: string }) {
  const config = ROLE_BADGE_CONFIG[role];
  if (!config) return null;
  return (
    <span
      title={`Роль: ${config.label}`}
      className={cn(
        "inline-flex items-center rounded px-1.5 py-0.5 text-xs select-none",
        config.bold ? "font-bold" : "font-semibold",
        config.cls,
      )}
    >
      {config.label}
    </span>
  );
}

// ── User menu ─────────────────────────────────────────────────────────────────

function UserMenu() {
  const { t } = useTranslation();
  const { claims, logout } = useAuth();
  const [open, setOpen] = React.useState(false);
  const menuRef = React.useRef<HTMLDivElement>(null);

  React.useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (!menuRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  React.useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [open]);

  if (!claims) return null;

  const initials = (claims.email[0] ?? "У").toUpperCase();
  const roleLabel: Record<string, string> = {
    admin: t("profile.role_admin"),
    editor: t("profile.role_editor"),
    viewer: t("profile.role_viewer"),
    super_admin: "Super-Admin",
  };

  const isSuperAdmin = claims.role === "super_admin";

  return (
    <div className="relative" ref={menuRef}>
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={t("header.user_menu")}
        title={`${claims.email} — ${t("header.user_menu")}`}
        onClick={() => setOpen((o) => !o)}
        className={cn(
          "inline-flex h-9 items-center gap-1 rounded-full pl-1 pr-1.5",
          "bg-primary/10 text-primary text-sm font-semibold",
          "hover:bg-primary/20 transition-colors",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        )}
      >
        <span className="inline-flex h-7 w-7 items-center justify-center rounded-full bg-primary/15">
          {initials}
        </span>
        <ChevronDown
          className={cn("h-3.5 w-3.5 transition-transform text-primary/70", open && "rotate-180")}
          aria-hidden
        />
      </button>

      {open && (
        <div
          role="menu"
          aria-label={t("header.user_menu")}
          className={cn(
            "absolute right-0 z-50 mt-2 w-56 rounded-md border border-border bg-card shadow-lg py-1",
          )}
        >
          {/* User info header */}
          <div className="px-3 py-2 border-b border-border">
            <p className="text-sm font-medium truncate">{claims.email}</p>
            <span
              className={cn(
                "inline-flex items-center mt-0.5 rounded px-1.5 py-0.5 text-xs font-semibold",
                claims.role === "super_admin" && "bg-destructive/10 text-destructive",
                claims.role === "admin" && "bg-destructive/10 text-destructive",
                claims.role === "editor" && "bg-status-soon/10 text-status-soon",
                claims.role === "viewer" && "bg-muted text-muted-foreground",
              )}
            >
              {roleLabel[claims.role] ?? claims.role}
            </span>
          </div>

          {/* Profile link */}
          <Link
            to="/profile"
            role="menuitem"
            onClick={() => setOpen(false)}
            className={cn(
              "flex w-full items-center gap-2 px-3 py-2 text-sm",
              "hover:bg-accent focus-visible:outline-none focus-visible:bg-accent",
            )}
          >
            <User className="h-4 w-4" aria-hidden />
            {t("nav.profile")}
          </Link>

          {/* Admin links — admin only (NOT super_admin) */}
          {claims.role === "admin" && (
            <>
              <Link
                to="/admin/users"
                role="menuitem"
                onClick={() => setOpen(false)}
                className={cn(
                  "flex w-full items-center gap-2 px-3 py-2 text-sm",
                  "hover:bg-accent focus-visible:outline-none focus-visible:bg-accent",
                )}
              >
                <Shield className="h-4 w-4" aria-hidden />
                {t("nav.admin_users")}
              </Link>
              <Link
                to="/admin/assets"
                role="menuitem"
                onClick={() => setOpen(false)}
                className={cn(
                  "flex w-full items-center gap-2 px-3 py-2 text-sm",
                  "hover:bg-accent focus-visible:outline-none focus-visible:bg-accent",
                )}
              >
                <Shield className="h-4 w-4" aria-hidden />
                {t("nav.admin_assets")}
              </Link>
              <Link
                to="/admin/document-types"
                role="menuitem"
                onClick={() => setOpen(false)}
                className={cn(
                  "flex w-full items-center gap-2 px-3 py-2 text-sm",
                  "hover:bg-accent focus-visible:outline-none focus-visible:bg-accent",
                )}
              >
                <Shield className="h-4 w-4" aria-hidden />
                {t("nav.admin_document_types")}
              </Link>
              <Link
                to="/admin/channels"
                role="menuitem"
                onClick={() => setOpen(false)}
                className={cn(
                  "flex w-full items-center gap-2 px-3 py-2 text-sm",
                  "hover:bg-accent focus-visible:outline-none focus-visible:bg-accent",
                )}
              >
                <Radio className="h-4 w-4" aria-hidden />
                {t("nav.admin_channels")}
              </Link>
              <Link
                to="/admin/calendar-subscriptions"
                role="menuitem"
                onClick={() => setOpen(false)}
                className={cn(
                  "flex w-full items-center gap-2 px-3 py-2 text-sm",
                  "hover:bg-accent focus-visible:outline-none focus-visible:bg-accent",
                )}
              >
                <Calendar className="h-4 w-4" aria-hidden />
                {t("nav.admin_calendar_subscriptions")}
              </Link>
              <Link
                to="/admin/notifications/history"
                role="menuitem"
                onClick={() => setOpen(false)}
                className={cn(
                  "flex w-full items-center gap-2 px-3 py-2 text-sm",
                  "hover:bg-accent focus-visible:outline-none focus-visible:bg-accent",
                )}
              >
                <Radio className="h-4 w-4" aria-hidden />
                История уведомлений
              </Link>
            </>
          )}

          {/* super_admin: link to System panel from dropdown too */}
          {isSuperAdmin && (
            <Link
              to="/system/health"
              role="menuitem"
              onClick={() => setOpen(false)}
              className={cn(
                "flex w-full items-center gap-2 px-3 py-2 text-sm",
                "hover:bg-accent focus-visible:outline-none focus-visible:bg-accent",
              )}
            >
              <Settings className="h-4 w-4" aria-hidden />
              {t("nav.system_panel")}
            </Link>
          )}

          <hr className="my-1 border-border" />

          {/* Logout */}
          <button
            type="button"
            role="menuitem"
            onClick={async () => {
              setOpen(false);
              await logout();
            }}
            className={cn(
              "flex w-full items-center gap-2 px-3 py-2 text-sm text-destructive",
              "hover:bg-accent focus-visible:outline-none focus-visible:bg-accent",
            )}
          >
            <LogOut className="h-4 w-4" aria-hidden />
            {t("nav.logout")}
          </button>
        </div>
      )}
    </div>
  );
}

// ── Quick logout button ──────────────────────────────────────────────────────

function QuickLogoutButton() {
  const { t } = useTranslation();
  const { logout } = useAuth();
  return (
    <button
      type="button"
      onClick={() => logout()}
      title={t("nav.logout")}
      aria-label={t("nav.logout")}
      className={cn(
        "inline-flex h-9 w-9 items-center justify-center rounded-md",
        "text-muted-foreground hover:bg-accent hover:text-destructive transition-colors",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
      )}
    >
      <LogOut className="h-4 w-4" aria-hidden />
    </button>
  );
}

// ── Header ────────────────────────────────────────────────────────────────────

export function Header({ className }: HeaderProps) {
  const { t } = useTranslation();
  const { status, claims } = useAuth();
  const isAuthenticated = status === "authenticated";
  const isSuperAdmin = claims?.role === "super_admin";

  return (
    <header
      className={cn(
        "sticky top-0 z-40 flex h-14 items-center gap-4 border-b bg-background/95 backdrop-blur-sm px-4",
        className,
      )}
    >
      {/* Brand — left */}
      {isSuperAdmin ? (
        <Link
          to="/system/health"
          className="flex items-center gap-2 font-semibold text-foreground hover:text-primary transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded"
          aria-label={`${t("app.brand")} — перейти к системной панели`}
        >
          <Compass className="h-5 w-5 text-primary" aria-hidden />
          <span className="font-semibold tracking-tight">Лоцман</span>
        </Link>
      ) : (
        <Link
          to="/registry"
          search={{
            q: undefined,
            type_code: undefined,
            status: undefined,
            asset_id: undefined,
            show_archived: undefined,
            sort: undefined,
            dir: undefined,
            page: undefined,
          }}
          className="flex items-center gap-2 font-semibold text-foreground hover:text-primary transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded"
          aria-label={`${t("app.brand")} — перейти к реестру`}
        >
          <Compass className="h-5 w-5 text-primary" aria-hidden />
          <span className="font-semibold tracking-tight">Лоцман</span>
        </Link>
      )}

      {/* super_admin: show "Системная панель" nav link (no search bar) */}
      {isAuthenticated && isSuperAdmin && (
        <nav aria-label={t("system.nav_label")}>
          <Link
            to="/system/health"
            className={cn(
              "text-sm font-medium text-foreground hover:text-primary transition-colors",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded px-2 py-1",
            )}
          >
            {t("nav.system_panel")}
          </Link>
        </nav>
      )}

      {/* Right: role badge + theme toggle + user menu + quick logout */}
      <div className="flex items-center gap-2 ml-auto">
        {isAuthenticated && claims && <RoleBadge role={claims.role} />}
        {isAuthenticated && !isSuperAdmin && <NotificationBell />}
        <ThemeToggle />
        {isAuthenticated && <UserMenu />}
        {isAuthenticated && <QuickLogoutButton />}
      </div>
    </header>
  );
}
