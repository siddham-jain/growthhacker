"use client";

import { RiCheckLine, RiCloseLine } from "@remixicon/react";
import { CompetitorFolder } from "@/components/competitors/competitor-folder";
import { Button } from "@/components/ui/button";
import type { CompetitorView } from "@/lib/api/types";
import { RELATION_LABELS } from "@/lib/labels";

interface CompetitorReviewProps {
  competitors: CompetitorView[];
  decisions: Record<string, "confirmed" | "rejected">;
  onDecide: (id: string, decision: "confirmed" | "rejected") => void;
}

export function CompetitorReview({ competitors, decisions, onDecide }: CompetitorReviewProps) {
  return (
    <ul className="grid grid-cols-1 gap-5 sm:grid-cols-2 xl:grid-cols-3">
      {competitors.map((competitor) => {
        const decision = decisions[competitor.id];
        return (
          <li key={competitor.id} className="flex flex-col gap-2">
            <CompetitorFolder
              size="sm"
              name={competitor.name}
              badge={RELATION_LABELS[competitor.relation]}
              subtitle={competitor.channel?.handle}
              previews={competitor.preview_video_ids.map((videoId) => ({ videoId, title: competitor.name }))}
              count={competitor.uploads_90d ?? "–"}
              countLabel="videos in 90 days"
            />
            <p className="px-1 text-caption-1-regular text-muted-foreground">{competitor.evidence_summary}</p>
            <dl className="grid gap-0.5 px-1 text-caption-1-regular">
              <div>
                <dt className="inline text-muted-foreground">Business match (from websites): </dt>
                <dd className="inline text-foreground">{competitor.business_relevance}</dd>
              </div>
              <div>
                <dt className="inline text-muted-foreground">YouTube activity and topic match (from YouTube): </dt>
                <dd className="inline text-foreground tabular-nums">{competitor.youtube_relevance.toFixed(2)}</dd>
              </div>
            </dl>
            <div className="flex gap-2 px-1">
              <Button variant={decision === "confirmed" ? "primary" : "secondary"} size="xs" leadingIcon={RiCheckLine} aria-pressed={decision === "confirmed"} onClick={() => onDecide(competitor.id, "confirmed")}>
                {decision === "confirmed" ? "Tracking" : "Track"}
              </Button>
              <Button variant="secondary" size="xs" leadingIcon={RiCloseLine} aria-pressed={decision === "rejected"} onClick={() => onDecide(competitor.id, "rejected")}>
                {decision === "rejected" ? "Skipped" : "Not a competitor"}
              </Button>
            </div>
          </li>
        );
      })}
    </ul>
  );
}
