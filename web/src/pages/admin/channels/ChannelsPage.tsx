// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * Channels administration page — /admin/channels
 *
 * US-2..6 (configure email/telegram/dion, test, toggle enable).
 * Phase 3 adds Exchange Calendar and ICS Feed channels (US-1, US-2).
 *
 * All mutating operations require a fresh TOTP code (totp_code in body, not re_mfa_token).
 * GET does NOT return secrets — forms are always blank (security per US-2 design).
 */

import { zodResolver } from "@hookform/resolvers/zod";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { format } from "date-fns";
import {
  AlertTriangle,
  Calendar,
  Check,
  Copy,
  ExternalLink,
  Loader2,
  Mail,
  MessageCircle,
  Rss,
  Webhook,
  X,
} from "lucide-react";
import * as React from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";
import {
  ChannelApiResponseError,
  type ChannelInfo,
  type ChannelName,
  type ExchangeCalendarConfig,
  getChannelConfig,
  type IcsFeedConfig,
  listChannels,
  patchChannel,
  setChannel,
  testChannel,
} from "@/features/admin/channels/api";
import { cn } from "@/shared/lib/cn";
import { Badge } from "@/shared/ui/badge";
import { Button } from "@/shared/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/shared/ui/card";
import { Dialog } from "@/shared/ui/dialog";
import { Input } from "@/shared/ui/input";
import { Label } from "@/shared/ui/label";
import { toast } from "@/shared/ui/toast";

// ── Error code → Russian message map ─────────────────────────────────────────

// Module-level constants — stable references safe for useMemo deps
const ALL_CHANNELS: ChannelName[] = ["email", "telegram", "dion", "exchange_calendar", "ics_feed"];

function defaultChannelInfo(ch: ChannelName): ChannelInfo {
  return {
    channel: ch,
    enabled: false,
    configured: false,
    status: "not_configured",
    updated_at: null,
  };
}

const ERROR_MESSAGES: Record<string, string> = {
  REMFA_REQUIRED: "Код TOTP обязателен для этой операции.",
  REMFA_REPLAY: "Этот TOTP-код уже был использован. Дождитесь следующего кода (30 с).",
  PENDING_INVITES: "Невозможно отключить канал — есть неподтверждённые приглашения через него.",
  MIN_ADMINS: "Должен оставаться минимум 1 активный администратор.",
  NO_CHANNEL: "Нет включённых каналов. Сначала настройте и включите хотя бы один канал.",
  SECRET_REQUIRED: "Секрет обязателен при первоначальной настройке канала.",
};

function mapError(err: unknown): string {
  if (err instanceof ChannelApiResponseError && err.code) {
    return ERROR_MESSAGES[err.code] ?? "Произошла ошибка. Попробуйте снова.";
  }
  return "Произошла ошибка подключения. Попробуйте снова.";
}

function lookupErrorMsg(code: string): string | null {
  return ERROR_MESSAGES[code] ?? null;
}

// ── Multi-channel warning banner ──────────────────────────────────────────────

const MULTI_CHANNEL_BANNER_KEY = "multi-channel-warning-dismissed";

const CHANNEL_DISPLAY_NAME: Record<ChannelName, string> = {
  email: "Email",
  telegram: "Telegram",
  dion: "Dion",
  exchange_calendar: "Календарь Exchange",
  ics_feed: "ICS подписка",
};

interface MultiChannelWarningBannerProps {
  channels: ChannelInfo[];
}

function MultiChannelWarningBanner({ channels }: MultiChannelWarningBannerProps) {
  const [dismissed, setDismissed] = React.useState(() => {
    try {
      return sessionStorage.getItem(MULTI_CHANNEL_BANNER_KEY) === "true";
    } catch {
      return false;
    }
  });

  const enabledChannels = channels.filter((c) => c.enabled);
  const enabledCount = enabledChannels.length;

  // Reset dismissal when count changes (so banner shows again after enabling/disabling)
  const prevCountRef = React.useRef(enabledCount);
  React.useEffect(() => {
    if (prevCountRef.current !== enabledCount) {
      prevCountRef.current = enabledCount;
      setDismissed(false);
      try {
        sessionStorage.removeItem(MULTI_CHANNEL_BANNER_KEY);
      } catch {
        // ignore
      }
    }
  }, [enabledCount]);

  if (dismissed || enabledCount === 0 || enabledCount >= 2) return null;

  const channelName = enabledChannels[0]
    ? CHANNEL_DISPLAY_NAME[enabledChannels[0].channel]
    : "неизвестный";

  return (
    <div
      role="alert"
      className="flex items-start gap-3 rounded-lg bg-amber-50 border border-amber-300 text-amber-900 px-4 py-3 mb-6"
      data-testid="multi-channel-warning"
    >
      <AlertTriangle className="h-5 w-5 text-amber-600 mt-0.5 shrink-0" aria-hidden />
      <div className="flex-1 min-w-0 text-sm">
        <p className="font-medium">
          Рекомендуется настроить минимум 2 канала уведомлений — на случай отказа основного.
        </p>
        <p className="mt-1">
          Сейчас включён только 1 канал ({channelName}). Если он недоступен, сотрудники не получат
          напоминания.
        </p>
      </div>
      <button
        type="button"
        aria-label="Скрыть"
        onClick={() => {
          try {
            sessionStorage.setItem(MULTI_CHANNEL_BANNER_KEY, "true");
          } catch {
            // ignore
          }
          setDismissed(true);
        }}
        className="shrink-0 rounded p-0.5 text-amber-600 hover:text-amber-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-500"
      >
        <X className="h-4 w-4" aria-hidden />
      </button>
    </div>
  );
}

// ── Status badge ──────────────────────────────────────────────────────────────

function ChannelStatusBadge({ info }: { info: ChannelInfo }) {
  if (info.status === "decrypt_error") {
    return (
      <Badge variant="destructive">
        Ошибка расшифровки — обратитесь к super-admin (см. runbook §6.4)
      </Badge>
    );
  }
  if (!info.configured) {
    return <Badge className="bg-amber-100 text-amber-800 border-amber-300">Не настроен</Badge>;
  }
  if (info.enabled) {
    return <Badge variant="ok">Активен</Badge>;
  }
  return <Badge variant="secondary">Отключён</Badge>;
}

// ── Inline TOTP prompt ────────────────────────────────────────────────────────

interface TotpPromptProps {
  value: string;
  onChange: (v: string) => void;
  error?: string | null;
  autoFocus?: boolean;
}

function TotpPrompt({ value, onChange, error, autoFocus }: TotpPromptProps) {
  const inputRef = React.useRef<HTMLInputElement>(null);
  React.useEffect(() => {
    if (autoFocus && inputRef.current) inputRef.current.focus();
  }, [autoFocus]);

  return (
    <div className="flex flex-col gap-1.5">
      <Label htmlFor="channel-totp">Код из приложения-аутентификатора</Label>
      <Input
        id="channel-totp"
        ref={inputRef}
        type="text"
        inputMode="numeric"
        autoComplete="one-time-code"
        maxLength={6}
        placeholder="123456"
        value={value}
        onChange={(e) => onChange(e.target.value.replace(/\D/g, "").slice(0, 6))}
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? "channel-totp-error" : "channel-totp-hint"}
        className={cn("font-mono text-center", error && "border-destructive")}
      />
      {error ? (
        <p id="channel-totp-error" role="alert" className="text-xs text-destructive font-medium">
          {error}
        </p>
      ) : (
        <p id="channel-totp-hint" className="text-xs text-muted-foreground">
          Подтвердите кодом из приложения-аутентификатора
        </p>
      )}
    </div>
  );
}

// ── Edit dialogs — one per channel type ──────────────────────────────────────

// ── Shared: config-loading skeleton inside a dialog ───────────────────────────

function ConfigLoadingSkeleton() {
  return (
    <div role="status" aria-busy="true" className="flex flex-col gap-4 animate-pulse py-2">
      <span className="sr-only">Загрузка конфигурации</span>
      {[...Array(5)].map((_, i) => (
        // biome-ignore lint/suspicious/noArrayIndexKey: stable skeleton
        <div key={i} className="flex flex-col gap-1.5">
          <div className="h-4 w-28 rounded bg-muted" />
          <div className="h-9 w-full rounded bg-muted" />
        </div>
      ))}
    </div>
  );
}

