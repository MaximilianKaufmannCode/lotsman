// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * LoginPage — three-step auth flow.
 *
 * Step 1: email + password
 * Step 2: 6-digit TOTP (with "use a backup code" escape hatch)
 * Step 3: 4-4 hex backup code
 *
 * Security:
 * - Generic 401 message "Неверные учётные данные" — no enumeration.
 * - Access token never persisted (lives in AuthContext memory only).
 * - zod: password min 12 / max 1024 per ADR-0003 §2.
 *
 * On success: redirect to ?next= param or /registry.
 */

import { zodResolver } from "@hookform/resolvers/zod";
import { Navigate, useNavigate, useSearch } from "@tanstack/react-router";
import { Compass, Loader2 } from "lucide-react";
import * as React from "react";
import { useForm } from "react-hook-form";
import { useTranslation } from "react-i18next";
import { z } from "zod";
import { useAuth } from "@/features/auth/AuthProvider";
import { ApiResponseError } from "@/features/auth/api";
import type { LoginTotpRequiredResponse } from "@/features/auth/types";
import { cn } from "@/shared/lib/cn";
import { Button } from "@/shared/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/shared/ui/card";
import { Input } from "@/shared/ui/input";
import { Label } from "@/shared/ui/label";
import { ThemeToggle } from "@/shared/ui/theme-toggle";
import { toast } from "@/shared/ui/toast";

// ── Schemas ───────────────────────────────────────────────────────────────────

const step1Schema = z.object({
  email: z.string().min(1, "login_errors.email_required").email("login_errors.email_invalid"),
  password: z
    .string()
    .min(1, "login_errors.password_required")
    .min(12, "login_errors.password_min")
    .max(1024, "login_errors.password_max"),
});

const step2Schema = z.object({
  totp: z
    .string()
    .min(1, "login_errors.totp_required")
    .length(6, "login_errors.totp_length")
    .regex(/^\d+$/, "login_errors.totp_digits"),
});

const step3Schema = z.object({
  backupCode: z
    .string()
    .min(1, "login_errors.backup_required")
    .regex(/^[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}$/, "login_errors.backup_format"),
});

type Step1Values = z.infer<typeof step1Schema>;
type Step2Values = z.infer<typeof step2Schema>;
type Step3Values = z.infer<typeof step3Schema>;

// ── Form field wrapper ────────────────────────────────────────────────────────

interface FormFieldProps {
  id: string;
  label: string;
  error?: string | undefined;
  hint?: string | undefined;
  children: React.ReactNode;
}

function FormField({ id, label, error, hint, children }: FormFieldProps) {
  const errorId = `${id}-error`;
  const hintId = hint ? `${id}-hint` : undefined;

  return (
    <div className="flex flex-col gap-1.5">
      <Label htmlFor={id}>{label}</Label>
      {React.cloneElement(
        children as React.ReactElement<React.InputHTMLAttributes<HTMLInputElement>>,
        {
          id,
          "aria-describedby":
            [hintId, error ? errorId : undefined].filter(Boolean).join(" ") || undefined,
          "aria-invalid": error ? (true as const) : undefined,
        },
      )}
      {hint && (
        <p id={hintId} className="text-xs text-muted-foreground">
          {hint}
        </p>
      )}
      {error && (
        <p id={errorId} role="alert" className="text-xs text-destructive font-medium">
          {error}
        </p>
      )}
    </div>
  );
}

// ── Step 1: email + password ──────────────────────────────────────────────────

interface Step1Props {
  onSuccess: (res: LoginTotpRequiredResponse) => void;
}

