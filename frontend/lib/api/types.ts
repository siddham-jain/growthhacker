// wire shapes come from schema.d.ts (generated from the backend's openapi.json, `pnpm gen:api`); the backend wins on any difference.
// shapes the ui adopts as-is are aliases below; view models the screens need are mapped from the wire in adapters.ts.
import type { components } from "./schema";

type Wire = components["schemas"];

export type WindowDays = 7 | 30 | 90;
export type Lens = "views" | "conversion";
export type LensFilter = Lens | "both";

export interface Page<T> {
  items: T[];
  next_cursor: string | null;
  limit: number;
  total: number | null;
}

export interface Freshness {
  data_through: string;
  age_hours: number;
  stale: boolean;
  stale_reasons: string[];
}

export interface RunNotice {
  code: string;
  // the backend sends no level; the notice registry decides the tone from the code
  level?: "info" | "warning" | "error";
  message: string;
  stage?: string | null;
  suggest?: { widen_window_to?: WindowDays } | null;
}

export interface ResultEnvelope<T> {
  data: T;
  window_days: number;
  as_of: string;
  run_id: string;
  generated_at: string;
  freshness: Freshness;
  notices: RunNotice[];
}

export interface FieldError {
  loc: (string | number)[];
  msg: string;
  type: string;
}

export interface Problem {
  type: string;
  title: string;
  status: number;
  detail: string;
  instance: string;
  code: string;
  request_id: string;
  errors: FieldError[] | null;
  retry_after_s: number | null;
  extra: Record<string, unknown> | null;
}

// auth (11 auth.py: session cookie or `Authorization: Bearer <api key>`)

export type WorkspaceRole = Wire["MeWorkspace"]["role"];
export type MeWorkspace = Wire["MeWorkspace"];
export type Me = Wire["MeOut"];

export interface LoginIn {
  email: string;
  password: string;
}

// clients

export type ClientLifecycle = Wire["ClientLifecycle"];

export interface Market {
  country: string;
  language: string;
  timezone: string;
}

export type ChannelVerifiedBy = "website_link" | "channel_link" | "oauth" | "manual";

export interface ChannelRef {
  channel_id: string;
  title: string;
  handle: string | null;
  thumbnail_url: string | null;
  subscriber_count: number | null;
  verified: boolean;
  verified_by: ChannelVerifiedBy | null;
}

export interface Client {
  id: string;
  name: string;
  website_url: string | null;
  own_channel: ChannelRef | null;
  lifecycle: ClientLifecycle;
  window_days: WindowDays;
  market: Market;
  refresh_hour_local: number;
  paused: boolean;
  oauth_connected: boolean;
  active_run_id: string | null;
  last_success_at: string | null;
  created_at: string;
  updated_at: string;
  version: number;
}

export interface ClientCreate {
  name: string;
  website_url?: string | null;
  channel_ref?: string | null;
  market: Market;
  window_days?: WindowDays;
  start_onboarding?: boolean;
}

export interface ClientCreated {
  client: Client;
  onboarding_run: PipelineRun | null;
}

export type ClientPatch = Partial<Pick<Client, "name" | "window_days" | "market" | "refresh_hour_local" | "paused">>;

export interface ChannelAttachIn {
  channel_id: string | null;
}

export interface ChannelResolveOut {
  candidates: {
    channel: ChannelRef;
    ownership_evidence: string[];
    confidence: "high" | "medium" | "low";
  }[];
}

// business profile (03; source_kind follows the acquisition cascade in 03 §14)

export type ContextSourceKind =
  | "site_direct"
  | "site_rendered"
  | "reader_proxy"
  | "search_index"
  | "archive"
  | "knowledge_base"
  | "web_mentions"
  | "youtube_channel"
  | "user";

export interface Evidence {
  url: string;
  quote: string;
  source_kind: ContextSourceKind;
  fetched_at: string;
  snapshot_at?: string | null;
}

export interface ClaimMeta {
  origin: "extracted" | "deterministic" | "derived" | "inferred" | "user";
  status: "proposed" | "confirmed" | "edited" | "rejected" | "stale" | "needs_confirmation";
  confidence: number;
  evidence: Evidence[];
}

export interface ProfileFact {
  id: string;
  label: string;
  detail?: string;
  meta: ClaimMeta;
}

export interface BusinessProfile {
  identity: {
    name: string;
    one_liner: string;
    industry: string;
    niche: string;
    country: string;
    timezone: string;
    website_domains: string[];
    brand_aliases: string[];
    one_liner_meta: ClaimMeta;
  };
  offerings: ProfileFact[];
  value_propositions: ProfileFact[];
  locations: ProfileFact[];
  conversion_actions: ProfileFact[];
  freshness: {
    sensitivity: "very_low" | "low" | "medium" | "high" | "very_high";
    regulated_domain: boolean;
    domain_kind: "healthcare" | "finance" | "legal" | "none";
    rationale: string;
  };
}

