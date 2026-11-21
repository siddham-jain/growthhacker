import type { ChannelLandscape, ChannelOverview, FormatStats, GrowthFigure } from "../../types";
import { channels, NOW } from "./base";

const WEEKS = 90 / 7;

function stats(uploads: number, meanViews: number, engagement: number): FormatStats {
  return {
    uploads,
    uploads_per_week: uploads / WEEKS,
    total_views: Math.round(uploads * meanViews),
    mean_views: uploads ? meanViews : null,
    median_views: uploads ? Math.round(meanViews * 0.6) : null,
    mean_engagement_rate: uploads ? engagement : null,
    rated_uploads: uploads,
  };
}

function growth(metric: GrowthFigure["metric"], source: GrowthFigure["source"], start: number, end: number, days: number): GrowthFigure {
  return {
    metric,
    source,
    requested_days: 365,
    start_day: "2025-11-21",
    end_day: "2026-11-18",
    days_covered: days,
    start_value: start,
    end_value: end,
    change: end - start,
    change_pct: (end - start) / start,
  };
}

function overview(
  ref: (typeof channels)[keyof typeof channels],
  role: ChannelOverview["role"],
  shorts: [number, number, number],
  longForm: [number, number, number],
  previous: number,
  extra: Partial<ChannelOverview> = {},
): ChannelOverview {
  const short = stats(...shorts);
  const long = stats(...longForm);
  const overall = stats(short.uploads + long.uploads, (short.total_views + long.total_views) / (short.uploads + long.uploads), (shorts[2] + longForm[2]) / 2);
  const subscribers = ref.subscriber_count ?? null;
  return {
    channel_id: ref.channel_id,
    role,
    title: ref.title ?? ref.channel_id,
    as_of: NOW,
    window_days: 90,
    subscribers,
    total_views: subscribers ? subscribers * 180 : null,
    total_videos: 1200,
    format_mix: { short_form: short.uploads, long_form: long.uploads, live: 0, upcoming: 0, unknown: 0 },
    overall,
    by_format: { short_form: short, long_form: long },
    views_share: { short_form: short.total_views / overall.total_views, long_form: long.total_views / overall.total_views },
    previous_window_uploads: previous,
    pace_change_pct: (overall.uploads - previous) / previous,
    days_since_last_upload: 1.5,
    median_gap_days: 90 / overall.uploads,
    longest_gap_days: 9,
    views_per_subscriber: subscribers && overall.mean_views ? overall.mean_views / subscribers : null,
    top_uploads: [
      { video_id: "dQw4w9WgXcQ", title: "Heart attack signs women miss", format: "short_form", published_at: "2026-10-30T06:00:00Z", views: 412_000, engagement_rate: 0.041 },
      { video_id: "9bZkp7q19f0", title: "Angioplasty vs bypass, explained by a cardiologist", format: "long_form", published_at: "2026-09-14T06:00:00Z", views: 188_000, engagement_rate: 0.032 },
    ],
    subscriber_growth: null,
    view_growth: null,
    scraped_uploads: 0,
    ...extra,
  };
}

export function landscape(): ChannelLandscape {
  const competitors = [
    overview(channels.fortis, "competitor", [22, 14_000, 0.038], [9, 21_000, 0.024], 25, {
      subscriber_growth: growth("subscribers", "channel_history", 371_000, 386_000, 28),
      view_growth: growth("views", "channel_history", 68_100_000, 69_400_000, 28),
    }),
    overview(channels.medanta, "competitor", [30, 26_000, 0.045], [12, 40_000, 0.03], 36, {
      subscriber_growth: growth("subscribers", "channel_history", 509_000, 521_000, 28),
      view_growth: growth("views", "channel_history", 92_800_000, 93_800_000, 28),
    }),
    overview(channels.narayana, "competitor", [8, 9_000, 0.029], [15, 16_000, 0.021], 20, { scraped_uploads: 3 }),
  ];
  return {
    as_of: NOW,
    window_days: 90,
    growth_days: 365,
    own: overview(channels.apollo, "own", [18, 31_000, 0.04], [21, 38_000, 0.028], 30, {
      subscriber_growth: growth("subscribers", "youtube_analytics", 1_090_000, 1_240_000, 363),
      view_growth: growth("views", "youtube_analytics", 196_000_000, 223_000_000, 363),
    }),
    competitors,
    competitor_median: {
      uploads_per_week: 2.3,
      short_form_uploads: 22,
      long_form_uploads: 12,
      mean_views: 17_600,
      median_views: 10_500,
      mean_engagement_rate: 0.031,
      views_per_subscriber: 0.046,
      days_since_last_upload: 1.5,
      subscriber_growth_pct: 0.032,
      view_growth_pct: 0.015,
    },
  };
}