// ── Secret field keep-hint ────────────────────────────────────────────────────

function SecretKeepHint({ id }: { id: string }) {
  return (
    <p id={id} className="text-xs text-muted-foreground">
      Оставьте <code className="font-mono">********</code> чтобы не менять, или введите новый
      секрет.
    </p>
  );
}

// Email

function makeEmailSchema(isFirstTime: boolean) {
  return z.object({
    smtp_host: z.string().min(1, "Введите SMTP-хост"),
    smtp_port: z.coerce
      .number()
      .int()
      .min(1, "Порт должен быть от 1 до 65535")
      .max(65535, "Порт должен быть от 1 до 65535"),
    smtp_user: z.string().min(1, "Введите SMTP-пользователя"),
    smtp_password: isFirstTime ? z.string().min(1, "Введите пароль") : z.string(),
    from_address: z.string().email("Некорректный адрес отправителя"),
    from_name: z.string().min(1, "Введите имя отправителя"),
  });
}
// Export the first-time schema for tests (maintains compat with existing schema export pattern)
export const emailSchema = makeEmailSchema(true);
type EmailFormValues = z.infer<ReturnType<typeof makeEmailSchema>>;

interface EmailEditDialogProps {
  open: boolean;
  isFirstTime: boolean;
  onClose: () => void;
  onSaved: () => void;
}

function EmailEditDialog({ open, isFirstTime, onClose, onSaved }: EmailEditDialogProps) {
  const [totp, setTotp] = React.useState("");
  const [totpError, setTotpError] = React.useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = React.useState(false);

  const {
    register,
    handleSubmit,
    reset,
    setError,
    formState: { errors },
  } = useForm<EmailFormValues>({
    resolver: zodResolver(makeEmailSchema(isFirstTime)),
    defaultValues: { smtp_port: 587 },
  });

  // Fetch current config when editing existing config
  const { data: currentConfig, isLoading: loadingConfig } = useQuery({
    queryKey: ["admin", "channels", "email", "config"],
    queryFn: () => getChannelConfig("email"),
    enabled: open && !isFirstTime,
    staleTime: 0,
    gcTime: 0,
  });

  // Pre-populate form when config loads
  React.useEffect(() => {
    if (currentConfig?.config && !isFirstTime) {
      const c = currentConfig.config;
      reset({
        smtp_host: (c.smtp_host as string | undefined) ?? "",
        smtp_port: (c.smtp_port as number | undefined) ?? 587,
        smtp_user: (c.smtp_user as string | undefined) ?? "",
        smtp_password: (c.smtp_password as string | undefined) ?? "",
        from_address: (c.from_address as string | undefined) ?? "",
        from_name: (c.from_name as string | undefined) ?? "",
      });
    }
  }, [currentConfig, isFirstTime, reset]);

  // Reset on open for first-time or re-open
  React.useEffect(() => {
    if (open && isFirstTime) {
      reset({ smtp_port: 587 });
      setTotp("");
      setTotpError(null);
    } else if (!open) {
      setTotp("");
      setTotpError(null);
    }
  }, [open, isFirstTime, reset]);

  const onSubmit = async (values: EmailFormValues) => {
    if (totp.length !== 6) {
      setTotpError("Введите 6-значный TOTP-код");
      return;
    }
    setIsSubmitting(true);
    setTotpError(null);
    try {
      await setChannel("email", {
        enabled: true,
        config: values,
        totp_code: totp,
      });
      toast.show({ title: "Конфигурация сохранена", variant: "success" });
      onSaved();
      onClose();
    } catch (err) {
      if (
        err instanceof ChannelApiResponseError &&
        (err.code === "REMFA_REQUIRED" || err.code === "REMFA_REPLAY")
      ) {
        setTotp("");
        setTotpError(lookupErrorMsg(err.code));
      } else if (err instanceof ChannelApiResponseError && err.code === "SECRET_REQUIRED") {
        setError("smtp_password", {
          message: lookupErrorMsg("SECRET_REQUIRED") ?? "Введите пароль",
        });
      } else if (err instanceof ChannelApiResponseError && err.status === 422) {
        toast.show({ title: "Ошибка валидации данных. Проверьте поля.", variant: "destructive" });
      } else {
        toast.show({ title: mapError(err), variant: "destructive" });
      }
    } finally {
      setIsSubmitting(false);
    }
  };

  const showLoader = !isFirstTime && loadingConfig;

  return (
    <Dialog open={open} onClose={onClose} title="Настройка Email-канала">
      {showLoader ? (
        <ConfigLoadingSkeleton />
      ) : (
        <form onSubmit={handleSubmit(onSubmit)} noValidate className="flex flex-col gap-4">
          {(
            [
              {
                id: "e-host",
                name: "smtp_host",
                label: "SMTP-хост",
                type: "text",
                isSecret: false,
              },
              {
                id: "e-port",
                name: "smtp_port",
                label: "SMTP-порт",
                type: "number",
                isSecret: false,
              },
              {
                id: "e-user",
                name: "smtp_user",
                label: "SMTP-пользователь",
                type: "text",
                isSecret: false,
              },
              {
                id: "e-pass",
                name: "smtp_password",
                label: "SMTP-пароль",
                type: "password",
                isSecret: true,
              },
              {
                id: "e-from",
                name: "from_address",
                label: "Email отправителя",
                type: "email",
                isSecret: false,
              },
              {
                id: "e-name",
                name: "from_name",
                label: "Имя отправителя",
                type: "text",
                isSecret: false,
              },
            ] as const
          ).map(({ id, name, label, type, isSecret }) => {
            const err = errors[name];
            const hintId = isSecret && !isFirstTime ? `${id}-hint` : undefined;
            return (
              <div key={id} className="flex flex-col gap-1.5">
                <Label htmlFor={id}>{label}</Label>
                <Input
                  id={id}
                  type={type}
                  aria-invalid={err ? true : undefined}
                  aria-describedby={err ? `${id}-err` : hintId}
                  className={cn(err && "border-destructive")}
                  {...register(name)}
                />
                {isSecret && !isFirstTime && <SecretKeepHint id={`${id}-hint`} />}
                {err?.message && (
                  <p id={`${id}-err`} role="alert" className="text-xs text-destructive font-medium">
                    {err.message}
                  </p>
                )}
              </div>
            );
          })}

          <TotpPrompt value={totp} onChange={setTotp} error={totpError} />

          <div className="flex justify-end gap-2">
            <Button type="button" variant="outline" onClick={onClose} disabled={isSubmitting}>
              Отмена
            </Button>
            <Button type="submit" disabled={isSubmitting} aria-busy={isSubmitting}>
              {isSubmitting ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                  Сохранение...
                </>
              ) : (
                "Сохранить"
              )}
            </Button>
          </div>
        </form>
      )}
    </Dialog>
  );
}

// Telegram

// Telegram bot_token regex — relaxed for pre-populate: allow "********" placeholder or real token format
function makeTelegramSchema(isFirstTime: boolean) {
  const tokenSchema = isFirstTime
    ? z
        .string()
        .regex(
          /^\d+:[A-Za-z0-9_-]{30,}$/,
          "Формат: <число>:<base64>, минимум 30 символов после ':'",
        )
    : z.string();
  return z.object({
    bot_token: tokenSchema,
    default_parse_mode: z.enum(["HTML", "MarkdownV2"] as const),
  });
}
export const telegramSchema = makeTelegramSchema(true);
type TelegramFormValues = z.infer<ReturnType<typeof makeTelegramSchema>>;

interface TelegramEditDialogProps {
  open: boolean;
  isFirstTime: boolean;
  onClose: () => void;
  onSaved: () => void;
}

