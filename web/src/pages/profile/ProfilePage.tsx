// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * ProfilePage — user self-service sections.
 *
 * Sections:
 * 1. Профиль — read-only identity info
 * 2. TOTP — enrollment status + backup codes management
 * 3. Смена пароля — re-MFA gated form (US-6, US-22)
 * 4. Активные сессии — list + revoke (US-14, US-20)
 */

import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { format } from "date-fns";
import {
  AlertTriangle,
  Bell,
  CheckCircle2,
  Edit2,
  Info,
  Loader2,
  Mail,
  Monitor,
  Trash2,
} from "lucide-react";
import * as React from "react";
import { useForm } from "react-hook-form";
import { useTranslation } from "react-i18next";
import { z } from "zod";
import { useAuth } from "@/features/auth/AuthProvider";
import type { NotificationPrefs } from "@/features/auth/api";
import {
  ApiResponseError,
  changePassword,
  confirmEmailChange,
  getMyNotificationPrefs,
  getMyProfile,
  getMySessions,
  regenerateBackupCodes,
  reMfa,
  requestEmailChange,
  revokeSession,
  sendMyTestEmail,
  updateMyNotificationPrefs,
  updateMyProfile,
} from "@/features/auth/api";
import type { SessionItem } from "@/features/auth/types";
import { cn } from "@/shared/lib/cn";
import { Button } from "@/shared/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/shared/ui/card";
import { Dialog } from "@/shared/ui/dialog";
import {
  applyServerScale,
  DEFAULT_SCALE,
  optionForPercent,
  SCALE_OPTIONS,
  setScale,
  useFontScale,
} from "@/shared/ui/font-scale";
import { Input } from "@/shared/ui/input";
import { Label } from "@/shared/ui/label";
import { toast } from "@/shared/ui/toast";

// ── Email change dialog (2-step state machine) ────────────────────────────────

type EmailChangeStep = "request" | "confirm" | "fatal";

interface EmailChangeDialogProps {
  currentEmail: string;
  onClose: () => void;
}

