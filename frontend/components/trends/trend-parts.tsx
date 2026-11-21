import { Chip } from "@/components/ui/primitives";
import { Thumbnail } from "@/components/ui/thumbnail";
import type { KeywordSource, KeywordTrend, TrendVideo } from "@/lib/api/types";
import { formatCompact, formatDate } from "@/lib/format";

const HEAT: Record<KeywordTrend["heat"], { label: string; color: "rose" | "orange" | "soft" }> = {
  hot: { label: "Trending", color: "rose" },
  warm: { label: "Rising", color: "orange" },
  quiet: { label: "Quiet", color: "soft" },
};

const SOURCE_LABELS: Record<KeywordSource, { label: string; hint: string }> = {
  autocomplete: { label: "YouTube search suggestion", hint: "YouTube suggests this while people type your topic" },
  web: { label: "In the news", hint: "Found in this week's news for your topics" },
  profile: { label: "Your topic", hint: "One of the keywords or offerings in your profile" },
};

export function HeatChip({ heat }: { heat: KeywordTrend["heat"] }) {
  return (
    <Chip variant="caption" color={HEAT[heat].color}>
      {HEAT[heat].label}
    </Chip>
  );
}

export function SourceChips({ sources }: { sources: KeywordSource[] }) {
  return (
    <>
      {sources.map((source) => (
        <Chip key={source} variant="caption" color="soft" title={SOURCE_LABELS[source].hint}>
          {SOURCE_LABELS[source].label}
        </Chip>
      ))}
    </>
  );
}

export function TrendVideoGrid({ videos }: { videos: TrendVideo[] }) {
  return (
    <ul className="grid grid-cols-2 gap-3 sm:grid-cols-3">
      {videos.map((video) => (
        <li key={video.video_id} className="flex min-w-0 flex-col gap-1.5">
          <Thumbnail videoId={video.video_id} title={video.title} url={video.thumbnail_url} channelTitle={video.channel_title} badge={`${formatCompact(Math.round(video.views_per_day))}/day`} />
          <p className="line-clamp-2 text-caption-1-medium text-foreground" dir="auto" title={video.title}>
            {video.title}
          </p>
          <p className="text-caption-1-regular text-muted-foreground">
            {video.channel_title}
            {video.is_competitor ? " · competitor" : ""} · {formatDate(video.published_at)} · {formatCompact(video.views)} views
          </p>
        </li>
      ))}
    </ul>
  );
}
