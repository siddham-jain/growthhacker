"use client";

import { useQuery } from "@tanstack/react-query";
import { EmptyState, ErrorState, LoadingBlock } from "@/components/states/states";
import { CalcNote } from "@/components/ui/calc-mark";
import { trendsQuery } from "@/lib/api/queries";
import { formatDateTime, plural } from "@/lib/format";
import { KeywordTrends } from "./keyword-trends";
import { TrendIdeaCard } from "./trend-idea-card";

// the landing section: what is trending in the client's niche on youtube, and what to post on it
export function TrendingIdeas({ clientId }: { clientId: string }) {
  const result = useQuery(trendsQuery(clientId));
  if (result.isPending) return <LoadingBlock rows={3} />;
  if (result.error) return <ErrorState error={result.error} onRetry={() => result.refetch()} />;

  const scan = result.data.data;
  const byKeyword = new Map(scan.keywords.map((trend) => [trend.candidate.keyword, trend]));
  const trending = scan.keywords.filter((trend) => trend.heat !== "quiet").length;

  return (
    <section aria-labelledby="trend-ideas" className="flex flex-col gap-4">
      <div className="flex flex-col gap-1">
        <h2 id="trend-ideas" className="text-title-3-medium text-foreground">
          Post on what’s trending
        </h2>
        {scan.keywords.length ? (
          <p className="text-body-regular text-muted-foreground">
            {plural(scan.keywords.length, "keyword")} from your topics checked on YouTube over the last {scan.window_days} days, {trending} of them moving. Checked{" "}
            {formatDateTime(scan.as_of)}.
          </p>
        ) : null}
      </div>
      {scan.messages.map((message) => (
        <p key={message} className="text-body-regular text-muted-foreground" role="status">
          {message}
        </p>
      ))}
      {scan.keywords.length && !scan.ideas.length ? (
        <EmptyState title="No trend ideas yet" body="None of your keywords had enough new videos to build an idea on. The keywords we checked are below." />
      ) : null}
      {scan.ideas.map((idea, index) => (
        <TrendIdeaCard key={idea.id} idea={idea} trend={byKeyword.get(idea.keyword)} defaultExpanded={index === 0} />
      ))}
      {scan.keywords.length ? <KeywordTrends trends={scan.keywords} /> : null}
      {scan.keywords.length ? (
        <p className="text-caption-1-regular text-subtle-foreground">
          Views a day and “Trending” are calculated by GrowthHacker from public YouTube counts; keywords come from your profile, YouTube search suggestions and this week’s news.
        </p>
      ) : null}
      {scan.ideas.length ? <CalcNote /> : null}
    </section>
  );
}
