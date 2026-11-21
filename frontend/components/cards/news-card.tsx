"use client";

import { RiCloseLine, RiExternalLinkLine, RiTimeLine } from "@remixicon/react";
import { InfoNote } from "@/components/states/states";
import { Button } from "@/components/ui/button";
import { OriginChip } from "@/components/ui/chips";
import { Chip } from "@/components/ui/primitives";
import type { NewsOpportunityView } from "@/lib/api/types";
import { formatCountdown, formatRelative, plural } from "@/lib/format";

export const MIN_REFERENCES = 3;

function FirstMoverLabel() {
  return (
    <span className="inline-flex items-center gap-2 text-caption-1-medium text-muted-foreground" title="Fewer than 3 YouTube videos on this so far, so there is no outlier evidence yet">
      <span aria-hidden className="flex items-end gap-0.5">
        {[0, 1, 2].map((bar) => (
          <span key={bar} className="h-2.5 w-1 rounded-full bg-border-strong" />
        ))}
      </span>
      <span className="text-foreground">First-mover</span>
    </span>
  );
}

// news leads with time because the opening closes; early signals say plainly that there's no evidence yet
export function NewsCard({ opportunity, now, onDismiss }: { opportunity: NewsOpportunityView; now: number; onDismiss?: () => void }) {
  const hoursLeft = (Date.parse(opportunity.expires_at) - now) / 3_600_000;
  const firstMover = opportunity.reference_count < MIN_REFERENCES;
  return (
    <article id={opportunity.id} className="flex scroll-mt-24 flex-col gap-3 rounded-card bg-card p-4 shadow-card sm:p-5">
      <header className="flex flex-wrap items-center gap-2">
        <Chip variant="caption" color="orange">
          News
        </Chip>
        {firstMover ? (
          <FirstMoverLabel />
        ) : (
          <span className="text-caption-1-medium text-muted-foreground">
            {plural(opportunity.reference_count, "reference video")}
          </span>
        )}
        <span className="ml-auto inline-flex items-center gap-1.5 text-caption-1-medium text-muted-foreground">
          <RiTimeLine aria-hidden className="size-3.5" />
          {formatCountdown(opportunity.expires_at, now)}
          {hoursLeft < 6 && hoursLeft > 0 ? (
            <Chip variant="caption" color="yellow">
              Closing soon
            </Chip>
          ) : null}
        </span>
      </header>
      <p className="text-caption-1-regular text-muted-foreground">
        <a href={opportunity.news.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-foreground underline decoration-border-strong underline-offset-4 hover:decoration-foreground">
          {opportunity.news.headline}
          <RiExternalLinkLine aria-hidden className="size-3.5" />
        </a>{" "}
        · {opportunity.news.source_domain}{opportunity.news.published_at ? ` · ${formatRelative(opportunity.news.published_at, now)}` : ""}
      </p>
      <h3 className="text-headline text-foreground">{opportunity.summary ?? opportunity.news.headline}</h3>
      <p className="text-body-regular text-muted-foreground">Ties to: {opportunity.offering_refs.join(", ")}</p>
      <p className="flex flex-wrap items-center gap-2 text-body-regular text-foreground">
        <span>
          <span className="text-muted-foreground">Early YouTube demand: </span>
          {opportunity.demand_check.youtube_results_7d === null
            ? "not checked today (data budget used up)"
            : `${plural(opportunity.demand_check.youtube_results_7d, "video")} in 7 days, ${plural(opportunity.demand_check.outliers ?? 0, "outlier")}`}
        </span>
        <OriginChip origin="YouTube public" />
      </p>
      {firstMover ? (
        <p className="text-body-regular text-muted-foreground">
          {opportunity.reference_count === 0 ? "No YouTube videos on this yet." : `Only ${plural(opportunity.reference_count, "YouTube video")} on this so far.`} Being early is the opportunity;
          there&apos;s no evidence yet that it will perform.
        </p>
      ) : null}
      {opportunity.review_required ? <InfoNote tone="warning">Needs medical review before publishing.</InfoNote> : null}
      {onDismiss ? (
        <footer className="flex border-t border-border pt-3">
          <Button variant="secondary" size="xs" leadingIcon={RiCloseLine} onClick={onDismiss}>
            Not for us
          </Button>
        </footer>
      ) : null}
    </article>
  );
}
