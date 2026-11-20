import { Thumbnail } from "@/components/ui/thumbnail";
import type { ReferenceVideo } from "@/lib/api/types";
import { formatCompact, formatDate, formatRatio } from "@/lib/format";

export function ReferenceStrip({ videos }: { videos: ReferenceVideo[] }) {
  return (
    <ul className="grid grid-cols-2 gap-3 sm:grid-cols-3">
      {videos.slice(0, 5).map((video) => (
        <li key={video.video_id} className="flex min-w-0 flex-col gap-1.5">
          <Thumbnail videoId={video.video_id} title={video.title} url={video.thumbnail_url} channelTitle={video.channel} badge={formatRatio(video.outlier_ratio)} />
          <p className="line-clamp-2 text-caption-1-medium text-foreground" dir="auto" title={video.title}>
            {video.title}
          </p>
          <p className="text-caption-1-regular text-muted-foreground">
            {video.channel} · {formatDate(video.published_at)} · {formatCompact(video.views_at_age?.views ?? video.views_day7)} {video.views_at_age?.label ?? "views at day 7"}
          </p>
        </li>
      ))}
    </ul>
  );
}
