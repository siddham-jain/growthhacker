"use client";

import { useQuery } from "@tanstack/react-query";
import { RiArrowLeftLine, RiArrowRightLine, RiExternalLinkLine } from "@remixicon/react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState, type ReactNode } from "react";
import { VideoList } from "@/components/cards/video-list";
import { CadenceChart, ViewsAtAgeChart } from "@/components/charts/lazy";
import { VideoEvidenceDrawer } from "@/components/evidence/lazy-drawers";
import { FreshnessLine } from "@/components/shell/freshness-line";
import { PageHeader } from "@/components/shell/page-header";
import { EmptyState, ErrorState, InfoNote, LoadingBlock } from "@/components/states/states";
import { MethodBadge, OriginChip } from "@/components/ui/chips";
import { Chip, Tab, TabList, TabPanel, Tabs } from "@/components/ui/primitives";
import { competitorDetailQuery } from "@/lib/api/queries";
import type { ChannelScorecard, CompetitorDetail, VideoMetricsView } from "@/lib/api/types";
import { formatCompact, formatRatio, plural } from "@/lib/format";
import { METRIC_DISCLOSURE, RELATION_LABELS } from "@/lib/labels";
import { useTabParam, useViewParams } from "@/lib/url-state";
import { cx } from "@/lib/cx";
import { useClientId } from "@/lib/use-client-id";

const TABS = ["overview", "working", "outliers", "gap"] as const;

function formatMix(mix: ChannelScorecard["segment_mix"]): string {
  const parts = [
    mix.long_form ? `${Math.round(mix.long_form * 100)}% long-form` : null,
    mix.short_form ? `${Math.round(mix.short_form * 100)}% Shorts` : null,
    mix.live ? `${Math.round(mix.live * 100)}% live` : null,
  ];
  return parts.filter(Boolean).join(" · ") || "–";
}

interface SheetRow {
  label: string;
  value: (card: ChannelScorecard) => ReactNode;
}

function sheetRows(windowDays: number): SheetRow[] {
  return [
    { label: "Uploads per week", value: (card) => card.uploads_per_week.toFixed(1) },
    { label: `Videos in the last ${windowDays} days`, value: (card) => card.videos_in_window },
    { label: "Days since last upload", value: (card) => card.days_since_last_upload },
    { label: "Longest gap between uploads", value: (card) => (card.longest_gap_days === null ? "–" : `${card.longest_gap_days} days`) },
    { label: "Format mix", value: (card) => formatMix(card.segment_mix) },
    {
      label: "Median views at day 7",
      value: (card) =>
        card.median_views_at_age ? (
          <span className="inline-flex flex-wrap items-center gap-1.5">
            {formatCompact(card.median_views_at_age.views)} <MethodBadge method={card.median_views_at_age.method} />
          </span>
        ) : (
          "–"
        ),
    },
    { label: "Median vs their own normal", value: (card) => formatRatio(card.median_ratio) },
    {
      label: "Outliers (3× their normal or more)",
      value: (card) => `${card.outlier_hit_rate.outliers} of ${card.outlier_hit_rate.videos} (likely ${card.outlier_hit_rate.likely_low}–${card.outlier_hit_rate.likely_high} of ${card.outlier_hit_rate.videos})`,
    },
    { label: "Subscribers", value: (card) => (card.subscriber_hidden ? "Hidden" : card.subscriber_band) },
  ];
}

