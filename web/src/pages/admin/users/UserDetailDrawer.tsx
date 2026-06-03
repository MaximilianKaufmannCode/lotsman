// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * UserDetailDrawer — side drawer for admin user-management (US-102, US-104, US-105, US-108).
 *
 * Opens on row single-click in UsersPage. Mirrors DocumentDetailDrawer pattern:
 * - Right-side drawer, backdrop click + Esc close.
 * - Sticky CTA toolbar near header (Fitts's-law fix carried from v1.13.20).
 * - 3 tabs: Профиль · Сессии · История.
 * - Reactivate banner if user.is_active === false.
 *
 * Keyboard:
 *   Esc           → close
 *   Tab/Shift-Tab → cycle inside drawer (focus trap inherited from parent app)
 *
 * Mutations carried by this drawer (others stay in the row kebab from UsersPage):
 *   - Reactivate user (US-104)         → adminReactivateUser
 *   - Revoke specific session (US-105) → adminRevokeSession
 *
 * Phase 1 scope: read-only profile view + sessions + history + reactivate.
 * Phase 2 will add edit-profile form (full_name / email) per BA §7.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { format, parseISO } from "date-fns";
import { ru } from "date-fns/locale";
import {
  Ban,
  History,
  KeyRound,
  Loader2,
  Lock,
  LogOut,
  Monitor,
  Pencil,
  Send,
  Shield,
  ShieldOff,
  Smartphone,
  Trash2,
  Unlock,
  UserCheck,
  X,
} from "lucide-react";
import * as React from "react";
import { computeUserStatus } from "@/features/admin/userStatus";
import {
  ApiResponseError,
  adminGetUser,
  adminGetUserSessions,
  adminReactivateUser,
  adminRevokeSession,
  adminUpdateUserProfile,
} from "@/features/auth/api";
import type { AdminUser, AdminUserDetail, AdminUserSession } from "@/features/auth/types";
import { cn } from "@/shared/lib/cn";
import { Button } from "@/shared/ui/button";
import { Input } from "@/shared/ui/input";
import { Skeleton } from "@/shared/ui/skeleton";
import { toast } from "@/shared/ui/toast";
import { ConfirmReMfaDialog } from "./components/ConfirmReMfaDialog";

// ── Types ─────────────────────────────────────────────────────────────────────

type DrawerTab = "profile" | "sessions" | "history";

interface AuditEvent {
  id: string;
  occurred_at: string;
  event_type: string;
  actor_id: string;
  actor_name?: string;
  payload: Record<string, unknown>;
}

/** Management actions the drawer can launch — mirror UsersPage DialogType. */
export type UserManageAction =
  | "change-role"
  | "reset-password"
  | "reset-totp"
  | "lock"
  | "unlock"
  | "revoke-sessions"
  | "re-invite"
  | "deactivate"
  | "delete";

interface UserDetailDrawerProps {
  user: AdminUser | null;
  onClose: () => void;
  /** Launch a management action (opens the matching dialog / re-MFA in UsersPage). */
  onManageAction?: (action: UserManageAction, user: AdminUser) => void;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

const ROLE_LABEL_RU: Record<string, string> = {
  super_admin: "Супер-админ",
  admin: "Администратор",
  editor: "Редактор",
  viewer: "Наблюдатель",
};

const SYSTEM_ACTOR_UUID = "00000000-0000-0000-0000-000000000000";

function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  try {
    return format(parseISO(iso), "d MMM yyyy, HH:mm", { locale: ru });
  } catch {
    return iso;
  }
}

function initials(fullName: string): string {
  return fullName
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((s) => s[0]?.toUpperCase() ?? "")
    .join("");
}

function statusText(user: AdminUserDetail | AdminUser): {
  text: string;
  className: string;
  dot: string;
} {
  // Single source of truth — same helper as the list badge + filter.
  const s = computeUserStatus(user);
  return { text: s.label, className: s.textClass, dot: s.dotClass };
}

function humaniseEventType(eventType: string): string {
  const map: Record<string, string> = {
    "auth.user.invited.v1": "Приглашён в систему",
    "auth.user.activated.v1": "Восстановлен",
    "auth.user.deactivated.v1": "Деактивирован",
    "auth.user.logged_in.v1": "Вошёл в систему",
    "auth.user.password_reset.v1": "Сменил пароль",
    "auth.user.totp_enrolled.v1": "Настроил TOTP",
    "auth.user.totp_reset.v1": "TOTP сброшен",
    "auth.user.backup_codes_regenerated.v1": "Backup-коды перевыпущены",
    "auth.user.profile_updated.v1": "Обновлён профиль",
    "auth.user.email_change_requested.v1": "Запрошена смена email",
    "auth.user.email_changed.v1": "Email изменён",
    "auth.user.locked.v1": "Заблокирован",
    "auth.user.unlocked.v1": "Разблокирован",
    "auth.user.role_changed.v1": "Сменена роль",
    "auth.session.revoked.v1": "Сессия отозвана",
    "auth.session.revoked_all.v1": "Все сессии отозваны",
    "auth.invitation.resent.v1": "Приглашение переотправлено",
  };
  return map[eventType] ?? eventType;
}

// ── Main drawer ───────────────────────────────────────────────────────────────

export function UserDetailDrawer({ user, onClose, onManageAction }: UserDetailDrawerProps) {
  const [activeTab, setActiveTab] = React.useState<DrawerTab>("profile");
  const [isEditing, setIsEditing] = React.useState(false);
  const [reMfaConfirm, setReMfaConfirm] = React.useState<{
    title: string;
    description: string;
    onConfirm: (totp: string) => Promise<void>;
  } | null>(null);
  const drawerRef = React.useRef<HTMLDivElement>(null);
  const queryClient = useQueryClient();

  // Reset tab + edit mode when user changes
  React.useEffect(() => {
    setActiveTab("profile");
    setIsEditing(false);
  }, [user?.id]);

  // Esc to close
  React.useEffect(() => {
    if (!user) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !reMfaConfirm) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [user, onClose, reMfaConfirm]);

  // Focus first action on open
  React.useEffect(() => {
    if (user && drawerRef.current) {
      drawerRef.current.querySelector<HTMLButtonElement>("[data-drawer-primary]")?.focus();
    }
  }, [user?.id]);

  // Detail query — always fetches up-to-date data (e.g. must_change_password, updated_at)
  const detailQuery = useQuery({
    queryKey: ["admin", "user-detail", user?.id],
    queryFn: () => adminGetUser(user!.id),
    enabled: !!user,
    staleTime: 30_000,
  });

  const reactivateMut = useMutation({
    mutationFn: ({ userId, totp }: { userId: string; totp: string }) =>
      adminReactivateUser(userId, totp),
    onSuccess: async () => {
      toast.show({ title: "Пользователь восстановлен", variant: "success" });
      await queryClient.invalidateQueries({ queryKey: ["admin", "users"] });
      await queryClient.invalidateQueries({ queryKey: ["admin", "user-detail"] });
    },
    onError: (e: unknown) => {
      const msg =
        e instanceof ApiResponseError
          ? (e.detail ?? "Не удалось восстановить пользователя")
          : "Не удалось восстановить пользователя";
      toast.show({ title: msg, variant: "destructive" });
    },
  });

  if (!user) return null;

  const detail = detailQuery.data;
  const sourceUser: AdminUserDetail | AdminUser = detail ?? user;
  const status = statusText(sourceUser);

  return (
    <>
      <div className="fixed inset-0 z-40 bg-black/30" aria-hidden="true" onClick={onClose} />

      <div
        ref={drawerRef}
        role="dialog"
        aria-modal="true"
        aria-label={`Карточка пользователя ${sourceUser.full_name}`}
        className={cn(
          "fixed inset-y-0 right-0 z-50 flex w-full max-w-xl flex-col bg-card shadow-xl",
          "border-l border-border",
          "animate-in slide-in-from-right duration-200",
        )}
      >
        {/* Header */}
        <div className="flex items-start justify-between gap-4 border-b p-4">
          <div className="flex items-center gap-3 min-w-0">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-blue-100 text-sm font-medium text-blue-700">
              {initials(sourceUser.full_name)}
            </div>
            <div className="min-w-0">
              <h2 className="truncate text-lg font-semibold">{sourceUser.full_name}</h2>
              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                <span className="truncate">
                  {ROLE_LABEL_RU[sourceUser.role] ?? sourceUser.role}
                </span>
                <span>·</span>
                <span className={cn("inline-flex items-center gap-1.5", status.className)}>
                  <span aria-hidden="true" className={cn("h-2 w-2 rounded-full", status.dot)} />
                  {status.text}
                </span>
              </div>
            </div>
          </div>
          <Button variant="ghost" size="icon" onClick={onClose} aria-label="Закрыть карточку">
            <X className="h-4 w-4" />
          </Button>
        </div>

        {/* Reactivate banner — US-104. Only for admin-deactivated users, NOT
            system service accounts (which are protected & unmanageable). */}
        {computeUserStatus(sourceUser).key === "deactivated" && (
          <div className="flex items-center justify-between gap-3 border-b bg-gray-50 px-4 py-3 dark:bg-gray-900">
            <div className="flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300">
              <ShieldOff className="h-4 w-4 shrink-0" />
              <span>Аккаунт деактивирован. Войти невозможно.</span>
            </div>
            <Button
              data-drawer-primary
              variant="default"
              size="sm"
              onClick={() => {
                setReMfaConfirm({
                  title: "Восстановить пользователя",
                  description: `Восстановить ${sourceUser.email}? Аккаунт снова сможет войти в систему.`,
                  onConfirm: async (totp) => {
                    await reactivateMut.mutateAsync({ userId: sourceUser.id, totp });
                  },
                });
              }}
              disabled={reactivateMut.isPending}
            >
              {reactivateMut.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <UserCheck className="h-4 w-4" />
              )}
              Восстановить
            </Button>
          </div>
        )}

        {/* Tabs */}
        <div role="tablist" aria-label="Разделы карточки" className="flex border-b">
          <TabBtn
            isActive={activeTab === "profile"}
            onClick={() => setActiveTab("profile")}
            icon={<KeyRound className="h-4 w-4" />}
            label="Профиль"
          />
          <TabBtn
            isActive={activeTab === "sessions"}
            onClick={() => setActiveTab("sessions")}
            icon={<Monitor className="h-4 w-4" />}
            label="Сессии"
          />
          <TabBtn
            isActive={activeTab === "history"}
            onClick={() => setActiveTab("history")}
            icon={<History className="h-4 w-4" />}
            label="История"
          />
        </div>

        {/* Tab content */}
        <div className="flex-1 overflow-y-auto p-4">
          {activeTab === "profile" && (
            <ProfileTab
              detail={detail}
              isLoading={detailQuery.isLoading}
              fallback={user}
              isEditing={isEditing}
              setIsEditing={setIsEditing}
              userId={sourceUser.id}
              canEdit={sourceUser.is_active}
              onEditSaved={() => {
                queryClient.invalidateQueries({ queryKey: ["admin", "users"] });
                queryClient.invalidateQueries({
                  queryKey: ["admin", "user-detail", sourceUser.id],
                });
              }}
              setReMfaConfirm={setReMfaConfirm}
              rowUser={user}
              onManageAction={onManageAction}
            />
          )}
          {activeTab === "sessions" && (
            <SessionsTab
              userId={sourceUser.id}
              isUserActive={sourceUser.is_active}
              onRequestRevoke={(sessionId) => {
                setReMfaConfirm({
                  title: "Отозвать сессию",
                  description: `Отозвать выбранную сессию пользователя ${sourceUser.email}?`,
                  onConfirm: async (totp) => {
                    await adminRevokeSession(sourceUser.id, sessionId, totp);
                    toast.show({ title: "Сессия отозвана", variant: "success" });
                    await queryClient.invalidateQueries({
                      queryKey: ["admin", "user-sessions", sourceUser.id],
                    });
                  },
                });
              }}
            />
          )}
          {activeTab === "history" && <HistoryTab userId={sourceUser.id} />}
        </div>
      </div>

      <ConfirmReMfaDialog
        open={!!reMfaConfirm}
        onClose={() => setReMfaConfirm(null)}
        onConfirm={(token) => {
          if (!reMfaConfirm) return;
          // Fire-and-forget; errors handled inside individual onConfirm callbacks via toast.
          void (async () => {
            try {
              await reMfaConfirm.onConfirm(token);
              setReMfaConfirm(null);
            } catch (e) {
              const msg =
                e instanceof ApiResponseError
                  ? (e.detail ?? "Операция не выполнена")
                  : "Операция не выполнена";
              toast.show({ title: msg, variant: "destructive" });
            }
          })();
        }}
        title={reMfaConfirm?.title}
        description={reMfaConfirm?.description}
      />
    </>
  );
}

