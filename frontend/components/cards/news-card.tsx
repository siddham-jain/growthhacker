import { RiTimeLine } from "@remixicon/react";
import { InfoNote } from "@/components/states/states";
import { Chip } from "@/components/ui/primitives";
import { OriginChip } from "@/components/ui/chips";
import type { NewsOpportunityView } from "@/lib/api/types";
import { formatCountdown, formatRelative } from "@/lib/format";

export function NewsCard({ opportunity, now }: { opportunity: NewsOpportunityView; now: number }) {
  const hoursLeft = (Date.parse(opportunity.expires_at) - now) / 3_600_000;
  const firstMover = opportunity.reference_count < 3;
  return (
    <article id={opportunity.id} className="flex scroll-mt-24 flex-col gap-3 rounded-card bg-card p-4 shadow-card sm:p-5">
      <header className="flex flex-wrap items-center gap-2">
        <Chip variant="caption" color="orange">
          News
        </Chip>
        <OriginChip origin="News" />
        <span className="ml-auto inline-flex items-center gap-1 text-caption-1-medium text-muted-foreground">
          <RiTimeLine aria-hidden className="size-3.5" />
          {formatCountdown(opportunity.expires_at, now)}
          {hoursLeft < 6 && hoursLeft > 0 ? <Chip variant="caption" color="yellow">Closing soon</Chip> : null}
        </span>
      </header>
      <p className="text-caption-1-regular text-muted-foreground">
        {opportunity.news.headline} · {opportunity.news.source_domain} · {formatRelative(opportunity.news.published_at, now)}
      </p>
      <h3 className="text-headline text-foreground">{opportunity.summary}</h3>
      <p className="text-body-regular text-muted-foreground">Ties to: {opportunity.offering_refs.join(", ")}</p>
      <p className="text-body-regular text-foreground">
        <span className="text-muted-foreground">Early YouTube demand: </span>
        {opportunity.demand_check.youtube_results_7d} videos in 7 days, {opportunity.demand_check.outliers} outliers
      </p>
      {firstMover ? (
        <p className="text-body-regular text-muted-foreground">
          {opportunity.reference_count === 0 ? "No YouTube videos on this yet." : `Only ${opportunity.reference_count} YouTube videos on this so far.`} Being early is the
          opportunity; there&apos;s no evidence yet that it will perform.
        </p>
      ) : null}
      {opportunity.review_required ? <InfoNote tone="warning">Needs medical review before publishing.</InfoNote> : null}
    </article>
  );
}
