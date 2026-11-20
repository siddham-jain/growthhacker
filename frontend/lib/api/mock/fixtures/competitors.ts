import type { CadenceWeek, ChannelRef, ChannelScorecard, CompetitorDetail, CompetitorView, GapMatrixView, VideoMetricsView } from "../../types";
import { channels, curve, daysAgo, NOW, video } from "./base";
import { buyerQuestions } from "./topics";
import { videos } from "./videos";

interface CompetitorSeed {
  channel: ChannelRef;
  role: CompetitorView["role"];
  relation: CompetitorView["relation"];
  businessRelevance: CompetitorView["business"]["tier"];
  youtubeRelevance: number;
  evidenceSummary: string;
  uploads90d: number;
  lastUploadDaysAgo: number;
  medianRatio: number;
  outliers: number;
  videosInWindow: number;
  longestGapDays: number;
  medianViewsDay7: number;
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
    longestGapDays: 9,
    medianViewsDay7: 8_600,
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
    longestGapDays: 12,
    medianViewsDay7: 7_400,
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
    longestGapDays: 6,
    medianViewsDay7: 15_400,
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
    longestGapDays: 21,
    medianViewsDay7: 4_800,
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
    longestGapDays: 14,
    medianViewsDay7: 6_900,
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
    longestGapDays: 4,
    medianViewsDay7: 36_500,
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
    longestGapDays: 7,
    medianViewsDay7: 57_000,
    topOutliers: [videos.doctorExplainsRobotic],
  },
];

function view(seed: CompetitorSeed, id: string, status: CompetitorView["status"], source: string): CompetitorView {
  return {
    id,
    status,
    name: seed.channel.title,
    channel: seed.channel,
    channels: [
      {
        channel: seed.channel,
        is_primary: true,
        tracked: status !== "rejected",
        flags: [],
        preview_video_ids: seed.topOutliers.map((item) => item.video_id),
        evidence: {
          country: "IN",
          fetched_at: daysAgo(0),
          last_upload_at: daysAgo(seed.lastUploadDaysAgo),
          published_at: daysAgo(3_000),
          recent_upload_ids: seed.topOutliers.map((item) => item.video_id),
          subscriber_count: seed.channel.subscriber_count,
          uploads_90d: seed.uploads90d,
          video_count: seed.uploads90d * 12,
        },
        ownership: { label: "verified", method: "link", score: 0.92, signals: [{ name: "website_link", detail: "Channel links to the brand's website", weight: 1 }] },
        relevance: { activity: Math.min(1, seed.uploads90d / 60), content_overlap: seed.youtubeRelevance, matched_topic_refs: [], size_band: "peer", subscriber_band: "100k_1m" },
      },
    ],
    added_by: "system",
    decided_by: null,
    business: { tier: seed.businessRelevance, geo_basis: "cities", geo_overlap: 0.6, offering_overlap: 0.7, source_agreement: 0.8, matched_offerings: [] },
    confidence: seed.businessRelevance,
    evidence: [{ kind: "website", url: null, text: seed.evidenceSummary }],
    evidence_summary: seed.evidenceSummary,
    flags: [],
    official_domain: null,
    reject_reason: null,
    relation: seed.relation,
    role: seed.role,
    sources: [source],
  };
}

export const competitorViews: CompetitorView[] = [
  ...seeds.map((seed, index) => view(seed, `cmp_${(seed.channel.handle ?? "").slice(1).toLowerCase()}`, "suggested", index % 2 === 0 ? "web_search" : "profile")),
  {
    ...view(
      { channel: channels.apollo247, role: "niche_set", relation: "direct", businessRelevance: "low", youtubeRelevance: 0.2, evidenceSummary: "Sister brand of the same group", uploads90d: 40, lastUploadDaysAgo: 1, medianRatio: 1, outliers: 0, videosInWindow: 0, longestGapDays: 3, medianViewsDay7: 0, topOutliers: [] },
      "cmp_apollo247",
      "rejected",
      "youtube_search",
    ),
    reject_reason: "not_competitor",
  },
];

function wilson(outliers: number, videos: number): [number, number] {
  // fixture stand-in for the server's wilson interval, expressed as counts
  const low = Math.max(0, outliers - Math.ceil(Math.sqrt(videos) / 2));
  return [low, Math.min(videos, outliers + Math.ceil(Math.sqrt(videos)))];
}

function sheet(seed: Omit<CompetitorSeed, "businessRelevance" | "youtubeRelevance" | "evidenceSummary">): ChannelScorecard {
  const [likelyLow, likelyHigh] = wilson(seed.outliers, seed.videosInWindow);
  const shorts = seed.topOutliers.some((item) => item.segment === "short_form") ? 0.35 : 0.15;
  return {
    channel: seed.channel,
    role: seed.role,
    relation: seed.relation,
    uploads_per_week: Math.round((seed.uploads90d / 13) * 10) / 10,
    median_ratio: seed.medianRatio,
    outliers_in_window: seed.outliers,
    videos_in_window: seed.videosInWindow,
    days_since_last_upload: seed.lastUploadDaysAgo,
    longest_gap_days: seed.longestGapDays,
    subscriber_hidden: false,
    subscriber_band: (seed.channel.subscriber_count ?? 0) >= 1_000_000 ? "1M+ subscribers" : (seed.channel.subscriber_count ?? 0) >= 100_000 ? "100K–1M subscribers" : "10K–100K subscribers",
    segment_mix: { long_form: Math.round((1 - shorts) * 100) / 100, short_form: shorts },
    median_views_at_age: { age_days: 7, views: seed.medianViewsDay7, method: "observed" },
    outlier_hit_rate: { outliers: seed.outliers, videos: seed.videosInWindow, likely_low: likelyLow, likely_high: likelyHigh },
    top_outliers: seed.topOutliers,
  };
}

