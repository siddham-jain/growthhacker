"use client";

import { RiExternalLinkLine } from "@remixicon/react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { ErrorState, LoadingBlock } from "@/components/states/states";
import { LensChip, MethodBadge, OriginChip, TypeChip } from "@/components/ui/chips";
import { ConfidenceMeter } from "@/components/ui/confidence-meter";
import { Chip, Drawer, DrawerBody, DrawerHeader } from "@/components/ui/primitives";
import { Thumbnail } from "@/components/ui/thumbnail";
import { api } from "@/lib/api";
import type { RecommendationsResult } from "@/lib/api/contract";
import { clientKey } from "@/lib/api/queries";
import type { RecommendationOut, VideoMetricsView } from "@/lib/api/types";
import { formatDate, formatExact, formatRatio, plural } from "@/lib/format";
import { METHOD_LABELS, METRIC_DISCLOSURE } from "@/lib/labels";
import { useEvidenceParam } from "@/lib/url-state";
import { ReferenceTable } from "./reference-table";

type Origin = "YouTube public" | "YouTube Analytics (owner)" | "Website" | "News";

function Block({ title, origin, children }: { title: string; origin?: Origin; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-2.5 border-t border-border pt-4 first:border-t-0 first:pt-0">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-eyebrow text-muted-foreground">{title}</h3>
        {origin ? <OriginChip origin={origin} /> : null}
      </div>
      {children}
    </section>
  );
}

function Disclosure() {
  return <p className="rounded-control bg-muted px-3 py-2 text-caption-1-regular text-muted-foreground">{METRIC_DISCLOSURE} Video data from YouTube.</p>;
}

// steps come from the card's own typed fields, never from generated prose
function buildSteps(rec: RecommendationOut): string[] {
  const channels = new Set(rec.reference_videos.map((video) => video.channel_id)).size;
  const steps = [
    `Compared every video in the last ${rec.window_days} days to its own channel's normal at the same age`,
    `Found ${plural(rec.confidence.outliers, "outlier")} across ${plural(rec.confidence.channels, "channel")} that share this idea`,
    `Kept ${plural(rec.reference_videos.length, "reference video")} from ${plural(channels, "channel")} as the evidence below`,
  ];
  if (rec.buyer_question) steps.push(`Matched it to your buyer question “${rec.buyer_question}”`);
  steps.push(rec.rendered_by === "template" ? "Wrote this card from a fixed template using the evidence above" : "Wrote this card from the evidence above, then checked every number against it");
  return steps;
}

function findCached(queryClient: ReturnType<typeof useQueryClient>, clientId: string, id: string): RecommendationOut | undefined {
  for (const [, result] of queryClient.getQueriesData<RecommendationsResult>({ queryKey: [...clientKey(clientId), "recommendations"] })) {
    const found = result?.state === "ready" ? result.envelope.data.items.find((item) => item.id === id) : undefined;
    if (found) return found;
  }
  return undefined;
}

export function RecommendationEvidence({ rec }: { rec: RecommendationOut }) {
  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-center gap-2">
        <TypeChip type={rec.type} />
        {rec.lens.map((lens) => (
          <LensChip key={lens} lens={lens} />
        ))}
        {rec.rendered_by === "template" ? (
          <Chip variant="caption" color="soft" title="The wording came from a fixed template instead of being written for this card">
            Plain wording
          </Chip>
        ) : null}
      </div>
      {rec.buyer_question ? <p className="text-body-regular text-lens-conversion">“{rec.buyer_question}”</p> : null}

      <Block title={`Reference videos · ${rec.reference_videos.length}`} origin="YouTube public">
        {rec.reference_videos.length ? (
          <ReferenceTable videos={rec.reference_videos} />
        ) : (
          <p className="text-body-regular text-muted-foreground">No YouTube videos on this yet. Being early is the opportunity; there&apos;s no evidence it will perform.</p>
        )}
      </Block>

      <Block title="Why it works">
        <ul className="list-disc space-y-1.5 pl-5 text-body-regular text-foreground">
          {rec.why_it_works.map((reason) => (
            <li key={reason}>{reason}</li>
          ))}
        </ul>
      </Block>

      <Block title="Fit to you">
        <p className="text-body-regular text-foreground">{rec.fit_to_client}</p>
      </Block>

      <Block title="Expected range">
        <p className="text-body-regular text-foreground">
          {rec.expected_range_vs_baseline ?? "Not enough comparable videos for a range."}
          {rec.expected_range_vs_baseline && !rec.expected_range_vs_baseline.includes("indicative") ? " (indicative)" : ""}
        </p>
        <p className="text-caption-1-regular text-muted-foreground">A range of similar videos against their own channel&apos;s normal at the same age. It isn&apos;t a promise of views.</p>
      </Block>

      <Block title="Confidence">
        <ConfidenceMeter confidence={rec.confidence} />
        {rec.confidence.caps_applied?.length ? (
          <ul className="flex flex-col gap-1 text-caption-1-regular text-muted-foreground">
            {rec.confidence.caps_applied.map((cap) => (
              <li key={cap}>{cap}</li>
            ))}
          </ul>
        ) : (
          <p className="text-caption-1-regular text-muted-foreground">No caps applied: the evidence spans enough channels and videos.</p>
        )}
      </Block>

      <Block title="How this card was built">
        <ol className="flex flex-col gap-2">
          {buildSteps(rec).map((step, index) => (
            <li key={step} className="flex gap-2.5 text-body-regular text-foreground">
              <span aria-hidden className="grid size-5 shrink-0 place-items-center rounded-full bg-secondary text-caption-2-medium text-muted-foreground">
                {index + 1}
              </span>
              {step}
            </li>
          ))}
        </ol>
      </Block>
      <Disclosure />
    </div>
  );
}

