// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * FirstLoginPage — forced enrollment flow for new users.
 *
 * Three sub-steps (per ADR-0003 §5, US-1):
 *   1. OOB OTP login (admin-issued one-time password)
 *   2. TOTP enrollment (QR code + confirm code → receive 10 backup codes)
 *   3. Set new password
 *
 * The QR code is rendered client-side from the otpauth_url — the secret
 * never goes to the server as an image (per auth-flow-review §3).
 */

import { zodResolver } from "@hookform/resolvers/zod";
import { useNavigate } from "@tanstack/react-router";
import { CheckCircle, Compass, Loader2 } from "lucide-react";
import QRCode from "qrcode";
import * as React from "react";
import { useForm } from "react-hook-form";
import { useTranslation } from "react-i18next";
import { z } from "zod";
import { useAuth } from "@/features/auth/AuthProvider";
import {
  ApiResponseError,
  changePassword,
  confirmTotpEnrollment,
  enrollTotp,
} from "@/features/auth/api";
import { cn } from "@/shared/lib/cn";
import { Button } from "@/shared/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/shared/ui/card";
import { Input } from "@/shared/ui/input";
import { Label } from "@/shared/ui/label";
import { ThemeToggle } from "@/shared/ui/theme-toggle";
import { toast } from "@/shared/ui/toast";

// ── Step indicator ────────────────────────────────────────────────────────────

type EnrollStep = 1 | 2 | 3;

function StepIndicator({ current }: { current: EnrollStep }) {
  const { t } = useTranslation();
  const labels = [
    t("first_login.step1_label"),
    t("first_login.step2_label"),
    t("first_login.step3_label"),
  ];

  return (
    <nav aria-label={t("first_login.steps_nav")} className="mb-6">
      <ol className="flex items-center justify-center gap-2 list-none m-0 p-0">
        {labels.map((label, i) => {
          const num = (i + 1) as EnrollStep;
          const done = num < current;
          const active = num === current;
          return (
            <React.Fragment key={num}>
              <li
                className="flex flex-col items-center gap-1"
                aria-current={active ? "step" : undefined}
              >
                <div
                  aria-hidden
                  className={cn(
                    "flex h-8 w-8 items-center justify-center rounded-full text-xs font-semibold",
                    done && "bg-status-ok text-white",
                    active && "bg-primary text-primary-foreground",
                    !done && !active && "bg-muted text-muted-foreground",
                  )}
                >
                  {done ? <CheckCircle className="h-4 w-4" aria-hidden /> : num}
                </div>
                <span className="text-xs text-muted-foreground">{label}</span>
              </li>
              {i < 2 && <li aria-hidden className="h-px w-8 bg-border self-start mt-4" />}
            </React.Fragment>
          );
        })}
      </ol>
    </nav>
  );
}

// ── Step 1: OOB OTP login ─────────────────────────────────────────────────────

const step1Schema = z.object({
  email: z.string().min(1, "login_errors.email_required").email("login_errors.email_invalid"),
  otp: z.string().min(1, "first_login.otp_required"),
});
type Step1Values = z.infer<typeof step1Schema>;

interface Step1Props {
  onSuccess: () => void;
}