function EmailChangeDialog({ currentEmail, onClose }: EmailChangeDialogProps) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const { refresh: refreshAuth } = useAuth();

  const [step, setStep] = React.useState<EmailChangeStep>("request");

  // Step 1 fields
  const [newEmail, setNewEmail] = React.useState("");
  const [totpCode, setTotpCode] = React.useState("");
  const [step1Error, setStep1Error] = React.useState<string | null>(null);
  const [step1EmailError, setStep1EmailError] = React.useState<string | null>(null);
  const [step1TotpError, setStep1TotpError] = React.useState<string | null>(null);
  const [channelError, setChannelError] = React.useState(false);
  const [isSubmitting1, setIsSubmitting1] = React.useState(false);

  // Step 2 fields
  const [requestId, setRequestId] = React.useState("");
  const [maskedNewEmail, setMaskedNewEmail] = React.useState("");
  const [verificationCode, setVerificationCode] = React.useState("");
  const [step2Error, setStep2Error] = React.useState<string | null>(null);
  const [fatalError, setFatalError] = React.useState<string | null>(null);
  const [isSubmitting2, setIsSubmitting2] = React.useState(false);

  const newEmailRef = React.useRef<HTMLInputElement>(null);
  const totpRef = React.useRef<HTMLInputElement>(null);
  const codeRef = React.useRef<HTMLInputElement>(null);

  // RFC 5322 simplified — matches what browsers validate with type="email"
  const isValidEmail = (v: string) => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v) && v.length <= 254;

  const isStep1Valid = isValidEmail(newEmail) && newEmail !== currentEmail && totpCode.length === 6;
  const isStep2Valid = /^\d{8}$/.test(verificationCode);

  const handleStep1Submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setStep1Error(null);
    setStep1EmailError(null);
    setStep1TotpError(null);
    setChannelError(false);

    // Client-side pre-checks
    if (!isValidEmail(newEmail)) {
      setStep1EmailError(t("profile.email_error_EMAIL_VALIDATION"));
      newEmailRef.current?.focus();
      return;
    }
    if (newEmail === currentEmail) {
      setStep1EmailError(t("profile.email_error_EMAIL_SAME"));
      newEmailRef.current?.focus();
      return;
    }

    setIsSubmitting1(true);
    try {
      const res = await requestEmailChange({ new_email: newEmail, totp_code: totpCode });
      setRequestId(res.request_id);
      setMaskedNewEmail(res.masked_new_email);
      setStep("confirm");
    } catch (err) {
      if (err instanceof ApiResponseError) {
        const code = err.code ?? "";
        if (code === "EMAIL_CHANNEL_REQUIRED" || err.status === 503) {
          setChannelError(true);
        } else if (code === "EMAIL_SAME" || (err.status === 422 && code === "EMAIL_SAME")) {
          setStep1EmailError(t("profile.email_error_EMAIL_SAME"));
          newEmailRef.current?.focus();
        } else if (code === "EMAIL_ALREADY_TAKEN" || err.status === 409) {
          setStep1EmailError(t("profile.email_error_EMAIL_ALREADY_TAKEN"));
          newEmailRef.current?.focus();
        } else if (code === "EMAIL_VALIDATION" || err.status === 422) {
          setStep1EmailError(t("profile.email_error_EMAIL_VALIDATION"));
          newEmailRef.current?.focus();
        } else if (code === "REMFA_REPLAY") {
          setStep1TotpError(t("profile.email_error_REMFA_REPLAY"));
          setTotpCode("");
          totpRef.current?.focus();
        } else if (code === "REMFA_REQUIRED" || err.status === 401) {
          setStep1TotpError(t("profile.email_error_REMFA_REQUIRED"));
          setTotpCode("");
          totpRef.current?.focus();
        } else {
          setStep1Error(t("login_errors.network_error_title"));
        }
      } else {
        setStep1Error(t("login_errors.network_error_title"));
      }
    } finally {
      setIsSubmitting1(false);
    }
  };

  const handleStep2Submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setStep2Error(null);
    setFatalError(null);

    setIsSubmitting2(true);
    try {
      const res = await confirmEmailChange({
        request_id: requestId,
        verification_code: verificationCode,
      });
      // Force JWT refresh so the new email lands in claims immediately —
      // otherwise BFF endpoints that read admin.email from claims (e.g.
      // /admin/channels/email/test) keep using the stale value for up to
      // ~15 min until silent refresh fires.
      try {
        await refreshAuth();
      } catch {
        // refresh failure is non-fatal here — claims will update on next cycle
      }
      await qc.invalidateQueries({ queryKey: ["profile", "me"] });
      toast.show({
        title: t("profile.email_change_success", { email: res.email }),
        variant: "success",
      });
      onClose();
    } catch (err) {
      if (err instanceof ApiResponseError) {
        const code = err.code ?? "";
        if (code === "VERIFICATION_FAILED") {
          const remaining = err.attemptsRemaining;
          setStep2Error(
            t("profile.email_error_VERIFICATION_FAILED", { attempts: remaining ?? "?" }),
          );
          setVerificationCode("");
          codeRef.current?.focus();
        } else if (code === "VERIFICATION_FAILED_LAST") {
          setFatalError(t("profile.email_error_VERIFICATION_FAILED_LAST"));
          setStep("fatal");
        } else if (code === "EMAIL_CHANGE_REQUEST_NOT_FOUND" || err.status === 404) {
          setFatalError(t("profile.email_error_EMAIL_CHANGE_REQUEST_NOT_FOUND"));
          setStep("fatal");
        } else {
          setStep2Error(t("login_errors.network_error_title"));
        }
      } else {
        setStep2Error(t("login_errors.network_error_title"));
      }
    } finally {
      setIsSubmitting2(false);
    }
  };

  const handleGoBack = () => {
    setStep("request");
    setVerificationCode("");
    setStep2Error(null);
    setFatalError(null);
    // Preserve newEmail so user doesn't re-type it
  };

  const handleRestart = () => {
    setStep("request");
    setRequestId("");
    setMaskedNewEmail("");
    setVerificationCode("");
    setStep2Error(null);
    setFatalError(null);
    setStep1Error(null);
    setStep1EmailError(null);
    setStep1TotpError(null);
    setChannelError(false);
    setTotpCode("");
    // Keep newEmail for convenience
  };

  const dialogTitle =
    step === "request"
      ? t("profile.email_change_dialog_title_step1")
      : t("profile.email_change_dialog_title_step2");

  return (
    <Dialog open onClose={onClose} title={dialogTitle}>
      {/* Step 1 — request */}
      {step === "request" && (
        <form onSubmit={handleStep1Submit} noValidate className="flex flex-col gap-4">
          {/* Generic error */}
          {step1Error && (
            <div
              role="alert"
              className="rounded bg-destructive/10 border border-destructive px-3 py-2"
            >
              <p className="text-sm text-destructive font-medium">{step1Error}</p>
            </div>
          )}

          {/* EMAIL_CHANNEL_REQUIRED — prominent red banner */}
          {channelError && (
            <div
              role="alert"
              className="rounded bg-destructive/10 border border-destructive px-3 py-2 text-sm text-destructive"
            >
              {t("profile.email_error_EMAIL_CHANNEL_REQUIRED")}
            </div>
          )}

          {/* Token lifetime warning */}
          <div className="rounded bg-status-soon/10 border border-status-soon px-3 py-2 text-sm text-status-soon flex gap-2 items-start">
            <Info className="h-4 w-4 mt-0.5 shrink-0" aria-hidden />
            {t("profile.email_change_token_warning")}
          </div>

          {/* New email */}
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="ec-new-email">{t("profile.email_change_new_email_label")}</Label>
            <Input
              id="ec-new-email"
              ref={newEmailRef}
              type="email"
              autoComplete="email"
              autoFocus
              value={newEmail}
              onChange={(e) => {
                setNewEmail(e.target.value);
                setStep1EmailError(null);
              }}
              aria-invalid={step1EmailError ? true : undefined}
              aria-describedby={step1EmailError ? "ec-new-email-error" : undefined}
              className={cn(step1EmailError && "border-destructive focus-visible:ring-destructive")}
            />
            {step1EmailError && (
              <p
                id="ec-new-email-error"
                role="alert"
                className="text-xs text-destructive font-medium"
              >
                {step1EmailError}
              </p>
            )}
          </div>

          {/* TOTP code */}
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="ec-totp">{t("profile.email_change_totp_label")}</Label>
            <Input
              id="ec-totp"
              ref={totpRef}
              type="text"
              inputMode="numeric"
              autoComplete="one-time-code"
              maxLength={6}
              value={totpCode}
              onChange={(e) => {
                setTotpCode(e.target.value.replace(/\D/g, "").slice(0, 6));
                setStep1TotpError(null);
              }}
              placeholder="123456"
              aria-invalid={step1TotpError ? true : undefined}
              aria-describedby={step1TotpError ? "ec-totp-error" : undefined}
              className={cn(
                "font-mono text-center w-36",
                step1TotpError && "border-destructive focus-visible:ring-destructive",
              )}
            />
            {step1TotpError && (
              <p id="ec-totp-error" role="alert" className="text-xs text-destructive font-medium">
                {step1TotpError}
              </p>
            )}
          </div>

          {/* Actions */}
          <div className="flex gap-2 justify-end pt-1">
            <Button type="button" variant="ghost" size="sm" onClick={onClose}>
              {t("common.cancel")}
            </Button>
            <Button
              type="submit"
              size="sm"
              disabled={!isStep1Valid || isSubmitting1}
              aria-busy={isSubmitting1}
            >
              {isSubmitting1 ? (
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
              ) : (
                t("profile.email_change_send_button")
              )}
            </Button>
          </div>
        </form>
      )}

      {/* Step 2 — confirm */}
      {(step === "confirm" || step === "fatal") && (
        <form onSubmit={handleStep2Submit} noValidate className="flex flex-col gap-4">
          {/* Fatal error (expired / exhausted) */}
          {step === "fatal" && fatalError && (
            <div
              role="alert"
              className="rounded bg-destructive/10 border border-destructive px-3 py-2 text-sm text-destructive"
            >
              {fatalError}
            </div>
          )}

          {/* Step 2 success-info banner (only when not fatal) */}
          {step === "confirm" && (
            <div className="rounded bg-status-ok/10 border border-status-ok px-3 py-2 text-sm text-status-ok">
              {t("profile.email_change_step2_banner", { masked: maskedNewEmail })}
            </div>
          )}

          {/* Verification code (only when active) */}
          {step === "confirm" && (
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="ec-code">{t("profile.email_change_code_label")}</Label>
              <Input
                id="ec-code"
                ref={codeRef}
                type="text"
                inputMode="numeric"
                autoComplete="one-time-code"
                maxLength={8}
                autoFocus
                value={verificationCode}
                onChange={(e) => {
                  setVerificationCode(e.target.value.replace(/\D/g, "").slice(0, 8));
                  setStep2Error(null);
                }}
                placeholder="12345678"
                aria-invalid={step2Error ? true : undefined}
                aria-describedby={step2Error ? "ec-code-error" : undefined}
                className={cn(
                  "font-mono text-center w-40",
                  step2Error && "border-destructive focus-visible:ring-destructive",
                )}
              />
              {step2Error && (
                <p id="ec-code-error" role="alert" className="text-xs text-destructive font-medium">
                  {step2Error}
                </p>
              )}
            </div>
          )}

          {/* Actions */}
          <div className="flex gap-2 justify-end pt-1">
            {step === "fatal" ? (
              <>
                <Button type="button" variant="ghost" size="sm" onClick={onClose}>
                  {t("common.cancel")}
                </Button>
                <Button type="button" size="sm" onClick={handleRestart}>
                  {t("profile.email_change_restart_button")}
                </Button>
              </>
            ) : (
              <>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={handleGoBack}
                  disabled={isSubmitting2}
                >
                  {t("profile.email_change_back_button")}
                </Button>
                <Button
                  type="submit"
                  size="sm"
                  disabled={!isStep2Valid || isSubmitting2}
                  aria-busy={isSubmitting2}
                >
                  {isSubmitting2 ? (
                    <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                  ) : (
                    t("profile.email_change_confirm_button")
                  )}
                </Button>
              </>
            )}
          </div>
        </form>
      )}
    </Dialog>
  );
}