export function RecommendationEvidenceDrawer({ clientId }: { clientId: string }) {
  const queryClient = useQueryClient();
  const { evidenceId, closeEvidence } = useEvidenceParam();
  const rec = useQuery({
    queryKey: ["recommendation", evidenceId],
    queryFn: () => api.recommendations.get(evidenceId ?? ""),
    enabled: Boolean(evidenceId),
    placeholderData: () => (evidenceId ? findCached(queryClient, clientId, evidenceId) : undefined),
  });
  return (
    <Drawer size="lg" isOpen={Boolean(evidenceId)} onOpenChange={(open) => (open ? null : closeEvidence())} aria-label="Evidence">
      <DrawerHeader title={rec.data?.summary ?? "Evidence"} description={rec.data ? `Window ${rec.data.window_days} days · generated ${formatDate(rec.data.generated_at)}` : undefined} />
      <DrawerBody>
        {rec.isPending ? <LoadingBlock rows={3} /> : null}
        {rec.error ? <ErrorState error={rec.error} onRetry={() => rec.refetch()} /> : null}
        {rec.data ? <RecommendationEvidence rec={rec.data} /> : null}
      </DrawerBody>
    </Drawer>
  );
}

export function VideoEvidence({ video }: { video: VideoMetricsView }) {
  const baseline = video.channel_baseline_views_at_age ?? null;
  const views = video.views_at_age.views;
  const method = METHOD_LABELS[video.views_at_age.method];
  return (
    <div className="flex flex-col gap-5">
      <Thumbnail videoId={video.video_id} title={video.title} channelTitle={video.channel_title} badge={formatRatio(video.outlier_ratio)} />
      <a href={`https://www.youtube.com/watch?v=${video.video_id}`} target="_blank" rel="noreferrer" className="inline-flex w-fit items-center gap-1 text-body-medium text-info">
        Open on YouTube <RiExternalLinkLine aria-hidden className="size-4" />
      </a>
      <Block title="The numbers" origin="YouTube public">
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-body-regular tabular-nums">
          <dt className="text-muted-foreground">Channel</dt>
          <dd className="text-foreground">{video.channel_title}</dd>
          <dt className="text-muted-foreground">Published</dt>
          <dd className="text-foreground">{formatDate(video.published_at)}</dd>
          <dt className="text-muted-foreground">Views at day {video.views_at_age.age_days}</dt>
          <dd className="flex flex-wrap items-center gap-1.5 text-foreground">
            {views === null ? "–" : formatExact(views)} <MethodBadge method={video.views_at_age.method} />
          </dd>
          <dt className="text-muted-foreground">Channel normal</dt>
          <dd className="text-foreground">{baseline ? `${formatExact(baseline)} at day ${video.views_at_age.age_days}` : "No baseline yet"}</dd>
          <dt className="text-muted-foreground">Outlier ratio</dt>
          <dd className="text-foreground">
            {formatRatio(video.outlier_ratio)}
            {video.is_outlier ? <span className="text-muted-foreground"> · counts as an outlier (3× or more)</span> : null}
          </dd>
        </dl>
        <p className="text-caption-1-regular text-muted-foreground">
          {method.label}: {method.description.toLowerCase()}.{method.proxy ? " Treat the ratio as a rough guide." : ""}
        </p>
      </Block>
      <p className="text-caption-1-regular text-muted-foreground">Titles are shown as evidence only; we never suggest copying them.</p>
      <Disclosure />
    </div>
  );
}

export function VideoEvidenceDrawer({ video, onClose }: { video: VideoMetricsView | null; onClose: () => void }) {
  return (
    <Drawer size="lg" isOpen={Boolean(video)} onOpenChange={(open) => (open ? null : onClose())} aria-label="Video evidence">
      <DrawerHeader title={video?.title ?? "Video"} description={video ? `${video.channel_title} · ${video.views_at_age.label}` : undefined} />
      <DrawerBody>{video ? <VideoEvidence video={video} /> : null}</DrawerBody>
    </Drawer>
  );
}