function TelegramEditDialog({ open, isFirstTime, onClose, onSaved }: TelegramEditDialogProps) {
  const [totp, setTotp] = React.useState("");
  const [totpError, setTotpError] = React.useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = React.useState(false);

  const {
    register,
    handleSubmit,
    reset,
    setError,
    formState: { errors },
  } = useForm<TelegramFormValues>({
    resolver: zodResolver(makeTelegramSchema(isFirstTime)),
    defaultValues: { default_parse_mode: "HTML" },
  });

  const { data: currentConfig, isLoading: loadingConfig } = useQuery({
    queryKey: ["admin", "channels", "telegram", "config"],
    queryFn: () => getChannelConfig("telegram"),
    enabled: open && !isFirstTime,
    staleTime: 0,
    gcTime: 0,
  });

  React.useEffect(() => {
    if (currentConfig?.config && !isFirstTime) {
      const c = currentConfig.config;
      reset({
        bot_token: (c.bot_token as string | undefined) ?? "",
        default_parse_mode:
          (c.default_parse_mode as string | undefined as "HTML" | "MarkdownV2" | undefined) ??
          "HTML",
      });
    }
  }, [currentConfig, isFirstTime, reset]);

  React.useEffect(() => {
    if (open && isFirstTime) {
      reset({ default_parse_mode: "HTML" });
      setTotp("");
      setTotpError(null);
    } else if (!open) {
      setTotp("");
      setTotpError(null);
    }
  }, [open, isFirstTime, reset]);

  const onSubmit = async (values: TelegramFormValues) => {
    if (totp.length !== 6) {
      setTotpError("Введите 6-значный TOTP-код");
      return;
    }
    setIsSubmitting(true);
    setTotpError(null);
    try {
      await setChannel("telegram", {
        enabled: true,
        config: { bot_token: values.bot_token, default_parse_mode: values.default_parse_mode },
        totp_code: totp,
      });
      toast.show({ title: "Конфигурация сохранена", variant: "success" });
      onSaved();
      onClose();
    } catch (err) {
      if (
        err instanceof ChannelApiResponseError &&
        (err.code === "REMFA_REQUIRED" || err.code === "REMFA_REPLAY")
      ) {
        setTotp("");
        setTotpError(lookupErrorMsg(err.code));
      } else if (err instanceof ChannelApiResponseError && err.code === "SECRET_REQUIRED") {
        setError("bot_token", { message: lookupErrorMsg("SECRET_REQUIRED") ?? "Введите токен" });
      } else {
        toast.show({ title: mapError(err), variant: "destructive" });
      }
    } finally {
      setIsSubmitting(false);
    }
  };

  const showLoader = !isFirstTime && loadingConfig;

  return (
    <Dialog open={open} onClose={onClose} title="Настройка Telegram-канала">
      {showLoader ? (
        <ConfigLoadingSkeleton />
      ) : (
        <form onSubmit={handleSubmit(onSubmit)} noValidate className="flex flex-col gap-4">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="tg-token">Bot-токен</Label>
            <Input
              id="tg-token"
              type="password"
              autoComplete="off"
              placeholder={isFirstTime ? "7000000000:AA-..." : undefined}
              aria-invalid={errors.bot_token ? true : undefined}
              aria-describedby={
                errors.bot_token ? "tg-token-err" : !isFirstTime ? "tg-token-hint" : "tg-token-fmt"
              }
              className={cn(errors.bot_token && "border-destructive")}
              {...register("bot_token")}
            />
            {!isFirstTime ? (
              <SecretKeepHint id="tg-token-hint" />
            ) : (
              <p id="tg-token-fmt" className="text-xs text-muted-foreground">
                Формат: &lt;число&gt;:&lt;base64&gt;
              </p>
            )}
            {errors.bot_token?.message && (
              <p id="tg-token-err" role="alert" className="text-xs text-destructive font-medium">
                {errors.bot_token.message}
              </p>
            )}
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="tg-parse">Режим форматирования</Label>
            <select
              id="tg-parse"
              className="h-9 rounded-md border border-input bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              {...register("default_parse_mode")}
            >
              <option value="HTML">HTML</option>
              <option value="MarkdownV2">MarkdownV2</option>
            </select>
          </div>

          <TotpPrompt value={totp} onChange={setTotp} error={totpError} />

          <div className="flex justify-end gap-2">
            <Button type="button" variant="outline" onClick={onClose} disabled={isSubmitting}>
              Отмена
            </Button>
            <Button type="submit" disabled={isSubmitting} aria-busy={isSubmitting}>
              {isSubmitting ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                  Сохранение...
                </>
              ) : (
                "Сохранить"
              )}
            </Button>
          </div>
        </form>
      )}
    </Dialog>
  );
}

// Dion

function makeDionSchema(isFirstTime: boolean) {
  return z.object({
    api_base: z.string().refine((v) => v.startsWith("https://"), {
      message: "api_base должен начинаться с https://",
    }),
    api_token: isFirstTime ? z.string().min(1, "Введите API-токен") : z.string(),
    workspace_id: z.string().optional(),
  });
}
export const dionSchema = makeDionSchema(true);
type DionFormValues = z.infer<ReturnType<typeof makeDionSchema>>;

interface DionEditDialogProps {
  open: boolean;
  isFirstTime: boolean;
  onClose: () => void;
  onSaved: () => void;
}

function DionEditDialog({ open, isFirstTime, onClose, onSaved }: DionEditDialogProps) {
  const [totp, setTotp] = React.useState("");
  const [totpError, setTotpError] = React.useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = React.useState(false);

  const {
    register,
    handleSubmit,
    reset,
    setError,
    formState: { errors },
  } = useForm<DionFormValues>({
    resolver: zodResolver(makeDionSchema(isFirstTime)),
    defaultValues: { api_base: "https://", workspace_id: "" },
  });

  const { data: currentConfig, isLoading: loadingConfig } = useQuery({
    queryKey: ["admin", "channels", "dion", "config"],
    queryFn: () => getChannelConfig("dion"),
    enabled: open && !isFirstTime,
    staleTime: 0,
    gcTime: 0,
  });

  React.useEffect(() => {
    if (currentConfig?.config && !isFirstTime) {
      const c = currentConfig.config;
      reset({
        api_base: (c.api_base as string | undefined) ?? "https://",
        api_token: (c.api_token as string | undefined) ?? "",
        workspace_id: (c.workspace_id as string | undefined) ?? "",
      });
    }
  }, [currentConfig, isFirstTime, reset]);

  React.useEffect(() => {
    if (open && isFirstTime) {
      reset({ api_base: "https://", workspace_id: "" });
      setTotp("");
      setTotpError(null);
    } else if (!open) {
      setTotp("");
      setTotpError(null);
    }
  }, [open, isFirstTime, reset]);

  const onSubmit = async (values: DionFormValues) => {
    if (totp.length !== 6) {
      setTotpError("Введите 6-значный TOTP-код");
      return;
    }
    setIsSubmitting(true);
    setTotpError(null);
    try {
      const dionConfig: import("@/features/admin/channels/api").DionConfig = {
        api_base: values.api_base,
        api_token: values.api_token ?? "",
        ...(values.workspace_id ? { workspace_id: values.workspace_id } : {}),
      };
      await setChannel("dion", {
        enabled: true,
        config: dionConfig,
        totp_code: totp,
      });
      toast.show({ title: "Конфигурация сохранена", variant: "success" });
      onSaved();
      onClose();
    } catch (err) {
      if (
        err instanceof ChannelApiResponseError &&
        (err.code === "REMFA_REQUIRED" || err.code === "REMFA_REPLAY")
      ) {
        setTotp("");
        setTotpError(lookupErrorMsg(err.code));
      } else if (err instanceof ChannelApiResponseError && err.code === "SECRET_REQUIRED") {
        setError("api_token", { message: lookupErrorMsg("SECRET_REQUIRED") ?? "Введите токен" });
      } else {
        toast.show({ title: mapError(err), variant: "destructive" });
      }
    } finally {
      setIsSubmitting(false);
    }
  };

  const showLoader = !isFirstTime && loadingConfig;

  return (
    <Dialog open={open} onClose={onClose} title="Настройка Dion-канала">
      {showLoader ? (
        <ConfigLoadingSkeleton />
      ) : (
        <form onSubmit={handleSubmit(onSubmit)} noValidate className="flex flex-col gap-4">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="dion-base">API Base URL</Label>
            <Input
              id="dion-base"
              type="url"
              placeholder="https://dion.corp/api"
              aria-invalid={errors.api_base ? true : undefined}
              aria-describedby={errors.api_base ? "dion-base-err" : undefined}
              className={cn(errors.api_base && "border-destructive")}
              {...register("api_base")}
            />
            {errors.api_base?.message && (
              <p id="dion-base-err" role="alert" className="text-xs text-destructive font-medium">
                {errors.api_base.message}
              </p>
            )}
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="dion-token">API-токен</Label>
            <Input
              id="dion-token"
              type="password"
              autoComplete="off"
              aria-invalid={errors.api_token ? true : undefined}
              aria-describedby={
                errors.api_token ? "dion-token-err" : !isFirstTime ? "dion-token-hint" : undefined
              }
              className={cn(errors.api_token && "border-destructive")}
              {...register("api_token")}
            />
            {!isFirstTime && <SecretKeepHint id="dion-token-hint" />}
            {errors.api_token?.message && (
              <p id="dion-token-err" role="alert" className="text-xs text-destructive font-medium">
                {errors.api_token.message}
              </p>
            )}
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="dion-ws">Workspace ID (опционально)</Label>
            <Input id="dion-ws" type="text" {...register("workspace_id")} />
          </div>

          <TotpPrompt value={totp} onChange={setTotp} error={totpError} />

          <div className="flex justify-end gap-2">
            <Button type="button" variant="outline" onClick={onClose} disabled={isSubmitting}>
              Отмена
            </Button>
            <Button type="submit" disabled={isSubmitting} aria-busy={isSubmitting}>
              {isSubmitting ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                  Сохранение...
                </>
              ) : (
                "Сохранить"
              )}
            </Button>
          </div>
        </form>
      )}
    </Dialog>
  );
}