// ── Tab button ────────────────────────────────────────────────────────────────

function TabBtn({
  isActive,
  onClick,
  icon,
  label,
}: {
  isActive: boolean;
  onClick: () => void;
  icon: React.ReactNode;
  label: string;
}) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={isActive}
      onClick={onClick}
      className={cn(
        "flex flex-1 items-center justify-center gap-2 border-b-2 px-3 py-2.5 text-sm font-medium transition-colors",
        isActive
          ? "border-blue-600 text-blue-700"
          : "border-transparent text-muted-foreground hover:text-foreground",
      )}
    >
      {icon}
      {label}
    </button>
  );
}

// ── Profile tab ───────────────────────────────────────────────────────────────

function ProfileTab({
  detail,
  isLoading,
  fallback,
  isEditing,
  setIsEditing,
  userId,
  canEdit,
  onEditSaved,
  setReMfaConfirm,
  rowUser,
  onManageAction,
}: {
  detail: AdminUserDetail | undefined;
  isLoading: boolean;
  fallback: AdminUser;
  isEditing: boolean;
  setIsEditing: (v: boolean) => void;
  userId: string;
  canEdit: boolean;
  onEditSaved: () => void;
  setReMfaConfirm: (
    v: {
      title: string;
      description: string;
      onConfirm: (totp: string) => Promise<void>;
    } | null,
  ) => void;
  rowUser: AdminUser;
  onManageAction: ((action: UserManageAction, user: AdminUser) => void) | undefined;
}) {
  const u = detail ?? fallback;
  const [draftFullName, setDraftFullName] = React.useState(u.full_name);
  const [isSaving, setIsSaving] = React.useState(false);

  React.useEffect(() => {
    setDraftFullName(u.full_name);
  }, [u.full_name, isEditing]);

  if (isLoading && !detail) {
    return (
      <div className="space-y-3">
        {Array.from({ length: 7 }).map((_, i) => (
          <Skeleton key={i} className="h-8 w-full" />
        ))}
      </div>
    );
  }

  // Edit mode
  if (isEditing) {
    const trimmed = draftFullName.trim();
    const isInvalid = trimmed.length === 0 || trimmed.length > 200;
    const isUnchanged = trimmed === u.full_name;

    return (
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (isInvalid || isUnchanged) return;
          setReMfaConfirm({
            title: "Сохранить изменения профиля",
            description: `Обновить ФИО для ${u.email}?`,
            onConfirm: async (totp) => {
              setIsSaving(true);
              try {
                await adminUpdateUserProfile(userId, { full_name: trimmed }, totp);
                toast.show({ title: "Профиль обновлён", variant: "success" });
                setIsEditing(false);
                onEditSaved();
              } catch (e) {
                const msg =
                  e instanceof ApiResponseError
                    ? (e.detail ?? "Не удалось сохранить")
                    : "Не удалось сохранить";
                toast.show({ title: msg, variant: "destructive" });
                throw e;
              } finally {
                setIsSaving(false);
              }
            },
          });
        }}
        className="space-y-4"
      >
        <div>
          <label htmlFor="profile-full-name" className="text-sm font-medium block mb-1">
            ФИО *
          </label>
          <Input
            id="profile-full-name"
            value={draftFullName}
            onChange={(e) => setDraftFullName(e.target.value)}
            placeholder="Иванов Иван Иванович"
            autoFocus
            maxLength={200}
            aria-invalid={isInvalid}
          />
          {isInvalid && (
            <p className="text-xs text-destructive mt-1">
              {trimmed.length === 0 ? "ФИО не может быть пустым" : "Максимум 200 символов"}
            </p>
          )}
        </div>
        <div>
          <span className="text-sm text-muted-foreground">Email</span>
          <p className="text-sm">{u.email}</p>
          <p className="text-xs text-muted-foreground mt-0.5">
            Смена email пока недоступна через интерфейс (нужен отдельный flow с верификацией).
          </p>
        </div>
        <div className="flex justify-end gap-2 pt-2 border-t">
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => {
              setDraftFullName(u.full_name);
              setIsEditing(false);
            }}
            disabled={isSaving}
          >
            Отмена
          </Button>
          <Button type="submit" size="sm" disabled={isInvalid || isUnchanged || isSaving}>
            {isSaving ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
            Сохранить
          </Button>
        </div>
      </form>
    );
  }

  // Read mode
  const fields: Array<[string, React.ReactNode]> = [
    ["ФИО", u.full_name],
    ["Email", u.email],
    ["Роль", ROLE_LABEL_RU[u.role] ?? u.role],
    [
      "TOTP",
      u.totp_enrolled ? (
        <span className="text-green-700">настроен</span>
      ) : (
        <span className="text-amber-600">не настроен</span>
      ),
    ],
    [
      "Принудит. смена пароля",
      detail?.must_change_password ? (
        <span className="text-amber-600">да</span>
      ) : (
        <span className="text-muted-foreground">нет</span>
      ),
    ],
    ["Создан", fmtDateTime(u.created_at)],
    detail?.updated_at ? ["Обновлён", fmtDateTime(detail.updated_at)] : ["", ""],
    ["Последний вход", fmtDateTime(u.last_login_at)],
  ];

  return (
    <>
      {canEdit && (
        <div className="mb-3 flex justify-end">
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => setIsEditing(true)}
            className="gap-1.5"
          >
            <Pencil className="h-3.5 w-3.5" />
            Редактировать
          </Button>
        </div>
      )}
      <dl className="space-y-2 text-sm">
        {fields
          .filter(([k]) => k !== "")
          .map(([label, value]) => (
            <div key={String(label)} className="grid grid-cols-[10rem_1fr] items-start gap-3 py-1">
              <dt className="text-muted-foreground">{label}</dt>
              <dd className="break-words">{value}</dd>
            </div>
          ))}
      </dl>

      {onManageAction && <ActionsSection user={rowUser} onManageAction={onManageAction} />}
    </>
  );
}

