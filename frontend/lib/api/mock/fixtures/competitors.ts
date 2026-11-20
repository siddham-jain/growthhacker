import type { ChannelRef, ChannelScorecard, CompetitorDetail, CompetitorView, VideoMetricsView } from "../../types";
import { channels, daysAgo, video } from "./base";
import { videos } from "./videos";

interface CompetitorSeed {
  channel: ChannelRef;
  role: CompetitorView["role"];
  relation: CompetitorView["relation"];
  businessRelevance: CompetitorView["business_relevance"];
  youtubeRelevance: number;
  evidenceSummary: string;
  uploads90d: number;
  lastUploadDaysAgo: number;
  medianRatio: number;
  outliers: number;
  videosInWindow: number;
  topOutliers: VideoMetricsView[];
}

const seeds: CompetitorSeed[] = [
  {
    channel: channels.fortis,
    role: "competitor_set",
    relation: "direct",
    businessRelevance: "high",
    youtubeRelevance: 0.81,
    evidenceSummary: "Same offerings in Delhi, Bengaluru, Mumbai · 4 sources",
    uploads90d: 38,
    lastUploadDaysAgo: 2,
    medianRatio: 1.3,
    outliers: 3,
    videosInWindow: 12,
    topOutliers: [videos.fortisBypassCost, videos.fortisSecondOpinion, video("fx-valve", channels.fortis, "Valve replacement without open surgery: TAVI explained", 25, 15_200, 3.2)],
  },
  {
    channel: channels.narayana,
    role: "competitor_set",
    relation: "direct",
    businessRelevance: "high",
    youtubeRelevance: 0.74,
    evidenceSummary: "Cardiac and transplant overlap in Bengaluru, Kolkata · 3 sources",
    uploads90d: 26,
    lastUploadDaysAgo: 4,
    medianRatio: 1.1,
    outliers: 2,
    videosInWindow: 9,
    topOutliers: [videos.narayanaHeartCost, video("nx-kids-heart", channels.narayana, "Heart surgery for babies: what parents should know", 22, 19_800, 3.3)],
  },
  {
    channel: channels.medanta,
    role: "competitor_set",
    relation: "direct",
    businessRelevance: "high",
    youtubeRelevance: 0.69,
    evidenceSummary: "Cardiac, oncology and transplant overlap in Delhi NCR · 3 sources",
    uploads90d: 44,
    lastUploadDaysAgo: 1,
    medianRatio: 1.4,
    outliers: 2,
    videosInWindow: 14,
    topOutliers: [videos.medantaAngioVsBypass, video("md-liver-diet", channels.medanta, "Fatty liver: the 4 food swaps doctors recommend", 13, 61_000, 3.1)],
  },
  {
    channel: channels.manipal,
    role: "competitor_set",
    relation: "direct",
    businessRelevance: "medium",
    youtubeRelevance: 0.58,
    evidenceSummary: "Orthopaedics and oncology overlap in Bengaluru · 2 sources",
    uploads90d: 17,
    lastUploadDaysAgo: 9,
    medianRatio: 0.9,
    outliers: 1,
    videosInWindow: 5,
    topOutliers: [videos.manipalKneeCost],
  },
  {
    channel: channels.max,
    role: "competitor_set",
    relation: "direct",
    businessRelevance: "high",
    youtubeRelevance: 0.66,
    evidenceSummary: "Transplant and cancer care overlap in Delhi NCR · 3 sources",
    uploads90d: 21,
    lastUploadDaysAgo: 6,
    medianRatio: 1.2,
    outliers: 1,
    videosInWindow: 7,
    topOutliers: [videos.maxTransplantJourney],
  },
  {
    channel: channels.practo,
    role: "niche_set",
    relation: "aggregator",
    businessRelevance: "medium",
    youtubeRelevance: 0.62,
    evidenceSummary: "Answers the same symptom and cost questions · 2 sources",
    uploads90d: 64,
    lastUploadDaysAgo: 1,
    medianRatio: 1.0,
    outliers: 2,
    videosInWindow: 22,
    topOutliers: [videos.practoSymptoms],
  },
  {
    channel: channels.doctorExplains,
    role: "benchmark_set",
    relation: "content",
    businessRelevance: "low",
    youtubeRelevance: 0.71,
    evidenceSummary: "Wins your buyer questions in Hindi · 1 source",
    uploads90d: 52,
    lastUploadDaysAgo: 3,
    medianRatio: 1.5,
    outliers: 4,
    videosInWindow: 18,
    topOutliers: [videos.doctorExplainsRobotic],
  },
];