// Exchange Calendar

function makeExchangeCalendarSchema(isFirstTime: boolean) {
  return z.object({
    ews_url: z
      .string()
      .min(1, "Введите EWS URL")
      .refine((v) => v.startsWith("https://"), {
        message: "EWS URL должен начинаться с https://",
      }),
    service_account_login: z.string().min(1, "Введите логин сервисного аккаунта"),
    service_account_password: isFirstTime ? z.string().min(1, "Введите пароль") : z.string(),
    target_mailbox: z
      .string()
      .min(1, "Введите адрес ящика")
      .refine((v) => /^[^@\s]+@[^@\s]+$/.test(v), {
        message: "Введите корректный email-адрес (домены .local допустимы)",
      }),
    auth_type: z.enum(["NTLM", "Basic"] as const),
    verify_ssl: z.boolean(),
    default_notice_days: z.coerce
      .number()
      .int()
      .min(1, "Минимум 1 день")
      .max(90, "Максимум 90 дней"),
  });
}
export const exchangeCalendarSchema = makeExchangeCalendarSchema(true);
type ExchangeCalendarFormValues = z.infer<ReturnType<typeof makeExchangeCalendarSchema>>;

interface ExchangeCalendarEditDialogProps {
  open: boolean;
  isFirstTime: boolean;
  onClose: () => void;
  onSaved: () => void;
}