export const scorecards: ChannelScorecard[] = seeds.map(sheet);

export const ownScorecard: ChannelScorecard = sheet({
  channel: channels.apollo,
  role: "competitor_set",
  relation: "direct",
  uploads90d: 24,
  lastUploadDaysAgo: 2,
  medianRatio: 1.3,
  outliers: 0,
  videosInWindow: 9,
  longestGapDays: 11,
  medianViewsDay7: 2_900,
  topOutliers: [],
});

function cadence(seed: CompetitorSeed): CadenceWeek[] {
  const perWeek = seed.uploads90d / 13;
  return Array.from({ length: 13 }, (_, index) => {
    const wobble = [0.6, 1.3, 0.9, 1.1, 0.4, 1.5, 1, 0.8, 1.2, 0.7, 1.4, 1, 0.9][index];
    return { week_start: new Date(Date.parse(NOW) - (12 - index) * 7 * 86_400_000).toISOString(), uploads: Math.max(0, Math.round(perWeek * wobble)) };
  });
}

const COVERAGE: Record<string, { videos: number; outliers: number; client: number; rec: string | null }> = {
  bq_bypass_cost: { videos: 3, outliers: 1, client: 0, rec: "rec_bypasscosthyd" },
  bq_angio_or_bypass: { videos: 2, outliers: 1, client: 1, rec: "rec_angiovsbypass" },
  bq_second_opinion: { videos: 2, outliers: 1, client: 0, rec: null },
  bq_knee_recovery: { videos: 1, outliers: 0, client: 1, rec: null },
  bq_transplant_wait: { videos: 1, outliers: 1, client: 0, rec: "rec_transplantdiary" },
  bq_proton_fit: { videos: 0, outliers: 0, client: 1, rec: null },
};

export function competitorDetail(channelId: string): CompetitorDetail | null {
  const index = seeds.findIndex((item) => item.channel.channel_id === channelId);
  if (index < 0) return null;
  const seed = seeds[index];
  const scorecard = scorecards[index];
  return {
    scorecard,
    you: ownScorecard,
    cadence_series: cadence(seed),
    curves: scorecard.top_outliers.slice(0, 3).map(curve),
    top_outliers: scorecard.top_outliers,
    trait_lift: [
      { trait_key: "title_question", label: "Question titles", outliers_with: 4, outliers_total: 5, n_with: 8, n_total: 30, lift: 2.1, status: "supported" },
      { trait_key: "title_cost", label: "Cost or price in the title", outliers_with: 3, outliers_total: 5, n_with: 6, n_total: 30, lift: 2.4, status: "supported" },
      { trait_key: "length_6_10", label: "6–10 minute videos", outliers_with: 3, outliers_total: 5, n_with: 12, n_total: 30, lift: 1.4, status: "exploratory" },
    ],
    topic_coverage: buyerQuestions
      .filter((question) => COVERAGE[question.id])
      .map((question) => {
        const row = COVERAGE[question.id];
        const scale = index % 2 === 0 ? 1 : 0;
        return { topic_ref_id: question.id, topic: question.text, videos: Math.max(0, row.videos - (1 - scale)), outliers: row.outliers * scale, client_videos: row.client, recommendation_id: row.rec };
      }),
    collaborations: index === 0 ? [{ video_id: "hs-collab-1", handle_or_name: "@HeartSmartIndia", kind: "mention" }] : [],
  };
}

export function gapMatrix(): GapMatrixView {
  const tracked = scorecards.filter((item) => item.role === "competitor_set");
  const topics = buyerQuestions.slice(0, 7).map((item) => ({ id: item.id, label: item.text }));
  const cells: GapMatrixView["cells"] = {};
  const own: GapMatrixView["own"] = {};
  topics.forEach((topic, row) => {
    cells[topic.id] = {};
    own[topic.id] = COVERAGE[topic.id]?.client ?? 0;
    tracked.forEach((card, column) => {
      const videos = (row * 3 + column * 2 + 1) % 5;
      const outliers = videos > 2 ? 1 + ((row + column) % 2) : videos > 0 && (row + column) % 3 === 0 ? 1 : 0;
      cells[topic.id][card.channel.channel_id] = { n_videos: videos, n_outliers: outliers, best_ratio: outliers ? 3 + ((row + column) % 3) * 0.7 : null };
    });
  });
  return { topics, channels: tracked.map((card) => ({ channel_id: card.channel.channel_id, title: card.channel.title })), cells, own };
}
