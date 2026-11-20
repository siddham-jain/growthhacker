import type { CreatorView } from "@/lib/api/types";
import { formatDate } from "@/lib/format";
import { ReferenceStrip } from "./reference-strip";

const COLLAB_LABELS = { mention: "Mention", disclosed_paid_promotion: "Disclosed paid promotion" } as const;

export function CreatorCard({ creator }: { creator: CreatorView }) {
  return (
    <article className="flex flex-col gap-3 rounded-card bg-card p-4 shadow-card sm:p-5">
      <header className="flex items-center gap-3">
        <span aria-hidden className="grid size-10 place-items-center rounded-full bg-secondary font-heading text-lg text-muted-foreground">
          {creator.channel.title.charAt(0)}
        </span>
        <div className="min-w-0">
          <h3 className="text-headline text-foreground">{creator.channel.title}</h3>
          <p className="text-caption-1-regular text-muted-foreground">
            {creator.channel.handle} · {creator.subscriber_band} · {creator.cadence_label}
          </p>
        </div>
      </header>
      <dl className="grid gap-1.5 text-body-regular">
        <div>
          <dt className="inline text-muted-foreground">Relevance: </dt>
          <dd className="inline text-foreground">
            {creator.relevance.matched_videos} of {creator.relevance.recent_videos} recent videos match your topics ({creator.relevance.topics.join(", ")})
          </dd>
        </div>
        <div>
          <dt className="inline text-muted-foreground">Recent performance: </dt>
          <dd className="inline text-foreground">{creator.recent_performance_vs_niche}</dd>
        </div>
        <div>
          <dt className="inline text-muted-foreground">Consistency: </dt>
          <dd className="inline text-foreground">{creator.consistency}</dd>
        </div>
        {creator.collaborations.length ? (
          <div>
            <dt className="inline text-muted-foreground">Worked with: </dt>
            <dd className="inline text-foreground">
              {creator.collaborations.map((collab) => `${collab.competitor_name} (${COLLAB_LABELS[collab.label]}, ${formatDate(collab.date)})`).join(" · ")}
            </dd>
          </div>
        ) : null}
      </dl>
      <ReferenceStrip videos={creator.reference_videos} />
    </article>
  );
}
