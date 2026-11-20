"use client";

import { RiExternalLinkLine, RiTimeLine } from "@remixicon/react";
import { OriginChip } from "@/components/ui/chips";
import { ConfidenceMeter } from "@/components/ui/confidence-meter";
import { Chip } from "@/components/ui/primitives";
import type { EarlySignalView } from "@/lib/api/types";
import { formatCountdown, formatRelative, plural } from "@/lib/format";
import { ReferenceStrip } from "./reference-strip";

const TIER_LABELS: Record<string, string> = {
  tier1_primary: "primary source",
  tier1_authority: "authority",
  tier2_established: "established outlet",
  tier3_low: "smaller outlet",
};

// fewer than 3 reference videos, so it can't be a recommendation; the label says so plainly (owner decision o3)
export function EarlySignalCard({ signal, now }: { signal: EarlySignalView; now: number }) {
  const firstMover = signal.confidence.basis === "first_mover";
  return (
    <article id={signal.id} className="flex scroll-mt-24 flex-col gap-3 rounded-card bg-card p-4 shadow-card sm:p-5">
      <header className="flex flex-wrap items-center gap-2">
        <Chip variant="caption" color="orange">
          Early signal
        </Chip>
        <ConfidenceMeter confidence={signal.confidence} firstMover={firstMover} compact />
        <span className="ml-auto inline-flex items-center gap-1.5 text-caption-1-medium text-muted-foreground">
          <RiTimeLine aria-hidden className="size-3.5" />
          {formatCountdown(signal.expires_at, now)}
        </span>
      </header>
      <p className="text-caption-1-regular text-muted-foreground">{signal.key_entity}</p>
      <h3 className="text-headline text-foreground">{signal.angle}</h3>
      {signal.statements?.length ? (
        <ul className="list-disc space-y-1 pl-5 text-body-regular text-foreground">
          {signal.statements.map((statement) => (
            <li key={statement}>{statement}</li>
          ))}
        </ul>
      ) : null}
      <div className="flex flex-col gap-1.5">
        <div className="flex items-center gap-2">
          <span className="text-caption-1-medium text-muted-foreground">Sources</span>
          <OriginChip origin="News" />
        </div>
        <ul className="flex flex-col gap-1 text-body-regular">
          {signal.sources.map((source) => (
            <li key={source.url} className="flex flex-wrap items-center gap-x-2">
              <a href={source.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-foreground underline decoration-border-strong underline-offset-4 hover:decoration-foreground">
                {source.title ?? source.publisher}
                <RiExternalLinkLine aria-hidden className="size-3.5" />
              </a>
              <span className="text-caption-1-regular text-muted-foreground">
                {source.publisher}
                {source.tier && TIER_LABELS[source.tier] ? ` · ${TIER_LABELS[source.tier]}` : ""}
                {source.published_at ? ` · ${formatRelative(source.published_at, now)}` : ""}
              </span>
            </li>
          ))}
        </ul>
      </div>
      {signal.reference_videos?.length ? (
        <div className="max-w-xl">
          <ReferenceStrip videos={signal.reference_videos} />
        </div>
      ) : (
        <p className="text-body-regular text-muted-foreground">No YouTube videos on this yet. Being early is the opportunity; there&apos;s no evidence yet that it will perform.</p>
      )}
      {signal.reference_videos?.length ? (
        <p className="text-caption-1-regular text-muted-foreground">
          Only {plural(signal.reference_videos.length, "YouTube video")} so far, fewer than the 3 a recommendation needs.
        </p>
      ) : null}
    </article>
  );
}
