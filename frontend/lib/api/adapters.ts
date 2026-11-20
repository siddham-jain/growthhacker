import type { RecommendationPage } from "./contract";
import type { components } from "./schema";
import type {
  AgeMethod,
  BusinessProfile,
  BusinessProfileView,
  ChannelRef,
  ChannelScorecard,
  Client,
  ClaimMeta,
  CompetitorDetail,
  CompetitorRole,
  ContextSourceKind,
  GapMatrixView,
  OwnPerformance,
  Page,
  PipelineRun,
  ProfileFact,
  RecommendationOut,
  ResultEnvelope,
  RunEvent,
  RunNotice,
  TrendingReport,
  VideoMetricsView,
  WindowDays,
} from "./types";

// wire → view mapping for the http client. it renames and reshapes only; every number is passed through as the server sent it
type Wire = components["schemas"];

const WINDOWS: WindowDays[] = [7, 30, 90];

function windowOf(value: number | null | undefined): WindowDays {
  return WINDOWS.includes(value as WindowDays) ? (value as WindowDays) : 30;
}

export function toChannel(channel: Wire["ChannelRef"]): ChannelRef {
  return {
    channel_id: channel.channel_id,
    title: channel.title ?? channel.handle ?? channel.channel_id,
    handle: channel.handle ?? null,
    thumbnail_url: channel.thumbnail_url ?? null,
    subscriber_count: channel.subscriber_count ?? null,
    verified: channel.verified,
    verified_by: channel.verified_by ?? null,
  };
}

export function toNotice(notice: Wire["RunNotice"]): RunNotice {
  return { code: notice.code, message: notice.message, stage: notice.stage ?? null, suggest: (notice.suggest as RunNotice["suggest"]) ?? null };
}

export function toRun(run: Wire["RunOut"]): PipelineRun {
  return {
    id: run.id,
    client_id: run.client_id,
    kind: run.kind,
    status: run.status,
    percent: run.progress_pct,
    window_days: windowOf(run.window_days),
    started_at: run.started_at ?? null,
    finished_at: run.finished_at ?? null,
    stages: run.stages?.map((stage) => ({ stage: stage.stage, status: stage.status, started_at: stage.started_at ?? null, finished_at: stage.finished_at ?? null, message: stage.error_code ?? null })),
    notices: (run.notices ?? []).map(toNotice),
    error: run.error ? { code: run.error.code, message: run.error.message, retryable: run.error.retryable ?? false, request_id: run.error.fingerprint ?? "" } : null,
  };
}

export function toEvent(event: Wire["RunEventOut"]): RunEvent {
  return {
    ...event,
    stage: event.stage ?? null,
    step: event.step ?? null,
    status: (event.status ?? null) as RunEvent["status"],
    level: (event.level as RunEvent["level"]) ?? "info",
    counts: (event.counts as RunEvent["counts"]) ?? null,
    percent: event.percent ?? null,
    stage_percent: event.stage_percent ?? null,
    payload: (event.payload ?? null) as RunEvent["payload"],
  };
}

export function toClient(client: Wire["ClientOut"]): Client {
  return { ...client, own_channel: client.own_channel ? toChannel(client.own_channel) : null };
}

export function toEnvelope<W, V>(envelope: ResultEnvelope<W>, map: (data: W) => V): ResultEnvelope<V> {
  return { ...envelope, data: map(envelope.data), notices: (envelope.notices ?? []).map((notice) => toNotice(notice as Wire["RunNotice"])) };
}

export function toRecommendation(item: Wire["RecommendationOut"]): RecommendationOut {
  const { recommendation, feedback } = item;
  return {
    ...recommendation,
    window_days: windowOf(recommendation.window_days),
    reference_videos: recommendation.reference_videos.map((video) => ({ ...video, views_at_age: video.views_at_age })),
    stable_key: recommendation.extras.stable_key,
    run_id: recommendation.extras.run_id,
    rendered_by: recommendation.extras.rendered_by,
    feedback: feedback ? { status: feedback.status ?? null, reason: feedback.reason ?? null, linked_video_id: feedback.linked_video_id ?? null } : null,
  };
}

