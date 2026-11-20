import type { CompetitorChannelView, CompetitorView } from "@/lib/api/types";
import { formatDate, plural } from "@/lib/format";

// the primary channel carries the youtube-side evidence; business relevance stays a separate object (04 §5)
export function primaryChannel(competitor: CompetitorView): CompetitorChannelView | undefined {
  return competitor.channels?.find((channel) => channel.is_primary) ?? competitor.channels?.[0];
}

export function previewVideoIds(competitor: CompetitorView): string[] {
  return primaryChannel(competitor)?.preview_video_ids ?? [];
}

export function isTracked(competitor: CompetitorView): boolean {
  return competitor.status === "confirmed" || competitor.status === "auto_confirmed" || competitor.status === "user_added";
}

export function isOut(competitor: CompetitorView): boolean {
  return competitor.status === "rejected" || competitor.status === "excluded";
}

// raw counts from the channel's uploads instead of composite relevance scores
export function channelFacts(channel: CompetitorChannelView): string[] {
  const { matching_uploads: matching, uploads_checked: checked } = channel.relevance;
  return [
    `${plural(channel.evidence.uploads_90d, "upload")} in the last 90 days`,
    matching != null && checked ? `${matching} of its last ${plural(checked, "upload")} match your topics` : "no recent uploads read",
    channel.evidence.last_upload_at ? `last upload ${formatDate(channel.evidence.last_upload_at)}` : "no uploads yet",
  ];
}