function ExchangeCalendarEditDialog({
  open,
  isFirstTime,
  onClose,
  onSaved,
}: ExchangeCalendarEditDialogProps) {
  const [totp, setTotp] = React.useState("");
  const [totpError, setTotpError] = React.useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = React.useState(false);

  const {
    register,
    handleSubmit,
    reset,
    watch,
    setValue,
    setError,
    formState: { errors },
  } = useForm<ExchangeCalendarFormValues>({
    resolver: zodResolver(makeExchangeCalendarSchema(isFirstTime)),
    defaultValues: {
      auth_type: "NTLM",
      verify_ssl: true,
      default_notice_days: 14,
    },
  });

  const verifySsl = watch("verify_ssl");

  const { data: currentConfig, isLoading: loadingConfig } = useQuery({
    queryKey: ["admin", "channels", "exchange_calendar", "config"],
    queryFn: () => getChannelConfig("exchange_calendar"),
    enabled: open && !isFirstTime,
    staleTime: 0,
    gcTime: 0,
  });

  React.useEffect(() => {
    if (currentConfig?.config && !isFirstTime) {
      const c = currentConfig.config;
      reset({
        ews_url: (c.ews_url as string | undefined) ?? "",
        service_account_login: (c.service_account_login as string | undefined) ?? "",
        service_account_password: (c.service_account_password as string | undefined) ?? "",
        target_mailbox: (c.target_mailbox as string | undefined) ?? "",
        auth_type: (c.auth_type as string | undefined as "NTLM" | "Basic" | undefined) ?? "NTLM",
        verify_ssl: (c.verify_ssl as boolean | undefined) ?? true,
        default_notice_days: (c.default_notice_days as number | undefined) ?? 14,
      });
    }
  }, [currentConfig, isFirstTime, reset]);

  React.useEffect(() => {
    if (open && isFirstTime) {
      reset({ auth_type: "NTLM", verify_ssl: true, default_notice_days: 14 });
      setTotp("");
      setTotpError(null);
    } else if (!open) {
      setTotp("");
      setTotpError(null);
    }
  }, [open, isFirstTime, reset]);

  const onSubmit = async (values: ExchangeCalendarFormValues) => {
    if (totp.length !== 6) {
      setTotpError("Введите 6-значный TOTP-код");
      return;
    }
    setIsSubmitting(true);
    setTotpError(null);
    try {
      const config: ExchangeCalendarConfig = {
        ews_url: values.ews_url,
        service_account_login: values.service_account_login,
        service_account_password: values.service_account_password,
        target_mailbox: values.target_mailbox,
        auth_type: values.auth_type,
        verify_ssl: values.verify_ssl,
        default_notice_days: values.default_notice_days,
      };
      await setChannel("exchange_calendar", {
        enabled: true,
        config,
        totp_code: totp,
      });
      toast.show({ title: "Конфигурация сохранена", variant: "success" });
      onSaved();
      onClose();
    } catch (err) {
      if (
        err instanceof ChannelApiResponseError &&
        (err.code === "REMFA_REQUIRED" || err.code === "REMFA_REPLAY")
      ) {
        setTotp("");
        setTotpError(lookupErrorMsg(err.code));
      } else if (err instanceof ChannelApiResponseError && err.code === "SECRET_REQUIRED") {
        setError("service_account_password", {
          message: lookupErrorMsg("SECRET_REQUIRED") ?? "Введите пароль",
        });
      } else if (err instanceof ChannelApiResponseError && err.status === 422) {
        toast.show({ title: "Ошибка валидации данных. Проверьте поля.", variant: "destructive" });
      } else {
        toast.show({ title: mapError(err), variant: "destructive" });
      }
    } finally {
      setIsSubmitting(false);
    }
  };

  const showLoader = !isFirstTime && loadingConfig;

  return (
    <Dialog open={open} onClose={onClose} title="Настройка Календаря Exchange">
      {showLoader ? (
        <ConfigLoadingSkeleton />
      ) : (
        <>
          {/* Probe banner */}
          <div className="mb-4 flex items-start gap-2 rounded border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900">
            <AlertTriangle className="h-4 w-4 mt-0.5 shrink-0 text-amber-600" aria-hidden />
            <p>
              После сохранения нажмите «Тест» — Лоцман создаст и сразу удалит probe-событие в
              календаре, чтобы убедиться, что credentials рабочие.
            </p>
          </div>

          <form onSubmit={handleSubmit(onSubmit)} noValidate className="flex flex-col gap-4">
            {/* EWS URL */}
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="exc-ews-url">EWS URL</Label>
              <Input
                id="exc-ews-url"
                type="url"
                placeholder="https://mail.org.local/EWS/Exchange.asmx"
                aria-invalid={errors.ews_url ? true : undefined}
                aria-describedby={errors.ews_url ? "exc-ews-url-err" : "exc-ews-url-hint"}
                className={cn(errors.ews_url && "border-destructive")}
                {...register("ews_url")}
              />
              <p id="exc-ews-url-hint" className="text-xs text-muted-foreground">
                Например: https://mail.org.local/EWS/Exchange.asmx
              </p>
              {errors.ews_url?.message && (
                <p
                  id="exc-ews-url-err"
                  role="alert"
                  className="text-xs text-destructive font-medium"
                >
                  {errors.ews_url.message}
                </p>
              )}
            </div>

            {/* Service account login */}
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="exc-login">Логин сервисного аккаунта</Label>
              <Input
                id="exc-login"
                type="text"
                autoComplete="off"
                className={cn("font-mono", errors.service_account_login && "border-destructive")}
                placeholder="DOMAIN\\user"
                aria-invalid={errors.service_account_login ? true : undefined}
                aria-describedby={errors.service_account_login ? "exc-login-err" : "exc-login-hint"}
                {...register("service_account_login")}
              />
              <p id="exc-login-hint" className="text-xs text-muted-foreground">
                Формат: DOMAIN\user
              </p>
              {errors.service_account_login?.message && (
                <p id="exc-login-err" role="alert" className="text-xs text-destructive font-medium">
                  {errors.service_account_login.message}
                </p>
              )}
            </div>

            {/* Service account password */}
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="exc-pass">Пароль сервисного аккаунта</Label>
              <Input
                id="exc-pass"
                type="password"
                autoComplete="new-password"
                aria-invalid={errors.service_account_password ? true : undefined}
                aria-describedby={
                  errors.service_account_password
                    ? "exc-pass-err"
                    : !isFirstTime
                      ? "exc-pass-hint"
                      : undefined
                }
                className={cn(errors.service_account_password && "border-destructive")}
                {...register("service_account_password")}
              />
              {!isFirstTime && <SecretKeepHint id="exc-pass-hint" />}
              {errors.service_account_password?.message && (
                <p id="exc-pass-err" role="alert" className="text-xs text-destructive font-medium">
                  {errors.service_account_password.message}
                </p>
              )}
            </div>

            {/* Target mailbox */}
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="exc-mailbox">Целевой почтовый ящик</Label>
              <Input
                id="exc-mailbox"
                type="text"
                autoComplete="off"
                placeholder="lotsman-deadlines@org.local"
                aria-invalid={errors.target_mailbox ? true : undefined}
                aria-describedby={errors.target_mailbox ? "exc-mailbox-err" : "exc-mailbox-hint"}
                className={cn(errors.target_mailbox && "border-destructive")}
                {...register("target_mailbox")}
              />
              <p id="exc-mailbox-hint" className="text-xs text-muted-foreground">
                Например: lotsman-deadlines@org.local
              </p>
              {errors.target_mailbox?.message && (
                <p
                  id="exc-mailbox-err"
                  role="alert"
                  className="text-xs text-destructive font-medium"
                >
                  {errors.target_mailbox.message}
                </p>
              )}
            </div>

            {/* Auth type */}
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="exc-auth-type">Тип авторизации</Label>
              <select
                id="exc-auth-type"
                className="h-9 rounded-md border border-input bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                {...register("auth_type")}
              >
                <option value="NTLM">NTLM</option>
                <option value="Basic">Basic</option>
              </select>
            </div>

            {/* Verify SSL */}
            <div className="flex items-center justify-between gap-3">
              <div className="flex flex-col gap-0.5">
                <Label htmlFor="exc-verify-ssl">Проверять SSL-сертификат</Label>
                <p id="exc-verify-ssl-hint" className="text-xs text-muted-foreground">
                  Отключайте только при self-signed сертификате
                </p>
              </div>
              <button
                type="button"
                id="exc-verify-ssl"
                role="switch"
                aria-checked={verifySsl}
                aria-describedby="exc-verify-ssl-hint"
                onClick={() => setValue("verify_ssl", !verifySsl)}
                className={cn(
                  "relative inline-flex h-6 w-11 shrink-0 cursor-pointer rounded-full border-2 border-transparent",
                  "transition-colors duration-200 ease-in-out",
                  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
                  verifySsl ? "bg-primary" : "bg-muted",
                )}
              >
                <span
                  aria-hidden
                  className={cn(
                    "pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow ring-0",
                    "transition duration-200 ease-in-out",
                    verifySsl ? "translate-x-5" : "translate-x-0",
                  )}
                />
              </button>
            </div>

            {/* Default notice days */}
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="exc-notice-days">Дней предупреждения по умолчанию</Label>
              <Input
                id="exc-notice-days"
                type="number"
                min={1}
                max={90}
                aria-invalid={errors.default_notice_days ? true : undefined}
                aria-describedby={
                  errors.default_notice_days ? "exc-notice-days-err" : "exc-notice-days-hint"
                }
                className={cn(errors.default_notice_days && "border-destructive")}
                {...register("default_notice_days")}
              />
              <p id="exc-notice-days-hint" className="text-xs text-muted-foreground">
                Используется когда у типа документа не задано pre_notice_days
              </p>
              {errors.default_notice_days?.message && (
                <p
                  id="exc-notice-days-err"
                  role="alert"
                  className="text-xs text-destructive font-medium"
                >
                  {errors.default_notice_days.message}
                </p>
              )}
            </div>

            <TotpPrompt value={totp} onChange={setTotp} error={totpError} />

            <div className="flex justify-end gap-2">
              <Button type="button" variant="outline" onClick={onClose} disabled={isSubmitting}>
                Отмена
              </Button>
              <Button type="submit" disabled={isSubmitting} aria-busy={isSubmitting}>
                {isSubmitting ? (
                  <>
                    <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                    Сохранение...
                  </>
                ) : (
                  "Сохранить"
                )}
              </Button>
            </div>
          </form>
        </>
      )}
    </Dialog>
  );
}

// ICS Feed

// ICS Feed has no user secrets — token is server-generated or user-supplied.
// For re-configure: pre-populate with the current token value from /config.
export const icsFeedSchema = z.object({
  token: z.string().refine((v) => v === "" || v.length >= 32, {
    message: "Токен должен быть не менее 32 символов (или оставьте пустым для автогенерации)",
  }),
  cache_ttl_seconds: z.coerce
    .number()
    .int()
    .min(60, "Минимум 60 секунд")
    .max(86400, "Максимум 86400 секунд (24 ч)"),
});
type IcsFeedFormValues = z.infer<typeof icsFeedSchema>;

interface IcsFeedEditDialogProps {
  open: boolean;
  isFirstTime: boolean;
  onClose: () => void;
  onSaved: (token: string) => void;
}