export type ProfileSource = Wire["ProfileSource"];

export interface BusinessProfileView {
  profile: BusinessProfile;
  overrides: Record<string, unknown>;
  effective: BusinessProfile;
  version: number;
  completeness: number;
  partial: boolean;
  sources: ProfileSource[];
}

export type ProfilePatch = Partial<Record<"offerings" | "value_propositions" | "locations" | "conversion_actions" | "niche_keywords", unknown>>;

// topics

export type TopicStatus = Wire["KeywordItem"]["status"];
export type KeywordItem = Wire["KeywordItem"];
export type BuyerQuestionItem = Wire["BuyerQuestionItem"];
export type KeywordCreate = Wire["KeywordCreate"];
export type BuyerQuestionCreate = Wire["BuyerQuestionCreate"];
export type TopicPatch = Wire["TopicRefPatch"];
export type BulkStatusIn = Wire["BulkStatusIn"];

// competitors (04's candidate model as the backend serves it)

export type CompetitorRole = Wire["CandidateRole"];
export type CompetitorRelation = Wire["CompetitorRelation"];
export type CompetitorStatus = Wire["CandidateStatus"];
export type CompetitorView = Wire["CompetitorView"];
export type CompetitorChannelView = Wire["CompetitorChannelView"];
export type CompetitorAddIn = Wire["CompetitorAddIn"];
export type CompetitorRejectIn = Wire["CompetitorRejectIn"];

// runs and events

export type RunKind = Wire["RunKind"];
// the run.awaiting_confirmation event hands an onboarding run to the confirm step; the reducer records it as a status
export type RunStatus = Wire["RunStatus"] | "awaiting_confirmation";
export type StageStatus = Wire["StageStatus"] | "retrying";

export interface StageRunView {
  stage: string;
  status: StageStatus;
  started_at: string | null;
  finished_at: string | null;
  message: string | null;
}

// view of the backend RunOut (progress_pct → percent)
export interface PipelineRun {
  id: string;
  client_id: string;
  kind: RunKind;
  status: RunStatus;
  percent: number;
  window_days: WindowDays;
  started_at: string | null;
  finished_at: string | null;
  stages?: StageRunView[];
  notices: RunNotice[];
  error: { code: string; message: string; retryable: boolean; request_id: string } | null;
}

export interface RunCreate {
  kind: RunKind;
  window_days?: WindowDays;
  force?: boolean;
  from_stage?: string;
  resume_of?: string;
}

export type RunEventType =
  | "run.queued"
  | "run.started"
  | "run.progress"
  | "run.awaiting_confirmation"
  | "run.succeeded"
  | "run.partial"
  | "run.failed"
  | "run.cancelled"
  | "run.superseded"
  | "stage.started"
  | "stage.step"
  | "stage.progress"
  | "stage.retrying"
  | "stage.reused"
  | "stage.succeeded"
  | "stage.degraded"
  | "stage.failed"
  | "stage.skipped"
  | "insight"
  | "notice"
  | "results.published";

export interface Counts {
  done: number;
  total: number | null;
  unit: string;
}

// age_band_proxy is an estimate from the video's age band, not a measured day-N value
export type AgeMethod = "observed" | "interpolated" | "owner_analytics" | "age_band_proxy" | "pending" | "unavailable";

export interface ViewsAtAgeRef {
  age_days: number;
  views: number | null;
  method: AgeMethod;
  label: string;
}

export interface ViewsAtAgePoint {
  age_days: number;
  views: number;
  method: AgeMethod;
}

export interface ViewsAtAgeCurve {
  video_id: string;
  title: string;
  channel_title: string;
  outlier_ratio: number | null;
  points: ViewsAtAgePoint[];
}