// an evidence sheet, not a scorecard: raw numbers side by side, no composite score and no rank
function EvidenceSheet({ detail, windowDays }: { detail: CompetitorDetail; windowDays: number }) {
  const them = detail.scorecard;
  return (
    <section aria-labelledby="sheet-heading" className="rounded-card bg-card shadow-card">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-4 py-3">
        <div>
          <h2 id="sheet-heading" className="text-body-medium text-foreground">
            Evidence sheet
          </h2>
          <p className="text-caption-1-regular text-muted-foreground">Raw numbers side by side. We don&apos;t score or rank channels.</p>
        </div>
        <OriginChip origin="YouTube public" />
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[520px] text-left text-body-regular tabular-nums">
          <thead>
            <tr className="text-caption-1-medium text-muted-foreground">
              <th scope="col" className="px-4 py-2 font-medium">
                <span className="sr-only">Measure</span>
              </th>
              <th scope="col" className="px-4 py-2 font-medium">
                {them.channel.title}
              </th>
              <th scope="col" className="px-4 py-2 font-medium">
                You
              </th>
            </tr>
          </thead>
          <tbody>
            {sheetRows(windowDays).map((row) => (
              <tr key={row.label} className="border-t border-border">
                <th scope="row" className="px-4 py-2.5 font-normal text-muted-foreground">
                  {row.label}
                </th>
                <td className="px-4 py-2.5 text-foreground">{row.value(them)}</td>
                <td className="px-4 py-2.5 text-foreground">{detail.you ? row.value(detail.you) : "No channel yet"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="border-t border-border px-4 py-2.5 text-caption-1-regular text-subtle-foreground">{METRIC_DISCLOSURE}</p>
    </section>
  );
}

function Traits({ detail }: { detail: CompetitorDetail }) {
  const supported = detail.trait_lift.filter((trait) => trait.status === "supported");
  const exploratory = detail.trait_lift.filter((trait) => trait.status !== "supported");
  const row = (trait: CompetitorDetail["trait_lift"][number]) => (
    <li key={trait.trait_key} className="relative overflow-hidden rounded-control px-3 py-2.5">
      <span aria-hidden className="absolute inset-y-0 left-0 bg-[color-mix(in_oklab,var(--chart-1)_12%,transparent)]" style={{ width: `${(trait.outliers_with / Math.max(1, trait.outliers_total)) * 100}%` }} />
      <span className="relative flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5">
        <span className="text-body-medium text-foreground">{trait.label}</span>
        <span className="text-caption-1-regular text-muted-foreground tabular-nums">
          {trait.outliers_with} of {trait.outliers_total} outliers vs {trait.n_with} of {trait.n_total} videos · {formatRatio(trait.lift)} more common among outliers
        </span>
      </span>
    </li>
  );
  if (!detail.trait_lift.length) return <EmptyState title="No trait stands out yet" body="We need at least 3 outliers sharing a trait before calling it a pattern." />;
  return (
    <div className="flex flex-col gap-4">
      <section className="rounded-card bg-card p-3 shadow-card">
        <h2 className="px-1 pb-2 text-eyebrow text-muted-foreground">Traits that win for them</h2>
        <ul className="flex flex-col gap-1">{supported.map(row)}</ul>
      </section>
      {exploratory.length ? (
        <details className="rounded-card bg-card p-3 shadow-card">
          <summary className="cursor-pointer px-1 text-body-medium text-foreground">Exploratory · {exploratory.length}</summary>
          <p className="px-1 pt-1 text-caption-1-regular text-muted-foreground">Too few videos to call these patterns. Worth watching, not acting on.</p>
          <ul className="mt-2 flex flex-col gap-1">{exploratory.map(row)}</ul>
        </details>
      ) : null}
    </div>
  );
}

function Gaps({ detail, clientId }: { detail: CompetitorDetail; clientId: string }) {
  if (!detail.topic_coverage.length) return <EmptyState title="No overlap yet" body="They haven't posted on any of your buyer questions in this window." />;
  return (
    <ul className="flex flex-col divide-y divide-border rounded-card bg-card shadow-card">
      {detail.topic_coverage.map((row) => {
        const gap = row.client_videos === 0 && row.videos > 0;
        return (
          <li key={row.topic_ref_id} className="flex flex-wrap items-center gap-x-3 gap-y-1.5 px-4 py-3">
            <span className="min-w-0 flex-1 basis-64 text-body-regular text-lens-conversion">“{row.topic}”</span>
            <span className="text-caption-1-regular text-muted-foreground tabular-nums">
              they: {plural(row.videos, "video")}, {plural(row.outliers, "outlier")} · you: {plural(row.client_videos, "video")}
            </span>
            {gap ? (
              <Chip variant="caption" color="orange">
                You haven&apos;t covered this
              </Chip>
            ) : null}
            {gap && row.recommendation_id ? (
              <Link href={`/c/${clientId}/ideas?card=${row.recommendation_id}`} className="inline-flex items-center gap-1 text-caption-1-medium text-info">
                See the idea <RiArrowRightLine aria-hidden className="size-3.5" />
              </Link>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}

export default function CompetitorDetailPage() {
  const clientId = useClientId();
  const { channelId } = useParams<{ channelId: string }>();
  const { windowDays } = useViewParams();
  const [tab, setTab] = useTabParam(TABS, "overview");
  const [inspected, setInspected] = useState<VideoMetricsView | null>(null);
  const detail = useQuery(competitorDetailQuery(clientId, channelId, windowDays));
  const data = detail.data?.data;
  const card = data?.scorecard;
  const gaps = data?.topic_coverage.filter((row) => row.client_videos === 0 && row.videos > 0).length ?? 0;

  return (
    <>
      <Link href={`/c/${clientId}/competitors?w=${windowDays}`} className="mb-3 inline-flex items-center gap-1 text-body-medium text-muted-foreground hover:text-foreground">
        <RiArrowLeftLine aria-hidden className="size-4" /> Competitors
      </Link>
      <PageHeader
        title={card?.channel.title ?? "Competitor"}
        subtitle={card ? `${[card.channel.handle, card.relation ? `${RELATION_LABELS[card.relation]} competitor` : null].filter(Boolean).join(" · ")} · what they post, what wins for them and where they answer your buyers' questions.` : "Loading…"}
        controls="window"
        actions={
          card ? (
            <a href={`https://www.youtube.com/${card.channel.handle}`} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-body-medium text-info">
              Open on YouTube <RiExternalLinkLine aria-hidden className="size-4" />
            </a>
          ) : null
        }
      />
      <FreshnessLine envelope={detail.data} />
      {detail.isPending ? <LoadingBlock /> : null}
      {detail.error ? <ErrorState error={detail.error} onRetry={() => detail.refetch()} /> : null}
      {data && card ? (
        <Tabs selectedKey={tab} onSelectionChange={(key) => setTab(key as (typeof TABS)[number])}>
          <TabList aria-label="Competitor sections">
            <Tab id="overview">Overview</Tab>
            <Tab id="working" count={data.trait_lift.filter((trait) => trait.status === "supported").length}>
              What&apos;s working
            </Tab>
            <Tab id="outliers" count={data.top_outliers.length}>
              Their outliers
            </Tab>
            <Tab id="gap" count={gaps}>
              Gaps vs your questions
            </Tab>
          </TabList>
          <TabPanel id="overview">
            <div className="flex flex-col gap-4">
              {card.days_since_last_upload > 45 ? <InfoNote tone="warning">No uploads in {card.days_since_last_upload} days, so they&apos;re left out of cadence comparisons.</InfoNote> : null}
              <EvidenceSheet detail={data} windowDays={windowDays} />
              <div className={cx("grid items-start gap-4", data.curves.length && "xl:grid-cols-2")}>
                <CadenceChart weeks={data.cadence_series} scorecard={card} />
                <ViewsAtAgeChart
                  curves={data.curves}
                  title="How their outliers picked up views"
                  baseline={card.median_views_at_age}
                  baselineLabel={`their normal at day 7, from ${card.videos_in_window} recent videos`}
                />
              </div>
              {data.collaborations.length ? (
                <section className="rounded-card bg-card p-4 shadow-card">
                  <h2 className="mb-2 text-eyebrow text-muted-foreground">Worked with</h2>
                  <ul className="flex flex-col gap-1 text-body-regular text-foreground">
                    {data.collaborations.map((collab) => (
                      <li key={collab.video_id}>
                        {collab.handle_or_name} <span className="text-muted-foreground">· {collab.kind === "mention" ? "Mention" : "Disclosed paid promotion"}</span>
                      </li>
                    ))}
                  </ul>
                </section>
              ) : null}
            </div>
          </TabPanel>
          <TabPanel id="working">
            <Traits detail={data} />
          </TabPanel>
          <TabPanel id="outliers">
            <p className="mb-3 text-caption-1-regular text-muted-foreground">Their videos that ran 3× their own normal or more. Titles are shown as evidence only; we never suggest copying them.</p>
            {data.top_outliers.length ? (
              <VideoList videos={data.top_outliers} onInspect={setInspected} />
            ) : (
              <EmptyState title="No outliers in this window" body={`None of their videos in the last ${windowDays} days ran 3× their normal. Try a wider window.`} />
            )}
          </TabPanel>
          <TabPanel id="gap">
            <Gaps detail={data} clientId={clientId} />
          </TabPanel>
        </Tabs>
      ) : null}
      <VideoEvidenceDrawer video={inspected} onClose={() => setInspected(null)} />
    </>
  );
}