function IcsFeedEditDialog({ open, isFirstTime, onClose, onSaved }: IcsFeedEditDialogProps) {
  const [totp, setTotp] = React.useState("");
  const [totpError, setTotpError] = React.useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = React.useState(false);

  const {
    register,
    handleSubmit,
    reset,
    formState: { errors },
  } = useForm<IcsFeedFormValues>({
    resolver: zodResolver(icsFeedSchema),
    defaultValues: { token: "", cache_ttl_seconds: 300 },
  });

  const { data: currentConfig, isLoading: loadingConfig } = useQuery({
    queryKey: ["admin", "channels", "ics_feed", "config"],
    queryFn: () => getChannelConfig("ics_feed"),
    enabled: open && !isFirstTime,
    staleTime: 0,
    gcTime: 0,
  });

  React.useEffect(() => {
    if (currentConfig?.config && !isFirstTime) {
      const c = currentConfig.config;
      reset({
        // ICS token is not a secret — backend returns the actual token value
        token: (c.token as string | undefined) ?? "",
        cache_ttl_seconds: (c.cache_ttl_seconds as number | undefined) ?? 300,
      });
    }
  }, [currentConfig, isFirstTime, reset]);

  React.useEffect(() => {
    if (open && isFirstTime) {
      reset({ token: "", cache_ttl_seconds: 300 });
      setTotp("");
      setTotpError(null);
    } else if (!open) {
      setTotp("");
      setTotpError(null);
    }
  }, [open, isFirstTime, reset]);

  const onSubmit = async (values: IcsFeedFormValues) => {
    if (totp.length !== 6) {
      setTotpError("Введите 6-значный TOTP-код");
      return;
    }
    setIsSubmitting(true);
    setTotpError(null);
    try {
      const config: IcsFeedConfig = {
        token: values.token,
        cache_ttl_seconds: values.cache_ttl_seconds,
      };
      const result = await setChannel("ics_feed", {
        enabled: true,
        config,
        totp_code: totp,
      });
      // Extract saved token from response config
      const savedToken = (result.config?.token as string | undefined) ?? values.token ?? "";
      toast.show({ title: "Конфигурация сохранена", variant: "success" });
      onSaved(savedToken);
      onClose();
    } catch (err) {
      if (
        err instanceof ChannelApiResponseError &&
        (err.code === "REMFA_REQUIRED" || err.code === "REMFA_REPLAY")
      ) {
        setTotp("");
        setTotpError(lookupErrorMsg(err.code));
      } else if (err instanceof ChannelApiResponseError && err.status === 422) {
        toast.show({ title: "Ошибка валидации данных. Проверьте поля.", variant: "destructive" });
      } else {
        toast.show({ title: mapError(err), variant: "destructive" });
      }
    } finally {
      setIsSubmitting(false);
    }
  };

  const showLoader = !isFirstTime && loadingConfig;

  return (
    <Dialog open={open} onClose={onClose} title="Настройка ICS-подписки">
      {showLoader ? (
        <ConfigLoadingSkeleton />
      ) : (
        <form onSubmit={handleSubmit(onSubmit)} noValidate className="flex flex-col gap-4">
          {/* Token */}
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="ics-token">Токен доступа</Label>
            <Input
              id="ics-token"
              type="text"
              autoComplete="off"
              className={cn("font-mono", errors.token && "border-destructive")}
              aria-invalid={errors.token ? true : undefined}
              aria-describedby={errors.token ? "ics-token-err" : "ics-token-hint"}
              {...register("token")}
            />
            <p id="ics-token-hint" className="text-xs text-muted-foreground">
              Оставьте пустым, чтобы сгенерировать автоматически (32+ символа). Для смены — введите
              новое значение и сохраните.
            </p>
            {errors.token?.message && (
              <p id="ics-token-err" role="alert" className="text-xs text-destructive font-medium">
                {errors.token.message}
              </p>
            )}
          </div>

          {/* Cache TTL */}
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="ics-ttl">Время кэша (секунд)</Label>
            <Input
              id="ics-ttl"
              type="number"
              min={60}
              max={86400}
              aria-invalid={errors.cache_ttl_seconds ? true : undefined}
              aria-describedby={errors.cache_ttl_seconds ? "ics-ttl-err" : "ics-ttl-hint"}
              className={cn(errors.cache_ttl_seconds && "border-destructive")}
              {...register("cache_ttl_seconds")}
            />
            <p id="ics-ttl-hint" className="text-xs text-muted-foreground">
              Как часто Outlook будет polling — рекомендуется 300 (5 мин)
            </p>
            {errors.cache_ttl_seconds?.message && (
              <p id="ics-ttl-err" role="alert" className="text-xs text-destructive font-medium">
                {errors.cache_ttl_seconds.message}
              </p>
            )}
          </div>

          <TotpPrompt value={totp} onChange={setTotp} error={totpError} />

          <div className="flex justify-end gap-2">
            <Button type="button" variant="outline" onClick={onClose} disabled={isSubmitting}>
              Отмена
            </Button>
            <Button type="submit" disabled={isSubmitting} aria-busy={isSubmitting}>
              {isSubmitting ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                  Сохранение...
                </>
              ) : (
                "Сохранить"
              )}
            </Button>
          </div>
        </form>
      )}
    </Dialog>
  );
}

// ── ICS Feed URL info block ────────────────────────────────────────────────────

interface IcsFeedUrlBlockProps {
  token: string;
  onClose: () => void;
}

function IcsFeedUrlBlock({ token, onClose }: IcsFeedUrlBlockProps) {
  const [copied, setCopied] = React.useState(false);
  const feedUrl = `https://lotsman.example.com/api/v1/calendar/feed/${token}.ics`;

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(feedUrl);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      toast.show({ title: "Не удалось скопировать URL", variant: "destructive" });
    }
  };

  return (
    <Dialog open title="ICS-подписка сохранена" onClose={onClose}>
      <div className="flex flex-col gap-4">
        <p className="text-sm text-muted-foreground">
          Используйте этот URL для подписки в Outlook, macOS Calendar или другом клиенте:
        </p>

        <div className="flex items-center gap-2 rounded-md border border-border bg-muted/50 px-3 py-2">
          <code className="flex-1 text-xs break-all font-mono text-foreground">{feedUrl}</code>
          <button
            type="button"
            onClick={handleCopy}
            aria-label={copied ? "Скопировано" : "Скопировать URL"}
            title={copied ? "Скопировано!" : "Скопировать URL"}
            className="shrink-0 rounded p-1 text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring transition-colors"
          >
            {copied ? (
              <Check className="h-4 w-4 text-status-ok" aria-hidden />
            ) : (
              <Copy className="h-4 w-4" aria-hidden />
            )}
          </button>
        </div>

        <div className="flex items-center justify-between">
          <a
            href="/docs/runbook#ics-subscription"
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 text-sm text-primary hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded"
          >
            <ExternalLink className="h-3.5 w-3.5" aria-hidden />
            Открыть инструкцию
          </a>
          <Button onClick={onClose}>Закрыть</Button>
        </div>
      </div>
    </Dialog>
  );
}

// ── Test dialog ───────────────────────────────────────────────────────────────

// Only email / telegram / dion support test. exchange_calendar has a special flow.
type TestableChannelName = "email" | "telegram" | "dion" | "exchange_calendar";

interface TestDialogProps {
  open: boolean;
  channel: TestableChannelName;
  onClose: () => void;
}

type TestResult =
  | { kind: "success"; title: string; description: string; hint?: string }
  | { kind: "error"; title: string; description: string; hint?: string };

