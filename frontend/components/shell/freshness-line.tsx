import { RiAlertLine } from "@remixicon/react";
import type { ResultEnvelope } from "@/lib/api/types";
import { formatDateTime } from "@/lib/format";

export function FreshnessLine({ envelope }: { envelope: Pick<ResultEnvelope<unknown>, "freshness" | "window_days"> | undefined }) {
  if (!envelope) return null;
  const { freshness, window_days } = envelope;
  if (freshness.stale) {
    return (
      <p role="status" className="mb-5 flex items-center gap-2 rounded-control bg-[color-mix(in_oklab,var(--warning)_10%,var(--card))] px-3 py-2 text-body-regular text-warning">
        <RiAlertLine aria-hidden className="size-4" />
        Data is from {formatDateTime(freshness.data_through)}. Today&apos;s refresh hasn&apos;t run yet.
      </p>
    );
  }
  return (
    <p className="mb-5 text-caption-1-regular text-subtle-foreground">
      Data through {formatDateTime(freshness.data_through)} · refreshed daily · {window_days}-day window
    </p>
  );
}
