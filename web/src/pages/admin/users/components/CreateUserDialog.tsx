// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * CreateUserDialog — create a new user with delivery selector (US-8/9/13).
 *
 * Flow:
 *   form (email, fullName, role, delivery) → inline TOTP → API → result
 *
 * On delivery="auto":
 *   - 201 with channel_used → toast "Приглашение отправлено по ${channel}"
 *   - 409 NO_CHANNEL → inline error
 *
 * On delivery="show-otp":
 *   - 201 with otp → OtpDisplayModal (cannot be dismissed by Esc/backdrop)
 *
 * TOTP errors (REMFA_REQUIRED / REMFA_REPLAY) clear the code field and refocus.
 */

import { zodResolver } from "@hookform/resolvers/zod";
import { useQuery } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import * as React from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";
import {
  ChannelApiResponseError,
  type InviteUserAutoResponse,
  type InviteUserOtpResponse,
  inviteUser,
  listChannels,
} from "@/features/admin/channels/api";
import { cn } from "@/shared/lib/cn";
import { Button } from "@/shared/ui/button";
import { Dialog } from "@/shared/ui/dialog";
import { Input } from "@/shared/ui/input";
import { Label } from "@/shared/ui/label";
import { toast } from "@/shared/ui/toast";
import { OtpDisplayModal } from "./OtpDisplayModal";

// ── Error map ─────────────────────────────────────────────────────────────────

const CHANNEL_ERROR_MESSAGES: Record<string, string> = {
  NO_CHANNEL:
    "Нет включённых каналов. Настройте канал на странице /admin/channels или выберите «Показать код».",
  REMFA_REQUIRED: "Код TOTP обязателен для этой операции.",
  REMFA_REPLAY: "Этот TOTP-код уже был использован. Дождитесь следующего кода (30 с).",
};

// ── Schema ────────────────────────────────────────────────────────────────────

const schema = z.object({
  email: z.string().min(1, "Введите адрес электронной почты").email("Некорректный адрес"),
  fullName: z.string().min(1, "Введите ФИО"),
  role: z.enum(["admin", "editor", "viewer"] as const),
  delivery: z.enum(["auto", "show-otp"] as const),
});

type FormValues = z.infer<typeof schema>;

// ── Props ─────────────────────────────────────────────────────────────────────

interface CreateUserDialogProps {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
  /** Pre-select admin role when coming from the admin warning banner CTA */
  defaultRole?: "admin" | "editor" | "viewer" | undefined;
}

// ── Component ─────────────────────────────────────────────────────────────────