// ── Section 1: Profile info (editable full_name, read-only email/role) ────────

const fullNameSchema = z.object({
  full_name: z.string().min(1, "profile.full_name_min").max(200, "profile.full_name_max"),
});

type FullNameValues = z.infer<typeof fullNameSchema>;

function ProfileSection() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [emailDialogOpen, setEmailDialogOpen] = React.useState(false);

  const { data: profile, isLoading } = useQuery({
    queryKey: ["profile", "me"],
    queryFn: getMyProfile,
  });

  const {
    register,
    handleSubmit,
    reset,
    formState: { errors, isDirty, isValid, isSubmitting },
  } = useForm<FullNameValues>({
    resolver: zodResolver(fullNameSchema),
    mode: "onChange",
    defaultValues: { full_name: "" },
  });

  // Sync form to server data once it arrives (or re-fetches).
  React.useEffect(() => {
    if (profile) {
      reset({ full_name: profile.full_name });
    }
  }, [profile, reset]);

  const saveMutation = useMutation({
    mutationFn: ({ full_name }: FullNameValues) => updateMyProfile(full_name),
    onSuccess: (updated) => {
      toast.show({ title: t("profile.full_name_saved"), variant: "success" });
      qc.setQueryData(["profile", "me"], updated);
      reset({ full_name: updated.full_name });
    },
    onError: (err) => {
      if (err instanceof ApiResponseError && err.status === 422) {
        toast.show({ title: err.detail, variant: "destructive" });
      } else {
        toast.show({ title: t("login_errors.network_error_title"), variant: "destructive" });
      }
    },
  });

  const roleLabel: Record<string, string> = {
    admin: t("profile.role_admin"),
    editor: t("profile.role_editor"),
    viewer: t("profile.role_viewer"),
  };

  const currentRole = profile?.role;

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("profile.section_profile")}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {isLoading && (
          <p className="text-sm text-muted-foreground" aria-busy="true">
            {t("common.loading")}
          </p>
        )}

        {/* ФИО — editable */}
        <form
          onSubmit={handleSubmit((v) => saveMutation.mutate(v))}
          noValidate
          className="flex flex-col gap-1.5 max-w-sm"
        >
          <Label htmlFor="profile-full-name">{t("profile.full_name_label")}</Label>
          <div className="flex gap-2">
            <Input
              id="profile-full-name"
              autoComplete="name"
              aria-invalid={errors.full_name ? true : undefined}
              aria-describedby={errors.full_name ? "profile-full-name-error" : undefined}
              {...register("full_name")}
              className={cn(
                "flex-1",
                errors.full_name && "border-destructive focus-visible:ring-destructive",
              )}
            />
            <Button
              type="submit"
              size="sm"
              disabled={!isDirty || !isValid || isSubmitting || saveMutation.isPending}
              aria-busy={isSubmitting || saveMutation.isPending}
            >
              {isSubmitting || saveMutation.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
              ) : (
                t("profile.save_full_name")
              )}
            </Button>
          </div>
          {errors.full_name?.message && (
            <p
              id="profile-full-name-error"
              role="alert"
              className="text-xs text-destructive font-medium"
            >
              {t(errors.full_name.message)}
            </p>
          )}
        </form>

        {/* Email — editable via dialog */}
        <div className="flex flex-col gap-1.5 max-w-sm">
          <Label htmlFor="profile-email">{t("profile.email_label")}</Label>
          <div className="flex gap-2 items-center">
            <Input
              id="profile-email"
              type="email"
              disabled
              value={profile?.email ?? ""}
              aria-describedby="profile-email-help"
              className="flex-1"
            />
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => setEmailDialogOpen(true)}
              aria-label={t("profile.email_change_button")}
              className="shrink-0 gap-1"
              disabled={isLoading}
            >
              <Edit2 className="h-3 w-3" aria-hidden />
              {t("profile.email_change_button")}
            </Button>
          </div>
          <p id="profile-email-help" className="text-xs text-muted-foreground">
            {t("profile.email_change_helper")}
          </p>
        </div>

        {emailDialogOpen && (
          <EmailChangeDialog
            currentEmail={profile?.email ?? ""}
            onClose={() => setEmailDialogOpen(false)}
          />
        )}

        {/* Role — read-only badge */}
        <div className="flex flex-col gap-1.5">
          <span className="text-sm font-medium text-muted-foreground">
            {t("profile.role_label")}
          </span>
          <span
            className={cn(
              "inline-flex items-center rounded px-2 py-0.5 text-xs font-semibold w-fit",
              currentRole === "admin" && "bg-destructive/10 text-destructive",
              currentRole === "editor" && "bg-status-soon/10 text-status-soon",
              currentRole === "viewer" && "bg-muted text-muted-foreground",
            )}
          >
            {currentRole ? roleLabel[currentRole] : "—"}
          </span>
        </div>
      </CardContent>
    </Card>
  );
}

