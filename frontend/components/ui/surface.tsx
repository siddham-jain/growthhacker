import type { HTMLAttributes } from "react";
import { cx } from "@/lib/cx";

export function Surface({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cx("rounded-card bg-card shadow-card", className)} {...props} />;
}

export function Eyebrow({ className, dot = false, ...props }: HTMLAttributes<HTMLParagraphElement> & { dot?: boolean }) {
  return (
    <p className={cx("flex items-center gap-2 text-eyebrow text-muted-foreground", className)} {...props}>
      {dot ? <span aria-hidden className="size-1.5 rounded-full bg-ring" /> : null}
      {props.children}
    </p>
  );
}
