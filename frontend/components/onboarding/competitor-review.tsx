"use client";

import { RiCheckLine, RiCloseLine } from "@remixicon/react";
import { CompetitorFolder } from "@/components/competitors/competitor-folder";
import { Button } from "@/components/ui/button";
import { CalcMark, CalcNote } from "@/components/ui/calc-mark";
import type { CompetitorView } from "@/lib/api/types";
import { channelFacts, previewVideoIds, primaryChannel } from "@/lib/competitors";
import { RELATION_LABELS } from "@/lib/labels";

interface CompetitorReviewProps {
  competitors: CompetitorView[];
  decisions: Record<string, "confirmed" | "rejected">;
  onDecide: (id: string, decision: "confirmed" | "rejected") => void;
}

export function CompetitorReview({ competitors, decisions, onDecide }: CompetitorReviewProps) {
  return (
    <div className="flex flex-col gap-3">
      <ul className="grid grid-cols-1 gap-5 sm:grid-cols-2 xl:grid-cols-3">
        {competitors.map((competitor) => {
          const decision = decisions[competitor.id];
          const primary = primaryChannel(competitor);
          return (
            <li key={competitor.id} className="flex flex-col gap-2">
              <CompetitorFolder
                size="sm"
                name={competitor.name}
                badge={RELATION_LABELS[competitor.relation]}
                subtitle={competitor.channel?.handle ?? undefined}
                previews={previewVideoIds(competitor).map((videoId) => ({ videoId, title: competitor.name }))}
                count={primary?.evidence.uploads_90d ?? "–"}
                countLabel="videos in 90 days"
              />
              <p className="px-1 text-caption-1-regular text-muted-foreground">{competitor.evidence_summary}</p>
              <dl className="grid gap-0.5 px-1 text-caption-1-regular">
                <div>
                  <dt className="inline text-muted-foreground">Business match (from websites): </dt>
                  <dd className="inline text-foreground">{competitor.business.tier}</dd>
                </div>
                <div>
                  <dt className="inline text-muted-foreground">On YouTube: </dt>
                  <dd className="inline text-foreground tabular-nums">
                    {primary ? (
                      <>
                        {channelFacts(primary).join(" · ")}
                        <CalcMark />
                      </>
                    ) : (
                      "no channel found"
                    )}
                  </dd>
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
      {competitors.length ? <CalcNote /> : null}
    </div>
  );
}
