// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * MaintenanceConfirmDialog — reusable double-gate dialog for destructive ops.
 *
 * Gate 1: TOTP (6 digits)
 * Gate 2: Typed literal confirmation (must match `expected` exactly)
 *
 * Submit is disabled until BOTH conditions are satisfied.
 * On REMFA_REPLAY → clears + focuses TOTP field.
 * On CONFIRMATION_MISMATCH → inline error below confirmation field.
 */

import { AlertTriangle, Loader2 } from "lucide-react";
import * as React from "react";
import { useTranslation } from "react-i18next";
import { SystemApiResponseError } from "@/features/system/api";
import { cn } from "@/shared/lib/cn";
import { Button } from "@/shared/ui/button";
import { Dialog } from "@/shared/ui/dialog";
import { Input } from "@/shared/ui/input";
import { Label } from "@/shared/ui/label";

interface MaintenanceConfirmDialogProps {
  open: boolean;
  onClose: () => void;
  title: string;
  description?: string | undefined;
  /** The literal string the user must type to confirm */
  expected: string;
  /** Called with (totp_code, confirmation) — should throw SystemApiResponseError on failure */
  onConfirm: (totpCode: string, confirmation: string) => Promise<void>;
}

export function MaintenanceConfirmDialog({
  open,
  onClose,
  title,
  description,
  expected,
  onConfirm,
}: MaintenanceConfirmDialogProps) {
  const { t } = useTranslation();
  const [totp, setTotp] = React.useState("");
  const [confirmation, setConfirmation] = React.useState("");
  const [totpError, setTotpError] = React.useState<string | null>(null);
  const [confirmError, setConfirmError] = React.useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = React.useState(false);
  const totpRef = React.useRef<HTMLInputElement>(null);

  // Reset on open
  React.useEffect(() => {
    if (open) {
      setTotp("");
      setConfirmation("");
      setTotpError(null);
      setConfirmError(null);
      setIsSubmitting(false);
    }
  }, [open]);

  const canSubmit = totp.length === 6 && confirmation === expected && !isSubmitting;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!canSubmit) return;

    setTotpError(null);
    setConfirmError(null);
    setIsSubmitting(true);

    try {
      await onConfirm(totp, confirmation);
    } catch (err) {
      if (err instanceof SystemApiResponseError) {
        if (err.code === "REMFA_REPLAY" || err.code === "REMFA_REQUIRED") {
          setTotp("");
          setTotpError(t("system.error_totp_replay"));
          setTimeout(() => totpRef.current?.focus(), 50);
        } else if (err.code === "CONFIRMATION_MISMATCH") {
          setConfirmError(t("system.error_confirmation_mismatch"));
        } else {
          setTotpError(err.detail);
        }
      } else {
        setTotpError(t("login_errors.network_error_title"));
      }
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onClose={onClose} title={title} description={description}>
      {/* Warning banner */}
      <div
        role="alert"
        className="mb-4 flex items-start gap-3 rounded-lg border border-amber-300 bg-amber-50 px-4 py-3"
      >
        <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-amber-600" aria-hidden />
        <p className="text-sm font-medium text-amber-900">{t("system.destructive_warning")}</p>
      </div>

      <form onSubmit={handleSubmit} noValidate className="flex flex-col gap-4">
        {/* TOTP input */}
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="maint-totp">{t("system.totp_label")}</Label>
          <Input
            id="maint-totp"
            ref={totpRef}
            type="text"
            inputMode="numeric"
            autoComplete="one-time-code"
            maxLength={6}
            placeholder="123456"
            value={totp}
            onChange={(e) => {
              setTotp(e.target.value.replace(/\D/g, "").slice(0, 6));
              setTotpError(null);
            }}
            aria-invalid={totpError ? true : undefined}
            aria-describedby={totpError ? "maint-totp-error" : "maint-totp-hint"}
            className={cn("font-mono text-center", totpError && "border-destructive")}
          />
          {totpError ? (
            <p id="maint-totp-error" role="alert" className="text-xs font-medium text-destructive">
              {totpError}
            </p>
          ) : (
            <p id="maint-totp-hint" className="text-xs text-muted-foreground">
              {t("system.totp_hint")}
            </p>
          )}
        </div>

        {/* Confirmation input */}
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="maint-confirm">{t("system.confirmation_label")}</Label>
          <Input
            id="maint-confirm"
            type="text"
            autoComplete="off"
            placeholder={t("system.confirmation_placeholder", { expected })}
            value={confirmation}
            onChange={(e) => {
              setConfirmation(e.target.value);
              setConfirmError(null);
            }}
            aria-invalid={confirmError ? true : undefined}
            aria-describedby={confirmError ? "maint-confirm-error" : "maint-confirm-hint"}
            className={cn("font-mono", confirmError && "border-destructive")}
          />
          {confirmError ? (
            <p
              id="maint-confirm-error"
              role="alert"
              className="text-xs font-medium text-destructive"
            >
              {confirmError}
            </p>
          ) : (
            <p id="maint-confirm-hint" className="text-xs text-muted-foreground">
              {t("system.confirmation_hint", { expected })}
            </p>
          )}
        </div>

        <div className="flex justify-end gap-2">
          <Button type="button" variant="outline" onClick={onClose} disabled={isSubmitting}>
            {t("common.cancel")}
          </Button>
          <Button
            type="submit"
            variant="destructive"
            disabled={!canSubmit}
            aria-busy={isSubmitting}
            data-testid="maint-submit"
          >
            {isSubmitting ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                {t("system.submitting")}
              </>
            ) : (
              t("system.confirm_action")
            )}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
