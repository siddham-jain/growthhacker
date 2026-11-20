"use client";

import { RiSearchEyeLine } from "@remixicon/react";
import { Button } from "@/components/ui/button";
import { MethodBadge } from "@/components/ui/chips";
import { Thumbnail } from "@/components/ui/thumbnail";
import type { VideoMetricsView } from "@/lib/api/types";
import { formatCompact, formatDate, formatExact, formatRatio } from "@/lib/format";

// raw numbers per video in a fixed order; there is no rank column and no score
export function VideoList({ videos, onInspect }: { videos: VideoMetricsView[]; onInspect: (video: VideoMetricsView) => void }) {
  return (
    <ul className="grid gap-3 md:grid-cols-2">
      {videos.map((video) => (
        <li key={video.video_id} className="grid grid-cols-[128px_1fr] gap-3 rounded-card bg-card p-3 shadow-card">
          <Thumbnail videoId={video.video_id} title={video.title} channelTitle={video.channel_title} badge={formatRatio(video.outlier_ratio)} />
          <div className="flex min-w-0 flex-col gap-1">
            <p className="line-clamp-2 text-body-medium text-foreground" dir="auto" title={video.title}>
              {video.title}
            </p>
            <p className="text-caption-1-regular text-muted-foreground">
              {video.channel_title} · {formatDate(video.published_at)}
            </p>
            <p className="flex flex-wrap items-center gap-1.5 text-caption-1-regular text-foreground tabular-nums">
              <span title={video.views_at_age.views === null ? undefined : formatExact(video.views_at_age.views)}>
                {formatCompact(video.views_at_age.views)} {video.views_at_age.label}
              </span>
              <MethodBadge method={video.views_at_age.method} />
            </p>
            <Button variant="ghost" size="xs" leadingIcon={RiSearchEyeLine} className="mt-auto w-fit" onClick={() => onInspect(video)} aria-label={`Evidence for ${video.title}`}>
              Evidence
            </Button>
          </div>
        </li>
      ))}
    </ul>
  );
}
