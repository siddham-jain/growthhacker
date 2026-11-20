import type { CompetitorChannelView, CompetitorView } from "@/lib/api/types";

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