export function CreateUserDialog({ open, onClose, onCreated, defaultRole }: CreateUserDialogProps) {
  const [totp, setTotp] = React.useState("");
  const [totpError, setTotpError] = React.useState<string | null>(null);
  const [channelError, setChannelError] = React.useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = React.useState(false);

  // OTP modal state — cleared immediately on close (US-13: no global state retention)
  const [otpModal, setOtpModal] = React.useState<{ otp: string; ttlMinutes: number } | null>(null);

  const totpRef = React.useRef<HTMLInputElement>(null);

  // Channels query — fetched once when dialog opens to determine default delivery
  const { data: channels } = useQuery({
    queryKey: ["admin", "channels"],
    queryFn: listChannels,
    staleTime: 15_000,
    enabled: open,
  });

  const hasEnabledChannel = React.useMemo(
    () => channels?.some((c) => c.enabled) ?? false,
    [channels],
  );

  const {
    register,
    handleSubmit,
    reset,
    watch,
    setValue,
    formState: { errors },
  } = useForm<FormValues>({
    resolver: zodResolver(schema),
    mode: "onChange",
    defaultValues: { role: defaultRole ?? "viewer", delivery: "auto" },
  });

  const delivery = watch("delivery");

  // Update default delivery when channels load
  React.useEffect(() => {
    if (channels !== undefined) {
      setValue("delivery", hasEnabledChannel ? "auto" : "show-otp");
    }
  }, [channels, hasEnabledChannel, setValue]);

  React.useEffect(() => {
    if (open) {
      reset({ role: defaultRole ?? "viewer", delivery: hasEnabledChannel ? "auto" : "show-otp" });
      setTotp("");
      setTotpError(null);
      setChannelError(null);
      setOtpModal(null);
    }
  }, [open, reset, hasEnabledChannel, defaultRole]);

  const onSubmit = async (values: FormValues) => {
    if (totp.length !== 6) {
      setTotpError("Введите 6-значный TOTP-код");
      totpRef.current?.focus();
      return;
    }
    setIsSubmitting(true);
    setTotpError(null);
    setChannelError(null);

    try {
      const result = await inviteUser({
        email: values.email,
        full_name: values.fullName,
        role: values.role,
        delivery: values.delivery,
        totp_code: totp,
      });

      if ("otp" in result) {
        // show-otp path
        const otpResult = result as InviteUserOtpResponse;
        setOtpModal({ otp: otpResult.otp, ttlMinutes: otpResult.otp_ttl_minutes });
      } else {
        // auto path
        const autoResult = result as InviteUserAutoResponse;
        const channelLabel: Record<string, string> = {
          email: "email",
          telegram: "Telegram",
          dion: "Dion",
        };
        toast.show({
          title: "Приглашение отправлено",
          description: `Отправлено по ${channelLabel[autoResult.channel_used] ?? autoResult.channel_used} на ${values.email}`,
          variant: "success",
        });
        onCreated();
        onClose();
      }
    } catch (err) {
      if (err instanceof ChannelApiResponseError) {
        if (err.code === "REMFA_REQUIRED" || err.code === "REMFA_REPLAY") {
          setTotp("");
          setTotpError(CHANNEL_ERROR_MESSAGES[err.code] ?? "Ошибка TOTP-кода.");
          totpRef.current?.focus();
        } else if (err.code === "NO_CHANNEL") {
          setChannelError(CHANNEL_ERROR_MESSAGES.NO_CHANNEL ?? "Нет включённых каналов.");
        } else if (err.status === 409) {
          setChannelError("Пользователь с таким email уже существует.");
        } else {
          toast.show({ title: "Ошибка подключения. Попробуйте снова.", variant: "destructive" });
        }
      } else {
        toast.show({ title: "Ошибка подключения. Попробуйте снова.", variant: "destructive" });
      }
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleOtpModalClose = () => {
    // Clear OTP from local state immediately (US-13)
    setOtpModal(null);
    onCreated();
    onClose();
  };

  // If OTP modal is shown, render it instead of the main dialog
  if (otpModal) {
    return (
      <OtpDisplayModal
        otp={otpModal.otp}
        ttlMinutes={otpModal.ttlMinutes}
        onClose={handleOtpModalClose}
      />
    );
  }

  return (
    <Dialog open={open} onClose={onClose} title="Новый пользователь">
      <form onSubmit={handleSubmit(onSubmit)} noValidate className="flex flex-col gap-4">
        {/* Email */}
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="cu-email">Электронная почта</Label>
          <Input
            id="cu-email"
            type="email"
            autoComplete="off"
            aria-invalid={errors.email ? true : undefined}
            aria-describedby={errors.email ? "cu-email-err" : undefined}
            className={cn(errors.email && "border-destructive")}
            {...register("email")}
          />
          {errors.email?.message && (
            <p id="cu-email-err" role="alert" className="text-xs text-destructive font-medium">
              {errors.email.message}
            </p>
          )}
        </div>

        {/* Full name */}
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="cu-name">ФИО</Label>
          <Input
            id="cu-name"
            type="text"
            aria-invalid={errors.fullName ? true : undefined}
            aria-describedby={errors.fullName ? "cu-name-err" : undefined}
            className={cn(errors.fullName && "border-destructive")}
            {...register("fullName")}
          />
          {errors.fullName?.message && (
            <p id="cu-name-err" role="alert" className="text-xs text-destructive font-medium">
              {errors.fullName.message}
            </p>
          )}
        </div>

        {/* Role */}
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="cu-role">Роль</Label>
          <select
            id="cu-role"
            className="h-9 rounded-md border border-input bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            {...register("role")}
          >
            <option value="viewer">Читатель</option>
            <option value="editor">Редактор</option>
            <option value="admin">Администратор</option>
          </select>
        </div>

        {/* Delivery selector */}
        <fieldset className="flex flex-col gap-2">
          <legend className="text-sm font-medium">Способ доставки приглашения</legend>
          <label className="flex items-start gap-2.5 cursor-pointer">
            <input
              type="radio"
              value="auto"
              disabled={!hasEnabledChannel}
              className="mt-0.5 h-4 w-4 shrink-0 border-input focus-visible:ring-2 focus-visible:ring-ring"
              {...register("delivery")}
            />
            <span className="flex flex-col gap-0.5">
              <span className={cn("text-sm", !hasEnabledChannel && "text-muted-foreground")}>
                Отправить по каналу{" "}
                <span className="text-xs text-muted-foreground">(рекомендуется)</span>
              </span>
              {!hasEnabledChannel && (
                <span className="text-xs text-muted-foreground">
                  Сначала настройте канал на странице /admin/channels
                </span>
              )}
            </span>
          </label>
          <label className="flex items-center gap-2.5 cursor-pointer">
            <input
              type="radio"
              value="show-otp"
              className="h-4 w-4 shrink-0 border-input focus-visible:ring-2 focus-visible:ring-ring"
              {...register("delivery")}
            />
            <span className="text-sm">Показать одноразовый код</span>
          </label>
        </fieldset>

        {/* Channel error (NO_CHANNEL or duplicate) */}
        {channelError && (
          <div
            role="alert"
            className="rounded bg-destructive/10 border border-destructive px-3 py-2"
          >
            <p className="text-sm text-destructive font-medium">{channelError}</p>
          </div>
        )}

        {/* TOTP */}
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="cu-totp">Код из приложения-аутентификатора</Label>
          <Input
            id="cu-totp"
            ref={totpRef}
            type="text"
            inputMode="numeric"
            autoComplete="one-time-code"
            maxLength={6}
            placeholder="123456"
            value={totp}
            onChange={(e) => {
              setTotpError(null);
              setTotp(e.target.value.replace(/\D/g, "").slice(0, 6));
            }}
            aria-invalid={totpError ? true : undefined}
            aria-describedby={totpError ? "cu-totp-err" : "cu-totp-hint"}
            className={cn("font-mono text-center", totpError && "border-destructive")}
          />
          {totpError ? (
            <p id="cu-totp-err" role="alert" className="text-xs text-destructive font-medium">
              {totpError}
            </p>
          ) : (
            <p id="cu-totp-hint" className="text-xs text-muted-foreground">
              Подтвердите действие кодом из приложения-аутентификатора
            </p>
          )}
        </div>

        <div className="flex justify-end gap-2">
          <Button type="button" variant="outline" onClick={onClose} disabled={isSubmitting}>
            Отмена
          </Button>
          <Button
            type="submit"
            disabled={isSubmitting || totp.length !== 6}
            aria-busy={isSubmitting}
          >
            {isSubmitting ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                {delivery === "auto" ? "Отправка..." : "Создание..."}
              </>
            ) : delivery === "auto" ? (
              "Отправить приглашение"
            ) : (
              "Создать и показать код"
            )}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