// ── Management actions section (the per-user hub) ────────────────────────────

function ActionsSection({
  user,
  onManageAction,
}: {
  user: AdminUser;
  onManageAction: (action: UserManageAction, user: AdminUser) => void;
}) {
  const isSystem = user.email.endsWith("@system.lotsman");
  if (isSystem) {
    return (
      <div className="mt-6 rounded-md border border-dashed border-border p-3 text-xs text-muted-foreground">
        Системная служебная учётная запись — управление недоступно.
      </div>
    );
  }

  const isPending = user.is_active && !user.totp_enrolled;
  const run = (a: UserManageAction) => onManageAction(a, user);

  // Deactivated account: role / lockout / session actions are meaningless on a
  // disabled login, and «Активировать» is offered by the «Восстановить» banner
  // above. Expose only permanent removal here to keep the choice unambiguous.
  if (!user.is_active) {
    return (
      <div className="mt-6 space-y-4 border-t border-border pt-4">
        <ActionGroup title="Опасные действия" danger>
          <ActionRow
            icon={<Trash2 className="h-4 w-4" />}
            label="Удалить пользователя"
            onClick={() => run("delete")}
            danger
          />
        </ActionGroup>
      </div>
    );
  }

  return (
    <div className="mt-6 space-y-4 border-t border-border pt-4">
      <ActionGroup title="Доступ">
        <ActionRow
          icon={<Shield className="h-4 w-4" />}
          label="Сменить роль"
          onClick={() => run("change-role")}
        />
        <ActionRow
          icon={<KeyRound className="h-4 w-4" />}
          label="Сбросить пароль"
          onClick={() => run("reset-password")}
        />
        <ActionRow
          icon={<Smartphone className="h-4 w-4" />}
          label="Сбросить TOTP"
          onClick={() => run("reset-totp")}
        />
      </ActionGroup>

      <ActionGroup title="Безопасность">
        {user.is_locked ? (
          <ActionRow
            icon={<Unlock className="h-4 w-4" />}
            label="Разблокировать"
            onClick={() => run("unlock")}
          />
        ) : (
          <ActionRow
            icon={<Lock className="h-4 w-4" />}
            label="Заблокировать"
            onClick={() => run("lock")}
          />
        )}
        <ActionRow
          icon={<LogOut className="h-4 w-4" />}
          label="Завершить все сессии"
          onClick={() => run("revoke-sessions")}
        />
        {isPending && (
          <ActionRow
            icon={<Send className="h-4 w-4" />}
            label="Повторно пригласить"
            onClick={() => run("re-invite")}
          />
        )}
      </ActionGroup>

      <ActionGroup title="Опасные действия" danger>
        <ActionRow
          icon={<Ban className="h-4 w-4" />}
          label="Деактивировать"
          onClick={() => run("deactivate")}
          danger
        />
        <ActionRow
          icon={<Trash2 className="h-4 w-4" />}
          label="Удалить пользователя"
          onClick={() => run("delete")}
          danger
        />
      </ActionGroup>
    </div>
  );
}

