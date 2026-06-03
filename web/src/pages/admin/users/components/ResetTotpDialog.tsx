// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * ResetTotpDialog — reset a user's TOTP (forces re-enrollment). Admin only.
 * Requires admin re-MFA (ADR-0003 §5, US-16).
 */

import * as React from "react";
import { useTranslation } from "react-i18next";
import { ApiResponseError, adminResetTotp } from "@/features/auth/api";
import type { AdminUser } from "@/features/auth/types";
import { Button } from "@/shared/ui/button";
import { Dialog } from "@/shared/ui/dialog";
import { toast } from "@/shared/ui/toast";
import { ConfirmReMfaDialog } from "./ConfirmReMfaDialog";

interface ResetTotpDialogProps {
  open: boolean;
  user: AdminUser | null;
  onClose: () => void;
  onReset: () => void;
}

export function ResetTotpDialog({ open, user, onClose, onReset }: ResetTotpDialogProps) {
  const { t } = useTranslation();
  const [showReMfa, setShowReMfa] = React.useState(false);
  const [isResetting, setIsResetting] = React.useState(false);

  const handleReMfaConfirm = async (reMfaToken: string) => {
    if (!user) return;
    setShowReMfa(false);
    setIsResetting(true);
    try {
      await adminResetTotp(user.id, reMfaToken);
      toast.show({ title: t("admin.totp_reset_success"), variant: "success" });
      onReset();
      onClose();
    } catch (err) {
      if (err instanceof ApiResponseError && err.status === 401) {
        toast.show({ title: t("login_errors.invalid_credentials"), variant: "destructive" });
      } else {
        toast.show({ title: t("login_errors.network_error_title"), variant: "destructive" });
      }
    } finally {
      setIsResetting(false);
    }
  };

  return (
    <>
      <Dialog
        open={open && !showReMfa}
        onClose={onClose}
        title={t("admin.reset_totp_title")}
        {...(user ? { description: t("admin.reset_totp_description", { email: user.email }) } : {})}
      >
        <div className="flex flex-col gap-4">
          <p className="text-sm text-muted-foreground">{t("admin.reset_totp_consequences")}</p>
          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={onClose}>
              {t("common.cancel")}
            </Button>
            <Button
              variant="destructive"
              onClick={() => setShowReMfa(true)}
              disabled={isResetting}
              aria-busy={isResetting}
            >
              {t("admin.reset_totp_submit")}
            </Button>
          </div>
        </div>
      </Dialog>

      <ConfirmReMfaDialog
        open={showReMfa}
        onClose={() => setShowReMfa(false)}
        onConfirm={handleReMfaConfirm}
      />
    </>
  );
}
