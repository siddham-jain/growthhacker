import type { ChannelRef, Freshness, ReferenceVideo, ResultEnvelope, VideoMetricsView, WindowDays } from "../../types";

export const NOW = "2026-11-20T00:40:00Z";
export const DATA_THROUGH = "2026-11-20T00:40:00Z";
export const RUN_ID = "run_01JD2ANALYSIS";
export const CLIENT_ID = "cl_apollo";

export function daysAgo(days: number): string {
  return new Date(Date.parse(NOW) - days * 86_400_000).toISOString();
}

export function hoursFromNow(hours: number): string {
  return new Date(Date.now() + hours * 3_600_000).toISOString();
}

const freshness: Freshness = { data_through: DATA_THROUGH, age_hours: 5.2, stale: false, stale_reasons: [] };

export function envelope<T>(data: T, windowDays: WindowDays): ResultEnvelope<T> {
  return {
    data,
    window_days: windowDays,
    as_of: NOW,
    run_id: RUN_ID,
    generated_at: NOW,
    freshness,
    notices: [],
  };
}

export const channels = {
  apollo: channel("UCapollohospitals0001", "Apollo Hospitals", "@ApolloHospitalsIndia", 1_240_000, "website_link"),
  apollo247: channel("UCapollo247000000001", "Apollo 24|7", "@Apollo247", 412_000, null),
  fortis: channel("UCfortishealthcare01", "Fortis Healthcare", "@FortisHealthcare", 386_000, "website_link"),
  narayana: channel("UCnarayanahealth0001", "Narayana Health", "@NarayanaHealth", 298_000, "website_link"),
  medanta: channel("UCmedantathemedicity", "Medanta", "@Medanta", 521_000, "website_link"),
  manipal: channel("UCmanipalhospitals01", "Manipal Hospitals", "@ManipalHospitals", 174_000, "website_link"),
  max: channel("UCmaxhealthcare00001", "Max Healthcare", "@MaxHealthcare", 233_000, "website_link"),
  practo: channel("UCpracto000000000001", "Practo", "@Practo", 655_000, "website_link"),
  drHeartTalks: channel("UCheartsmartindia001", "HeartSmart India", "@HeartSmartIndia", 182_000, null),
  doctorExplains: channel("UCdoctorexplainshin1", "Doctor Explains Hindi", "@DoctorExplainsHindi", 940_000, null),
  healthWithAsha: channel("UChealthwithasha0001", "Health with Asha", "@HealthWithAsha", 96_000, null),
} satisfies Record<string, ChannelRef>;

function channel(
  channelId: string,
  title: string,
  handle: string,
  subscribers: number,
  verifiedBy: ChannelRef["verified_by"],
): ChannelRef {
  return {
    channel_id: channelId,
    title,
    handle,
    thumbnail_url: null,
    subscriber_count: subscribers,
    verified: verifiedBy !== null,
    verified_by: verifiedBy,
  };
}

export function video(
  videoId: string,
  owner: ChannelRef,
  title: string,
  publishedDaysAgo: number,
  viewsAtAge: number,
  ratio: number | null,
  segment: VideoMetricsView["segment"] = "long_form",
): VideoMetricsView {
  return {
    video_id: videoId,
    channel_id: owner.channel_id,
    channel_title: owner.title,
    title,
    published_at: daysAgo(publishedDaysAgo),
    views_at_age: {
      age_days: 7,
      views: viewsAtAge,
      method: publishedDaysAgo > 20 ? "interpolated" : "observed",
      label: publishedDaysAgo > 20 ? "views at day 7 (interpolated)" : "views at day 7",
    },
    outlier_ratio: ratio,
    is_outlier: ratio !== null && ratio >= 3,
    segment,
  };
}

export function reference(metrics: VideoMetricsView, role: ReferenceVideo["channel_role"]): ReferenceVideo {
  return {
    video_id: metrics.video_id,
    url: `https://www.youtube.com/watch?v=${metrics.video_id}`,
    title: metrics.title,
    channel: metrics.channel_title,
    channel_id: metrics.channel_id,
    channel_role: role,
    published_at: metrics.published_at,
    views_day7: metrics.views_at_age.views,
    views_at_age: metrics.views_at_age,
    outlier_ratio: metrics.outlier_ratio,
    thumbnail_url: null,
  };
}