function ActionGroup({
  title,
  danger,
  children,
}: {
  title: string;
  danger?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div>
      <h4
        className={cn(
          "mb-1.5 text-xs font-semibold uppercase tracking-wide",
          danger ? "text-destructive/80" : "text-muted-foreground",
        )}
      >
        {title}
      </h4>
      <div className="overflow-hidden rounded-md border border-border divide-y divide-border">
        {children}
      </div>
    </div>
  );
}

function ActionRow({
  icon,
  label,
  onClick,
  danger,
}: {
  icon: React.ReactNode;
  label: string;
  onClick: () => void;
  danger?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "flex w-full items-center gap-2.5 px-3 py-2.5 text-sm text-left transition-colors",
        "hover:bg-accent focus-visible:outline-none focus-visible:bg-accent",
        danger ? "text-destructive" : "text-foreground",
      )}
    >
      <span className={cn("shrink-0", danger ? "text-destructive" : "text-muted-foreground")}>
        {icon}
      </span>
      {label}
    </button>
  );
}

// ── Sessions tab ──────────────────────────────────────────────────────────────

function SessionsTab({
  userId,
  isUserActive,
  onRequestRevoke,
}: {
  userId: string;
  isUserActive: boolean;
  onRequestRevoke: (sessionId: string) => void;
}) {
  const sessQuery = useQuery({
    queryKey: ["admin", "user-sessions", userId],
    queryFn: () => adminGetUserSessions(userId),
    enabled: isUserActive,
    staleTime: 15_000,
  });

  if (!isUserActive) {
    return (
      <div className="rounded border border-dashed p-4 text-center text-sm text-muted-foreground">
        Пользователь деактивирован — активных сессий нет.
      </div>
    );
  }

  if (sessQuery.isLoading) {
    return (
      <div className="space-y-2">
        {Array.from({ length: 3 }).map((_, i) => (
          <Skeleton key={i} className="h-14 w-full" />
        ))}
      </div>
    );
  }

  if (sessQuery.isError) {
    return (
      <div className="rounded border border-red-300 bg-red-50 p-3 text-sm text-red-700">
        Не удалось загрузить сессии: {String((sessQuery.error as Error)?.message ?? "ошибка")}
      </div>
    );
  }

  const sessions: AdminUserSession[] = (sessQuery.data ?? []).filter((s) => !s.revoked_at);

  if (sessions.length === 0) {
    return (
      <div className="rounded border border-dashed p-4 text-center text-sm text-muted-foreground">
        Активных сессий нет.
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <div className="text-sm text-muted-foreground">
        Активных сессий: <span className="font-medium text-foreground">{sessions.length}</span>
      </div>
      <ul className="divide-y rounded border">
        {sessions.map((s) => (
          <li key={s.id} className="flex items-start justify-between gap-3 p-3 text-sm">
            <div className="min-w-0">
              <div className="text-xs font-mono text-muted-foreground">{s.id.slice(0, 8)}…</div>
              <div className="mt-1">
                <span className="text-muted-foreground">Создана: </span>
                {fmtDateTime(s.created_at)}
              </div>
              <div>
                <span className="text-muted-foreground">Истекает: </span>
                {fmtDateTime(s.expires_at)}
              </div>
              {s.user_agent && (
                <div className="mt-1 truncate text-xs text-muted-foreground">{s.user_agent}</div>
              )}
              {s.ip_address && (
                <div className="text-xs text-muted-foreground">IP: {s.ip_address}</div>
              )}
            </div>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => onRequestRevoke(s.id)}
              aria-label={`Отозвать сессию ${s.id.slice(0, 8)}`}
            >
              Отозвать
            </Button>
          </li>
        ))}
      </ul>
    </div>
  );
}