// ── Section: Appearance — web-interface font size (per-user preference) ───────

const APPEARANCE_GLYPH_SIZE = ["text-xs", "text-sm", "text-base", "text-lg"] as const;

function FontSizeSection() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const scale = useFontScale();

  const { data: profile } = useQuery({
    queryKey: ["profile", "me"],
    queryFn: getMyProfile,
  });

  const radioRefs = React.useRef<Array<HTMLButtonElement | null>>([]);
  const reconciledRef = React.useRef(false);
  const userTouchedRef = React.useRef(false);
  const saveTimer = React.useRef<ReturnType<typeof setTimeout> | null>(null);

  // Server is the system of record: once the profile resolves, adopt its stored
  // value (and refresh the localStorage cache) — unless the user already chose
  // a size this session. Runs once.
  React.useEffect(() => {
    if (!profile || reconciledRef.current) return;
    reconciledRef.current = true;
    if (userTouchedRef.current) return;
    if (typeof profile.ui_font_scale === "number" && profile.ui_font_scale !== scale) {
      applyServerScale(profile.ui_font_scale);
    }
  }, [profile, scale]);

  // Clean up any pending debounced save on unmount.
  React.useEffect(
    () => () => {
      if (saveTimer.current) clearTimeout(saveTimer.current);
    },
    [],
  );

  const saveMutation = useMutation({
    mutationFn: (percent: number) => updateMyProfile(profile?.full_name ?? "", percent),
    onSuccess: (updated) => {
      qc.setQueryData(["profile", "me"], updated);
    },
    onError: () => {
      // The size is already applied + cached locally; only the cross-device sync
      // failed. Surface it without reverting the user's comfortable local size.
      toast.show({ title: t("login_errors.network_error_title"), variant: "destructive" });
    },
  });

  // Debounce the server write so arrow-key sweeps through the presets coalesce
  // into a single PATCH; the local apply below stays instant either way.
  const scheduleSave = (percent: number) => {
    if (!profile) return;
    if (saveTimer.current) clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(() => saveMutation.mutate(percent), 400);
  };

  const apply = (percent: number) => {
    userTouchedRef.current = true;
    setScale(percent); // instant, flash-free local apply + localStorage cache
    scheduleSave(percent);
  };

  const selectedIndex = SCALE_OPTIONS.findIndex((o) => o.percent === scale);
  const tabbableIndex = selectedIndex >= 0 ? selectedIndex : 0;

  const onKeyDown = (e: React.KeyboardEvent, index: number) => {
    let target: number | null = null;
    if (e.key === "ArrowRight" || e.key === "ArrowDown") {
      target = (index + 1) % SCALE_OPTIONS.length;
    } else if (e.key === "ArrowLeft" || e.key === "ArrowUp") {
      target = (index - 1 + SCALE_OPTIONS.length) % SCALE_OPTIONS.length;
    } else if (e.key === "Home") {
      target = 0;
    } else if (e.key === "End") {
      target = SCALE_OPTIONS.length - 1;
    } else {
      return;
    }
    const targetOpt = SCALE_OPTIONS[target];
    if (!targetOpt) return;
    e.preventDefault();
    radioRefs.current[target]?.focus();
    apply(targetOpt.percent); // selection follows focus (WAI-ARIA APG)
  };

  const currentLabel = t(`profile.appearance_${optionForPercent(scale)?.key ?? "normal"}`);

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("profile.section_appearance")}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-sm text-muted-foreground">{t("profile.appearance_description")}</p>

        <div
          role="radiogroup"
          aria-label={t("profile.appearance_group_label")}
          className="inline-flex rounded-md border border-input overflow-hidden"
        >
          {SCALE_OPTIONS.map((opt, i) => {
            const checked = scale === opt.percent;
            return (
              // biome-ignore lint/a11y/useSemanticElements: segmented control uses the ARIA radio pattern intentionally
              <button
                key={opt.key}
                ref={(el) => {
                  radioRefs.current[i] = el;
                }}
                type="button"
                role="radio"
                aria-checked={checked}
                tabIndex={i === tabbableIndex ? 0 : -1}
                disabled={!profile}
                onClick={() => apply(opt.percent)}
                onKeyDown={(e) => onKeyDown(e, i)}
                className={cn(
                  "flex flex-col items-center justify-center gap-0.5 px-4 py-2 min-w-[4.5rem]",
                  "border-r border-input last:border-r-0 transition-colors",
                  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring",
                  checked ? "bg-primary text-primary-foreground" : "bg-background hover:bg-muted",
                  !profile && "opacity-50 cursor-not-allowed",
                )}
              >
                <span
                  className={cn("font-semibold leading-none", APPEARANCE_GLYPH_SIZE[i])}
                  aria-hidden
                >
                  А
                </span>
                <span className="text-xs">{t(`profile.appearance_${opt.key}`)}</span>
              </button>
            );
          })}
        </div>

        {/* Live preview — inherits the global font scale */}
        <div className="rounded-md border border-border bg-muted/40 px-3 py-2">
          <p className="text-xs text-muted-foreground mb-1">
            {t("profile.appearance_preview_label")}
          </p>
          <p className="text-sm">{t("profile.appearance_preview_text")}</p>
        </div>

        {scale !== DEFAULT_SCALE && (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => apply(DEFAULT_SCALE)}
            disabled={!profile}
          >
            {t("profile.appearance_reset")}
          </Button>
        )}

        {/* SR-only live status — announces the committed size on change */}
        <p role="status" aria-live="polite" className="sr-only">
          {t("profile.appearance_saved_status", { label: currentLabel })}
        </p>
      </CardContent>
    </Card>
  );
}

