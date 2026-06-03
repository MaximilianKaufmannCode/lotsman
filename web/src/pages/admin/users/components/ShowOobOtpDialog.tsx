// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * ShowOobOtpDialog — displays the OOB OTP once after user creation or password reset.
 *
 * Admin must acknowledge "I have delivered this OTP to the user" before closing.
 * The dialog cannot be dismissed via Escape or backdrop click until acknowledged.
 * This prevents accidental loss of the single-use credential (ADR-0003 §5).
 */

import * as React from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/shared/ui/button";
import { Dialog } from "@/shared/ui/dialog";

interface ShowOobOtpDialogProps {
  open: boolean;
  /** oob_otp value from the API response */
  otp: string;
  userEmail: string;
  onClose: () => void;
}

export function ShowOobOtpDialog({ open, otp, userEmail, onClose }: ShowOobOtpDialogProps) {
  const { t } = useTranslation();
  const [confirmed, setConfirmed] = React.useState(false);

  React.useEffect(() => {
    if (open) setConfirmed(false);
  }, [open]);

  // Override onClose to require explicit acknowledgement
  const handleClose = () => {
    if (confirmed) onClose();
  };

  return (
    <Dialog
      open={open}
      onClose={handleClose}
      title={t("admin.oob_otp_title")}
      description={t("admin.oob_otp_description", { email: userEmail })}
    >
      <div className="flex flex-col gap-4">
        <div
          role="alert"
          aria-live="assertive"
          className="rounded bg-status-soon/10 border border-status-soon px-3 py-2 text-sm text-status-soon font-medium"
        >
          {t("admin.oob_otp_shown_once")}
        </div>

        <div className="rounded bg-muted border border-border px-4 py-3 text-center">
          <p id="oob-otp-label" className="text-xs text-muted-foreground mb-1">
            {t("admin.oob_otp_label")}
          </p>
          <output
            aria-labelledby="oob-otp-label"
            className="font-mono text-2xl font-bold tracking-widest select-all block mt-1"
          >
            {otp}
          </output>
        </div>

        <p className="text-sm text-muted-foreground">{t("admin.oob_otp_instructions")}</p>

        <label className="flex items-center gap-2 cursor-pointer select-none text-sm">
          <input
            type="checkbox"
            checked={confirmed}
            onChange={(e) => setConfirmed(e.target.checked)}
            className="h-4 w-4 rounded border border-input focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          />
          {t("admin.oob_otp_acknowledge")}
        </label>

        <Button onClick={handleClose} disabled={!confirmed} className="w-full">
          {t("admin.oob_otp_close")}
        </Button>
      </div>
    </Dialog>
  );
}
