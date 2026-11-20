import type { Confidence, NewsOpportunityView, RecommendationOut, RefreshSuggestionView, RunNotice } from "@/lib/api/types";

export const BRIEF_CONFIG = { maxItems: 5, minItems: 3, alsoWorthALook: 5, newsWindowHours: 48, maxVideoIdeas: 2 } as const;

export type BriefKind = "video_idea" | "pattern" | "news" | "refresh" | "setup";

export interface BriefItem {
  id: string;
  kind: BriefKind;
  headline: string;
  evidenceLine: string;
  href: string;
  actionLabel: string;
  expiresAt: string | null;
  confidence?: Confidence;
}

export interface BriefInput {
  clientId: string;
  recommendations: RecommendationOut[];
  news: NewsOpportunityView[];
  refresh: RefreshSuggestionView[];
  setupGap: string | null;
  thinNotice: RunNotice | null;
  now?: number;
  // the brief proper is capped at maxItems; a higher limit also fills "Also worth a look"
  limit?: number;
}

const PATTERN_TYPES = new Set(["title_pattern", "thumbnail_pattern", "format"]);

function fromRecommendation(clientId: string, rec: RecommendationOut): BriefItem {
  return {
    id: rec.id,
    kind: PATTERN_TYPES.has(rec.type) ? "pattern" : "video_idea",
    headline: rec.summary,
    evidenceLine: rec.why_it_works[0] ?? rec.fit_to_client,
    href: `/c/${clientId}/ideas?card=${rec.id}`,
    actionLabel: PATTERN_TYPES.has(rec.type) ? "Pattern" : "Video idea",
    expiresAt: null,
    confidence: rec.confidence,
  };
}

// selects and orders existing objects only (plan 13 §5.1); it never computes a metric
export function composeBrief(input: BriefInput): BriefItem[] {
  const now = input.now ?? Date.now();
  const limit = input.limit ?? BRIEF_CONFIG.maxItems;
  const items: BriefItem[] = [];
  const usedLeadVideos = new Set<string>();
  const ideas = input.recommendations.filter((rec) => rec.feedback?.status !== "dismissed" && rec.feedback?.status !== "done");

  const push = (item: BriefItem, leadVideoId?: string) => {
    if (items.length >= limit || items.some((existing) => existing.id === item.id)) return;
    if (leadVideoId && usedLeadVideos.has(leadVideoId)) return;
    if (leadVideoId) usedLeadVideos.add(leadVideoId);
    items.push(item);
  };
  const pushRec = (rec: RecommendationOut) => push(fromRecommendation(input.clientId, rec), rec.reference_videos[0]?.video_id);

  if (input.setupGap) {
    push({ id: "setup", kind: "setup", headline: input.setupGap, evidenceLine: "Finish setup so recommendations fit you.", href: `/c/${input.clientId}/settings?tab=competitors`, actionLabel: "Fix", expiresAt: null });
  }

  const urgent = input.news
    .filter((item) => item.status === "new" && item.reference_count >= 3)
    .filter((item) => item.urgency === "now" || Date.parse(item.expires_at) - now < BRIEF_CONFIG.newsWindowHours * 3_600_000)
    .sort((a, b) => b.relevance - a.relevance)[0];
  if (urgent) {
    push({ id: urgent.id, kind: "news", headline: urgent.summary ?? urgent.news.headline, evidenceLine: urgent.news.headline, href: `/c/${input.clientId}/radar?card=${urgent.id}`, actionLabel: "News", expiresAt: urgent.expires_at });
  }

  ideas.filter((rec) => rec.type === "video_idea").slice(0, BRIEF_CONFIG.maxVideoIdeas).forEach(pushRec);

  const refresh = input.refresh.find((item) => item.status === "open" && item.basis !== "rule_based");
  if (refresh) {
    push({ id: refresh.id, kind: "refresh", headline: `Add one line to "${refresh.video.title}"`, evidenceLine: refresh.warranted_because[0] ?? refresh.expected_effect, href: `/c/${input.clientId}/channel?tab=refresh`, actionLabel: "Refresh", expiresAt: null });
  }

  const pattern = ideas.find((rec) => PATTERN_TYPES.has(rec.type) && rec.confidence.label !== "Low");
  if (pattern) pushRec(pattern);

  ideas.filter((rec) => rec.type !== "creator").forEach(pushRec);

  // only a notice that names a wider window becomes an action; otherwise it stays a plain notice
  const widen = input.thinNotice?.suggest?.widen_window_to;
  if (items.length < BRIEF_CONFIG.minItems && input.thinNotice && widen) {
    push({ id: "thin", kind: "setup", headline: input.thinNotice.message, evidenceLine: "Widening the window gives us more videos to learn from.", href: `/c/${input.clientId}/today?w=${widen}`, actionLabel: `Widen to ${widen} days`, expiresAt: null });
  }
  return items;
}