// ── Section 2: TOTP + Backup codes ───────────────────────────────────────────

interface BackupCodesModalProps {
  codes: string[];
  onClose: () => void;
}

function BackupCodesModal({ codes, onClose }: BackupCodesModalProps) {
  const { t } = useTranslation();
  const [confirmed, setConfirmed] = React.useState(false);

  return (
    <Dialog
      open
      onClose={onClose}
      title={t("profile.backup_codes_modal_title")}
      description={t("profile.backup_codes_modal_description")}
    >
      <div className="flex flex-col gap-4">
        <div
          role="alert"
          aria-live="assertive"
          className="rounded bg-status-soon/10 border border-status-soon px-3 py-2 text-sm text-status-soon"
        >
          {t("profile.backup_codes_once_warning")}
        </div>

        <ul
          className="grid grid-cols-2 gap-2 font-mono text-sm"
          aria-label={t("first_login.backup_codes_list_label")}
        >
          {codes.map((code) => (
            <li
              key={code}
              className="rounded border border-border bg-muted px-2 py-1 text-center select-all"
            >
              {code}
            </li>
          ))}
        </ul>

        <label className="flex items-center gap-2 cursor-pointer select-none text-sm">
          <input
            type="checkbox"
            checked={confirmed}
            onChange={(e) => setConfirmed(e.target.checked)}
            className="h-4 w-4 rounded border border-input focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          />
          {t("profile.backup_codes_saved_confirm")}
        </label>

        <Button onClick={onClose} disabled={!confirmed} className="w-full">
          {t("profile.backup_codes_close")}
        </Button>
      </div>
    </Dialog>
  );
}

function TotpSection() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [newCodes, setNewCodes] = React.useState<string[] | null>(null);
  const [reMfaCode, setReMfaCode] = React.useState("");
  const [showReMfa, setShowReMfa] = React.useState(false);
  const [isBusy, setIsBusy] = React.useState(false);

  const handleRegenerate = async () => {
    if (!reMfaCode || reMfaCode.length !== 6) {
      toast.show({
        title: t("profile.re_mfa_required"),
        description: t("profile.re_mfa_totp_hint"),
        variant: "destructive",
      });
      return;
    }
    setIsBusy(true);
    try {
      await reMfa(reMfaCode);
      const res = await regenerateBackupCodes();
      setNewCodes(res.codes);
      setShowReMfa(false);
      setReMfaCode("");
      await qc.invalidateQueries({ queryKey: ["profile", "backup-codes-count"] });
    } catch (err) {
      if (err instanceof ApiResponseError && err.status === 401) {
        toast.show({ title: t("login_errors.invalid_credentials"), variant: "destructive" });
      } else {
        toast.show({ title: t("login_errors.network_error_title"), variant: "destructive" });
      }
    } finally {
      setIsBusy(false);
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("profile.section_totp")}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-sm flex items-center gap-2">
          <span className="text-status-ok font-semibold">{t("profile.totp_enrolled")}</span>
        </p>

        <div className="border-t pt-4">
          <p className="text-sm font-medium mb-2">{t("profile.backup_codes_section")}</p>
          <p className="text-sm text-muted-foreground mb-3">
            {t("profile.backup_codes_description")}
          </p>

          {!showReMfa ? (
            <Button variant="outline" size="sm" onClick={() => setShowReMfa(true)}>
              {t("profile.backup_codes_regenerate")}
            </Button>
          ) : (
            <div className="flex flex-col gap-3">
              <p className="text-sm text-muted-foreground">{t("profile.re_mfa_description")}</p>
              <div className="flex gap-2">
                <Label htmlFor="profile-remfa" className="sr-only">
                  {t("profile.re_mfa_label")}
                </Label>
                <Input
                  id="profile-remfa"
                  type="text"
                  inputMode="numeric"
                  maxLength={6}
                  autoFocus
                  value={reMfaCode}
                  onChange={(e) => setReMfaCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
                  placeholder="123456"
                  aria-label={t("profile.re_mfa_label")}
                  className="w-32 font-mono text-center"
                />
                <Button
                  size="sm"
                  onClick={handleRegenerate}
                  disabled={reMfaCode.length !== 6 || isBusy}
                  aria-busy={isBusy}
                >
                  {isBusy ? (
                    <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                  ) : (
                    t("profile.backup_codes_confirm_btn")
                  )}
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => {
                    setShowReMfa(false);
                    setReMfaCode("");
                  }}
                >
                  {t("common.cancel")}
                </Button>
              </div>
            </div>
          )}
        </div>

        {newCodes && <BackupCodesModal codes={newCodes} onClose={() => setNewCodes(null)} />}
      </CardContent>
    </Card>
  );
}

// ── Section 2.5: Email-channel self-test ──────────────────────────────────────

