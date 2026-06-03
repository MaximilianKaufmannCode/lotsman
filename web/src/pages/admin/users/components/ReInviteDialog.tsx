// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * ReInviteDialog — re-send invitation for a pending user (US-10).
 *
 * Mirrors CreateUserDialog delivery selector flow but uses
 * POST /api/v1/admin/users/{id}/invite instead.
 *
 * Errors:
 *   - USER_ACTIVATED → inline error (user already enrolled)
 *   - NO_CHANNEL → inline error
 *   - REMFA_REQUIRED / REMFA_REPLAY → clear TOTP, refocus
 */

import { useQuery } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import * as React from "react";
import {
  ChannelApiResponseError,
  type InviteUserAutoResponse,
  type InviteUserOtpResponse,
  listChannels,
  reInviteUser,
} from "@/features/admin/channels/api";
import type { AdminUser } from "@/features/auth/types";
import { cn } from "@/shared/lib/cn";
import { Button } from "@/shared/ui/button";
import { Dialog } from "@/shared/ui/dialog";
import { Input } from "@/shared/ui/input";
import { Label } from "@/shared/ui/label";
import { toast } from "@/shared/ui/toast";
import { OtpDisplayModal } from "./OtpDisplayModal";

const ERROR_MESSAGES: Record<string, string> = {
  NO_CHANNEL:
    "Нет включённых каналов. Настройте канал на странице /admin/channels или выберите «Показать код».",
  REMFA_REQUIRED: "Код TOTP обязателен для этой операции.",
  REMFA_REPLAY: "Этот TOTP-код уже был использован. Дождитесь следующего кода (30 с).",
  USER_ACTIVATED: "Пользователь уже активирован — используйте сброс пароля.",
};

interface ReInviteDialogProps {
  open: boolean;
  user: AdminUser | null;
  onClose: () => void;
  onReinvited: () => void;
}

export function ReInviteDialog({ open, user, onClose, onReinvited }: ReInviteDialogProps) {
  const [delivery, setDelivery] = React.useState<"auto" | "show-otp">("auto");
  const [totp, setTotp] = React.useState("");
  const [totpError, setTotpError] = React.useState<string | null>(null);
  const [inlineError, setInlineError] = React.useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = React.useState(false);
  const [otpModal, setOtpModal] = React.useState<{ otp: string; ttlMinutes: number } | null>(null);

  const totpRef = React.useRef<HTMLInputElement>(null);

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

  React.useEffect(() => {
    if (open) {
      setDelivery(hasEnabledChannel ? "auto" : "show-otp");
      setTotp("");
      setTotpError(null);
      setInlineError(null);
      setOtpModal(null);
    }
  }, [open, hasEnabledChannel]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!user) return;
    if (totp.length !== 6) {
      setTotpError("Введите 6-значный TOTP-код");
      totpRef.current?.focus();
      return;
    }
    setIsSubmitting(true);
    setTotpError(null);
    setInlineError(null);

    try {
      const result = await reInviteUser(user.id, { delivery, totp_code: totp });

      if ("otp" in result) {
        const r = result as InviteUserOtpResponse;
        setOtpModal({ otp: r.otp, ttlMinutes: r.otp_ttl_minutes });
      } else {
        const r = result as InviteUserAutoResponse;
        const channelLabel: Record<string, string> = {
          email: "email",
          telegram: "Telegram",
          dion: "Dion",
        };
        toast.show({
          title: "Приглашение отправлено повторно",
          description: `Отправлено по ${channelLabel[r.channel_used] ?? r.channel_used} на ${user.email}`,
          variant: "success",
        });
        onReinvited();
        onClose();
      }
    } catch (err) {
      if (err instanceof ChannelApiResponseError) {
        if (err.code === "REMFA_REQUIRED" || err.code === "REMFA_REPLAY") {
          setTotp("");
          setTotpError(ERROR_MESSAGES[err.code] ?? "Ошибка TOTP-кода.");
          totpRef.current?.focus();
        } else if (err.code === "USER_ACTIVATED" || err.code === "NO_CHANNEL") {
          setInlineError(ERROR_MESSAGES[err.code] ?? "Неизвестная ошибка.");
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

  const handleOtpClose = () => {
    setOtpModal(null);
    onReinvited();
    onClose();
  };

  if (otpModal) {
    return (
      <OtpDisplayModal
        otp={otpModal.otp}
        ttlMinutes={otpModal.ttlMinutes}
        onClose={handleOtpClose}
      />
    );
  }

  return (
    <Dialog
      open={open && !!user}
      onClose={onClose}
      title="Повторное приглашение"
      description={user ? `Повторно отправить приглашение для ${user.email}` : undefined}
    >
      <form onSubmit={handleSubmit} noValidate className="flex flex-col gap-4">
        {/* Delivery selector */}
        <fieldset className="flex flex-col gap-2">
          <legend className="text-sm font-medium">Способ доставки</legend>
          <label className="flex items-start gap-2.5 cursor-pointer">
            <input
              type="radio"
              value="auto"
              checked={delivery === "auto"}
              disabled={!hasEnabledChannel}
              onChange={() => setDelivery("auto")}
              className="mt-0.5 h-4 w-4 shrink-0"
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
              checked={delivery === "show-otp"}
              onChange={() => setDelivery("show-otp")}
              className="h-4 w-4 shrink-0"
            />
            <span className="text-sm">Показать одноразовый код</span>
          </label>
        </fieldset>

        {inlineError && (
          <div
            role="alert"
            className="rounded bg-destructive/10 border border-destructive px-3 py-2"
          >
            <p className="text-sm text-destructive font-medium">{inlineError}</p>
          </div>
        )}

        {/* TOTP */}
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="ri-totp">Код из приложения-аутентификатора</Label>
          <Input
            id="ri-totp"
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
            aria-describedby={totpError ? "ri-totp-err" : "ri-totp-hint"}
            className={cn("font-mono text-center", totpError && "border-destructive")}
          />
          {totpError ? (
            <p id="ri-totp-err" role="alert" className="text-xs text-destructive font-medium">
              {totpError}
            </p>
          ) : (
            <p id="ri-totp-hint" className="text-xs text-muted-foreground">
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
                Отправка...
              </>
            ) : (
              "Отправить повторно"
            )}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