// mirrors backend app/domain/runs.py EventPayload; unknown kinds are ignored by the reducer
export type EventPayload =
  | { kind: "source_fallback"; from_source: string; to_source: string; reason?: string | null }
  | { kind: "page_read"; url: string; path: string; title: string | null; page_type: "service" | "about" | "location" | "pricing" | "blog" | "other" }
  | { kind: "page_skipped"; url: string; reason: "robots" | "timeout" | "non_html" | "duplicate" | "budget" | "blocked" }
  | { kind: "profile_field"; field: "offerings" | "value_props" | "locations" | "conversion_actions" | "buyer_questions"; count: number; preview: string[] }
  | { kind: "channel_resolved"; channel_id: string; title: string; handle: string | null; thumbnail_url: string | null; subscriber_count: number | null; verified_by: ChannelVerifiedBy }
  | { kind: "competitor_candidate"; candidate_id: string; name: string; channel_id: string | null; thumbnail_url: string | null; source: "web_search" | "youtube_search" | "profile" | "llm" | "user"; evidence_count: number }
  | { kind: "videos_fetched"; channel_id: string; channel_title: string; count: number; window_days: number }
  | { kind: "snapshot_progress"; videos_tracked: number; snapshots_taken: number }
  | { kind: "topic_summary"; topic_ref_id: string; label: string; matched_videos: number; review_flagged: number }
  | { kind: "outlier_found"; video_id: string; channel_title: string; title: string; outlier_ratio: number; views_at_age: ViewsAtAgeRef }
  | { kind: "trait_lift"; trait: string; lift: number; outliers_with_trait: number; sample: number }
  | { kind: "news_found"; news_id: string; headline: string; source_domain: string; published_at: string | null }
  | { kind: "card_written"; recommendation_id: string; type: string; summary: string; lens: string[]; confidence_label: string }
  | { kind: "refresh_suggested"; video_id: string; title: string; insertions: number }
  | { kind: "quota"; youtube_units_used: number; search_calls_used: number; remaining_today: number }
  | { kind: "fallback"; component: "llm" | "tavily" | "crawler"; reason: string; mode: "template" | "secondary_provider" | "skipped" };

export type PayloadKind = EventPayload["kind"];

export interface RunEvent {
  run_id: string;
  client_id: string;
  seq: number;
  type: RunEventType;
  stage: string | null;
  step: string | null;
  status: StageStatus | RunStatus | null;
  level: "info" | "warning" | "error";
  message: string;
  counts: Counts | null;
  percent: number | null;
  stage_percent: number | null;
  ts: string;
  payload: EventPayload | null;
}

export interface RunEventsPage {
  items: RunEvent[];
  last_seq: number;
  run_status: RunStatus;
}

export interface ConfirmSetupOut {
  run: PipelineRun;
  validation: { competitors_confirmed: number; keywords_active: number; buyer_questions_active: number };
}

// recommendations (contract 1.1)

export type RecType =
  | "video_idea"
  | "title_pattern"
  | "thumbnail_pattern"
  | "format"
  | "creator"
  | "news_opportunity"
  | "description_refresh";

export type ConfidenceLabel = Wire["ConfidenceLabel"];

export interface Confidence {
  label: ConfidenceLabel;
  outliers: number;
  channels: number;
  basis?: "standard" | "first_mover" | "rule_based";
  caps_applied?: string[];
}

export type ReferenceVideo = Wire["ReferenceVideo"];

export interface RecommendationOut {
  id: string;
  type: RecType;
  summary: string;
  lens: Lens[];
  buyer_question: string | null;
  why_it_works: string[];
  reference_videos: ReferenceVideo[];
  fit_to_client: string;
  expected_range_vs_baseline: string | null;
  confidence: Confidence;
  window_days: WindowDays;
  generated_at: string;
  stable_key?: string;
  run_id?: string;
  rendered_by?: "llm" | "template";
  topic_ref_ids?: string[];
  feedback?: RecommendationFeedback | null;
}

export type FeedbackIn = Wire["FeedbackIn"];
export type FeedbackStatus = NonNullable<FeedbackIn["status"]>;
export type FeedbackReason = NonNullable<FeedbackIn["reason"]>;

export interface RecommendationFeedback {
  status: FeedbackStatus | null;
  reason?: FeedbackReason | null;
  linked_video_id?: string | null;
}

export type WhiteSpaceNote = Wire["WhiteSpaceNote"];

// news, refresh, creators

export type Urgency = Wire["NewsOpportunityView"]["urgency"];
export type NewsOpportunityView = Wire["NewsOpportunityView"];
export type NewsSignal = Wire["NewsSignal"];
// fewer than 3 reference videos (owner decision o3): listed apart from recommendations as first-mover signals
export type EarlySignalView = Wire["EarlySignalView"];

export type InsertionPosition = Wire["InsertionPosition"];
export type InsertionOp = Wire["InsertionOp"];
export type RefreshSuggestionView = Wire["RefreshSuggestionView"];
export type RefreshAppliedIn = Wire["RefreshAppliedIn"];
// rule-based fixes, kept apart from refresh suggestions (owner decision o3)
export type HygieneData = Wire["HygieneData"];

// grouped by relevance and sorted by one user-chosen criterion server-side; there is no rank
export type CreatorView = Wire["CreatorView"];
export type CreatorsData = Wire["CreatorsData"];

// analytics (06 payloads, display fields only)

export interface OwnVideoPoint {
  video_id: string;
  title: string;
  published_at: string;
  ratio: number | null;
  method: AgeMethod;
  segment: VideoMetricsView["segment"];
}

