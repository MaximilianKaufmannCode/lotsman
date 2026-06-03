// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * Calendar Subscriptions admin page — /admin/calendar-subscriptions
 *
 * US-3: whitelist of users to include in Exchange calendar events.
 * All mutating operations (add/remove) require a fresh TOTP code.
 */

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import {
  createColumnHelper,
  flexRender,
  getCoreRowModel,
  useReactTable,
} from "@tanstack/react-table";
import { format } from "date-fns";
import {
  ArrowLeft,
  Calendar,
  Copy,
  Loader2,
  Plus,
  Trash2,
} from "lucide-react";
import * as React from "react";
import {
  addCalendarSubscription,
  type CalendarSubscription,
  CalendarSubscriptionApiResponseError,
  listCalendarSubscriptions,
  removeCalendarSubscription,
} from "@/features/admin/calendar-subscriptions/api";
import { adminListUsers } from "@/features/auth/api";
import { cn } from "@/shared/lib/cn";
import { Button } from "@/shared/ui/button";
import { Dialog } from "@/shared/ui/dialog";
import { Input } from "@/shared/ui/input";
import { Label } from "@/shared/ui/label";
import { toast } from "@/shared/ui/toast";

// ── Column helper ─────────────────────────────────────────────────────────────

const col = createColumnHelper<CalendarSubscription>();

// ── Error code map ────────────────────────────────────────────────────────────

const SUBSCRIPTION_ERRORS: Record<string, string> = {
  USER_ALREADY_SUBSCRIBED: "Этот пользователь уже добавлен в подписчики.",
  SUBSCRIPTION_NOT_FOUND: "Подписка не найдена или уже удалена.",
  REMFA_REQUIRED: "Код TOTP обязателен для этой операции.",
  REMFA_REPLAY: "Этот TOTP-код уже был использован. Дождитесь следующего кода (30 с).",
};

function mapSubError(err: unknown): string {
  if (err instanceof CalendarSubscriptionApiResponseError && err.code) {
    return SUBSCRIPTION_ERRORS[err.code] ?? "Произошла ошибка. Попробуйте снова.";
  }
  return "Произошла ошибка подключения. Попробуйте снова.";
}

// ── Inline TOTP prompt ────────────────────────────────────────────────────────

interface TotpPromptProps {
  id: string;
  value: string;
  onChange: (v: string) => void;
  error?: string | null;
  autoFocus?: boolean;
}

function TotpPrompt({ id, value, onChange, error, autoFocus }: TotpPromptProps) {
  const inputRef = React.useRef<HTMLInputElement>(null);
  React.useEffect(() => {
    if (autoFocus && inputRef.current) inputRef.current.focus();
  }, [autoFocus]);

  const hintId = `${id}-hint`;
  const errId = `${id}-error`;

  return (
    <div className="flex flex-col gap-1.5">
      <Label htmlFor={id}>Код из приложения-аутентификатора</Label>
      <Input
        id={id}
        ref={inputRef}
        type="text"
        inputMode="numeric"
        autoComplete="one-time-code"
        maxLength={6}
        placeholder="123456"
        value={value}
        onChange={(e) => onChange(e.target.value.replace(/\D/g, "").slice(0, 6))}
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? errId : hintId}
        className={cn("font-mono text-center", error && "border-destructive")}
      />
      {error ? (
        <p id={errId} role="alert" className="text-xs text-destructive font-medium">
          {error}
        </p>
      ) : (
        <p id={hintId} className="text-xs text-muted-foreground">
          Подтвердите кодом из приложения-аутентификатора
        </p>
      )}
    </div>
  );
}

// ── Info banner (always visible, dismissible per session) ─────────────────────

function ExchangeInfoBanner() {
  return (
    <div
      role="note"
      className="rounded-lg bg-blue-50 border border-blue-300 text-blue-900 px-4 py-3 mb-6 text-sm"
    >
      <p className="font-medium">Как подписчик увидит события в Outlook</p>
      <ol className="mt-2 list-decimal list-inside space-y-0.5 text-xs">
        <li>
          Скопируй ссылку из колонки «Ссылка для Outlook» (кнопка-копия рядом).
        </li>
        <li>
          Передай её подписчику. Он в Outlook:
          <span className="ml-1 font-medium">
            Календарь → «Открыть календарь» → «Из Интернета…»
          </span>{" "}
          → вставить ссылку → ОК.
        </li>
        <li>
          Календарь подхватится автоматически. Outlook обновляет его раз в
          ~1–3&nbsp;часа без участия Лоцмана.
        </li>
      </ol>
      <p className="mt-2 text-xs text-blue-800">
        Ссылка персональная и привязана к этой подписке. После удаления подписчика
        ссылка перестаёт работать (Outlook покажет «нет данных» при следующем
        обновлении).
      </p>
    </div>
  );
}