// ── History tab ───────────────────────────────────────────────────────────────

function HistoryTab({ userId }: { userId: string }) {
  // Re-use the same audit endpoint pattern as DocumentDetailDrawer/HistoryTab
  const histQuery = useQuery({
    queryKey: ["admin", "user-history", userId],
    queryFn: async () => {
      const resp = await fetch(
        `/api/v1/audit/events?entity_type=user&entity_id=${userId}&limit=200`,
        {
          credentials: "include",
        },
      );
      if (!resp.ok) throw new Error(`audit ${resp.status}`);
      const data = (await resp.json()) as { items?: AuditEvent[] } | AuditEvent[];
      return Array.isArray(data) ? data : (data.items ?? []);
    },
    staleTime: 30_000,
  });

  if (histQuery.isLoading) {
    return (
      <div className="space-y-2">
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} className="h-12 w-full" />
        ))}
      </div>
    );
  }

  if (histQuery.isError) {
    return (
      <div className="rounded border border-red-300 bg-red-50 p-3 text-sm text-red-700">
        Не удалось загрузить историю
      </div>
    );
  }

  const events = histQuery.data ?? [];
  if (events.length === 0) {
    return (
      <div className="rounded border border-dashed p-4 text-center text-sm text-muted-foreground">
        История пока пуста.
      </div>
    );
  }

  // Group by day
  const groups = new Map<string, AuditEvent[]>();
  for (const ev of events) {
    const day = format(parseISO(ev.occurred_at), "d MMMM yyyy", { locale: ru });
    const arr = groups.get(day) ?? [];
    arr.push(ev);
    groups.set(day, arr);
  }

  return (
    <div className="space-y-4 text-sm">
      {Array.from(groups.entries()).map(([day, items]) => (
        <section key={day}>
          <h4 className="sticky top-0 z-10 -mx-4 mb-1 bg-card px-4 py-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">
            {day}
          </h4>
          <ul className="space-y-1.5">
            {items.map((ev) => (
              <li
                key={ev.id}
                className="flex items-start gap-3 rounded px-2 py-1.5 hover:bg-accent/40"
              >
                <span className="w-12 shrink-0 text-xs font-mono text-muted-foreground">
                  {format(parseISO(ev.occurred_at), "HH:mm")}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="font-medium">{humaniseEventType(ev.event_type)}</div>
                  <div className="text-xs text-muted-foreground">
                    Актор:{" "}
                    {ev.actor_id === SYSTEM_ACTOR_UUID
                      ? "Система"
                      : (ev.actor_name ?? ev.actor_id.slice(0, 8) + "…")}
                  </div>
                </div>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}
