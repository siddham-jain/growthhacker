"use client";

import { RiArrowDownSLine, RiBookmarkLine, RiCheckLine, RiCloseLine } from "@remixicon/react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { LensChip, TypeChip } from "@/components/ui/chips";
import { ConfidenceMeter } from "@/components/ui/confidence-meter";
import type { FeedbackStatus, LensFilter, RecommendationOut } from "@/lib/api/types";
import { cx } from "@/lib/cx";
import { formatDate } from "@/lib/format";
import { ReferenceStrip } from "./reference-strip";

interface RecommendationCardProps {
  recommendation: RecommendationOut;
  lensFilter: LensFilter;
  defaultExpanded?: boolean;
  highlighted?: boolean;
  onFeedback: (status: FeedbackStatus | null) => void;
}

// l1 shows summary, buyer question and one reason; l2 (expand in place) shows every reason, fit, range and references
export function RecommendationCard({ recommendation: rec, lensFilter, defaultExpanded = false, highlighted = false, onFeedback }: RecommendationCardProps) {
  const [expanded, setExpanded] = useState(defaultExpanded);
  const status = rec.feedback?.status ?? null;
  const [firstReason, ...moreReasons] = rec.why_it_works;

  if (status === "dismissed") {
    return (
      <div className="flex items-center justify-between rounded-card border border-dashed border-border px-5 py-3 text-body-regular text-muted-foreground">
        <span>Dismissed · {rec.summary}</span>
        <Button variant="secondary" size="xs" onClick={() => onFeedback(null)}>
          Undo
        </Button>
      </div>
    );
  }

  return (
    <article
      id={rec.id}
      className={cx(
        "flex scroll-mt-24 flex-col gap-3 rounded-card bg-card p-4 shadow-card transition-shadow focus-within:shadow-raised hover:shadow-raised sm:p-5",
        highlighted && "ring-2 ring-ring",
      )}
    >
      <header className="flex flex-wrap items-center gap-2">
        <TypeChip type={rec.type} />
        {rec.lens.map((lens) => (
          <LensChip key={lens} lens={lens} emphasised={lensFilter === lens} />
        ))}
        <span className="ml-auto">
          <ConfidenceMeter confidence={rec.confidence} />
        </span>
      </header>
      <h3 className="line-clamp-2 text-headline text-foreground" title={rec.summary}>
        {rec.summary}
      </h3>
      {rec.buyer_question ? <p className="text-body-regular text-lens-conversion">“{rec.buyer_question}”</p> : null}
      {firstReason ? <p className="text-body-regular text-muted-foreground">Why: {firstReason}</p> : null}

      <button
        type="button"
        aria-expanded={expanded}
        onClick={() => setExpanded(!expanded)}
        className="flex w-fit items-center gap-1 rounded-sm text-body-medium text-info"
      >
        <RiArrowDownSLine aria-hidden className={cx("size-4 transition-transform", expanded && "rotate-180")} />
        {expanded ? "Less evidence" : `More evidence (${moreReasons.length + rec.reference_videos.length})`}
      </button>

      {expanded ? (
        <div className="flex animate-reveal flex-col gap-3 border-t border-border pt-3">
          {moreReasons.length ? (
            <ul className="list-disc space-y-1 pl-5 text-body-regular text-foreground">
              {moreReasons.map((reason) => (
                <li key={reason}>{reason}</li>
              ))}
            </ul>
          ) : null}
          <p className="text-body-regular text-foreground">
            <span className="text-muted-foreground">Fit: </span>
            {rec.fit_to_client}
          </p>
          <p className="text-body-regular text-foreground">
            <span className="text-muted-foreground">Expected: </span>
            {rec.expected_range_vs_baseline ?? "Not enough comparable videos for a range."}
          </p>
          {rec.confidence.caps_applied?.length ? (
            <p className="text-caption-1-regular text-muted-foreground">{rec.confidence.caps_applied.join(" · ")}</p>
          ) : null}
          <ReferenceStrip videos={rec.reference_videos} />
        </div>
      ) : null}

      <footer className="flex flex-wrap items-center gap-2 border-t border-border pt-3">
        <Button variant="secondary" size="xs" leadingIcon={RiBookmarkLine} aria-pressed={status === "saved"} onClick={() => onFeedback(status === "saved" ? null : "saved")}>
          {status === "saved" ? "Saved" : "Save"}
        </Button>
        <Button variant="secondary" size="xs" leadingIcon={RiCheckLine} aria-pressed={status === "done"} onClick={() => onFeedback(status === "done" ? null : "done")}>
          {status === "done" ? "Done" : "Mark done"}
        </Button>
        <Button variant="secondary" size="xs" leadingIcon={RiCloseLine} onClick={() => onFeedback("dismissed")}>
          Dismiss
        </Button>
        <span className="ml-auto text-caption-1-regular text-subtle-foreground">
          window {rec.window_days}d · {formatDate(rec.generated_at)}
        </span>
      </footer>
    </article>
  );
}