function TestDialog({ open, channel, onClose }: TestDialogProps) {
  const [totp, setTotp] = React.useState("");
  const [totpError, setTotpError] = React.useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = React.useState(false);
  const [result, setResult] = React.useState<TestResult | null>(null);

  React.useEffect(() => {
    if (open) {
      setTotp("");
      setTotpError(null);
      setResult(null);
    }
  }, [open]);

  const channelLabel: Record<TestableChannelName, string> = {
    email: "Email",
    telegram: "Telegram",
    dion: "Dion",
    exchange_calendar: "Календарь Exchange",
  };

  const isExchange = channel === "exchange_calendar";

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (totp.length !== 6) {
      setTotpError("Введите 6-значный TOTP-код");
      return;
    }
    setIsSubmitting(true);
    setTotpError(null);
    setResult(null);
    try {
      const res = await testChannel(channel, totp);
      // Backend returns either: {success:true, detail, latency_ms} (exchange)
      // OR {status:"queued", destination, test_id} (email/tg/dion)
      // OR {success:false, detail} on provider failure (still HTTP 200)
      const make = (
        kind: "success" | "error",
        title: string,
        description: string,
        hint?: string,
      ): TestResult => (hint ? { kind, title, description, hint } : { kind, title, description });

      if (isExchange) {
        const ok = res.success !== false;
        if (ok) {
          setResult(
            make(
              "success",
              "Подключение к Exchange успешно",
              res.detail ?? "Probe-событие создано и сразу удалено в общем календаре.",
              res.latency_ms != null ? `Время ответа: ${Math.round(res.latency_ms)} мс` : undefined,
            ),
          );
        } else {
          setResult(
            make(
              "error",
              "Не удалось подключиться к Exchange",
              res.detail ?? "Сервер вернул ошибку.",
              "Проверьте: EWS URL, логин/пароль service-account, ApplicationImpersonation/Full-Access права на target_mailbox.",
            ),
          );
        }
      } else if (res.success === false) {
        setResult(
          make(
            "error",
            `Канал ${channelLabel[channel]} вернул ошибку`,
            res.detail ?? "Provider returned error.",
            "Проверьте параметры подключения в карточке «Настроить».",
          ),
        );
      } else {
        // Production-aware hint. For email — show actual transport that
        // succeeded (smtp / ews) so admin understands path; for other
        // channels — generic «check your inbox» note.
        let hint: string | undefined;
        if (channel === "email") {
          const transport = (res as { transport?: string }).transport;
          if (transport === "ews") {
            hint =
              "Доставлено через корпоративный Exchange (EWS). Проверьте Outlook/OWA — письмо должно прийти в течение минуты.";
          } else if (transport === "smtp") {
            hint =
              "Доставлено через настроенный SMTP-сервер. Проверьте свой почтовый клиент — письмо должно прийти в течение минуты.";
          } else {
            hint = "Проверьте свой почтовый клиент — письмо должно прийти в течение минуты.";
          }
        } else {
          hint = `Проверьте свой ${channelLabel[channel]} в течение 30 секунд.`;
        }
        setResult(
          make(
            "success",
            "Тестовое сообщение отправлено",
            `Адрес доставки: ${res.destination ?? "(не указан)"}`,
            hint,
          ),
        );
      }
    } catch (err) {
      if (
        err instanceof ChannelApiResponseError &&
        (err.code === "REMFA_REQUIRED" || err.code === "REMFA_REPLAY")
      ) {
        setTotp("");
        setTotpError(lookupErrorMsg(err.code));
        return;
      }
      // Map common HTTP statuses to typed user-facing messages.
      let title = `Тест канала ${channelLabel[channel]} провалился`;
      let description = mapError(err);
      let hint: string | undefined;
      if (err instanceof ChannelApiResponseError) {
        if (err.status === 504) {
          title = "Таймаут подключения";
          description = "Тест не уложился в 45 секунд.";
          hint =
            "Сервер не отвечает. Проверьте host/port в настройках канала и доступность сервера из сети сервиса notifications.";
        } else if (err.status === 502) {
          title = `Канал ${channelLabel[channel]} вернул ошибку`;
          description = err.detail ?? description;
          hint = "Проверьте параметры подключения в карточке «Настроить».";
        } else if (err.status === 501) {
          title = "Тест для этого канала не реализован";
          description = "Telegram и Dion test-endpoints отложены в backlog.";
        }
      }
      const errorResult: TestResult = hint
        ? { kind: "error", title, description, hint }
        : { kind: "error", title, description };
      setResult(errorResult);
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={`Тест канала ${channelLabel[channel]}`}
      description={
        result
          ? undefined
          : isExchange
            ? "Лоцман создаст probe-событие в Exchange и сразу удалит его."
            : "Тестовое сообщение будет отправлено на ваш адрес."
      }
    >
      <form onSubmit={handleSubmit} noValidate className="flex flex-col gap-4">
        {/* Inline result panel — replaces description after Submit */}
        {result && (
          <div
            role={result.kind === "error" ? "alert" : "status"}
            className={cn(
              "rounded border px-3 py-3 flex flex-col gap-1",
              result.kind === "success"
                ? "bg-status-ok/10 border-status-ok text-status-ok"
                : "bg-destructive/10 border-destructive text-destructive",
            )}
          >
            <p className="text-sm font-semibold">{result.title}</p>
            <p className="text-sm">{result.description}</p>
            {result.hint && <p className="text-xs opacity-90 mt-1">{result.hint}</p>}
          </div>
        )}

        {/* TOTP input — hide after a final result; user can close or retry */}
        {!result && <TotpPrompt value={totp} onChange={setTotp} error={totpError} autoFocus />}

        <div className="flex justify-end gap-2">
          {result ? (
            <>
              <Button
                type="button"
                variant="outline"
                onClick={() => {
                  setResult(null);
                  setTotp("");
                  setTotpError(null);
                }}
              >
                Повторить
              </Button>
              <Button type="button" onClick={onClose}>
                Готово
              </Button>
            </>
          ) : (
            <>
              <Button type="button" variant="outline" onClick={onClose} disabled={isSubmitting}>
                Отмена
              </Button>
              <Button
                type="submit"
                disabled={totp.length !== 6 || isSubmitting}
                aria-busy={isSubmitting}
              >
                {isSubmitting ? (
                  <>
                    <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                    {isExchange
                      ? "Подключение к Exchange..."
                      : channel === "email"
                        ? "Отправляем письмо..."
                        : "Запуск теста..."}
                  </>
                ) : (
                  "Запустить тест"
                )}
              </Button>
            </>
          )}
        </div>
      </form>
    </Dialog>
  );
}

// ── Toggle dialog (PENDING_INVITES) ───────────────────────────────────────────

interface PendingInvitesDialogProps {
  open: boolean;
  onClose: () => void;
}

function PendingInvitesDialog({ open, onClose }: PendingInvitesDialogProps) {
  return (
    <Dialog open={open} onClose={onClose} title="Невозможно отключить канал">
      <div className="flex flex-col gap-4">
        <div className="flex items-start gap-3 rounded bg-amber-50 border border-amber-300 px-3 py-3">
          <AlertTriangle className="h-5 w-5 text-amber-600 mt-0.5 shrink-0" aria-hidden />
          <p className="text-sm text-amber-900">
            Невозможно отключить канал — есть неподтверждённые приглашения через него. Дождитесь
            истечения срока приглашений или используйте другой канал.
          </p>
        </div>
        <div className="flex justify-end">
          <Button onClick={onClose}>Понятно</Button>
        </div>
      </div>
    </Dialog>
  );
}

// ── Toggle with TOTP dialog ───────────────────────────────────────────────────

interface ToggleDialogProps {
  open: boolean;
  channel: ChannelName;
  targetEnabled: boolean;
  onClose: () => void;
  onToggled: () => void;
  onPendingInvites: () => void;
}

function ToggleDialog({
  open,
  channel,
  targetEnabled,
  onClose,
  onToggled,
  onPendingInvites,
}: ToggleDialogProps) {
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
    if (totp.length !== 6) {
      setTotpError("Введите 6-значный TOTP-код");
      return;
    }
    setIsSubmitting(true);
    setTotpError(null);
    try {
      await patchChannel(channel, { enabled: targetEnabled, totp_code: totp });
      onToggled();
      onClose();
    } catch (err) {
      if (err instanceof ChannelApiResponseError && err.code === "PENDING_INVITES") {
        onClose();
        onPendingInvites();
      } else if (
        err instanceof ChannelApiResponseError &&
        (err.code === "REMFA_REQUIRED" || err.code === "REMFA_REPLAY")
      ) {
        setTotp("");
        setTotpError(lookupErrorMsg(err.code));
      } else {
        toast.show({ title: mapError(err), variant: "destructive" });
      }
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={`${targetEnabled ? "Включить" : "Отключить"} канал ${CHANNEL_DISPLAY_NAME[channel]}`}
      description="Требуется подтверждение кодом TOTP."
    >
      <form onSubmit={handleSubmit} noValidate className="flex flex-col gap-4">
        <TotpPrompt value={totp} onChange={setTotp} error={totpError} autoFocus />
        <div className="flex justify-end gap-2">
          <Button type="button" variant="outline" onClick={onClose} disabled={isSubmitting}>
            Отмена
          </Button>
          <Button
            type="submit"
            disabled={totp.length !== 6 || isSubmitting}
            aria-busy={isSubmitting}
          >
            {isSubmitting ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                Подтверждение...
              </>
            ) : (
              "Подтвердить"
            )}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

// ── Channel icons ─────────────────────────────────────────────────────────────

const CHANNEL_META: Record<
  ChannelName,
  {
    label: string;
    hint: string;
    Icon: React.FC<React.SVGProps<SVGSVGElement>>;
    canTest: boolean;
  }
> = {
  email: {
    label: "Email",
    hint: "Email-уведомления о сроках документов",
    Icon: Mail,
    canTest: true,
  },
  telegram: {
    label: "Telegram",
    hint: "Уведомления через Telegram-бот",
    Icon: MessageCircle,
    canTest: true,
  },
  dion: {
    label: "Dion",
    hint: "Уведомления через корпоративный мессенджер Dion",
    Icon: Webhook,
    canTest: true,
  },
  exchange_calendar: {
    label: "Календарь Exchange",
    hint: "События дедлайнов в общем календаре отдела",
    Icon: Calendar,
    canTest: true,
  },
  ics_feed: {
    label: "ICS подписка",
    hint: "Календарная подписка по URL — для тех, кто не может настроить Exchange",
    Icon: Rss,
    canTest: false,
  },
};

// ── Toggle switch ─────────────────────────────────────────────────────────────

interface ToggleSwitchProps {
  checked: boolean;
  disabled?: boolean;
  onChange: (next: boolean) => void;
  ariaLabel: string;
}

function ToggleSwitch({ checked, disabled, onChange, ariaLabel }: ToggleSwitchProps) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={ariaLabel}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cn(
        "relative inline-flex h-6 w-11 shrink-0 cursor-pointer rounded-full border-2 border-transparent",
        "transition-colors duration-200 ease-in-out",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
        checked ? "bg-primary" : "bg-muted",
        disabled && "cursor-not-allowed opacity-50",
      )}
    >
      <span
        aria-hidden
        className={cn(
          "pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow ring-0",
          "transition duration-200 ease-in-out",
          checked ? "translate-x-5" : "translate-x-0",
        )}
      />
    </button>
  );
}