function Step1({ onSuccess }: Step1Props) {
  const { t } = useTranslation();
  const { login, status } = useAuth();
  const navigate = useNavigate();
  const search = useSearch({ strict: false }) as { next?: string };

  const isSubmitting = status === "loading";

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
    try {
      const res = await login(values.email, values.password);
      if (res.next_step === "verify_totp") {
        onSuccess(res as LoginTotpRequiredResponse);
      } else if (res.next_step === "none") {
        // Fully authenticated — redirect
        const next = search.next ?? "/registry";
        navigate({ to: next as "/" });
      } else if (res.next_step === "enroll_totp") {
        // First-login: AuthProvider already applied the enrollment_token and
        // set status="first-login-required", but AuthGuard only redirects from
        // PRIVATE routes. /login is public, so navigate explicitly.
        navigate({ to: "/first-login" as "/" });
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
    }
  };

  return (
    <form
      onSubmit={handleSubmit(onSubmit)}
      noValidate
      aria-label={t("login.title")}
      className="flex flex-col gap-4"
    >
      {errors.root && (
        <div
          role="alert"
          className="rounded-md bg-destructive/10 border border-destructive px-3 py-2"
        >
          <p className="text-sm text-destructive font-medium">{errors.root.message}</p>
        </div>
      )}

      <FormField
        id="login-email"
        label={t("login.email_label")}
        error={errors.email?.message ? t(errors.email.message) : undefined}
      >
        <Input
          type="email"
          autoComplete="email"
          placeholder={t("login.email_placeholder")}
          autoFocus
          {...register("email")}
          className={cn(errors.email && "border-destructive focus-visible:ring-destructive")}
        />
      </FormField>

      <FormField
        id="login-password"
        label={t("login.password_label")}
        error={errors.password?.message ? t(errors.password.message) : undefined}
      >
        <Input
          type="password"
          autoComplete="current-password"
          placeholder={t("login.password_placeholder")}
          {...register("password")}
          className={cn(errors.password && "border-destructive focus-visible:ring-destructive")}
        />
      </FormField>

      <Button
        type="submit"
        disabled={!isValid || isSubmitting}
        className="mt-2 w-full"
        aria-busy={isSubmitting}
      >
        {isSubmitting ? (
          <>
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
            {t("login.submitting")}
          </>
        ) : (
          t("login.submit")
        )}
      </Button>
    </form>
  );
}

// ── Step 2: TOTP ──────────────────────────────────────────────────────────────

interface Step2Props {
  totpSessionToken: string;
  onBackupCode: () => void;
}

function Step2({ totpSessionToken, onBackupCode }: Step2Props) {
  const { t } = useTranslation();
  const { completeTotp, status } = useAuth();
  const navigate = useNavigate();
  const search = useSearch({ strict: false }) as { next?: string };
  const totpRef = React.useRef<HTMLInputElement>(null);

  const isSubmitting = status === "loading";

  const {
    register,
    handleSubmit,
    setError,
    formState: { errors, isValid },
  } = useForm<Step2Values>({
    resolver: zodResolver(step2Schema),
    mode: "onChange",
  });

  // autofocus TOTP on mount
  React.useEffect(() => {
    totpRef.current?.focus();
  }, []);

  const onSubmit = async (values: Step2Values) => {
    try {
      await completeTotp(totpSessionToken, values.totp);
      const next = search.next ?? "/registry";
      navigate({ to: next as "/" });
    } catch (err) {
      if (err instanceof ApiResponseError && err.status === 401) {
        setError("totp", { message: t("login_errors.invalid_credentials") });
      } else {
        toast.show({
          title: t("login_errors.network_error_title"),
          description: t("login_errors.network_error_description"),
          variant: "destructive",
        });
      }
    }
  };

  const { ref: rhfRef, ...totpRest } = register("totp");

  return (
    <form
      onSubmit={handleSubmit(onSubmit)}
      noValidate
      aria-label={t("login.totp_step_label")}
      className="flex flex-col gap-4"
    >
      <p className="text-sm text-muted-foreground">{t("login.totp_step_description")}</p>

      <FormField
        id="login-totp"
        label={t("login.totp_label")}
        error={errors.totp?.message ? t(errors.totp.message) : undefined}
        hint={t("login.totp_hint")}
      >
        <Input
          type="text"
          inputMode="numeric"
          autoComplete="one-time-code"
          maxLength={6}
          placeholder={t("login.totp_placeholder")}
          {...totpRest}
          ref={(el) => {
            rhfRef(el);
            (totpRef as React.MutableRefObject<HTMLInputElement | null>).current = el;
          }}
          className={cn(
            "font-mono tracking-widest text-center",
            errors.totp && "border-destructive focus-visible:ring-destructive",
          )}
        />
      </FormField>

      <Button
        type="submit"
        disabled={!isValid || isSubmitting}
        className="w-full"
        aria-busy={isSubmitting}
      >
        {isSubmitting ? (
          <>
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
            {t("login.submitting")}
          </>
        ) : (
          t("login.totp_submit")
        )}
      </Button>

      <button
        type="button"
        onClick={onBackupCode}
        className="text-sm text-primary underline-offset-4 hover:underline self-center focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded"
      >
        {t("login.use_backup_code")}
      </button>
    </form>
  );
}

