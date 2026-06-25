// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * QuickTypeDialog — compact, admin-only inline creation of a document type,
 * launched from the document-creation form (issue #5).
 *
 * UX (psychology of perception):
 *  - Sensible defaults (schedule pre-filled) → minimal decisions (Hick's law).
 *  - Code auto-derived (translit) from the name → recognition over recall;
 *    editable for power users (progressive disclosure).
 *  - Opens as a focused secondary modal over the document form, which is
 *    preserved underneath → flow/context is never lost.
 * Custom fields stay a separate admin task; this creates the type + schedule.
 */

import { zodResolver } from "@hookform/resolvers/zod";
import { X } from "lucide-react";
import * as React from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";
import type { CreateDocumentTypePayload } from "@/features/registry/types";
import { cn } from "@/shared/lib/cn";
import { Button } from "@/shared/ui/button";
import { Input } from "@/shared/ui/input";

// ── Cyrillic → Latin slug (для авто-кода типа) ────────────────────────────────
const TRANSLIT: Record<string, string> = {
  а: "a",
  б: "b",
  в: "v",
  г: "g",
  д: "d",
  е: "e",
  ё: "e",
  ж: "zh",
  з: "z",
  и: "i",
  й: "i",
  к: "k",
  л: "l",
  м: "m",
  н: "n",
  о: "o",
  п: "p",
  р: "r",
  с: "s",
  т: "t",
  у: "u",
  ф: "f",
  х: "h",
  ц: "c",
  ч: "ch",
  ш: "sh",
  щ: "sch",
  ъ: "",
  ы: "y",
  ь: "",
  э: "e",
  ю: "yu",
  я: "ya",
};

function slugify(input: string): string {
  const lower = input.trim().toLowerCase();
  let out = "";
  for (const ch of lower) {
    out += ch in TRANSLIT ? TRANSLIT[ch] : ch;
  }
  out = out.replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
  if (out && !/^[a-z]/.test(out)) out = `t_${out}`;
  return out.slice(0, 64);
}

function parsePreNotice(raw: string): number[] {
  return raw
    .split(",")
    .map((s) => Number(s.trim()))
    .filter((n) => Number.isInteger(n) && n > 0);
}

const typeSchema = z.object({
  display_name: z.string().min(1, "Обязательное поле").max(200),
  code: z.string().regex(/^[a-z][a-z0-9_]{0,63}$/, "Латиница, цифры и _, начинается с буквы"),
  pre_notice_days: z
    .string()
    .refine((v) => parsePreNotice(v).length > 0, "Укажите дни через запятую, напр. 30, 7"),
  overdue_every_days: z.coerce.number().int().min(1, "Не меньше 1"),
  notify_in_day: z.boolean(),
});

type TypeFormValues = z.infer<typeof typeSchema>;

interface QuickTypeDialogProps {
  open: boolean;
  onClose: () => void;
  onSubmit: (payload: CreateDocumentTypePayload) => Promise<void>;
}

export function QuickTypeDialog({ open, onClose, onSubmit }: QuickTypeDialogProps) {
  const dialogRef = React.useRef<HTMLDivElement>(null);
  const codeTouched = React.useRef(false);

  const {
    register,
    handleSubmit,
    reset,
    setValue,
    watch,
    formState: { errors, isSubmitting, isValid },
  } = useForm<TypeFormValues>({
    resolver: zodResolver(typeSchema),
    mode: "onChange",
    defaultValues: {
      display_name: "",
      code: "",
      pre_notice_days: "30, 7",
      overdue_every_days: 7,
      notify_in_day: true,
    },
  });

  // Auto-derive the code from the name until the user edits the code manually.
  const displayName = watch("display_name");
  React.useEffect(() => {
    if (!codeTouched.current) {
      setValue("code", slugify(displayName ?? ""), { shouldValidate: true });
    }
  }, [displayName, setValue]);

  React.useEffect(() => {
    if (open) {
      codeTouched.current = false;
      reset({
        display_name: "",
        code: "",
        pre_notice_days: "30, 7",
        overdue_every_days: 7,
        notify_in_day: true,
      });
      requestAnimationFrame(() => dialogRef.current?.focus());
    }
  }, [open, reset]);

  React.useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        reset();
        onClose();
      }
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [open, onClose, reset]);

  const submit = handleSubmit(async (values) => {
    await onSubmit({
      code: values.code,
      display_name: values.display_name.trim(),
      pre_notice_days: parsePreNotice(values.pre_notice_days),
      notify_in_day: values.notify_in_day,
      overdue_every_days: values.overdue_every_days,
    });
    reset();
    onClose();
  });

  if (!open) return null;

  return (
    <>
      {/* Secondary modal sits above the document form (rendered later → on top). */}
      <div
        className="fixed inset-0 z-[60] bg-black/50 backdrop-blur-sm"
        aria-hidden="true"
        onClick={() => {
          reset();
          onClose();
        }}
      />
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="quick-type-title"
        tabIndex={-1}
        className={cn(
          "fixed left-1/2 top-1/2 z-[60] w-full max-w-md -translate-x-1/2 -translate-y-1/2",
          "rounded-xl border bg-card text-card-foreground shadow-xl focus:outline-none",
        )}
      >
        <div className="flex items-center justify-between p-4 border-b">
          <h2 id="quick-type-title" className="text-base font-semibold">
            Создать тип документа
          </h2>
          <button
            type="button"
            onClick={() => {
              reset();
              onClose();
            }}
            aria-label="Закрыть"
            className="rounded p-1.5 text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <X className="size-4" aria-hidden />
          </button>
        </div>

        <form
          onSubmit={submit}
          aria-label="Форма создания типа документа"
          className="p-4 space-y-4"
        >
          <FormField label="Название *" error={errors.display_name?.message}>
            <Input {...register("display_name")} placeholder="Например: Паспорт" autoFocus />
          </FormField>

          <FormField
            label="Код *"
            error={errors.code?.message}
            hint="Латиницей, авто из названия. Менять не обязательно."
          >
            <Input
              {...register("code", {
                onChange: () => {
                  codeTouched.current = true;
                },
              })}
              placeholder="passport"
              className="font-mono"
            />
          </FormField>

          <FormField
            label="Напоминать за (дней) *"
            error={errors.pre_notice_days?.message}
            hint="Через запятую, напр. 30, 7"
          >
            <Input {...register("pre_notice_days")} placeholder="30, 7" inputMode="numeric" />
          </FormField>

          <div className="grid grid-cols-2 gap-3">
            <FormField
              label="Просрочка: каждые (дней) *"
              error={errors.overdue_every_days?.message}
            >
              <Input type="number" min={1} {...register("overdue_every_days")} />
            </FormField>
            <label className="flex items-end gap-2 pb-2 text-sm cursor-pointer select-none">
              <input
                type="checkbox"
                {...register("notify_in_day")}
                className="h-4 w-4 rounded border border-input focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              />
              Уведомлять в день истечения
            </label>
          </div>

          <div className="flex gap-2 pt-1">
            <Button type="submit" disabled={isSubmitting || !isValid}>
              {isSubmitting ? "Создание..." : "Создать тип"}
            </Button>
            <Button
              type="button"
              variant="outline"
              onClick={() => {
                reset();
                onClose();
              }}
            >
              Отмена
            </Button>
          </div>
        </form>
      </div>
    </>
  );
}

function FormField({
  label,
  error,
  hint,
  children,
}: {
  label: string;
  error?: string | undefined;
  hint?: string | undefined;
  children: React.ReactNode;
}) {
  const id = React.useId();
  const hintId = hint ? `${id}-hint` : undefined;
  const errId = error ? `${id}-error` : undefined;
  const describedBy = [hintId, errId].filter((x): x is string => x !== undefined).join(" ");

  return (
    <div>
      <label htmlFor={id} className="mb-1 block text-sm font-medium">
        {label}
      </label>
      {React.isValidElement(children)
        ? React.cloneElement(
            children as React.ReactElement<{
              id?: string | undefined;
              "aria-describedby"?: string | undefined;
              "aria-invalid"?: boolean | undefined;
            }>,
            {
              id,
              ...(describedBy ? { "aria-describedby": describedBy } : {}),
              "aria-invalid": !!error,
            },
          )
        : children}
      {hint && !error && (
        <p id={hintId} className="mt-1 text-xs text-muted-foreground">
          {hint}
        </p>
      )}
      {error && (
        <p id={errId} role="alert" className="mt-1 text-xs text-destructive font-medium">
          {error}
        </p>
      )}
    </div>
  );
}
