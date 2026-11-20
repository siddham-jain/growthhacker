import { useId, type InputHTMLAttributes, type ReactNode } from "react";
import { cx } from "@/lib/cx";

interface TextFieldProps extends InputHTMLAttributes<HTMLInputElement> {
  label: string;
  hint?: ReactNode;
  error?: string | null;
}

export function TextField({ label, hint, error, className, ...props }: TextFieldProps) {
  const id = useId();
  const describedBy = error ? `${id}-error` : hint ? `${id}-hint` : undefined;
  return (
    <div className={cx("flex flex-col gap-1.5", className)}>
      <label htmlFor={id} className="text-body-medium text-foreground">
        {label}
      </label>
      <input
        id={id}
        aria-invalid={Boolean(error)}
        aria-describedby={describedBy}
        className={cx(
          "h-13 rounded-input border bg-card px-4 text-[15px] text-foreground shadow-raised outline-none transition-colors",
          "placeholder:text-subtle-foreground focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/30",
          error ? "border-danger" : "border-border",
        )}
        {...props}
      />
      {error ? (
        <p id={`${id}-error`} role="alert" className="text-caption-1-regular text-danger">
          {error}
        </p>
      ) : hint ? (
        <p id={`${id}-hint`} className="text-caption-1-regular text-muted-foreground">
          {hint}
        </p>
      ) : null}
    </div>
  );
}