function NotificationsTestSection() {
  const { t } = useTranslation();
  const { data: profile } = useQuery({
    queryKey: ["profile", "me"],
    queryFn: getMyProfile,
  });

  const [status, setStatus] = React.useState<"idle" | "sending" | "sent" | "error">("idle");
  const [errorMsg, setErrorMsg] = React.useState<string | null>(null);
  const [cooldownUntil, setCooldownUntil] = React.useState<number | null>(null);
  const [now, setNow] = React.useState(() => Date.now());

  React.useEffect(() => {
    if (!cooldownUntil) return;
    const tick = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(tick);
  }, [cooldownUntil]);

  const cooldownSec = cooldownUntil ? Math.max(0, Math.ceil((cooldownUntil - now) / 1000)) : 0;
  const onCooldown = cooldownSec > 0;

  const onSend = async () => {
    setStatus("sending");
    setErrorMsg(null);
    try {
      await sendMyTestEmail();
      setStatus("sent");
      setCooldownUntil(Date.now() + 60_000);
      toast.show({ title: t("profile.test_email_sent_toast"), variant: "success" });
    } catch (err) {
      setStatus("error");
      if (err instanceof ApiResponseError) {
        if (err.status === 429) {
          const retry = err.retryAfterSeconds ?? 60;
          setCooldownUntil(Date.now() + retry * 1000);
          setErrorMsg(t("profile.test_email_rate_limited", { seconds: retry }));
        } else if (err.status === 503) {
          setErrorMsg(t("profile.test_email_channel_unavailable"));
        } else {
          setErrorMsg(t("profile.test_email_generic_error"));
        }
      } else {
        setErrorMsg(t("profile.test_email_generic_error"));
      }
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("profile.section_email_self_test")}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-sm text-muted-foreground">
          {t("profile.test_email_description", { email: profile?.email ?? "—" })}
        </p>

        {status === "sent" && (
          <div
            role="status"
            aria-live="polite"
            className="rounded bg-status-ok/10 border border-status-ok px-3 py-2 text-sm text-status-ok flex gap-2 items-start"
          >
            <CheckCircle2 className="h-4 w-4 mt-0.5 shrink-0" aria-hidden />
            <span>{t("profile.test_email_sent_banner")}</span>
          </div>
        )}

        {status === "error" && errorMsg && (
          <div
            role="alert"
            className="rounded bg-destructive/10 border border-destructive px-3 py-2 text-sm text-destructive flex gap-2 items-start"
          >
            <AlertTriangle className="h-4 w-4 mt-0.5 shrink-0" aria-hidden />
            <span>{errorMsg}</span>
          </div>
        )}

        <Button
          type="button"
          variant="outline"
          onClick={onSend}
          disabled={status === "sending" || onCooldown || !profile?.email}
          aria-busy={status === "sending"}
          className="gap-2"
        >
          {status === "sending" ? (
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
          ) : (
            <Mail className="h-4 w-4" aria-hidden />
          )}
          {onCooldown
            ? t("profile.test_email_cooldown", { seconds: cooldownSec })
            : t("profile.test_email_button")}
        </Button>
      </CardContent>
    </Card>
  );
}

// ── Section: Notification preferences (ADR-0011) ─────────────────────────────

interface SwitchButtonProps {
  checked: boolean;
  disabled?: boolean;
  ariaLabel: string;
  onChange: (next: boolean) => void;
}

function SwitchButton({ checked, disabled, ariaLabel, onChange }: SwitchButtonProps) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={ariaLabel}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cn(
        "relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
        checked ? "bg-primary" : "bg-input",
        disabled && "opacity-50 cursor-not-allowed",
      )}
    >
      <span
        className={cn(
          "inline-block h-5 w-5 transform rounded-full bg-white shadow transition-transform",
          checked ? "translate-x-5" : "translate-x-0.5",
        )}
      />
    </button>
  );
}

interface PrefSwitchProps {
  label: string;
  description?: string;
  checked: boolean;
  disabled?: boolean;
  onChange: (next: boolean) => void;
}

function PrefSwitch({ label, description, checked, disabled, onChange }: PrefSwitchProps) {
  return (
    <div className="flex items-start justify-between gap-4 py-1">
      <div className="min-w-0">
        <p className="text-sm font-medium">{label}</p>
        {description && <p className="text-xs text-muted-foreground mt-0.5">{description}</p>}
      </div>
      <SwitchButton
        checked={checked}
        disabled={disabled ?? false}
        ariaLabel={label}
        onChange={onChange}
      />
    </div>
  );
}

// Category catalogue — keep ids in sync with backend domain.notification_prefs.
const NOTIF_CATEGORIES: ReadonlyArray<{ id: string; label: string; hint: string }> = [
  { id: "deadline", label: "Сроки актуализации", hint: "Приближение и наступление сроков" },
  { id: "doc_created", label: "Новые документы", hint: "Добавление документа в реестр" },
  { id: "doc_updated", label: "Изменения документов", hint: "Правки полей (сгруппированно)" },
  { id: "doc_assigned", label: "Назначение ответственным", hint: "Вас назначили ответственным" },
  { id: "doc_attachment", label: "Вложения", hint: "Файлы добавлены или удалены" },
  { id: "doc_archived", label: "Архивирование", hint: "Архивирование и восстановление" },
  { id: "asset", label: "Компании", hint: "Изменения по компаниям" },
];

const EMAIL_MODES: ReadonlyArray<{ id: "instant" | "digest" | "off"; label: string }> = [
  { id: "instant", label: "Сразу" },
  { id: "digest", label: "Сводка раз в день" },
  { id: "off", label: "Без писем" },
];