// ── Channel card ──────────────────────────────────────────────────────────────

interface ChannelCardProps {
  info: ChannelInfo;
  onRefresh: () => void;
}

function ChannelCard({ info, onRefresh }: ChannelCardProps) {
  const meta = CHANNEL_META[info.channel];
  const { label, hint, Icon } = meta;

  const [editOpen, setEditOpen] = React.useState(false);
  const [testOpen, setTestOpen] = React.useState(false);
  const [toggleOpen, setToggleOpen] = React.useState(false);
  const [pendingOpen, setPendingOpen] = React.useState(false);
  const [pendingEnabled, setPendingEnabled] = React.useState(false);
  // ICS URL display after save
  const [icsSavedToken, setIcsSavedToken] = React.useState<string | null>(null);

  const handleToggle = (next: boolean) => {
    setPendingEnabled(next);
    setToggleOpen(true);
  };

  const isIcsFeed = info.channel === "ics_feed";
  const isExchange = info.channel === "exchange_calendar";
  const canRunTest = info.enabled && info.configured && info.status !== "decrypt_error";

  return (
    <>
      <Card>
        <CardHeader className="pb-3">
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <Icon className="h-5 w-5 text-muted-foreground" aria-hidden />
              <CardTitle className="text-base">{label}</CardTitle>
            </div>
            <ToggleSwitch
              checked={info.enabled}
              ariaLabel={`${info.enabled ? "Отключить" : "Включить"} канал ${label}`}
              onChange={handleToggle}
            />
          </div>
        </CardHeader>

        <CardContent className="flex flex-col gap-3">
          <p className="text-xs text-muted-foreground">{hint}</p>

          <div className="flex items-center gap-2 flex-wrap">
            <ChannelStatusBadge info={info} />
            {info.updated_at && (
              <span className="text-xs text-muted-foreground">
                Обновлён {format(new Date(info.updated_at), "dd.MM.yyyy HH:mm")}
              </span>
            )}
          </div>

          <div className="flex gap-2 mt-1">
            <Button size="sm" variant="outline" onClick={() => setEditOpen(true)}>
              Настроить
            </Button>
            {isIcsFeed ? (
              <Button
                size="sm"
                variant="outline"
                disabled
                aria-disabled
                title="Тест для ICS не нужен — feed работает по подписке"
              >
                Тест
              </Button>
            ) : (
              <Button
                size="sm"
                variant="outline"
                disabled={!canRunTest}
                aria-disabled={!canRunTest}
                onClick={() => setTestOpen(true)}
                title={!canRunTest ? "Включите и настройте канал перед тестированием" : undefined}
              >
                Тест
              </Button>
            )}
          </div>
        </CardContent>
      </Card>

      {/* Edit dialog — channel-specific */}
      {info.channel === "email" && (
        <EmailEditDialog
          open={editOpen}
          isFirstTime={!info.configured}
          onClose={() => setEditOpen(false)}
          onSaved={onRefresh}
        />
      )}
      {info.channel === "telegram" && (
        <TelegramEditDialog
          open={editOpen}
          isFirstTime={!info.configured}
          onClose={() => setEditOpen(false)}
          onSaved={onRefresh}
        />
      )}
      {info.channel === "dion" && (
        <DionEditDialog
          open={editOpen}
          isFirstTime={!info.configured}
          onClose={() => setEditOpen(false)}
          onSaved={onRefresh}
        />
      )}
      {isExchange && (
        <ExchangeCalendarEditDialog
          open={editOpen}
          isFirstTime={!info.configured}
          onClose={() => setEditOpen(false)}
          onSaved={onRefresh}
        />
      )}
      {isIcsFeed && (
        <IcsFeedEditDialog
          open={editOpen}
          isFirstTime={!info.configured}
          onClose={() => setEditOpen(false)}
          onSaved={(token) => {
            onRefresh();
            setIcsSavedToken(token);
          }}
        />
      )}

      {/* ICS URL info block after save */}
      {isIcsFeed && icsSavedToken !== null && (
        <IcsFeedUrlBlock token={icsSavedToken} onClose={() => setIcsSavedToken(null)} />
      )}

      {/* Test dialog — exchange and standard */}
      {(info.channel === "email" ||
        info.channel === "telegram" ||
        info.channel === "dion" ||
        info.channel === "exchange_calendar") && (
        <TestDialog open={testOpen} channel={info.channel} onClose={() => setTestOpen(false)} />
      )}

      {/* Toggle dialog */}
      <ToggleDialog
        open={toggleOpen}
        channel={info.channel}
        targetEnabled={pendingEnabled}
        onClose={() => setToggleOpen(false)}
        onToggled={onRefresh}
        onPendingInvites={() => setPendingOpen(true)}
      />

      {/* Pending invites info */}
      <PendingInvitesDialog open={pendingOpen} onClose={() => setPendingOpen(false)} />
    </>
  );
}

// ── Skeleton ──────────────────────────────────────────────────────────────────

function ChannelSkeleton() {
  return (
    <Card className="animate-pulse">
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <div className="h-5 w-5 rounded bg-muted" />
            <div className="h-4 w-20 rounded bg-muted" />
          </div>
          <div className="h-6 w-11 rounded-full bg-muted" />
        </div>
      </CardHeader>
      <CardContent>
        <div className="h-5 w-24 rounded bg-muted mb-3" />
        <div className="flex gap-2">
          <div className="h-8 w-20 rounded bg-muted" />
          <div className="h-8 w-16 rounded bg-muted" />
        </div>
      </CardContent>
    </Card>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────

export function ChannelsPage() {
  const qc = useQueryClient();

  const {
    data: channels,
    isLoading,
    isError,
  } = useQuery({
    queryKey: ["admin", "channels"],
    queryFn: listChannels,
    staleTime: 15_000,
  });

  const handleRefresh = () => {
    qc.invalidateQueries({ queryKey: ["admin", "channels"] });
  };

  // Ensure all 5 channels appear even if backend returns partial list
  const channelMap = React.useMemo(() => {
    const m = new Map<ChannelName, ChannelInfo>();
    for (const ch of ALL_CHANNELS) m.set(ch, defaultChannelInfo(ch));
    if (channels) {
      for (const info of channels) m.set(info.channel, info);
    }
    return m;
  }, [channels]);

  // For the multi-channel warning banner we need the full list
  const channelList = React.useMemo(
    () => ALL_CHANNELS.map((ch) => channelMap.get(ch) ?? defaultChannelInfo(ch)),
    [channelMap],
  );

  return (
    <div className="max-w-5xl mx-auto px-4 py-8">
      <div className="mb-6">
        <h1 className="text-2xl font-semibold">Каналы уведомлений</h1>
        <p className="mt-1 text-muted-foreground">
          Настройте откуда приходят напоминания о сроках документов.
        </p>
      </div>

      {isError && (
        <div
          role="alert"
          className="rounded bg-destructive/10 border border-destructive px-3 py-2 mb-6"
        >
          <p className="text-sm text-destructive">Не удалось загрузить список каналов.</p>
        </div>
      )}

      {/* Multi-channel warning banner — only when exactly 1 channel enabled */}
      {!isLoading && channels && <MultiChannelWarningBanner channels={channelList} />}

      <div aria-busy={isLoading} className="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3">
        {isLoading
          ? ALL_CHANNELS.map((ch) => <ChannelSkeleton key={ch} />)
          : ALL_CHANNELS.map((ch) => {
              const info = channelMap.get(ch) ?? defaultChannelInfo(ch);
              return <ChannelCard key={ch} info={info} onRefresh={handleRefresh} />;
            })}
      </div>
    </div>
  );
}
