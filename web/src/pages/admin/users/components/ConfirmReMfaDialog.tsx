// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * ConfirmReMfaDialog — asks the admin for their current TOTP code before a
 * sensitive admin action (ADR-0003 §5, ASVS V2.7.5).
 *
 * IMPORTANT: this dialog hands the RAW 6-digit code to `onConfirm`. The admin
 * mutation endpoints are gated server-side by web-bff `_verify_re_mfa`, which
 * re-checks the code via /auth/mfa-check (single-use, anti-replay). The dialog
 * must NOT call /auth/re-mfa itself — doing so consumed the TOTP and returned a
 * re_mfa_token, which the BFF then rejected as a non-6-digit code (HTTP 422).
 */

import * as React from "react";
import { useTranslation } from "react-i18next";
import { cn } from "@/shared/lib/cn";
import { Button } from "@/shared/ui/button";
import { Dialog } from "@/shared/ui/dialog";
import { Input } from "@/shared/ui/input";
import { Label } from "@/shared/ui/label";

interface ConfirmReMfaDialogProps {
  open: boolean;
  onClose: () => void;
  /** Called with the raw 6-digit TOTP code (verified server-side by the BFF). */
  onConfirm: (totpCode: string) => void;
  title?: string | undefined;
  description?: string | undefined;
}

export function ConfirmReMfaDialog({
  open,
  onClose,
  onConfirm,
  title,
  description,
}: ConfirmReMfaDialogProps) {
  const { t } = useTranslation();
  const [code, setCode] = React.useState("");

  React.useEffect(() => {
    if (open) setCode("");
  }, [open]);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (code.length !== 6) return;
    // Hand off the raw code — the caller performs the action and the web-bff
    // re-MFA gate verifies it. Error feedback is surfaced by the caller (toast).
    onConfirm(code);
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={title ?? t("admin.re_mfa_title")}
      description={description ?? t("admin.re_mfa_description")}
    >
      <form
        onSubmit={handleSubmit}
        noValidate
        aria-label={t("admin.re_mfa_form_label")}
        className="flex flex-col gap-4"
      >
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="remfa-code">{t("profile.totp_confirm_label")}</Label>
          <Input
            id="remfa-code"
            type="text"
            inputMode="numeric"
            autoComplete="one-time-code"
            autoFocus
            maxLength={6}
            value={code}
            onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
            placeholder="123456"
            aria-describedby="remfa-hint"
            className={cn("font-mono text-center")}
          />
          <p id="remfa-hint" className="text-xs text-muted-foreground">
            {t("admin.re_mfa_hint")}
          </p>
        </div>

        <div className="flex gap-2 justify-end">
          <Button type="button" variant="outline" onClick={onClose}>
            {t("common.cancel")}
          </Button>
          <Button type="submit" disabled={code.length !== 6}>
            {t("admin.re_mfa_confirm")}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