function NotificationsPrefsSection() {
  const { t } = useTranslation();
  const qc = useQueryClient();

  const { data, isLoading } = useQuery({
    queryKey: ["profile", "notif-prefs"],
    queryFn: getMyNotificationPrefs,
  });

  const [draft, setDraft] = React.useState<NotificationPrefs | null>(null);
  React.useEffect(() => {
    if (data) setDraft(data);
  }, [data]);

  const saveMutation = useMutation({
    mutationFn: (p: NotificationPrefs) => updateMyNotificationPrefs(p),
    onSuccess: (saved) => {
      qc.setQueryData(["profile", "notif-prefs"], saved);
      setDraft(saved);
      toast.show({ title: "Настройки уведомлений сохранены", variant: "success" });
    },
    onError: () => {
      toast.show({ title: t("login_errors.network_error_title"), variant: "destructive" });
    },
  });

  const dirty = Boolean(draft && data && JSON.stringify(draft) !== JSON.stringify(data));

  const setChannel = (cat: string, channel: "in_app" | "email", val: boolean) => {
    if (!draft) return;
    const current = draft.categories?.[cat] ?? { in_app: false, email: false };
    setDraft({
      ...draft,
      categories: { ...draft.categories, [cat]: { ...current, [channel]: val } },
    });
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Bell className="h-5 w-5" aria-hidden />
          Уведомления
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {isLoading || !draft ? (
          <p className="text-sm text-muted-foreground" aria-busy="true">
            {t("common.loading")}
          </p>
        ) : (
          <>
            <PrefSwitch
              label="Получать уведомления"
              description="Главный выключатель — отключает все уведомления для вас."
              checked={draft.enabled}
              onChange={(v) => setDraft({ ...draft, enabled: v })}
            />
            <PrefSwitch
              label="Не уведомлять о моих действиях"
              description="Не присылать уведомления о том, что сделали вы сами."
              checked={draft.suppress_own}
              disabled={!draft.enabled}
              onChange={(v) => setDraft({ ...draft, suppress_own: v })}
            />

            <div className="border-t border-border pt-3">
              <p className="text-sm font-medium mb-2">Письма на почту</p>
              <div
                className="inline-flex rounded-md border border-input overflow-hidden"
                role="radiogroup"
                aria-label="Режим email-уведомлений"
              >
                {EMAIL_MODES.map((m) => (
                  // biome-ignore lint/a11y/useSemanticElements: segmented control uses the ARIA radio pattern intentionally
                  <button
                    key={m.id}
                    type="button"
                    role="radio"
                    aria-checked={draft.email_mode === m.id}
                    disabled={!draft.enabled}
                    onClick={() => setDraft({ ...draft, email_mode: m.id })}
                    className={cn(
                      "px-3 py-1.5 text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                      draft.email_mode === m.id
                        ? "bg-primary text-primary-foreground"
                        : "bg-background hover:bg-muted",
                      !draft.enabled && "opacity-50 cursor-not-allowed",
                    )}
                  >
                    {m.label}
                  </button>
                ))}
              </div>
            </div>

            <div className="border-t border-border pt-3">
              <div className="flex items-center justify-end gap-6 pb-2 text-xs text-muted-foreground">
                <span className="w-24 text-center">В приложении</span>
                <span className="w-24 text-center">На почту</span>
              </div>
              <ul className="divide-y divide-border">
                {NOTIF_CATEGORIES.map((cat) => {
                  const c = draft.categories?.[cat.id] ?? { in_app: false, email: false };
                  return (
                    <li key={cat.id} className="flex items-center justify-between gap-4 py-2">
                      <div className="min-w-0">
                        <p className="text-sm font-medium">{cat.label}</p>
                        <p className="text-xs text-muted-foreground">{cat.hint}</p>
                      </div>
                      <div className="flex items-center gap-6">
                        <div className="w-24 flex justify-center">
                          <SwitchButton
                            checked={c.in_app}
                            disabled={!draft.enabled}
                            ariaLabel={`${cat.label}: в приложении`}
                            onChange={(v) => setChannel(cat.id, "in_app", v)}
                          />
                        </div>
                        <div className="w-24 flex justify-center">
                          <SwitchButton
                            checked={c.email}
                            disabled={!draft.enabled || draft.email_mode === "off"}
                            ariaLabel={`${cat.label}: на почту`}
                            onChange={(v) => setChannel(cat.id, "email", v)}
                          />
                        </div>
                      </div>
                    </li>
                  );
                })}
              </ul>
            </div>

            <p className="text-xs text-muted-foreground">
              «В приложении» — лента уведомлений (колокольчик) появится в ближайшем обновлении;
              настройки уже сохраняются.
            </p>

            <div className="flex justify-end">
              <Button
                size="sm"
                disabled={!dirty || saveMutation.isPending}
                onClick={() => draft && saveMutation.mutate(draft)}
              >
                {saveMutation.isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                ) : (
                  "Сохранить"
                )}
              </Button>
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}

// ── Section 3: Change password ────────────────────────────────────────────────

const passwordSchema = z
  .object({
    currentPassword: z.string().min(1, "profile.current_password_required"),
    totpCode: z
      .string()
      .length(6, "login_errors.totp_length")
      .regex(/^\d+$/, "login_errors.totp_digits"),
    newPassword: z
      .string()
      .min(12, "login_errors.password_min")
      .max(1024, "login_errors.password_max"),
    confirmPassword: z.string(),
  })
  .refine((v) => v.newPassword === v.confirmPassword, {
    message: "first_login.passwords_mismatch",
    path: ["confirmPassword"],
  });

type PasswordValues = z.infer<typeof passwordSchema>;

function ChangePasswordSection() {
  const { t } = useTranslation();
  const [hibpWarning, setHibpWarning] = React.useState(false);

  const {
    register,
    handleSubmit,
    reset,
    setError,
    formState: { errors, isValid, isSubmitting },
  } = useForm<PasswordValues>({
    resolver: zodResolver(passwordSchema),
    mode: "onChange",
  });

  const onSubmit = async (values: PasswordValues) => {
    setHibpWarning(false);
    try {
      const reMfaRes = await reMfa(values.totpCode);
      await changePassword({
        currentPassword: values.currentPassword,
        newPassword: values.newPassword,
        reMfaToken: reMfaRes.re_mfa_token,
      });
      toast.show({ title: t("profile.password_changed"), variant: "success" });
      reset();
    } catch (err) {
      if (err instanceof ApiResponseError) {
        if (err.status === 401) {
          setError("root", { message: t("login_errors.invalid_credentials") });
        } else if (err.status === 422 && err.detail.includes("breach")) {
          setHibpWarning(true);
        } else {
          setError("root", { message: t("login_errors.network_error_title") });
        }
      } else {
        toast.show({ title: t("login_errors.network_error_title"), variant: "destructive" });
      }
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("profile.section_password")}</CardTitle>
      </CardHeader>
      <CardContent>
        <form
          onSubmit={handleSubmit(onSubmit)}
          noValidate
          aria-label={t("profile.section_password")}
          className="flex flex-col gap-4 max-w-sm"
        >
          {errors.root && (
            <div
              role="alert"
              className="rounded bg-destructive/10 border border-destructive px-3 py-2"
            >
              <p className="text-sm text-destructive font-medium">{errors.root.message}</p>
            </div>
          )}

          {hibpWarning && (
            <div
              role="alert"
              className="rounded bg-status-soon/10 border border-status-soon px-3 py-2 text-sm text-status-soon flex gap-2 items-start"
            >
              <AlertTriangle className="h-4 w-4 mt-0.5 shrink-0" aria-hidden />
              {t("profile.password_breach_warning")}
            </div>
          )}

          {[
            {
              id: "cp-current",
              name: "currentPassword" as const,
              label: t("profile.current_password_label"),
              type: "password",
              autoComplete: "current-password",
            },
            {
              id: "cp-totp",
              name: "totpCode" as const,
              label: t("profile.totp_confirm_label"),
              type: "text",
              autoComplete: "one-time-code",
              inputMode: "numeric" as const,
              maxLength: 6,
              className: "font-mono text-center",
            },
            {
              id: "cp-new",
              name: "newPassword" as const,
              label: t("profile.new_password_label"),
              type: "password",
              autoComplete: "new-password",
            },
            {
              id: "cp-confirm",
              name: "confirmPassword" as const,
              label: t("profile.confirm_password_label"),
              type: "password",
              autoComplete: "new-password",
            },
          ].map(({ id, name, label, ...inputProps }) => {
            const err = errors[name];
            return (
              <div key={id} className="flex flex-col gap-1.5">
                <Label htmlFor={id}>{label}</Label>
                <Input
                  id={id}
                  aria-invalid={err ? true : undefined}
                  aria-describedby={err ? `${id}-error` : undefined}
                  {...register(name)}
                  {...inputProps}
                  className={cn(
                    inputProps.className,
                    err && "border-destructive focus-visible:ring-destructive",
                  )}
                />
                {err?.message && (
                  <p
                    id={`${id}-error`}
                    role="alert"
                    className="text-xs text-destructive font-medium"
                  >
                    {t(err.message)}
                  </p>
                )}
              </div>
            );
          })}

          <Button
            type="submit"
            disabled={!isValid || isSubmitting}
            className="self-start"
            aria-busy={isSubmitting}
          >
            {isSubmitting ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                {t("login.submitting")}
              </>
            ) : (
              t("profile.change_password_submit")
            )}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}

// ── Section 4: Sessions ───────────────────────────────────────────────────────

function SessionsSection() {
  const { t } = useTranslation();
  const qc = useQueryClient();

  const { data: sessions, isLoading } = useQuery({
    queryKey: ["profile", "sessions"],
    queryFn: getMySessions,
  });

  const revokeMutation = useMutation({
    mutationFn: revokeSession,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["profile", "sessions"] });
      toast.show({ title: t("profile.session_revoked"), variant: "success" });
    },
    onError: () => {
      toast.show({ title: t("login_errors.network_error_title"), variant: "destructive" });
    },
  });

  const revokeAll = async () => {
    const nonCurrent = sessions?.filter((s) => !s.is_current) ?? [];
    await Promise.allSettled(nonCurrent.map((s) => revokeSession(s.id)));
    qc.invalidateQueries({ queryKey: ["profile", "sessions"] });
    toast.show({ title: t("profile.sessions_all_revoked"), variant: "success" });
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("profile.section_sessions")}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {isLoading && (
          <p className="text-sm text-muted-foreground" aria-busy="true">
            {t("common.loading")}
          </p>
        )}

        {sessions && sessions.length === 0 && (
          <p className="text-sm text-muted-foreground">{t("profile.no_sessions")}</p>
        )}

        <ul className="divide-y divide-border" aria-label={t("profile.sessions_list_label")}>
          {sessions?.map((session) => (
            <SessionRow
              key={session.id}
              session={session}
              onRevoke={() => revokeMutation.mutate(session.id)}
              isRevoking={revokeMutation.isPending && revokeMutation.variables === session.id}
            />
          ))}
        </ul>

        {(sessions?.filter((s) => !s.is_current).length ?? 0) > 0 && (
          <Button variant="outline" size="sm" onClick={revokeAll} className="mt-2">
            {t("profile.revoke_all_others")}
          </Button>
        )}
      </CardContent>
    </Card>
  );
}

