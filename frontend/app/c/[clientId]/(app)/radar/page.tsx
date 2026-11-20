"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { NewsCard } from "@/components/cards/news-card";
import { ReferenceStrip } from "@/components/cards/reference-strip";
import { FreshnessLine } from "@/components/shell/freshness-line";
import { PageHeader } from "@/components/shell/page-header";
import { EmptyState, LoadingBlock } from "@/components/states/states";
import { Tab, TabList, TabPanel, Tabs } from "@/components/ui/primitives";
import { newsQuery, outliersQuery, recommendationsQuery, trendingQuery } from "@/lib/api/queries";
import type { ReferenceVideo, VideoMetricsView } from "@/lib/api/types";
import { formatCompact } from "@/lib/format";
import { useTabParam, useViewParams } from "@/lib/url-state";
import { useClientId } from "@/lib/use-client-id";

const TABS = ["news", "early", "trending", "outliers", "unanswered"] as const;
const MIN_REFERENCES = 3;

function asReference(video: VideoMetricsView): ReferenceVideo {
  return {
    video_id: video.video_id,
    url: `https://www.youtube.com/watch?v=${video.video_id}`,
    title: video.title,
    channel: video.channel_title,
    channel_id: video.channel_id,
    published_at: video.published_at,
    views_day7: video.views_at_age.views,
    views_at_age: video.views_at_age,
    outlier_ratio: video.outlier_ratio,
  };
}

export default function RadarPage() {
  const clientId = useClientId();
  const { windowDays } = useViewParams();
  const [tab, setTab] = useTabParam(TABS, "news");
  const [now] = useState(() => Date.now());
  const news = useQuery(newsQuery(clientId));
  const trending = useQuery(trendingQuery(clientId, windowDays));
  const outliers = useQuery(outliersQuery(clientId, windowDays));
  const recommendations = useQuery(recommendationsQuery(clientId, { window_days: windowDays }));

  const live = (news.data?.data.items ?? []).filter((item) => item.status === "new" && Date.parse(item.expires_at) > now);
  const opportunities = live.filter((item) => item.reference_count >= MIN_REFERENCES);
  const earlySignals = live.filter((item) => item.reference_count < MIN_REFERENCES);
  const whiteSpace = recommendations.data?.state === "ready" ? (recommendations.data.envelope.white_space ?? []) : [];

  return (
    <>
      <PageHeader title="Niche radar" subtitle="What's moving in your niche right now: news worth reacting to, early signals, trending videos and open questions." />
      <FreshnessLine envelope={news.data} />
      <Tabs selectedKey={tab} onSelectionChange={(key) => setTab(key as (typeof TABS)[number])}>
        <TabList aria-label="Radar sections">
          <Tab id="news" count={opportunities.length}>News opportunities</Tab>
          <Tab id="early" count={earlySignals.length}>Early signals</Tab>
          <Tab id="trending">Trending</Tab>
          <Tab id="outliers">Outliers</Tab>
          <Tab id="unanswered" count={whiteSpace.length}>Unanswered</Tab>
        </TabList>
        <TabPanel id="news">
          <div className="flex flex-col gap-4">
            {news.isPending ? <LoadingBlock /> : null}
            {opportunities.map((item) => (
              <NewsCard key={item.id} opportunity={item} now={now} />
            ))}
            {news.data && !opportunities.length ? <EmptyState title="No news opportunities" body="Nothing in the news ties to your offerings with enough YouTube evidence right now." /> : null}
          </div>
        </TabPanel>
        <TabPanel id="early">
          <div className="flex flex-col gap-4">
            <p className="text-body-regular text-muted-foreground">
              Fresh stories with fewer than {MIN_REFERENCES} YouTube videos so far. They can&apos;t be recommendations yet, so we list them here, labelled honestly.
            </p>
            {earlySignals.map((item) => (
              <NewsCard key={item.id} opportunity={item} now={now} />
            ))}
          </div>
        </TabPanel>
        <TabPanel id="trending">
          <div className="flex flex-col gap-6">
            <ul className="grid gap-3 sm:grid-cols-2">
              {trending.data?.data.rising_topics.map((topic) => (
                <li key={topic.label} className="rounded-card bg-card px-4 py-3 shadow-card">
                  <p className="text-body-medium text-foreground">{topic.label}</p>
                  <p className="text-caption-1-regular text-muted-foreground">
                    {topic.uploads_prev} → {topic.uploads_now} uploads, {topic.outliers_prev} → {topic.outliers_now} outliers vs the window before
                  </p>
                </li>
              ))}
            </ul>
            <ul className="flex flex-col divide-y divide-border rounded-card bg-card shadow-card">
              {trending.data?.data.items.map((item) => (
                <li key={item.video_id} className="flex flex-wrap items-center gap-2 px-4 py-3">
                  <span className="min-w-0 flex-1 text-body-regular text-foreground" dir="auto">
                    {item.title}
                  </span>
                  <span className="text-caption-1-regular text-muted-foreground">
                    {item.channel_title} · {formatCompact(item.velocity_per_hour)} views/hour
                  </span>
                </li>
              ))}
            </ul>
          </div>
        </TabPanel>
        <TabPanel id="outliers">
          {outliers.isPending ? <LoadingBlock /> : <ReferenceStrip videos={(outliers.data?.data.items ?? []).map(asReference)} />}
        </TabPanel>
        <TabPanel id="unanswered">
          <ul className="flex flex-col gap-3">
            {whiteSpace.map((note) => (
              <li key={note.buyer_question} className="rounded-card bg-card px-4 py-3 shadow-card text-body-regular">
                No one has answered “{note.buyer_question}” on YouTube in the last {note.window_days} days{" "}
                <span className="text-muted-foreground">
                  (checked {note.videos_checked} videos across {note.channels_checked} channels).
                </span>
              </li>
            ))}
          </ul>
        </TabPanel>
      </Tabs>
    </>
  );
}
