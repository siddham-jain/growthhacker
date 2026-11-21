import { subscriberBandLabel } from "@/lib/api/adapters";
import type { CreatorView } from "@/lib/api/types";
import { formatCompact, formatDate, formatRatio } from "@/lib/format";
import { ReferenceStrip } from "./reference-strip";

const COLLAB_LABELS: Record<CreatorView["collaborations"][number]["kind"], string> = {
  mentions_competitor: "Mention",
  mentioned_by_competitor: "Mentioned by them",
  sponsor_language: "Sponsor wording",
  disclosed_paid_promotion: "Disclosed paid promotion",
};

// raw numbers only: subscriber band, never an inferred audience; no rank is shown. the page carries the disclosure footnote per group
export function CreatorCard({ creator }: { creator: CreatorView }) {
  const title = creator.channel.title ?? creator.channel.handle ?? creator.channel.channel_id;
  const { performance, consistency } = creator;
  return (
    <article className="flex flex-col gap-3 rounded-card bg-card p-4 shadow-card sm:p-5">
      <header className="flex items-center gap-3">
        <span aria-hidden className="grid size-10 place-items-center rounded-full bg-secondary font-heading text-lg text-muted-foreground">
          {title.charAt(0)}
        </span>
        <div className="min-w-0">
          <h3 className="text-headline text-foreground">{title}</h3>
          <p className="text-caption-1-regular text-muted-foreground">
            {[creator.channel.handle, subscriberBandLabel(creator.subscriber_band), `${creator.uploads_90d} uploads in 90 days`].filter(Boolean).join(" · ")}
          </p>
        </div>
      </header>
      <p className="text-body-regular text-foreground">{creator.summary}</p>
      <dl className="grid gap-1.5 text-body-regular">
        <div>
          <dt className="inline text-muted-foreground">Relevance: </dt>
          <dd className="inline text-foreground">
            {Math.round(creator.match_share * 100)}% of recent videos match your keywords · {creator.topics_covered} of your topics covered
          </dd>
        </div>
        <div>
          <dt className="inline text-muted-foreground">Recent performance: </dt>
          <dd className="inline text-foreground">
            median {formatRatio(performance.median_outlier_ratio)} their own normal
            {performance.niche_median_views_at_age ? ` · niche median ${formatCompact(performance.niche_median_views_at_age)} views at day ${performance.comparison_age_days}` : ""}
          </dd>
        </div>
        <div>
          <dt className="inline text-muted-foreground">Consistency: </dt>
          <dd className="inline text-foreground">
            {consistency.median_gap_days === null ? "–" : `posts every ${consistency.median_gap_days} days`}
            {consistency.hit_rate === null ? "" : ` · ${Math.round(consistency.hit_rate * 100)}% of videos beat their normal`}
            {consistency.last_upload_at ? ` · last upload ${formatDate(consistency.last_upload_at)}` : ""}
          </dd>
        </div>
        {creator.collaborations.length ? (
          <div>
            <dt className="inline text-muted-foreground">Worked with: </dt>
            <dd className="inline text-foreground">
              {creator.collaborations.map((collab) => `${collab.competitor_name} (${COLLAB_LABELS[collab.kind]}, ${formatDate(collab.published_at)})`).join(" · ")}
            </dd>
          </div>
        ) : null}
      </dl>
      {creator.reference_videos.length ? <ReferenceStrip videos={creator.reference_videos} note={false} /> : null}
    </article>
  );
}