export const competitorViews: CompetitorView[] = [
  ...seeds.map((seed, index) => ({
    id: `cmp_${seed.channel.handle.slice(1).toLowerCase()}`,
    status: "suggested" as const,
    channel: seed.channel,
    name: seed.channel.title,
    source: index % 2 === 0 ? ("web_search" as const) : ("profile" as const),
    score: seed.youtubeRelevance,
    evidence: [{ kind: "website", url: null, text: seed.evidenceSummary }],
    reject_reason: null,
    added_by: "system" as const,
    role: seed.role,
    relation: seed.relation,
    business_relevance: seed.businessRelevance,
    youtube_relevance: seed.youtubeRelevance,
    evidence_summary: seed.evidenceSummary,
    uploads_90d: seed.uploads90d,
    last_upload_at: daysAgo(seed.lastUploadDaysAgo),
    preview_video_ids: seed.topOutliers.map((item) => item.video_id),
  })),
  {
    id: "cmp_apollo247",
    status: "rejected",
    channel: channels.apollo247,
    name: "Apollo 24|7",
    source: "youtube_search",
    score: 0.2,
    evidence: [{ kind: "ownership", url: null, text: "Sister brand of the same group, not a competitor" }],
    reject_reason: "not_competitor",
    added_by: "system",
    role: "niche_set",
    relation: "direct",
    business_relevance: "low",
    youtube_relevance: 0.2,
    evidence_summary: "Sister brand of the same group",
    uploads_90d: 40,
    last_upload_at: daysAgo(1),
    preview_video_ids: [],
  },
];

export const scorecards: ChannelScorecard[] = seeds.map((seed) => ({
  channel: seed.channel,
  role: seed.role,
  relation: seed.relation,
  uploads_per_week: Math.round((seed.uploads90d / 13) * 10) / 10,
  median_ratio: seed.medianRatio,
  outliers_in_window: seed.outliers,
  videos_in_window: seed.videosInWindow,
  days_since_last_upload: seed.lastUploadDaysAgo,
  subscriber_hidden: false,
  top_outliers: seed.topOutliers,
}));

export function competitorDetail(channelId: string): CompetitorDetail | null {
  const scorecard = scorecards.find((item) => item.channel.channel_id === channelId);
  if (!scorecard) return null;
  return {
    scorecard,
    top_outliers: scorecard.top_outliers,
    trait_lift: [
      { trait_key: "title_question", label: "Question titles", outliers_with: 4, outliers_total: 5, n_with: 8, n_total: 30, lift: 2.1, status: "supported" },
      { trait_key: "title_cost", label: "Cost or price in the title", outliers_with: 3, outliers_total: 5, n_with: 6, n_total: 30, lift: 2.4, status: "supported" },
      { trait_key: "length_6_10", label: "6–10 minute videos", outliers_with: 3, outliers_total: 5, n_with: 12, n_total: 30, lift: 1.4, status: "exploratory" },
    ],
    topic_coverage: [
      { topic: "How much does bypass surgery cost in Hyderabad?", videos: 3, outliers: 1, client_videos: 0 },
      { topic: "Do I need angioplasty or bypass surgery?", videos: 2, outliers: 1, client_videos: 1 },
      { topic: "Should I get a second opinion before heart surgery?", videos: 2, outliers: 1, client_videos: 0 },
      { topic: "How long does recovery take after knee replacement?", videos: 1, outliers: 0, client_videos: 0 },
    ],
  };
}