// ── Step 3: backup code ───────────────────────────────────────────────────────

interface Step3Props {
  totpSessionToken: string;
  onBack: () => void;
}

function Step3({ totpSessionToken, onBack }: Step3Props) {
  const { t } = useTranslation();
  const { useBackupCode: submitBackupCode, status } = useAuth();
  const navigate = useNavigate();
  const search = useSearch({ strict: false }) as { next?: string };

  const isSubmitting = status === "loading";

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
    try {
      await submitBackupCode(totpSessionToken, values.backupCode);
      const next = search.next ?? "/registry";
      navigate({ to: next as "/" });
    } catch (err) {
      if (err instanceof ApiResponseError && err.status === 401) {
        setError("backupCode", { message: t("login_errors.invalid_credentials") });
      } else {
        toast.show({
          title: t("login_errors.network_error_title"),
          description: t("login_errors.network_error_description"),
          variant: "destructive",
        });
      }
    }
  };

  return (
    <form
      onSubmit={handleSubmit(onSubmit)}
      noValidate
      aria-label={t("login.backup_step_label")}
      className="flex flex-col gap-4"
    >
      <p className="text-sm text-muted-foreground">{t("login.backup_step_description")}</p>

      <FormField
        id="login-backup-code"
        label={t("login.backup_code_label")}
        error={errors.backupCode?.message ? t(errors.backupCode.message) : undefined}
        hint={t("login.backup_code_hint")}
      >
        <Input
          type="text"
          autoComplete="off"
          placeholder="A1B2-C3D4"
          autoFocus
          maxLength={9}
          {...register("backupCode")}
          className={cn(
            "font-mono tracking-widest",
            errors.backupCode && "border-destructive focus-visible:ring-destructive",
          )}
        />
      </FormField>

      <Button
        type="submit"
        disabled={!isValid || isSubmitting}
        className="w-full"
        aria-busy={isSubmitting}
      >
        {isSubmitting ? (
          <>
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
            {t("login.submitting")}
          </>
        ) : (
          t("login.backup_submit")
        )}
      </Button>

      <button
        type="button"
        onClick={onBack}
        className="text-sm text-primary underline-offset-4 hover:underline self-center focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded"
      >
        {t("login.back_to_totp")}
      </button>
    </form>
  );
}

// ── Page shell ────────────────────────────────────────────────────────────────

type LoginStep = "credentials" | "totp" | "backup";

export function LoginPage() {
  const { t } = useTranslation();
  const { status } = useAuth();
  const [step, setStep] = React.useState<LoginStep>("credentials");
  const [totpSessionToken, setTotpSessionToken] = React.useState<string>("");

  // If already authenticated — redirect to registry
  if (status === "authenticated") {
    return (
      <Navigate
        to="/registry"
        search={{
          q: undefined,
          type_code: undefined,
          status: undefined,
          asset_id: undefined,
          show_archived: undefined,
          sort: undefined,
          dir: undefined,
          page: undefined,
        }}
        replace
      />
    );
  }

  const stepLabel: Record<LoginStep, string> = {
    credentials: t("login.step_credentials"),
    totp: t("login.step_totp"),
    backup: t("login.step_backup"),
  };

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
          <CardTitle className="text-xl">{t("login.title")}</CardTitle>
          <CardDescription>{t("login.subtitle")}</CardDescription>

          {/* Step indicator */}
          <p className="text-xs text-muted-foreground mt-1" aria-live="polite">
            {stepLabel[step]}
          </p>
        </CardHeader>

        <CardContent>
          {step === "credentials" && (
            <Step1
              onSuccess={(res) => {
                setTotpSessionToken(res.totp_session_token);
                setStep("totp");
              }}
            />
          )}
          {step === "totp" && (
            <Step2 totpSessionToken={totpSessionToken} onBackupCode={() => setStep("backup")} />
          )}
          {step === "backup" && (
            <Step3 totpSessionToken={totpSessionToken} onBack={() => setStep("totp")} />
          )}
        </CardContent>
      </Card>

      {/* Version & copyright shown by global Footer (router.tsx RootShell). */}
    </div>
  );
}
