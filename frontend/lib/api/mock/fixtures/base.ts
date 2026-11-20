import type { ChannelRef, Freshness, ReferenceVideo, ResultEnvelope, VideoMetricsView, ViewsAtAgeCurve, WindowDays } from "../../types";

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
  const metrics: VideoMetricsView = {
    video_id: videoId,
    channel_id: owner.channel_id,
    channel_title: owner.title,
    title,
    published_at: daysAgo(publishedDaysAgo),
    views_at_age: {
      age_days: 7,
      views: viewsAtAge,
      method: publishedDaysAgo > 25 ? "age_band_proxy" : publishedDaysAgo > 20 ? "interpolated" : "observed",
      label: publishedDaysAgo > 25 ? "views at about day 7 (age-band estimate)" : publishedDaysAgo > 20 ? "views at day 7 (interpolated)" : "views at day 7",
    },
    outlier_ratio: ratio,
    is_outlier: ratio !== null && ratio >= 3,
    segment,
  };
  return { ...metrics, channel_baseline_views_at_age: baselineOf(metrics) };
}

export function reference(metrics: VideoMetricsView, role: ReferenceVideo["channel_role"]): ReferenceVideo {
  return {
    video_id: metrics.video_id,
    url: `https://www.youtube.com/watch?v=${metrics.video_id}`,
    title: metrics.title,
    channel: metrics.channel_title,
    channel_id: metrics.channel_id,
    channel_role: role ?? "competitor",
    is_outlier: metrics.is_outlier,
    matched_topic_ids: [],
    published_at: metrics.published_at,
    views_day7: metrics.views_at_age.views,
    views_at_age: metrics.views_at_age,
    channel_baseline_views_at_age: baselineOf(metrics),
    outlier_ratio: metrics.outlier_ratio,
    thumbnail_url: null,
  };
}

// fixture stand-in for the server's channel median at the same age
export function baselineOf(metrics: VideoMetricsView): number | null {
  const views = metrics.views_at_age.views;
  return views !== null && metrics.outlier_ratio ? Math.round(views / metrics.outlier_ratio / 100) * 100 : null;
}

const CURVE_AGES = [1, 2, 3, 5, 7, 10, 14, 21, 28];

// snapshots started 20 days ago, so older videos have proxied early points and observed later ones
export function curve(metrics: VideoMetricsView): ViewsAtAgeCurve {
  const ageNow = (Date.parse(NOW) - Date.parse(metrics.published_at)) / 86_400_000;
  const daySeven = metrics.views_at_age.views ?? 0;
  const tracked = ageNow <= 20;
  return {
    video_id: metrics.video_id,
    title: metrics.title,
    channel_title: metrics.channel_title,
    outlier_ratio: metrics.outlier_ratio,
    points: CURVE_AGES.filter((age) => age <= ageNow).map((age) => ({
      age_days: age,
      views: Math.round((daySeven * Math.pow(age / 7, 0.62)) / 10) * 10,
      method: tracked ? "observed" : ageNow - age > 20 ? "age_band_proxy" : "interpolated",
    })),
  };
}

// a small unified diff (difflib style, 3 lines of context) so fixtures carry the same preview_diff the backend sends
export function unifiedDiff(before: string, after: string, context = 3): string {
  const a = before.split("\n");
  const b = after.split("\n");
  const ops: { tag: " " | "-" | "+"; text: string; ai: number; bi: number }[] = [];
  let i = 0;
  let j = 0;
  while (i < a.length || j < b.length) {
    if (i < a.length && j < b.length && a[i] === b[j]) ops.push({ tag: " ", text: a[i++], ai: i, bi: ++j });
    // removals before additions, as difflib orders a replaced line
    else if (i < a.length && (j >= b.length || !b.slice(j).includes(a[i]))) ops.push({ tag: "-", text: a[i++], ai: i, bi: j });
    else ops.push({ tag: "+", text: b[j++], ai: i, bi: j });
  }
  const changed = ops.map((op, index) => (op.tag === " " ? -1 : index)).filter((index) => index >= 0);
  const keep = ops.map((_, index) => changed.some((at) => Math.abs(at - index) <= context));
  const lines = ["--- published", "+++ suggested"];
  let index = 0;
  while (index < ops.length) {
    if (!keep[index]) {
      index += 1;
      continue;
    }
    const start = index;
    while (index < ops.length && keep[index]) index += 1;
    const hunk = ops.slice(start, index);
    const oldStart = hunk.find((op) => op.tag !== "+")?.ai ?? 0;
    const newStart = hunk.find((op) => op.tag !== "-")?.bi ?? 0;
    const oldLen = hunk.filter((op) => op.tag !== "+").length;
    const newLen = hunk.filter((op) => op.tag !== "-").length;
    lines.push(`@@ -${oldStart},${oldLen} +${newStart},${newLen} @@`, ...hunk.map((op) => `${op.tag}${op.text}`));
  }
  return lines.join("\n");
}