export function toRecommendationPage(envelope: Wire["ResultEnvelope_RecommendationsData_"]): RecommendationPage {
  const items = (envelope.data.items as Wire["RecommendationOut"][]).filter((item) => "recommendation" in item).map(toRecommendation);
  const thin = (envelope.data.notices ?? []).filter((notice) => notice.kind === "thin_window");
  const notices = [
    ...(envelope.notices ?? []).map(toNotice),
    ...thin.map((notice): RunNotice => ({ code: "thin_data", message: notice.reason_code, suggest: notice.suggestion as RunNotice["suggest"] })),
  ];
  return {
    ...envelope,
    data: { items, next_cursor: envelope.data.next_cursor ?? null, limit: envelope.data.limit, total: null },
    notices,
    white_space: envelope.data.white_space ?? [],
  };
}

const ROLE_FROM_CHANNEL: Record<Wire["ChannelRole"], CompetitorRole> = {
  own: "competitor_set",
  competitor: "competitor_set",
  niche: "niche_set",
  benchmark: "benchmark_set",
  creator: "creator_seed",
};

const BAND_LABELS: Record<Wire["SubscriberBand"], string> = {
  lt_10k: "Under 10K subscribers",
  "10k_100k": "10K–100K subscribers",
  "100k_1m": "100K–1M subscribers",
  gt_1m: "1M+ subscribers",
  hidden: "Subscribers hidden",
};

export function toVideo(summary: Wire["VideoSummary"] | undefined, outlier: Wire["OutlierRecord"]): VideoMetricsView {
  return {
    video_id: outlier.video_id,
    channel_id: outlier.channel_id,
    channel_title: summary?.channel_title ?? "",
    title: summary?.title ?? outlier.video_id,
    published_at: outlier.published_at,
    views_at_age: outlier.views_at_age,
    outlier_ratio: outlier.ratio,
    is_outlier: true,
    segment: outlier.segment,
    channel_baseline_views_at_age: null,
  };
}

function toVideos(rows: Wire["OutlierRow"][] | undefined): VideoMetricsView[] {
  return (rows ?? []).map((row) => toVideo(row.video, row.outlier));
}

// the wilson interval arrives as proportions; shown as counts of the same denominator ("likely 1–6 of 12")
function hitRate(scorecard: Wire["ChannelScorecard"]): ChannelScorecard["outlier_hit_rate"] {
  const [outliers, videos] = scorecard.outlier_hit_rate as [number, number];
  const [low, high] = scorecard.outlier_hit_rate_wilson as [number, number];
  return { outliers, videos, likely_low: Math.round(low * videos), likely_high: Math.round(high * videos) };
}

export function toScorecard(channel: Wire["ChannelRef"], scorecard: Wire["ChannelScorecard"], outliers: Wire["OutlierRow"][] | undefined): ChannelScorecard {
  const rate = hitRate(scorecard);
  const median = scorecard.median_views_at_age.long_form ?? Object.values(scorecard.median_views_at_age)[0];
  return {
    channel: toChannel(channel),
    role: ROLE_FROM_CHANNEL[scorecard.role],
    relation: null,
    uploads_per_week: scorecard.uploads_per_week,
    median_ratio: scorecard.median_ratio,
    outliers_in_window: rate.outliers,
    videos_in_window: scorecard.uploads_in_window,
    days_since_last_upload: scorecard.days_since_last_upload === null ? 0 : Math.round(scorecard.days_since_last_upload),
    longest_gap_days: scorecard.longest_gap_days === null ? null : Math.round(scorecard.longest_gap_days),
    subscriber_hidden: scorecard.subscriber_band === "hidden",
    subscriber_band: BAND_LABELS[scorecard.subscriber_band],
    segment_mix: scorecard.segment_mix,
    // 06 reports the median at its comparison age, which defaults to day 7
    median_views_at_age: median ? { age_days: 7, views: median.value, method: median.method } : null,
    outlier_hit_rate: rate,
    top_outliers: toVideos(outliers),
  };
}

