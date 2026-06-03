// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * ChangeRoleDialog — change a user's role.
 * Requires admin re-MFA (US-19).
 */

import * as React from "react";
import { useTranslation } from "react-i18next";
import { ApiResponseError, adminChangeRole } from "@/features/auth/api";
import type { AdminUser, UserRole } from "@/features/auth/types";
import { Button } from "@/shared/ui/button";
import { Dialog } from "@/shared/ui/dialog";
import { Label } from "@/shared/ui/label";
import { toast } from "@/shared/ui/toast";
import { ConfirmReMfaDialog } from "./ConfirmReMfaDialog";

interface ChangeRoleDialogProps {
  open: boolean;
  user: AdminUser | null;
  onClose: () => void;
  onChanged: () => void;
}

export function ChangeRoleDialog({ open, user, onClose, onChanged }: ChangeRoleDialogProps) {
  const { t } = useTranslation();
  const [selectedRole, setSelectedRole] = React.useState<UserRole>("viewer");
  const [showReMfa, setShowReMfa] = React.useState(false);
  const [isChanging, setIsChanging] = React.useState(false);

  React.useEffect(() => {
    if (open && user) setSelectedRole(user.role);
  }, [open, user]);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setShowReMfa(true);
  };

  const handleReMfaConfirm = async (reMfaToken: string) => {
    if (!user) return;
    setShowReMfa(false);
    setIsChanging(true);
    try {
      await adminChangeRole(user.id, selectedRole, reMfaToken);
      toast.show({ title: t("admin.role_changed"), variant: "success" });
      onChanged();
      onClose();
    } catch (err) {
      if (err instanceof ApiResponseError && err.status === 401) {
        toast.show({ title: t("login_errors.invalid_credentials"), variant: "destructive" });
      } else {
        toast.show({ title: t("login_errors.network_error_title"), variant: "destructive" });
      }
    } finally {
      setIsChanging(false);
    }
  };

  return (
    <>
      <Dialog
        open={open && !showReMfa}
        onClose={onClose}
        title={t("admin.change_role_title")}
        {...(user
          ? { description: t("admin.change_role_description", { email: user.email }) }
          : {})}
      >
        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="cr-role">{t("admin.role_label")}</Label>
            <select
              id="cr-role"
              value={selectedRole}
              onChange={(e) => setSelectedRole(e.target.value as UserRole)}
              className="h-9 rounded-md border border-input bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <option value="viewer">{t("profile.role_viewer")}</option>
              <option value="editor">{t("profile.role_editor")}</option>
              <option value="admin">{t("profile.role_admin")}</option>
            </select>
          </div>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="outline" onClick={onClose}>
              {t("common.cancel")}
            </Button>
            <Button
              type="submit"
              disabled={isChanging || selectedRole === user?.role}
              aria-busy={isChanging}
            >
              {t("admin.change_role_submit")}
            </Button>
          </div>
        </form>
      </Dialog>

      <ConfirmReMfaDialog
        open={showReMfa}
        onClose={() => setShowReMfa(false)}
        onConfirm={handleReMfaConfirm}
      />
    </>
  );
}
