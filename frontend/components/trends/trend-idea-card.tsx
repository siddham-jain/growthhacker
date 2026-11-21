"use client";

import { RiArrowDownSLine } from "@remixicon/react";
import { useState } from "react";
import { Chip } from "@/components/ui/primitives";
import type { KeywordTrend, TrendIdea } from "@/lib/api/types";
import { cx } from "@/lib/cx";
import { HeatChip, TrendVideoGrid } from "./trend-parts";

// l1: title, keyword and angle; l2 (expand in place): the facts, fit and the videos behind it
export function TrendIdeaCard({ idea, trend, defaultExpanded = false }: { idea: TrendIdea; trend: KeywordTrend | undefined; defaultExpanded?: boolean }) {
  const [expanded, setExpanded] = useState(defaultExpanded);
  const references = trend ? idea.reference_video_ids.map((id) => trend.videos.find((video) => video.video_id === id)).filter((video) => video !== undefined) : [];

  return (
    <article id={idea.id} className="flex scroll-mt-24 flex-col gap-3 rounded-card bg-card p-4 shadow-card transition-shadow focus-within:shadow-raised hover:shadow-raised sm:p-5">
      <header className="flex flex-wrap items-center gap-2">
        {trend ? <HeatChip heat={trend.heat} /> : null}
        <Chip variant="caption" color="gray">
          {idea.keyword}
        </Chip>
        <Chip variant="caption" color="soft">
          {idea.format === "short" ? "Short" : "Long video"}
        </Chip>
      </header>
      <h3 className="text-headline text-foreground">{idea.title}</h3>
      <p className="text-body-regular text-foreground">{idea.angle}</p>
      {idea.buyer_question ? <p className="text-body-regular text-lens-conversion">Answers “{idea.buyer_question}”</p> : null}
      <p className="text-body-regular text-muted-foreground">Why now: {idea.why[0]}</p>

      <button type="button" aria-expanded={expanded} onClick={() => setExpanded(!expanded)} className="flex w-fit items-center gap-1 rounded-sm text-body-medium text-info">
        <RiArrowDownSLine aria-hidden className={cx("size-4 transition-transform", expanded && "rotate-180")} />
        {expanded ? "Less evidence" : `See the ${references.length} videos behind it`}
      </button>

      {expanded ? (
        <div className="flex animate-reveal flex-col gap-3 border-t border-border pt-3">
          {idea.why.length > 1 ? (
            <ul className="list-disc space-y-1 pl-5 text-body-regular text-foreground">
              {idea.why.slice(1).map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
          ) : null}
          <p className="text-body-regular text-foreground">
            <span className="text-muted-foreground">Why you: </span>
            {idea.fit_to_client}
          </p>
          {trend?.candidate.web_reason ? (
            <p className="text-body-regular text-foreground">
              <span className="text-muted-foreground">In the news: </span>
              {trend.candidate.web_reason}{" "}
              {trend.candidate.web_urls[0] ? (
                <a href={trend.candidate.web_urls[0]} target="_blank" rel="noreferrer" className="text-info underline underline-offset-2">
                  Source
                </a>
              ) : null}
            </p>
          ) : null}
          {references.length ? <TrendVideoGrid videos={references} /> : null}
        </div>
      ) : null}
    </article>
  );
}