export function toCompetitorDetail(detail: Wire["CompetitorDetail"]): CompetitorDetail {
  return {
    scorecard: toScorecard(detail.channel, detail.scorecard, detail.top_outliers),
    you: null,
    cadence_series: detail.cadence_series ?? [],
    curves: [],
    top_outliers: toVideos(detail.top_outliers),
    trait_lift: (detail.trait_lift ?? []).map((trait) => ({
      trait_key: trait.trait_key,
      label: `${trait.trait_key.replace(/_/g, " ")}: ${trait.trait_value.replace(/_/g, " ")}`,
      outliers_with: trait.outliers_with,
      outliers_total: trait.outliers_with + trait.outliers_without,
      n_with: trait.n_with,
      n_total: trait.n_with + trait.n_without,
      lift: trait.lift ?? trait.lift_smoothed,
      status: trait.status === "supported" ? "supported" : "exploratory",
    })),
    topic_coverage: (detail.topic_coverage ?? []).map((row) => ({
      topic_ref_id: row.topic_ref_id,
      topic: row.label,
      videos: row.competitor.n_videos,
      outliers: row.competitor.n_outliers,
      client_videos: row.client?.n_videos ?? 0,
      recommendation_id: null,
    })),
    collaborations: detail.collaborations ?? [],
  };
}

export function toOutlierPage(page: Wire["Page_OutlierRow_"]): Page<VideoMetricsView> {
  return { items: toVideos(page.items), next_cursor: page.next_cursor ?? null, limit: page.limit, total: null };
}

export function toTrending(data: Wire["TrendingData"]): TrendingReport {
  return {
    items: data.report.items.map((item) => {
      const summary = data.videos?.[item.video_id];
      return {
        video_id: item.video_id,
        channel_id: item.channel_id,
        channel_title: summary?.channel_title ?? "",
        title: summary?.title ?? item.video_id,
        published_at: summary?.published_at ?? "",
        views_at_age: { age_days: 7, views: null, method: item.method, label: "" },
        outlier_ratio: item.ratio,
        is_outlier: false,
        segment: item.segment,
        views_per_day: item.views_per_day,
      };
    }),
    rising_topics: data.report.rising_topics.map((topic) => ({ label: data.topics?.[topic.topic_ref_id] ?? topic.topic_ref_id, ...topic })),
  };
}

export function toOwnPerformance(data: Wire["OwnPerformanceData"]): OwnPerformance {
  const report = data.youtube_public;
  const summaries = data.videos ?? {};
  const perf = new Map((report?.videos ?? []).map((video) => [video.video_id, video]));
  const metric = (videoId: string): VideoMetricsView => {
    const video = perf.get(videoId);
    const summary = summaries[videoId];
    return {
      video_id: videoId,
      channel_id: summary?.channel_id ?? "",
      channel_title: summary?.channel_title ?? "",
      title: summary?.title ?? videoId,
      published_at: video?.published_at ?? summary?.published_at ?? "",
      views_at_age: { age_days: report?.comparison_age_days ?? 7, views: null, method: (video?.method ?? "unavailable") as AgeMethod, label: `vs normal at day ${report?.comparison_age_days ?? 7}` },
      outlier_ratio: video?.ratio ?? null,
      is_outlier: video?.tier === "outlier",
      segment: video?.segment ?? "long_form",
    };
  };
  const baseline = report?.baselines.find((item) => item.metric === "views_at_age" && item.segment === "long_form");
  return {
    youtube_public: report
      ? {
          median_ratio_window: report.median_ratio_window,
          median_ratio_prev_window: report.median_ratio_prev_window,
          comparison_age_days: report.comparison_age_days,
          videos_in_window: report.videos.length,
          method_mix: report.method_mix as Partial<Record<AgeMethod, number>>,
          videos: report.videos.map((video) => ({ video_id: video.video_id, title: summaries[video.video_id]?.title ?? video.video_id, published_at: video.published_at, ratio: video.ratio, method: video.method, segment: video.segment })),
          baseline: baseline ? { age_days: baseline.age_days ?? report.comparison_age_days, views: Math.round(baseline.median), method: baseline.method, n: baseline.n } : null,
          curves: [],
          best: report.best.map(metric),
          worst: report.worst.map(metric),
        }
      : null,
    youtube_analytics: data.youtube_analytics ? { top_search_terms: Object.keys(data.youtube_analytics.top_search_terms ?? {}) } : null,
    website: null,
  };
}

function toMeta(meta: Wire["ClaimMeta"]): ClaimMeta {
  return {
    origin: meta.origin,
    status: meta.status,
    confidence: meta.confidence,
    evidence: (meta.evidence ?? []).map((item) => ({ url: item.source_url, quote: item.quote, source_kind: item.source_kind as ContextSourceKind, fetched_at: item.fetched_at, snapshot_at: item.snapshot_at ?? null })),
  };
}