interface SessionRowProps {
  session: SessionItem;
  onRevoke: () => void;
  isRevoking: boolean;
}

function SessionRow({ session, onRevoke, isRevoking }: SessionRowProps) {
  const { t } = useTranslation();

  return (
    <li className="flex items-start justify-between gap-3 py-3">
      <div className="flex items-start gap-2 min-w-0">
        <Monitor className="h-4 w-4 mt-0.5 shrink-0 text-muted-foreground" aria-hidden />
        <div className="min-w-0">
          <p className="text-sm font-medium truncate">
            {session.user_agent || t("profile.unknown_ua")}
          </p>
          <p className="text-xs text-muted-foreground">
            {session.ip_address} &middot; {t("profile.session_created")}:{" "}
            {format(new Date(session.created_at), "dd.MM.yyyy HH:mm")}
          </p>
          {session.is_current && (
            <span className="text-xs font-medium text-status-ok">
              {t("profile.current_session")}
            </span>
          )}
        </div>
      </div>
      {!session.is_current && (
        <Button
          variant="ghost"
          size="icon"
          onClick={onRevoke}
          disabled={isRevoking}
          aria-label={t("profile.revoke_session_label")}
          aria-busy={isRevoking}
          className="shrink-0 text-destructive hover:text-destructive hover:bg-destructive/10"
        >
          {isRevoking ? (
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
          ) : (
            <Trash2 className="h-4 w-4" aria-hidden />
          )}
        </Button>
      )}
    </li>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────

export function ProfilePage() {
  const { t } = useTranslation();

  return (
    <div className="max-w-2xl mx-auto px-4 py-8 space-y-6">
      <h1 className="text-2xl font-semibold">{t("profile.title")}</h1>
      <ProfileSection />
      <FontSizeSection />
      <TotpSection />
      <NotificationsPrefsSection />
      <NotificationsTestSection />
      <ChangePasswordSection />
      <SessionsSection />
    </div>
  );
}
