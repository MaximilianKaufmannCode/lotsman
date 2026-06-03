// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * Admin user management page (US-11, US-13, extended with channel-aware invite).
 *
 * Changes vs Phase 2:
 * - ≥2 admin warning banner (US-11): shown when active admins < 2, dismissible per session.
 * - CreateUserDialog now accepts defaultRole prop for the banner's CTA.
 * - Re-invite button in row actions for pending users (must_change_password + no TOTP).
 *
 * Visible only to admins — RoleGuard wraps this route in router.tsx.
 */

import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createColumnHelper,
  flexRender,
  getCoreRowModel,
  useReactTable,
} from "@tanstack/react-table";
import { format } from "date-fns";
import { AlertTriangle, Loader2, MoreHorizontal, Plus, RotateCcw, Search, X } from "lucide-react";
import * as React from "react";
import { useTranslation } from "react-i18next";
import { computeUserStatus } from "@/features/admin/userStatus";
import {
  ApiResponseError,
  adminDeactivateUser,
  adminDeleteUser,
  adminListUsers,
  adminLockUser,
  adminReactivateUser,
  adminRevokeAllSessions,
  adminUnlockUser,
} from "@/features/auth/api";
import type { AdminUser } from "@/features/auth/types";
import { cn } from "@/shared/lib/cn";
import { Button } from "@/shared/ui/button";
import { toast } from "@/shared/ui/toast";
import { ChangeRoleDialog } from "./components/ChangeRoleDialog";
import { ConfirmReMfaDialog } from "./components/ConfirmReMfaDialog";
import { CreateUserDialog } from "./components/CreateUserDialog";
import { ReInviteDialog } from "./components/ReInviteDialog";
import { ResetPasswordDialog } from "./components/ResetPasswordDialog";
import { ResetTotpDialog } from "./components/ResetTotpDialog";
import { UserDetailDrawer } from "./UserDetailDrawer";

// ── Column helper ─────────────────────────────────────────────────────────────

const col = createColumnHelper<AdminUser>();

// ── Status badge ──────────────────────────────────────────────────────────────

function UserStatusBadge({ user }: { user: AdminUser }) {
  const s = computeUserStatus(user);
  return (
    <span
      className={cn(
        "inline-flex items-center rounded px-2 py-0.5 text-xs font-semibold",
        s.badgeClass,
      )}
    >
      {s.label}
    </span>
  );
}

// ── ≥2 admin warning banner ───────────────────────────────────────────────────

const BANNER_DISMISSED_KEY = "admin-warning-dismissed";

interface AdminWarningBannerProps {
  count: number;
  onAddAdmin: () => void;
}

function AdminWarningBanner({ count, onAddAdmin }: AdminWarningBannerProps) {
  const [dismissed, setDismissed] = React.useState(() => {
    try {
      return sessionStorage.getItem(BANNER_DISMISSED_KEY) === "true";
    } catch {
      return false;
    }
  });

  if (dismissed) return null;

  const handleDismiss = () => {
    try {
      sessionStorage.setItem(BANNER_DISMISSED_KEY, "true");
    } catch {
      // sessionStorage unavailable — silently ignore
    }
    setDismissed(true);
  };

  return (
    <div
      role="alert"
      className="flex items-start gap-3 rounded-lg bg-amber-50 border border-amber-300 text-amber-900 px-4 py-3 mb-6"
    >
      <AlertTriangle className="h-5 w-5 text-amber-600 mt-0.5 shrink-0" aria-hidden />
      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium">
          Рекомендуется иметь минимум 2 активных admin'а на случай отпуска или потери TOTP. Сейчас
          активных admin'ов: <strong>{count}</strong>.
        </p>
        <button
          type="button"
          onClick={onAddAdmin}
          className={cn(
            "mt-2 text-sm font-medium underline underline-offset-2",
            "hover:text-amber-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-500 rounded",
          )}
        >
          Назначить ещё одного admin'а
        </button>
      </div>
      <button
        type="button"
        aria-label="Скрыть"
        onClick={handleDismiss}
        className={cn(
          "shrink-0 rounded p-0.5 text-amber-600",
          "hover:text-amber-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-500",
        )}
      >
        <X className="h-4 w-4" aria-hidden />
      </button>
    </div>
  );
}

// ── Row actions menu ──────────────────────────────────────────────────────────

type DialogType =
  | "change-role"
  | "lock"
  | "unlock"
  | "reset-totp"
  | "reset-password"
  | "revoke-sessions"
  | "deactivate"
  | "activate"
  | "delete"
  | "re-invite"
  | null;

