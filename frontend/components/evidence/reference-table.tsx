import { MethodBadge } from "@/components/ui/chips";
import { Thumbnail } from "@/components/ui/thumbnail";
import type { ReferenceVideo } from "@/lib/api/types";
import { formatCompact, formatDate, formatExact, formatRatio } from "@/lib/format";

const ROLE_LABELS: Record<NonNullable<ReferenceVideo["channel_role"]>, string> = {
  own: "your channel",
  competitor: "competitor",
  niche: "niche channel",
  benchmark: "benchmark",
  creator: "creator",
};

// one row per reference video with the arithmetic spelled out; the ratio itself comes from the server
export function ReferenceTable({ videos }: { videos: ReferenceVideo[] }) {
  return (
    <ul className="flex flex-col divide-y divide-border">
      {videos.map((video) => {
        const views = video.views_at_age?.views ?? video.views_day7;
        const age = video.views_at_age?.age_days ?? 7;
        const baseline = video.channel_baseline_views_at_age;
        return (
          <li key={video.video_id} className="grid grid-cols-[120px_1fr] gap-3 py-3 first:pt-0 last:pb-0">
            <Thumbnail videoId={video.video_id} title={video.title} url={video.thumbnail_url} channelTitle={video.channel} badge={formatRatio(video.outlier_ratio)} />
            <div className="flex min-w-0 flex-col gap-1">
              <p className="line-clamp-2 text-body-medium text-foreground" dir="auto" title={video.title}>
                {video.title}
              </p>
              <p className="text-caption-1-regular text-muted-foreground">
                {video.channel}
                {video.channel_role ? ` · ${ROLE_LABELS[video.channel_role]}` : ""} · published {formatDate(video.published_at)}
              </p>
              <p className="flex flex-wrap items-center gap-1.5 text-caption-1-regular text-foreground tabular-nums">
                <span title={views === null ? undefined : formatExact(views)}>
                  {formatCompact(views)} {video.views_at_age?.label ?? `views at day ${age}`}
                </span>
                {video.views_at_age ? <MethodBadge method={video.views_at_age.method} /> : null}
              </p>
              <p className="text-caption-1-regular text-muted-foreground tabular-nums">
                {baseline
                  ? `${formatExact(views ?? 0)} vs channel normal ${formatExact(baseline)} at day ${age} → ${formatRatio(video.outlier_ratio)}`
                  : video.outlier_ratio === null
                    ? "No baseline yet for this channel."
                    : `${formatRatio(video.outlier_ratio)} the channel's normal at day ${age}`}
              </p>
            </div>
          </li>
        );
      })}
    </ul>
  );
}