function toProfile(profile: Wire["BusinessProfile"]): BusinessProfile {
  return {
    identity: {
      name: profile.identity.name,
      one_liner: profile.identity.one_liner,
      industry: profile.identity.industry,
      niche: profile.identity.niche,
      country: profile.identity.country ?? "",
      timezone: profile.identity.timezone ?? "",
      website_domains: profile.identity.website_domains ?? [],
      brand_aliases: profile.identity.brand_aliases ?? [],
      one_liner_meta: toMeta(profile.identity.one_liner_meta),
    },
    offerings: (profile.offerings ?? []).map((item): ProfileFact => ({ id: item.id, label: item.name, detail: item.description ?? undefined, meta: toMeta(item.meta) })),
    value_propositions: (profile.value_propositions ?? []).map((item): ProfileFact => ({ id: item.id, label: item.statement, meta: toMeta(item.meta) })),
    locations: (profile.locations?.items ?? []).map((item): ProfileFact => ({ id: item.id, label: item.name, detail: item.city ?? undefined, meta: toMeta(item.meta) })),
    conversion_actions: (profile.conversion_actions ?? []).map((item): ProfileFact => ({ id: item.id, label: item.label, meta: toMeta(item.meta) })),
    freshness: {
      sensitivity: profile.freshness.sensitivity as BusinessProfile["freshness"]["sensitivity"],
      regulated_domain: profile.freshness.regulated_domain ?? false,
      domain_kind: profile.freshness.domain_kind ?? "none",
      rationale: profile.freshness.rationale,
    },
  };
}

export function toProfileView(view: Wire["BusinessProfileView"]): BusinessProfileView {
  return { ...view, profile: toProfile(view.profile), effective: toProfile(view.effective), sources: view.sources ?? [] };
}

// edits go back as a merge patch of the domain profile: only the label field of each edited claim changes
const LABEL_FIELD = { offerings: "name", value_propositions: "statement", conversion_actions: "label", locations: "name" } as const;

export function toProfilePatch(raw: Wire["BusinessProfile"], patch: Partial<Record<keyof typeof LABEL_FIELD, ProfileFact[]>>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [group, facts] of Object.entries(patch) as [keyof typeof LABEL_FIELD, ProfileFact[]][]) {
    const originals: { id: string; meta: Wire["ClaimMeta"] }[] = group === "locations" ? (raw.locations?.items ?? []) : ((raw[group] ?? []) as { id: string; meta: Wire["ClaimMeta"] }[]);
    const items = facts.map((fact) => {
      const original = originals.find((item) => item.id === fact.id);
      const meta = { ...(original?.meta ?? { confidence: 1, first_seen_version: raw.version, origin: "user" }), status: fact.meta.status, origin: fact.meta.origin, updated_at: new Date().toISOString() };
      return { ...(original ?? { id: fact.id }), [LABEL_FIELD[group]]: fact.label, meta };
    });
    out[group] = group === "locations" ? { ...raw.locations, items } : items;
  }
  return out;
}

export function toGapMatrix(sheets: Wire["CompetitorSheet"][], questions: Wire["BuyerQuestionItem"][], topics: Wire["TopicSummary"][]): GapMatrixView {
  const tracked = sheets.filter((sheet) => sheet.scorecard.role === "competitor");
  const active = questions.filter((question) => question.status === "confirmed");
  const own: GapMatrixView["own"] = {};
  for (const topic of topics) {
    const id = String((topic.topic_ref as { id?: string }).id ?? "");
    if (id) own[id] = topic.own_videos;
  }
  const cells: GapMatrixView["cells"] = {};
  for (const question of active) {
    cells[question.id] = {};
    for (const sheet of tracked) {
      const cell = sheet.scorecard.topic_coverage[question.id];
      if (cell) cells[question.id][sheet.channel.channel_id] = { n_videos: cell.n_videos, n_outliers: cell.n_outliers, best_ratio: cell.best_ratio };
    }
  }
  return {
    topics: active.map((question) => ({ id: question.id, label: question.text })),
    channels: tracked.map((sheet) => ({ channel_id: sheet.channel.channel_id, title: sheet.channel.title ?? sheet.channel.handle ?? sheet.channel.channel_id })),
    cells,
    own,
  };
}
