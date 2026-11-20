"use client";

import { RiAlertLine, RiInformationLine } from "@remixicon/react";
import type { ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { Shimmer } from "@/components/ui/primitives";
import { isApiError } from "@/lib/api/errors";
import { cx } from "@/lib/cx";

export function LoadingBlock({ rows = 3, className }: { rows?: number; className?: string }) {
  return (
    <div className={cx("flex flex-col gap-3", className)} aria-busy="true" aria-label="Loading">
      {Array.from({ length: rows }, (_, index) => (
        <Shimmer key={index} dark="auto" className="h-24 w-full rounded-card" />
      ))}
    </div>
  );
}

export function EmptyState({ title, body, action }: { title: string; body: string; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-start gap-2 rounded-card border border-dashed border-border-strong px-6 py-8">
      <p className="text-headline text-foreground">{title}</p>
      <p className="max-w-xl text-body-regular text-muted-foreground">{body}</p>
      {action}
    </div>
  );
}

function errorMessage(error: unknown): string {
  if (!isApiError(error)) return "Something went wrong while loading this.";
  if (error.status === 404) return "We couldn't find that. It may have been removed or you may not have access.";
  if (error.status === 401) return "Please sign in again.";
  if (error.status === 429) return `Too many requests. Try again in ${error.retryAfterSeconds ?? 30} s.`;
  if (error.status === 0 || error.status >= 500) return "We can't reach GrowthHacker right now.";
  return error.message;
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  return (
    <div role="alert" className="flex items-start gap-3 rounded-card bg-card px-5 py-4 shadow-card">
      <RiAlertLine aria-hidden className="mt-0.5 size-5 text-danger" />
      <div className="flex flex-col gap-2">
        <p className="text-body-regular text-foreground">{errorMessage(error)}</p>
        {onRetry ? (
          <Button variant="secondary" size="small" onClick={onRetry} className="w-fit">
            Try again
          </Button>
        ) : null}
      </div>
    </div>
  );
}

export function InfoNote({ children, tone = "info" }: { children: ReactNode; tone?: "info" | "warning" }) {
  return (
    <p
      className={cx(
        "flex items-start gap-2 rounded-control px-3 py-2 text-body-regular",
        tone === "warning" ? "bg-[color-mix(in_oklab,var(--warning)_10%,var(--card))] text-warning" : "bg-muted text-muted-foreground",
      )}
    >
      <RiInformationLine aria-hidden className="mt-0.5 size-4 shrink-0" />
      <span>{children}</span>
    </p>
  );
}