export interface OwnPerformanceReport {
  median_ratio_window: number | null;
  median_ratio_prev_window: number | null;
  comparison_age_days: number;
  videos_in_window: number;
  method_mix: Partial<Record<AgeMethod, number>>;
  videos: OwnVideoPoint[];
  baseline: { age_days: number; views: number | null; method: AgeMethod; n: number } | null;
  curves: ViewsAtAgeCurve[];
  best: VideoMetricsView[];
  worst: VideoMetricsView[];
}

export interface OwnPerformance {
  youtube_public: OwnPerformanceReport | null;
  youtube_analytics: { top_search_terms: string[] } | null;
  website: { conversion_actions_seen: number } | null;
}

export interface VideoMetricsView {
  video_id: string;
  channel_id: string;
  channel_title: string;
  title: string;
  published_at: string;
  views_at_age: ViewsAtAgeRef;
  outlier_ratio: number | null;
  is_outlier: boolean;
  segment: "long_form" | "short_form" | "live";
  channel_baseline_views_at_age?: number | null;
}

export interface ChannelScorecard {
  channel: ChannelRef;
  role: CompetitorRole;
  // the analytics sheet carries no relation; it's joined from the competitor list when known
  relation: CompetitorRelation | null;
  uploads_per_week: number;
  median_ratio: number | null;
  outliers_in_window: number;
  videos_in_window: number;
  days_since_last_upload: number;
  longest_gap_days: number | null;
  subscriber_hidden: boolean;
  subscriber_band: string;
  segment_mix: Partial<Record<VideoMetricsView["segment"], number>>;
  median_views_at_age: { age_days: number; views: number | null; method: AgeMethod } | null;
  // outliers of videos, with the wilson interval as counts of the same denominator
  outlier_hit_rate: { outliers: number; videos: number; likely_low: number; likely_high: number };
  top_outliers: VideoMetricsView[];
}

export interface CadenceWeek {
  week_start: string;
  uploads: number;
}

export interface TraitLiftView {
  trait_key: string;
  label: string;
  outliers_with: number;
  outliers_total: number;
  n_with: number;
  n_total: number;
  lift: number;
  status: "supported" | "exploratory";
}

export interface CompetitorDetail {
  scorecard: ChannelScorecard;
  // the client's own sheet for side-by-side reading; null when the client has no channel
  you: ChannelScorecard | null;
  cadence_series: CadenceWeek[];
  curves: ViewsAtAgeCurve[];
  top_outliers: VideoMetricsView[];
  trait_lift: TraitLiftView[];
  topic_coverage: { topic_ref_id: string; topic: string; videos: number; outliers: number; client_videos: number; recommendation_id: string | null }[];
  collaborations: { video_id: string; handle_or_name: string; kind: "mention" | "disclosed_paid" }[];
}

// counts only (06 GapMatrix): buyer questions as rows, tracked channels as columns
export interface GapCellView {
  n_videos: number;
  n_outliers: number;
  best_ratio: number | null;
}

export interface GapMatrixView {
  topics: { id: string; label: string }[];
  channels: { channel_id: string; title: string }[];
  cells: Record<string, Record<string, GapCellView>>;
  own: Record<string, number>;
}

export interface TrendingReport {
  items: (VideoMetricsView & { views_per_day: number })[];
  rising_topics: { topic_ref_id: string; label: string; uploads_prev: number; uploads_now: number; outliers_prev: number; outliers_now: number }[];
}


// workspace llm settings (owner only); api keys are write-only and never returned

export type LlmTier = "low" | "medium" | "high";
export type LlmProviderId = Wire["LLMSettingsIn"]["provider_order"] extends (infer T)[] | null | undefined ? T : never;
export type LlmSettings = Wire["LLMSettingsOut"];
export type LlmSettingsIn = Wire["LLMSettingsIn"];
export type LlmProviderOut = Wire["LLMProviderOut"];
export type LlmProviderIn = Wire["LLMProviderIn"];

// proposed: no backend endpoint yet, served by mock mode only
export interface NotificationSettings {
  email: string | null;
  first_brief_ready: boolean;
  daily_digest: boolean;
  news_closing_soon: boolean;
}

// ops and evaluation (12's report objects are not frozen yet)

export interface OpsQuotaOut {
  youtube: { pt_day: string; units_used: number; units_limit: number; search_calls_used: number; search_calls_limit: number; reset_at: string };
  llm: { usd_today: number };
  tavily: { credits_today: number };
}

export interface ReviewRating {
  recommendation_id: string;
  reviewer: string;
  specific: number;
  evidenced: number;
  relevant: number;
  actionable: number;
  not_obvious: number;
  comment?: string;
}

export interface OutlierLabel {
  video_id: string;
  is_outlier: boolean;
  labeller: string;
  note?: string;
}