// ── Add Subscriber dialog ─────────────────────────────────────────────────────

interface AddSubscriberDialogProps {
  open: boolean;
  existingUserIds: Set<string>;
  onClose: () => void;
  onAdded: () => void;
}

function AddSubscriberDialog({
  open,
  existingUserIds,
  onClose,
  onAdded,
}: AddSubscriberDialogProps) {
  const [totp, setTotp] = React.useState("");
  const [totpError, setTotpError] = React.useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = React.useState(false);
  const [selectedUserId, setSelectedUserId] = React.useState("");
  const [search, setSearch] = React.useState("");
  const [selectError, setSelectError] = React.useState<string | null>(null);

  const { data: allUsers } = useQuery({
    queryKey: ["admin", "users"],
    queryFn: adminListUsers,
    enabled: open,
    staleTime: 30_000,
  });

  // Filter: enabled, not already subscribed, match search
  const filteredUsers = React.useMemo(() => {
    const q = search.toLowerCase().trim();
    return (allUsers ?? [])
      .filter((u) => u.is_active && !existingUserIds.has(u.id))
      .filter(
        (u) => !q || u.full_name.toLowerCase().includes(q) || u.email.toLowerCase().includes(q),
      );
  }, [allUsers, existingUserIds, search]);

  React.useEffect(() => {
    if (open) {
      setTotp("");
      setTotpError(null);
      setSelectedUserId("");
      setSearch("");
      setSelectError(null);
    }
  }, [open]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    if (!selectedUserId) {
      setSelectError("Выберите пользователя");
      return;
    }
    if (totp.length !== 6) {
      setTotpError("Введите 6-значный TOTP-код");
      return;
    }

    setIsSubmitting(true);
    setTotpError(null);
    setSelectError(null);

    try {
      await addCalendarSubscription(selectedUserId, totp);
      toast.show({ title: "Подписчик добавлен", variant: "success" });
      onAdded();
      onClose();
    } catch (err) {
      if (
        err instanceof CalendarSubscriptionApiResponseError &&
        (err.code === "REMFA_REQUIRED" || err.code === "REMFA_REPLAY")
      ) {
        setTotp("");
        setTotpError(SUBSCRIPTION_ERRORS[err.code] ?? null);
      } else if (
        err instanceof CalendarSubscriptionApiResponseError &&
        err.code === "USER_ALREADY_SUBSCRIBED"
      ) {
        toast.show({
          title: "Этот пользователь уже добавлен в подписчики.",
          variant: "destructive",
        });
      } else {
        toast.show({ title: mapSubError(err), variant: "destructive" });
      }
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onClose={onClose} title="Добавить подписчика">
      <form onSubmit={handleSubmit} noValidate className="flex flex-col gap-4">
        {/* Search field */}
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="sub-search">Поиск пользователя</Label>
          <Input
            id="sub-search"
            type="search"
            placeholder="Имя или email..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            autoComplete="off"
          />
        </div>

        {/* User list */}
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="sub-user-select">Пользователь</Label>
          <div
            id="sub-user-select"
            role="listbox"
            aria-label="Выберите пользователя"
            aria-invalid={selectError ? true : undefined}
            aria-describedby={selectError ? "sub-user-select-error" : undefined}
            className={cn(
              "max-h-48 overflow-y-auto rounded-md border border-input bg-background",
              selectError && "border-destructive",
            )}
          >
            {filteredUsers.length === 0 ? (
              <p className="px-3 py-4 text-sm text-muted-foreground text-center">
                {allUsers === undefined ? "Загрузка..." : "Нет подходящих пользователей"}
              </p>
            ) : (
              filteredUsers.map((u) => (
                <button
                  key={u.id}
                  type="button"
                  role="option"
                  aria-selected={selectedUserId === u.id}
                  onClick={() => {
                    setSelectedUserId(u.id);
                    setSelectError(null);
                  }}
                  className={cn(
                    "w-full text-left px-3 py-2 text-sm flex flex-col gap-0.5",
                    "hover:bg-accent focus-visible:outline-none focus-visible:bg-accent",
                    selectedUserId === u.id && "bg-primary/10 text-primary",
                  )}
                >
                  <span className="font-medium">{u.full_name}</span>
                  <span className="text-xs text-muted-foreground">{u.email}</span>
                </button>
              ))
            )}
          </div>
          {selectError && (
            <p
              id="sub-user-select-error"
              role="alert"
              className="text-xs text-destructive font-medium"
            >
              {selectError}
            </p>
          )}
        </div>

        <TotpPrompt id="sub-totp" value={totp} onChange={setTotp} error={totpError} />

        <div className="flex justify-end gap-2">
          <Button type="button" variant="outline" onClick={onClose} disabled={isSubmitting}>
            Отмена
          </Button>
          <Button type="submit" disabled={isSubmitting} aria-busy={isSubmitting}>
            {isSubmitting ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                Добавление...
              </>
            ) : (
              "Добавить"
            )}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

// ── Remove confirmation dialog ─────────────────────────────────────────────────

interface RemoveSubscriberDialogProps {
  open: boolean;
  subscription: CalendarSubscription | null;
  subscriberLabel: string;
  onClose: () => void;
  onRemoved: () => void;
}

function RemoveSubscriberDialog({
  open,
  subscription,
  subscriberLabel,
  onClose,
  onRemoved,
}: RemoveSubscriberDialogProps) {
  const [totp, setTotp] = React.useState("");
  const [totpError, setTotpError] = React.useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = React.useState(false);

  React.useEffect(() => {
    if (open) {
      setTotp("");
      setTotpError(null);
    }
  }, [open]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!subscription) return;
    if (totp.length !== 6) {
      setTotpError("Введите 6-значный TOTP-код");
      return;
    }

    setIsSubmitting(true);
    setTotpError(null);

    try {
      await removeCalendarSubscription(subscription.user_id, totp);
      toast.show({ title: "Подписчик удалён", variant: "success" });
      onRemoved();
      onClose();
    } catch (err) {
      if (
        err instanceof CalendarSubscriptionApiResponseError &&
        (err.code === "REMFA_REQUIRED" || err.code === "REMFA_REPLAY")
      ) {
        setTotp("");
        setTotpError(SUBSCRIPTION_ERRORS[err.code] ?? null);
      } else {
        toast.show({ title: mapSubError(err), variant: "destructive" });
      }
    } finally {
      setIsSubmitting(false);
    }
  };

  if (!subscription) return null;

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Удалить подписчика"
      description={`Подписчик ${subscriberLabel} будет отключён от учёта в календаре.`}
    >
      <form onSubmit={handleSubmit} noValidate className="flex flex-col gap-4">
        <TotpPrompt
          id="remove-sub-totp"
          value={totp}
          onChange={setTotp}
          error={totpError}
          autoFocus
        />
        <div className="flex justify-end gap-2">
          <Button type="button" variant="outline" onClick={onClose} disabled={isSubmitting}>
            Отмена
          </Button>
          <Button
            type="submit"
            variant="destructive"
            disabled={totp.length !== 6 || isSubmitting}
            aria-busy={isSubmitting}
          >
            {isSubmitting ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                Удаление...
              </>
            ) : (
              "Удалить"
            )}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

// ── Skeleton ──────────────────────────────────────────────────────────────────

function TableSkeleton() {
  return (
    <div className="animate-pulse space-y-2" aria-busy="true" role="status">
      {Array.from({ length: 4 }).map((_, i) => (
        // biome-ignore lint/suspicious/noArrayIndexKey: skeleton rows have no stable identity
        <div key={i} className="flex gap-4 py-3 border-b border-border">
          <div className="h-4 w-40 rounded bg-muted" />
          <div className="h-4 w-24 rounded bg-muted" />
          <div className="h-4 w-28 rounded bg-muted" />
          <div className="h-4 w-32 rounded bg-muted" />
          <div className="h-4 w-16 rounded bg-muted ml-auto" />
        </div>
      ))}
    </div>
  );
}

// ── Role label ────────────────────────────────────────────────────────────────

const ROLE_LABEL: Record<string, string> = {
  admin: "Администратор",
  editor: "Редактор",
  viewer: "Читатель",
};

// ── Page ──────────────────────────────────────────────────────────────────────

export function CalendarSubscriptionsPage() {
  const qc = useQueryClient();
  const [addOpen, setAddOpen] = React.useState(false);
  const [removeTarget, setRemoveTarget] = React.useState<CalendarSubscription | null>(null);

  const {
    data: subscriptions,
    isLoading,
    isError,
  } = useQuery({
    queryKey: ["admin", "calendar-subscriptions"],
    queryFn: listCalendarSubscriptions,
    staleTime: 15_000,
  });

  // We also need role info — fetch from users list to display in table
  const { data: allUsers } = useQuery({
    queryKey: ["admin", "users"],
    queryFn: adminListUsers,
    staleTime: 60_000,
  });

  interface UserInfo {
    full_name: string;
    email: string;
    role: string;
  }
  const userInfoMap = React.useMemo(() => {
    const m = new Map<string, UserInfo>();
    for (const u of allUsers ?? []) {
      m.set(u.id, { full_name: u.full_name, email: u.email, role: u.role });
    }
    return m;
  }, [allUsers]);

  const existingUserIds = React.useMemo(
    () => new Set((subscriptions ?? []).map((s) => s.user_id)),
    [subscriptions],
  );

  const invalidate = () => qc.invalidateQueries({ queryKey: ["admin", "calendar-subscriptions"] });

  const columns = React.useMemo(
    () => [
      col.accessor("user_id", {
        id: "user",
        header: "ФИО и Email",
        cell: (info) => {
          const info_ = userInfoMap.get(info.getValue());
          if (!info_) {
            return (
              <div className="flex flex-col gap-0.5">
                <span className="text-sm text-muted-foreground italic">
                  Пользователь удалён
                </span>
                <span className="text-xs text-muted-foreground font-mono">
                  {info.getValue().slice(0, 8)}…
                </span>
              </div>
            );
          }
          return (
            <div className="flex flex-col gap-0.5">
              <span className="font-medium text-sm">{info_.full_name}</span>
              <span className="text-xs text-muted-foreground">{info_.email}</span>
            </div>
          );
        },
      }),
      col.accessor("user_id", {
        id: "role",
        header: "Роль",
        cell: (info) => {
          const role = userInfoMap.get(info.getValue())?.role ?? "—";
          return <span className="text-sm">{ROLE_LABEL[role] ?? role}</span>;
        },
      }),
      col.display({
        id: "ics_url",
        header: "Ссылка для Outlook",
        cell: (info) => {
          const sub = info.row.original;
          if (!sub.ics_feed_token) {
            return (
              <span className="text-xs text-muted-foreground italic">
                ссылка появится после миграции
              </span>
            );
          }
          const url = `${window.location.origin}/api/v1/calendar/feed/${sub.ics_feed_token}.ics`;
          return (
            <span className="inline-flex items-center gap-1.5">
              <code className="text-[11px] font-mono bg-muted px-1.5 py-0.5 rounded max-w-[280px] truncate inline-block">
                {url}
              </code>
              <Button
                variant="ghost"
                size="icon"
                aria-label="Скопировать ссылку"
                title="Скопировать ссылку"
                onClick={() => {
                  navigator.clipboard
                    .writeText(url)
                    .then(() =>
                      toast.show({
                        title: "Ссылка скопирована",
                        variant: "success",
                      }),
                    )
                    .catch(() =>
                      toast.show({
                        title: "Не удалось скопировать",
                        variant: "destructive",
                      }),
                    );
                }}
                className="text-muted-foreground hover:text-foreground"
              >
                <Copy className="h-3.5 w-3.5" aria-hidden />
              </Button>
            </span>
          );
        },
      }),
      col.accessor("created_at", {
        id: "subscribed_at",
        header: "Подписан с",
        cell: (info) => (
          <span className="text-sm text-muted-foreground whitespace-nowrap">
            {format(new Date(info.getValue()), "dd.MM.yyyy")}
          </span>
        ),
      }),
      col.accessor("created_by", {
        id: "created_by",
        header: "Кем добавлен",
        cell: (info) => {
          const adder = userInfoMap.get(info.getValue());
          return (
            <span className="text-sm text-muted-foreground whitespace-nowrap">
              {adder?.email ?? "—"}
            </span>
          );
        },
      }),
      col.display({
        id: "actions",
        header: "Действия",
        cell: (info) => {
          const ui = userInfoMap.get(info.row.original.user_id);
          const sub = info.row.original;
          return (
            <span className="inline-flex items-center gap-1">
              <Button
                variant="ghost"
                size="icon"
                aria-label={`Удалить подписчика ${ui?.email ?? sub.user_id}`}
                onClick={() => setRemoveTarget(sub)}
                className="text-destructive hover:text-destructive hover:bg-destructive/10"
              >
                <Trash2 className="h-4 w-4" aria-hidden />
              </Button>
            </span>
          );
        },
      }),
    ],
    [userInfoMap],
  );

  const table = useReactTable({
    data: subscriptions ?? [],
    columns,
    getCoreRowModel: getCoreRowModel(),
  });

  return (
    <div className="max-w-7xl mx-auto px-6 py-8">
      {/* Back link */}
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
        className="mb-4 inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-primary transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded"
      >
        <ArrowLeft className="h-4 w-4" aria-hidden />
        Назад в реестр
      </Link>

      {/* Page header */}
      <div className="mb-6 flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-2xl font-semibold">Подписчики календаря</h1>
          <p className="mt-1 text-sm text-muted-foreground max-w-2xl">
            Сотрудники, для которых Лоцман учитывает подписку. Просмотр самих событий настраивается
            отдельно через sharing-доступ к ящику в Exchange (см. документацию).
          </p>
        </div>
        <Button onClick={() => setAddOpen(true)} className="shrink-0">
          <Plus className="h-4 w-4 mr-2" aria-hidden />
          Добавить подписчика
        </Button>
      </div>

      {/* Exchange info banner */}
      <ExchangeInfoBanner />

      {/* Error */}
      {isError && (
        <div
          role="alert"
          className="rounded bg-destructive/10 border border-destructive px-3 py-2 mb-6"
        >
          <p className="text-sm text-destructive">Не удалось загрузить список подписчиков.</p>
        </div>
      )}

      {/* Table */}
      {isLoading ? (
        <TableSkeleton />
      ) : subscriptions && subscriptions.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-16 gap-3 text-center">
          <Calendar className="h-10 w-10 text-muted-foreground" aria-hidden />
          <p className="text-muted-foreground text-sm">
            Нет подписчиков. Добавьте первого, чтобы Лоцман начал учитывать его в события календаря.
          </p>
        </div>
      ) : (
        <div className="rounded-md border border-border overflow-x-auto">
          <table className="w-full text-sm" aria-label="Список подписчиков календаря">
            <thead>
              {table.getHeaderGroups().map((headerGroup) => (
                <tr key={headerGroup.id} className="border-b border-border bg-muted/50">
                  {headerGroup.headers.map((header) => (
                    <th
                      key={header.id}
                      className={cn(
                        "px-4 py-3 text-left text-xs font-semibold text-muted-foreground uppercase tracking-wide whitespace-nowrap",
                        header.id === "actions" && "w-16 text-right",
                      )}
                    >
                      {header.isPlaceholder
                        ? null
                        : flexRender(header.column.columnDef.header, header.getContext())}
                    </th>
                  ))}
                </tr>
              ))}
            </thead>
            <tbody>
              {table.getRowModel().rows.map((row) => (
                <tr
                  key={row.id}
                  className="border-b border-border last:border-0 hover:bg-muted/30 transition-colors"
                >
                  {row.getVisibleCells().map((cell) => (
                    <td
                      key={cell.id}
                      className={cn(
                        "px-4 py-3",
                        cell.column.id === "actions" && "w-16 text-right",
                      )}
                    >
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Add subscriber dialog */}
      <AddSubscriberDialog
        open={addOpen}
        existingUserIds={existingUserIds}
        onClose={() => setAddOpen(false)}
        onAdded={invalidate}
      />

      {/* Remove subscriber dialog */}
      <RemoveSubscriberDialog
        open={removeTarget !== null}
        subscription={removeTarget}
        subscriberLabel={(() => {
          if (!removeTarget) return "";
          const ui = userInfoMap.get(removeTarget.user_id);
          return ui ? `${ui.full_name} (${ui.email})` : removeTarget.user_id;
        })()}
        onClose={() => setRemoveTarget(null)}
        onRemoved={invalidate}
      />
    </div>
  );
}