interface RowActionsProps {
  user: AdminUser;
  onAction: (type: DialogType, user: AdminUser) => void;
}

/** A user is "pending" (hasn't completed first-login enrollment) when totp_enrolled=false */
function isPendingUser(user: AdminUser): boolean {
  return user.is_active && !user.totp_enrolled;
}

function RowActions({ user, onAction }: RowActionsProps) {
  const { t } = useTranslation();
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

  // Status-aware action set (Hick's law): present only the moves that make sense
  // for this account's state, so the admin never faces a contradictory choice.
  // A deactivated account's lockout flag is an internal side-effect of
  // deactivation, so it must NOT offer «Разблокировать» — its only restore path
  // is «Активировать», which clears the lockout server-side.
  type Action = { label: string; type: DialogType; disabled?: boolean; icon?: React.ReactNode };
  const statusKey = computeUserStatus(user).key;

  let actions: Action[];
  if (statusKey === "system") {
    // Built-in service account — not manageable from the UI.
    actions = [];
  } else if (statusKey === "deactivated") {
    // Disabled account: no live sessions, lockout is internal. Turn it back on
    // or remove it for good — nothing else applies.
    actions = [
      { label: "Активировать", type: "activate" },
      { label: "Удалить", type: "delete" },
    ];
  } else {
    // active / locked / pending
    actions = [
      { label: t("admin.action_change_role"), type: "change-role" },
      user.is_locked
        ? { label: t("admin.action_unlock"), type: "unlock" }
        : { label: t("admin.action_lock"), type: "lock" },
      { label: t("admin.action_reset_totp"), type: "reset-totp" },
      { label: t("admin.action_reset_password"), type: "reset-password" },
      { label: t("admin.action_revoke_sessions"), type: "revoke-sessions" },
      ...(isPendingUser(user)
        ? [
            {
              label: "Повторно пригласить",
              type: "re-invite" as DialogType,
              icon: <RotateCcw className="h-3.5 w-3.5" aria-hidden />,
            },
          ]
        : []),
      { label: t("admin.action_deactivate"), type: "deactivate" },
      { label: "Удалить", type: "delete" },
    ];
  }

  if (actions.length === 0) return null;

  return (
    <div className="relative" ref={menuRef}>
      <Button
        variant="ghost"
        size="icon"
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={t("admin.row_actions_label", { email: user.email })}
      >
        <MoreHorizontal className="h-4 w-4" aria-hidden />
      </Button>

      {open && (
        <div
          role="menu"
          aria-label={t("admin.row_actions_menu")}
          className="absolute right-0 z-50 mt-1 w-52 rounded-md border border-border bg-card shadow-lg py-1"
        >
          {actions.map((action) => (
            <button
              key={action.type}
              type="button"
              role="menuitem"
              disabled={action.disabled}
              onClick={() => {
                setOpen(false);
                onAction(action.type, user);
              }}
              className={cn(
                "w-full text-left px-3 py-2 text-sm flex items-center gap-2",
                "hover:bg-accent focus-visible:outline-none focus-visible:bg-accent",
                action.disabled && "opacity-40 cursor-not-allowed",
                (action.type === "deactivate" || action.type === "delete") && "text-destructive",
              )}
            >
              {action.icon}
              {action.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────

export function UsersPage() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [createOpen, setCreateOpen] = React.useState(false);
  const [createDefaultRole, setCreateDefaultRole] = React.useState<
    "admin" | "editor" | "viewer" | undefined
  >(undefined);
  const [activeUser, setActiveUser] = React.useState<AdminUser | null>(null);
  const [activeDialog, setActiveDialog] = React.useState<DialogType>(null);
  // US-102: detail drawer on row single-click (Phase 1 of admin-user-management-v2).
  const [drawerUser, setDrawerUser] = React.useState<AdminUser | null>(null);

  const {
    data: users,
    isLoading,
    isError,
  } = useQuery({
    queryKey: ["admin", "users"],
    queryFn: adminListUsers,
  });

  // ≥2 admin banner logic
  const activeAdminsCount = React.useMemo(
    () => (users ?? []).filter((u) => u.role === "admin" && u.is_active).length,
    [users],
  );

  const handleAction = React.useCallback((type: DialogType, user: AdminUser) => {
    setActiveUser(user);
    setActiveDialog(type);
  }, []);

  const closeDialog = () => {
    setActiveDialog(null);
    setActiveUser(null);
  };

  const invalidate = () => qc.invalidateQueries({ queryKey: ["admin", "users"] });

  const [reMfaForAction, setReMfaForAction] = React.useState(false);

  const handleInlineReMfa = async (reMfaToken: string) => {
    if (!activeUser || !activeDialog) return;
    setReMfaForAction(false);
    try {
      if (activeDialog === "lock") await adminLockUser(activeUser.id, reMfaToken);
      else if (activeDialog === "unlock") await adminUnlockUser(activeUser.id, reMfaToken);
      else if (activeDialog === "revoke-sessions")
        await adminRevokeAllSessions(activeUser.id, reMfaToken);
      else if (activeDialog === "deactivate") await adminDeactivateUser(activeUser.id, reMfaToken);
      else if (activeDialog === "activate") await adminReactivateUser(activeUser.id, reMfaToken);
      else if (activeDialog === "delete") await adminDeleteUser(activeUser.id, reMfaToken);

      toast.show({ title: t("admin.action_success"), variant: "success" });
      closeDialog();
      invalidate();
    } catch (err) {
      if (err instanceof ApiResponseError && err.status === 401) {
        toast.show({ title: t("login_errors.invalid_credentials"), variant: "destructive" });
      } else {
        toast.show({ title: t("login_errors.network_error_title"), variant: "destructive" });
      }
    }
  };

  const INLINE_ACTION_TYPES = React.useMemo<DialogType[]>(
    () => ["lock", "unlock", "revoke-sessions", "deactivate", "activate", "delete"],
    [],
  );
  React.useEffect(() => {
    if (activeDialog && INLINE_ACTION_TYPES.includes(activeDialog)) {
      setReMfaForAction(true);
    }
  }, [activeDialog, INLINE_ACTION_TYPES]);

  const columns = React.useMemo(
    () => [
      col.accessor("email", {
        header: t("login.email_label"),
        cell: (info) => <span className="font-medium text-sm">{info.getValue()}</span>,
      }),
      col.accessor("full_name", {
        header: t("admin.col_name"),
        cell: (info) => <span className="text-sm">{info.getValue()}</span>,
      }),
      col.accessor("role", {
        header: t("admin.col_role"),
        cell: (info) => {
          const roleLabels: Record<string, string> = {
            admin: t("profile.role_admin"),
            editor: t("profile.role_editor"),
            viewer: t("profile.role_viewer"),
          };
          return <span className="text-sm">{roleLabels[info.getValue()] ?? info.getValue()}</span>;
        },
      }),
      col.display({
        id: "status",
        header: t("admin.col_status"),
        cell: ({ row }) => <UserStatusBadge user={row.original} />,
      }),
      col.accessor("created_at", {
        header: t("admin.col_created"),
        cell: (info) => (
          <span className="text-sm text-muted-foreground">
            {format(new Date(info.getValue()), "dd.MM.yyyy")}
          </span>
        ),
      }),
      col.accessor("last_login_at", {
        header: t("admin.col_last_login"),
        cell: (info) => (
          <span className="text-sm text-muted-foreground">
            {info.getValue()
              ? format(new Date(info.getValue() as string), "dd.MM.yyyy HH:mm")
              : "—"}
          </span>
        ),
      }),
      col.display({
        id: "actions",
        header: () => <span className="sr-only">{t("admin.col_actions")}</span>,
        cell: ({ row }) => <RowActions user={row.original} onAction={handleAction} />,
      }),
    ],
    [t, handleAction],
  );

  // ── Phase 3 (US-101): client-side filter + search ──────────────────────────
  // Server-side BE-3 расширение `GET /admin/users?search=&role=&status=` —
  // плановая работа, см. the requirements §6 BE-3.
  // Пока (4 пользователя) клиентский фильтр достаточен; рефакторинг тривиален.
  const [searchInput, setSearchInput] = React.useState("");
  const [search, setSearch] = React.useState("");
  const [filterRole, setFilterRole] = React.useState<
    "all" | "admin" | "editor" | "viewer" | "super_admin"
  >("all");
  const [filterStatus, setFilterStatus] = React.useState<
    "all" | "active" | "locked" | "pending" | "deactivated" | "system"
  >("all");

  // Debounce search 200ms
  React.useEffect(() => {
    const id = window.setTimeout(() => setSearch(searchInput.trim().toLowerCase()), 200);
    return () => window.clearTimeout(id);
  }, [searchInput]);

  const filteredUsers = React.useMemo(() => {
    if (!users) return [];
    return users.filter((u) => {
      // search by email or full_name
      if (search) {
        const s = `${u.email} ${u.full_name}`.toLowerCase();
        if (!s.includes(search)) return false;
      }
      // role
      if (filterRole !== "all" && u.role !== filterRole) return false;
      // status — single source of truth (matches the badge + drawer)
      const status = computeUserStatus(u).key;
      if (filterStatus !== "all" && status !== filterStatus) return false;
      return true;
    });
  }, [users, search, filterRole, filterStatus]);

  const table = useReactTable({
    data: filteredUsers,
    columns,
    getCoreRowModel: getCoreRowModel(),
  });

  const handleBannerCta = () => {
    setCreateDefaultRole("admin");
    setCreateOpen(true);
  };

  return (
    <div className="max-w-6xl mx-auto px-4 py-8">
      {/* ≥2 admin warning banner */}
      {!isLoading && !isError && activeAdminsCount < 2 && (
        <AdminWarningBanner count={activeAdminsCount} onAddAdmin={handleBannerCta} />
      )}

      <div className="flex items-center justify-between mb-4">
        <h1 className="text-2xl font-semibold">{t("admin.users_title")}</h1>
        <Button
          onClick={() => {
            setCreateDefaultRole(undefined);
            setCreateOpen(true);
          }}
          className="gap-2"
        >
          <Plus className="h-4 w-4" aria-hidden />
          {t("admin.create_user_btn")}
        </Button>
      </div>

      {/* Phase 3 filter + search bar (US-101) */}
      {!isLoading && !isError && (
        <div className="mb-4 space-y-2">
          {/* Search */}
          <div className="relative max-w-md">
            <Search
              className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground"
              aria-hidden="true"
            />
            <input
              type="search"
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              placeholder="Поиск по email или ФИО…"
              aria-label="Поиск пользователей"
              className="w-full rounded-md border border-input bg-background py-2 pl-9 pr-3 text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
            />
          </div>

          {/* Filter chips */}
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1.5">
            <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Роль:
            </span>
            <FilterChip active={filterRole === "all"} onClick={() => setFilterRole("all")}>
              Все
            </FilterChip>
            <FilterChip active={filterRole === "admin"} onClick={() => setFilterRole("admin")}>
              Админ
            </FilterChip>
            <FilterChip active={filterRole === "editor"} onClick={() => setFilterRole("editor")}>
              Редактор
            </FilterChip>
            <FilterChip active={filterRole === "viewer"} onClick={() => setFilterRole("viewer")}>
              Наблюдатель
            </FilterChip>
            <FilterChip
              active={filterRole === "super_admin"}
              onClick={() => setFilterRole("super_admin")}
            >
              Супер-админ
            </FilterChip>

            <span className="ml-3 text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Статус:
            </span>
            <FilterChip active={filterStatus === "all"} onClick={() => setFilterStatus("all")}>
              Все
            </FilterChip>
            <FilterChip
              active={filterStatus === "active"}
              onClick={() => setFilterStatus("active")}
              variant="green"
            >
              Активные
            </FilterChip>
            <FilterChip
              active={filterStatus === "locked"}
              onClick={() => setFilterStatus("locked")}
              variant="red"
            >
              Заблок.
            </FilterChip>
            <FilterChip
              active={filterStatus === "pending"}
              onClick={() => setFilterStatus("pending")}
              variant="amber"
            >
              Ожидают
            </FilterChip>
            <FilterChip
              active={filterStatus === "deactivated"}
              onClick={() => setFilterStatus("deactivated")}
              variant="gray"
            >
              Деактив.
            </FilterChip>
            <FilterChip
              active={filterStatus === "system"}
              onClick={() => setFilterStatus("system")}
              variant="gray"
            >
              Системные
            </FilterChip>

            {(search || filterRole !== "all" || filterStatus !== "all") && (
              <button
                type="button"
                onClick={() => {
                  setSearchInput("");
                  setSearch("");
                  setFilterRole("all");
                  setFilterStatus("all");
                }}
                className="ml-2 inline-flex items-center gap-1 rounded-full border border-dashed border-muted-foreground/40 px-2.5 py-1 text-xs text-muted-foreground hover:bg-muted"
              >
                <X className="h-3 w-3" /> Сбросить
              </button>
            )}

            <span className="ml-auto text-xs text-muted-foreground">
              {filteredUsers.length} из {users?.length ?? 0}
            </span>
          </div>
        </div>
      )}

      {isError && (
        <div
          role="alert"
          className="rounded bg-destructive/10 border border-destructive px-3 py-2 mb-4"
        >
          <p className="text-sm text-destructive">{t("login_errors.network_error_title")}</p>
        </div>
      )}

      {isLoading && (
        <div
          role="status"
          className="flex justify-center py-12"
          aria-busy="true"
          aria-label={t("common.loading")}
        >
          <Loader2 className="h-8 w-8 animate-spin text-primary" aria-hidden />
        </div>
      )}

      {!isLoading && (
        <div className="rounded-md border border-border overflow-auto">
          <table className="w-full text-sm" aria-label={t("admin.users_table_label")}>
            <thead className="bg-muted/50 sticky top-0">
              {table.getHeaderGroups().map((hg) => (
                <tr key={hg.id}>
                  {hg.headers.map((header) => (
                    <th
                      key={header.id}
                      scope="col"
                      className="px-3 py-2 text-left text-xs font-semibold text-muted-foreground uppercase tracking-wide whitespace-nowrap"
                    >
                      {flexRender(header.column.columnDef.header, header.getContext())}
                    </th>
                  ))}
                </tr>
              ))}
            </thead>
            <tbody className="divide-y divide-border">
              {table.getRowModel().rows.length === 0 && (
                <tr>
                  <td
                    colSpan={columns.length}
                    className="px-3 py-8 text-center text-sm text-muted-foreground"
                  >
                    {t("admin.no_users")}
                  </td>
                </tr>
              )}
              {table.getRowModel().rows.map((row) => (
                <tr
                  key={row.id}
                  className="hover:bg-muted/30 transition-colors cursor-pointer"
                  onClick={(e) => {
                    // Skip if the click was inside a button (kebab menu / inline actions)
                    // or its descendant — those handle their own actions.
                    if ((e.target as HTMLElement).closest("button,[role='menuitem']")) return;
                    setDrawerUser(row.original);
                  }}
                  aria-label={`Открыть карточку ${row.original.full_name}`}
                >
                  {row.getVisibleCells().map((cell) => (
                    <td key={cell.id} className="px-3 py-2 whitespace-nowrap">
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Dialogs */}
      <CreateUserDialog
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={invalidate}
        defaultRole={createDefaultRole}
      />

      <ChangeRoleDialog
        open={activeDialog === "change-role"}
        user={activeUser}
        onClose={closeDialog}
        onChanged={invalidate}
      />

      <ResetTotpDialog
        open={activeDialog === "reset-totp"}
        user={activeUser}
        onClose={closeDialog}
        onReset={invalidate}
      />

      <ResetPasswordDialog
        open={activeDialog === "reset-password"}
        user={activeUser}
        onClose={closeDialog}
        onReset={invalidate}
      />

      {/* Re-invite dialog (US-10) */}
      <ReInviteDialog
        open={activeDialog === "re-invite"}
        user={activeUser}
        onClose={closeDialog}
        onReinvited={invalidate}
      />

      {/* Inline re-MFA for lock/unlock/revoke-sessions/deactivate */}
      <ConfirmReMfaDialog
        open={reMfaForAction}
        onClose={() => {
          setReMfaForAction(false);
          closeDialog();
        }}
        onConfirm={handleInlineReMfa}
        title={t("admin.action_confirm_title")}
        {...(activeUser
          ? { description: t("admin.action_confirm_description", { email: activeUser.email }) }
          : {})}
      />

      {/* US-102/104/105/108: detail drawer on row single-click — also the
          management hub: launches the same dialogs/re-MFA as the row menu. */}
      <UserDetailDrawer
        user={drawerUser}
        onClose={() => setDrawerUser(null)}
        onManageAction={(action, user) => {
          setDrawerUser(null);
          handleAction(action as DialogType, user);
        }}
      />
    </div>
  );
}

// ── FilterChip — used by Phase 3 filter bar (US-101) ─────────────────────────

function FilterChip({
  active,
  onClick,
  children,
  variant,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
  variant?: "green" | "red" | "amber" | "gray";
}) {
  const activeColor =
    variant === "green"
      ? "bg-green-100 text-green-800 border-green-300"
      : variant === "red"
        ? "bg-red-100 text-red-800 border-red-300"
        : variant === "amber"
          ? "bg-amber-100 text-amber-800 border-amber-300"
          : variant === "gray"
            ? "bg-gray-200 text-gray-700 border-gray-400"
            : "bg-primary/10 text-primary border-primary/30";

  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        "inline-flex items-center rounded-full border px-2.5 py-1 text-xs font-medium transition-colors",
        active ? activeColor : "border-input bg-background text-muted-foreground hover:bg-muted",
      )}
    >
      {children}
    </button>
  );
}