function Step1Form({ onSuccess }: Step1Props) {
  const { t } = useTranslation();
  const { login } = useAuth();
  const [isLoading, setIsLoading] = React.useState(false);

  const {
    register,
    handleSubmit,
    setError,
    formState: { errors, isValid },
  } = useForm<Step1Values>({
    resolver: zodResolver(step1Schema),
    mode: "onChange",
  });

  const onSubmit = async (values: Step1Values) => {
    setIsLoading(true);
    try {
      const res = await login(values.email, values.otp);
      if (res.next_step === "enroll_totp") {
        onSuccess();
      } else {
        setError("root", { message: t("first_login.unexpected_response") });
      }
    } catch (err) {
      if (err instanceof ApiResponseError && err.status === 401) {
        setError("root", { message: t("login_errors.invalid_credentials") });
      } else {
        toast.show({
          title: t("login_errors.network_error_title"),
          description: t("login_errors.network_error_description"),
          variant: "destructive",
        });
      }
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <form
      onSubmit={handleSubmit(onSubmit)}
      noValidate
      aria-label={t("first_login.step1_form_label")}
      className="flex flex-col gap-4"
    >
      <p className="text-sm text-muted-foreground">{t("first_login.step1_description")}</p>

      {errors.root && (
        <div
          role="alert"
          className="rounded-md bg-destructive/10 border border-destructive px-3 py-2"
        >
          <p className="text-sm text-destructive font-medium">{errors.root.message}</p>
        </div>
      )}

      <div className="flex flex-col gap-1.5">
        <Label htmlFor="fl-email">{t("login.email_label")}</Label>
        <Input
          id="fl-email"
          type="email"
          autoComplete="email"
          autoFocus
          aria-invalid={errors.email ? true : undefined}
          aria-describedby={errors.email ? "fl-email-error" : undefined}
          {...register("email")}
          className={cn(errors.email && "border-destructive focus-visible:ring-destructive")}
        />
        {errors.email && (
          <p id="fl-email-error" role="alert" className="text-xs text-destructive font-medium">
            {errors.email.message ? t(errors.email.message) : undefined}
          </p>
        )}
      </div>

      <div className="flex flex-col gap-1.5">
        <Label htmlFor="fl-otp">{t("first_login.otp_label")}</Label>
        <Input
          id="fl-otp"
          type="text"
          autoComplete="off"
          aria-invalid={errors.otp ? true : undefined}
          aria-describedby={errors.otp ? "fl-otp-error" : "fl-otp-hint"}
          {...register("otp")}
          className={cn(
            "font-mono",
            errors.otp && "border-destructive focus-visible:ring-destructive",
          )}
        />
        <p id="fl-otp-hint" className="text-xs text-muted-foreground">
          {t("first_login.otp_hint")}
        </p>
        {errors.otp && (
          <p id="fl-otp-error" role="alert" className="text-xs text-destructive font-medium">
            {t("first_login.otp_required")}
          </p>
        )}
      </div>

      <Button
        type="submit"
        disabled={!isValid || isLoading}
        className="w-full"
        aria-busy={isLoading}
      >
        {isLoading ? (
          <>
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
            {t("login.submitting")}
          </>
        ) : (
          t("first_login.step1_submit")
        )}
      </Button>
    </form>
  );
}

// ── Step 2: TOTP enrollment ───────────────────────────────────────────────────

interface Step2Props {
  onSuccess: (backupCodes: string[]) => void;
}

const step2Schema = z.object({
  code: z
    .string()
    .min(1, "login_errors.totp_required")
    .length(6, "login_errors.totp_length")
    .regex(/^\d+$/, "login_errors.totp_digits"),
});
type Step2Values = z.infer<typeof step2Schema>;

function Step2Form({ onSuccess }: Step2Props) {
  const { t } = useTranslation();
  const [enrollData, setEnrollData] = React.useState<{
    secret_b32: string;
    otpauth_url: string;
    qrSvg: string;
  } | null>(null);
  const [isLoadingEnroll, setIsLoadingEnroll] = React.useState(true);
  const [enrollError, setEnrollError] = React.useState<string | null>(null);

  const {
    register,
    handleSubmit,
    setError,
    formState: { errors, isValid },
  } = useForm<Step2Values>({
    resolver: zodResolver(step2Schema),
    mode: "onChange",
  });

  // Fetch enrollment data on mount
  React.useEffect(() => {
    let cancelled = false;
    setIsLoadingEnroll(true);
    setEnrollError(null);

    enrollTotp()
      .then(async (res) => {
        if (cancelled) return;
        // Render QR as SVG (not canvas/PNG) — canvas-fingerprinting protection
        // in Brave, Firefox `privacy.resistFingerprinting`, Tor Browser, and
        // some anti-tracking extensions randomizes canvas readback, which
        // corrupts QRCode.toDataURL() output (looks like color stripes).
        // SVG bypasses canvas entirely and is also resolution-independent.
        try {
          const qrSvg = await QRCode.toString(res.otpauth_url, {
            type: "svg",
            width: 200,
            margin: 1,
            color: { dark: "#000000ff", light: "#ffffffff" },
          });
          if (!cancelled) {
            setEnrollData({ ...res, qrSvg });
          }
        } catch {
          if (!cancelled) setEnrollError(t("first_login.qr_error"));
        }
      })
      .catch(() => {
        if (!cancelled) setEnrollError(t("first_login.enroll_fetch_error"));
      })
      .finally(() => {
        if (!cancelled) setIsLoadingEnroll(false);
      });

    return () => {
      cancelled = true;
    };
  }, [t]);

  const [isConfirming, setIsConfirming] = React.useState(false);

  const onSubmit = async (values: Step2Values) => {
    setIsConfirming(true);
    try {
      const res = await confirmTotpEnrollment(values.code);
      onSuccess(res.backup_codes);
    } catch (err) {
      if (err instanceof ApiResponseError && err.status === 401) {
        setError("code", { message: t("login_errors.invalid_credentials") });
      } else if (err instanceof ApiResponseError && err.status === 422) {
        setError("code", { message: t("first_login.invalid_totp_code") });
      } else {
        toast.show({
          title: t("login_errors.network_error_title"),
          description: t("login_errors.network_error_description"),
          variant: "destructive",
        });
      }
    } finally {
      setIsConfirming(false);
    }
  };

  if (isLoadingEnroll) {
    return (
      <div
        className="flex justify-center py-8"
        role="status"
        aria-busy="true"
        aria-label={t("first_login.loading_qr")}
      >
        <Loader2 className="h-8 w-8 animate-spin text-primary" aria-hidden />
      </div>
    );
  }

  if (enrollError) {
    return (
      <div
        role="alert"
        className="rounded-md bg-destructive/10 border border-destructive px-3 py-2"
      >
        <p className="text-sm text-destructive font-medium">{enrollError}</p>
      </div>
    );
  }

  return (
    <form
      onSubmit={handleSubmit(onSubmit)}
      noValidate
      aria-label={t("first_login.step2_form_label")}
      className="flex flex-col gap-4"
    >
      <p className="text-sm text-muted-foreground">{t("first_login.step2_description")}</p>

      {/* QR code rendered as inline SVG (not canvas/PNG) — canvas-fingerprinting
          protection (Brave shields, Firefox resistFingerprinting, Tor Browser,
          some extensions) randomizes canvas readback and corrupts
          QRCode.toDataURL() into color-stripe noise. SVG is canvas-free and
          resolution-independent. Defensive wrappers (data-darkreader-ignore,
          filter:none, isolation:isolate, explicit white bg + padding) remain
          to guard against the orthogonal Dark Reader / OS-color-filter class. */}
      <div
        className="flex justify-center"
        data-darkreader-ignore="true"
        style={{
          filter: "none",
          isolation: "isolate",
          mixBlendMode: "normal",
        }}
      >
        <div
          data-darkreader-ignore="true"
          style={{
            backgroundColor: "#ffffff",
            padding: "8px",
            borderRadius: "6px",
            filter: "none",
          }}
        >
          <div
            role="img"
            aria-label={t("first_login.qr_alt")}
            data-darkreader-ignore="true"
            // SVG content is generated client-side from the otpauth_url returned
            // by auth-svc — no untrusted input is involved.
            dangerouslySetInnerHTML={{ __html: enrollData?.qrSvg ?? "" }}
            style={{
              width: 200,
              height: 200,
              filter: "none",
              mixBlendMode: "normal",
              display: "block",
            }}
            className="rounded border border-border"
          />
        </div>
      </div>

      {/* Manual key */}
      <details className="text-sm">
        <summary className="cursor-pointer text-primary underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded">
          {t("first_login.show_manual_key")}
        </summary>
        <div className="mt-2 rounded bg-muted px-3 py-2">
          <p className="text-xs text-muted-foreground mb-1">{t("first_login.manual_key_label")}</p>
          <code className="font-mono text-sm break-all select-all">{enrollData?.secret_b32}</code>
        </div>
      </details>

      {/* Confirmation code */}
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="fl-totp-confirm">{t("first_login.confirm_code_label")}</Label>
        <Input
          id="fl-totp-confirm"
          type="text"
          inputMode="numeric"
          autoComplete="one-time-code"
          maxLength={6}
          aria-invalid={errors.code ? true : undefined}
          aria-describedby={errors.code ? "fl-totp-error" : "fl-totp-hint"}
          {...register("code")}
          className={cn(
            "font-mono tracking-widest text-center",
            errors.code && "border-destructive focus-visible:ring-destructive",
          )}
        />
        <p id="fl-totp-hint" className="text-xs text-muted-foreground">
          {t("first_login.confirm_code_hint")}
        </p>
        {errors.code && (
          <p id="fl-totp-error" role="alert" className="text-xs text-destructive font-medium">
            {errors.code.message ? t(errors.code.message) : undefined}
          </p>
        )}
      </div>

      <Button
        type="submit"
        disabled={!isValid || isConfirming}
        className="w-full"
        aria-busy={isConfirming}
      >
        {isConfirming ? (
          <>
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
            {t("first_login.confirming")}
          </>
        ) : (
          t("first_login.step2_submit")
        )}
      </Button>
    </form>
  );
}

// ── Backup codes display ──────────────────────────────────────────────────────

interface BackupCodesDisplayProps {
  codes: string[];
  onConfirmed: () => void;
}

function BackupCodesDisplay({ codes, onConfirmed }: BackupCodesDisplayProps) {
  const { t } = useTranslation();
  const [confirmed, setConfirmed] = React.useState(false);

  return (
    <div className="flex flex-col gap-4">
      <div
        role="alert"
        aria-live="assertive"
        className="rounded-md bg-status-soon/10 border border-status-soon px-3 py-2"
      >
        <p className="text-sm font-medium text-status-soon">
          {t("first_login.backup_codes_warning")}
        </p>
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
        {t("first_login.backup_codes_confirm_checkbox")}
      </label>

      <Button onClick={onConfirmed} disabled={!confirmed} className="w-full">
        {t("first_login.backup_codes_continue")}
      </Button>
    </div>
  );
}

// ── Step 3: new password ──────────────────────────────────────────────────────

const step3Schema = z
  .object({
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

type Step3Values = z.infer<typeof step3Schema>;

interface Step3Props {
  onSuccess: () => void;
}

function Step3Form({ onSuccess }: Step3Props) {
  const { t } = useTranslation();
  const [isLoading, setIsLoading] = React.useState(false);

  const {
    register,
    handleSubmit,
    setError,
    formState: { errors, isValid },
  } = useForm<Step3Values>({
    resolver: zodResolver(step3Schema),
    mode: "onChange",
  });

  const onSubmit = async (values: Step3Values) => {
    setIsLoading(true);
    try {
      // Per ADR-0003 §5b: current_password is the OOB OTP already consumed.
      // The server accepts empty/placeholder current_password when must_change_at_next_login=true.
      await changePassword({
        currentPassword: "",
        newPassword: values.newPassword,
        reMfaToken: "",
      });
      onSuccess();
    } catch (err) {
      if (err instanceof ApiResponseError && err.status === 422) {
        setError("newPassword", { message: t("first_login.password_breach") });
      } else {
        toast.show({
          title: t("login_errors.network_error_title"),
          description: t("login_errors.network_error_description"),
          variant: "destructive",
        });
      }
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <form
      onSubmit={handleSubmit(onSubmit)}
      noValidate
      aria-label={t("first_login.step3_form_label")}
      className="flex flex-col gap-4"
    >
      <p className="text-sm text-muted-foreground">{t("first_login.step3_description")}</p>

      <div className="flex flex-col gap-1.5">
        <Label htmlFor="fl-new-password">{t("first_login.new_password_label")}</Label>
        <Input
          id="fl-new-password"
          type="password"
          autoComplete="new-password"
          autoFocus
          aria-invalid={errors.newPassword ? true : undefined}
          aria-describedby={errors.newPassword ? "fl-pw-error" : "fl-pw-hint"}
          {...register("newPassword")}
          className={cn(errors.newPassword && "border-destructive focus-visible:ring-destructive")}
        />
        <p id="fl-pw-hint" className="text-xs text-muted-foreground">
          {t("first_login.password_hint")}
        </p>
        {errors.newPassword && (
          <p id="fl-pw-error" role="alert" className="text-xs text-destructive font-medium">
            {errors.newPassword.message ? t(errors.newPassword.message) : undefined}
          </p>
        )}
      </div>

      <div className="flex flex-col gap-1.5">
        <Label htmlFor="fl-confirm-password">{t("first_login.confirm_password_label")}</Label>
        <Input
          id="fl-confirm-password"
          type="password"
          autoComplete="new-password"
          aria-invalid={errors.confirmPassword ? true : undefined}
          aria-describedby={errors.confirmPassword ? "fl-confirm-error" : undefined}
          {...register("confirmPassword")}
          className={cn(
            errors.confirmPassword && "border-destructive focus-visible:ring-destructive",
          )}
        />
        {errors.confirmPassword && (
          <p id="fl-confirm-error" role="alert" className="text-xs text-destructive font-medium">
            {errors.confirmPassword.message ? t(errors.confirmPassword.message) : undefined}
          </p>
        )}
      </div>

      <Button
        type="submit"
        disabled={!isValid || isLoading}
        className="w-full"
        aria-busy={isLoading}
      >
        {isLoading ? (
          <>
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
            {t("login.submitting")}
          </>
        ) : (
          t("first_login.step3_submit")
        )}
      </Button>
    </form>
  );
}

// ── Page ─────────────────────────────────────────────────────────────────────

export function FirstLoginPage() {
  const { t } = useTranslation();
  const { status } = useAuth();
  const navigate = useNavigate();
  // If the user arrived here via LoginPage redirect, AuthProvider already
  // applied the enrollment_token and set status="first-login-required" —
  // Step 1 (re-enter email+OTP) would be a redundant duplicate of /login.
  // Skip straight to TOTP enrollment. The Step 1 fallback remains for users
  // who land on /first-login directly (bookmark / reload / etc).
  const [step, setStep] = React.useState<EnrollStep>(
    status === "first-login-required" ? 2 : 1,
  );
  const [backupCodes, setBackupCodes] = React.useState<string[]>([]);
  const [showingCodes, setShowingCodes] = React.useState(false);

  // If already fully authenticated — skip to registry
  if (status === "authenticated") {
    navigate({
      to: "/registry",
      search: {
        q: undefined,
        type_code: undefined,
        status: undefined,
        asset_id: undefined,
        show_archived: undefined,
        sort: undefined,
        dir: undefined,
        page: undefined,
      },
    });
    return null;
  }

  return (
    <div className="flex flex-1 flex-col items-center justify-center bg-muted/30 px-4">
      <div className="fixed top-4 right-4">
        <ThemeToggle />
      </div>

      <Card className="w-full max-w-md shadow-lg">
        <CardHeader className="items-center text-center pb-2">
          <div className="flex items-center gap-2 mb-2" aria-hidden>
            <Compass className="h-8 w-8 text-primary" />
          </div>
          <CardTitle className="text-xl">{t("first_login.title")}</CardTitle>
          <CardDescription>{t("first_login.subtitle")}</CardDescription>
        </CardHeader>

        <CardContent>
          <StepIndicator current={step} />

          {step === 1 && <Step1Form onSuccess={() => setStep(2)} />}

          {step === 2 && !showingCodes && (
            <Step2Form
              onSuccess={(codes) => {
                setBackupCodes(codes);
                setShowingCodes(true);
              }}
            />
          )}

          {step === 2 && showingCodes && (
            <BackupCodesDisplay
              codes={backupCodes}
              onConfirmed={() => {
                setShowingCodes(false);
                setStep(3);
              }}
            />
          )}

          {step === 3 && (
            <Step3Form
              onSuccess={() => {
                // setAccessToken is called internally after password change
                // Navigate to registry — AuthProvider has authenticated status
                navigate({
                  to: "/registry",
                  search: {
                    q: undefined,
                    type_code: undefined,
                    status: undefined,
                    asset_id: undefined,
                    show_archived: undefined,
                    sort: undefined,
                    dir: undefined,
                    page: undefined,
                  },
                });
              }}
            />
          )}
        </CardContent>
      </Card>

      {/* Version & copyright shown by global Footer (router.tsx RootShell). */}
    </div>
  );
}
