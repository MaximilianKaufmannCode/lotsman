// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * ResetPasswordDialog — admin password reset → issues new OOB OTP (US-7, ADR-0003 §5b).
 * Requires admin re-MFA.
 */

import * as React from "react";
import { useTranslation } from "react-i18next";
import { ApiResponseError, adminResetPassword } from "@/features/auth/api";
import type { AdminUser } from "@/features/auth/types";
import { Button } from "@/shared/ui/button";
import { Dialog } from "@/shared/ui/dialog";
import { toast } from "@/shared/ui/toast";
import { ConfirmReMfaDialog } from "./ConfirmReMfaDialog";
import { ShowOobOtpDialog } from "./ShowOobOtpDialog";

interface ResetPasswordDialogProps {
  open: boolean;
  user: AdminUser | null;
  onClose: () => void;
  onReset: () => void;
}

export function ResetPasswordDialog({ open, user, onClose, onReset }: ResetPasswordDialogProps) {
  const { t } = useTranslation();
  const [showReMfa, setShowReMfa] = React.useState(false);
  const [oobOtp, setOobOtp] = React.useState<string | null>(null);
  const [isResetting, setIsResetting] = React.useState(false);

  const handleReMfaConfirm = async (reMfaToken: string) => {
    if (!user) return;
    setShowReMfa(false);
    setIsResetting(true);
    try {
      const res = await adminResetPassword(user.id, reMfaToken);
      setOobOtp(res.oob_otp);
      onReset();
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
        open={open && !showReMfa && !oobOtp}
        onClose={onClose}
        title={t("admin.reset_password_title")}
        {...(user
          ? { description: t("admin.reset_password_description", { email: user.email }) }
          : {})}
      >
        <div className="flex flex-col gap-4">
          <p className="text-sm text-muted-foreground">{t("admin.reset_password_consequences")}</p>
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
              {t("admin.reset_password_submit")}
            </Button>
          </div>
        </div>
      </Dialog>

      <ConfirmReMfaDialog
        open={showReMfa}
        onClose={() => setShowReMfa(false)}
        onConfirm={handleReMfaConfirm}
      />

      {oobOtp && user && (
        <ShowOobOtpDialog
          open
          otp={oobOtp}
          userEmail={user.email}
          onClose={() => {
            setOobOtp(null);
            onClose();
          }}
        />
      )}
    </>
  );
}
