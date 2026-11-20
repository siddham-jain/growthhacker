// hand-written from plan 11 §3.3–3.4 and plan 99 §4 until the backend publishes /api/v1/openapi.json

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
  level: "info" | "warning" | "error";
  message: string;
  suggest?: { widen_window_to?: WindowDays };
}

export interface ResultEnvelope<T> {
  data: T;
  window_days: WindowDays;
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

// clients

export type ClientLifecycle =
  | "onboarding"
  | "awaiting_confirmation"
  | "analyzing"
  | "active"
  | "paused"
  | "failed"
  | "deleting";

export interface Market {
  country: string;
  language: string;
  timezone: string;
}

export type ChannelVerifiedBy = "website_link" | "channel_link" | "oauth" | "manual";

export interface ChannelRef {
  channel_id: string;
  title: string;
  handle: string;
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

export type ClientCreated = Client & { onboarding_run?: PipelineRun };

export type ClientPatch = Partial<Pick<Client, "name" | "window_days" | "market" | "refresh_hour_local" | "paused">>;

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

export interface ProfileSource {
  url: string;
  title: string;
  fetched_at: string;
  source_kind: ContextSourceKind;
  snapshot_at?: string | null;
  pages: number;
}

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

export type TopicStatus = "suggested" | "active" | "rejected";

export interface KeywordItem {
  id: string;
  text: string;
  status: TopicStatus;
  source: "profile" | "user" | "spotlit";
  priority: number;
  created_at: string;
}

export interface BuyerQuestionItem {
  id: string;
  text: string;
  status: TopicStatus;
  source: "profile" | "user" | "spotlit_ai_tracking";
  intent_stage: "awareness" | "consideration" | "decision" | null;
  cited_video_ids: string[];
  priority: number;
  created_at: string;
}

export interface TopicCreate {
  text: string;
  priority?: number;
  status?: TopicStatus;
}

export interface BulkStatusIn {
  ids: string[];
  status: "active" | "rejected";
}

// competitors (fields beyond 11's CompetitorView follow plan 13 §13.4)

export type CompetitorRole = "competitor_set" | "niche_set" | "benchmark_set";
export type CompetitorRelation = "direct" | "aggregator" | "content";

export interface CompetitorView {
  id: string;
  status: "suggested" | "confirmed" | "rejected";
  channel: ChannelRef | null;
  name: string;
  source: "web_search" | "youtube_search" | "profile" | "user";
  score: number;
  evidence: { kind: string; url: string | null; text: string }[];
  reject_reason: string | null;
  added_by: "system" | "user";
  role: CompetitorRole;
  relation: CompetitorRelation;
  business_relevance: "high" | "medium" | "low";
  youtube_relevance: number;
  evidence_summary: string;
  uploads_90d: number | null;
  last_upload_at: string | null;
  preview_video_ids: string[];
}

export interface CompetitorAddIn {
  channel_ref: string;
  name?: string;
}

export interface CompetitorRejectIn {
  reason?: "not_competitor" | "wrong_channel" | "too_big" | "too_small" | "other";
  note?: string;
}

// runs and events

export type RunKind = "analysis" | "daily_refresh" | "onboarding" | "news_scan" | "cards" | "recrawl";
export type RunStatus =
  | "queued"
  | "running"
  | "awaiting_confirmation"
  | "succeeded"
  | "partial"
  | "failed"
  | "cancelled"
  | "superseded";
export type StageStatus =
  | "pending"
  | "running"
  | "retrying"
  | "reused"
  | "succeeded"
  | "degraded"
  | "failed"
  | "skipped";

export interface StageRunView {
  stage: string;
  status: StageStatus;
  started_at: string | null;
  finished_at: string | null;
  message: string | null;
}

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

export interface ViewsAtAgeRef {
  age_days: number;
  views: number | null;
  method: "observed" | "interpolated" | "owner_analytics" | "age_band_proxy" | "pending" | "unavailable";
  label: string;
}

// source_* kinds carry the 03 §14 cascade events (crawl.source_started / crawl.blocked / crawl.fallback)
export type EventPayload =
  | { kind: "source_started"; tier: number; source_kind: ContextSourceKind; target: string }
  | { kind: "source_blocked"; source_kind: ContextSourceKind; reason: string }
  | { kind: "source_fallback"; from: ContextSourceKind; to: ContextSourceKind }
  | { kind: "page_read"; url: string; path: string; title: string; page_type: "service" | "about" | "location" | "pricing" | "blog" | "other"; source_kind?: ContextSourceKind }
  | { kind: "page_skipped"; url: string; reason: "robots" | "timeout" | "non_html" | "duplicate" | "budget" | "blocked" }
  | { kind: "profile_field"; field: "offerings" | "value_props" | "locations" | "conversion_actions" | "buyer_questions"; count: number; preview: string[]; source_kinds?: ContextSourceKind[] }
  | { kind: "channel_resolved"; channel_id: string; title: string; handle: string; thumbnail_url: string | null; subscriber_count: number | null; verified_by: ChannelVerifiedBy }
  | { kind: "competitor_candidate"; candidate_id: string; name: string; channel_id: string | null; thumbnail_url: string | null; source: "web_search" | "youtube_search" | "profile"; evidence_count: number }
  | { kind: "videos_fetched"; channel_id: string; channel_title: string; count: number; window_days: number }
  | { kind: "snapshot_progress"; videos_tracked: number; snapshots_taken: number }
  | { kind: "topic_summary"; topic_ref_id: string; label: string; matched_videos: number; review_flagged: boolean }
  | { kind: "outlier_found"; video_id: string; channel_title: string; title: string; outlier_ratio: number; views_at_age: ViewsAtAgeRef }
  | { kind: "trait_lift"; trait: string; lift: number; outliers_with_trait: number; sample: number }
  | { kind: "news_found"; news_id: string; headline: string; source_domain: string; published_at: string }
  | { kind: "card_written"; recommendation_id: string; type: RecType; summary: string; lens: Lens[]; confidence_label: ConfidenceLabel }
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

export type ConfidenceLabel = "High" | "Medium" | "Low";

export interface Confidence {
  label: ConfidenceLabel;
  outliers: number;
  channels: number;
  basis?: "standard" | "first_mover" | "rule_based";
  caps_applied?: string[];
}

export interface ReferenceVideo {
  video_id: string;
  url: string;
  title: string;
  channel: string;
  channel_id: string;
  channel_role?: "own" | "competitor" | "niche" | "benchmark" | "creator";
  published_at: string;
  views_day7: number | null;
  views_at_age?: ViewsAtAgeRef;
  outlier_ratio: number | null;
  thumbnail_url?: string | null;
}

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
  feedback?: { rating: "up" | "down" | null; status: FeedbackStatus | null };
}

export type FeedbackStatus = "saved" | "dismissed" | "done";

export interface FeedbackIn {
  rating?: "up" | "down";
  reasons?: ("not_relevant" | "too_generic" | "already_made" | "wrong_data" | "great")[];
  status?: FeedbackStatus;
  note?: string;
}

export interface WhiteSpaceNote {
  buyer_question: string;
  videos_checked: number;
  channels_checked: number;
  window_days: WindowDays;
}

// news, refresh, creators

export type Urgency = "now" | "this_week" | "evergreen_window";

export interface NewsOpportunityView {
  id: string;
  news: { news_id: string; headline: string; url: string; source_domain: string; published_at: string };
  recommendation_id: string | null;
  summary: string;
  relevance: number;
  offering_refs: string[];
  demand_check: { youtube_results_7d: number; outliers: number };
  status: "new" | "dismissed" | "used";
  urgency: Urgency;
  expires_at: string;
  reference_count: number;
  review_required: boolean;
}

export type InsertionPosition = "top_line" | "after_first_paragraph" | "before_hashtag_line" | "before_links_block" | "append";

export interface InsertionOp {
  op_id: string;
  kind: "insert" | "link_fix";
  position: InsertionPosition;
  anchor_excerpt: string;
  old_text?: string;
  text: string;
  keywords_added: string[];
}

export interface RefreshSuggestionView {
  id: string;
  video: { video_id: string; title: string; published_at: string; url: string; views: number };
  warranted_because: string[];
  operations: InsertionOp[];
  inserted_bytes: number;
  resulting_bytes: number;
  expected_effect: string;
  basis: "keyword" | "news" | "rule_based";
  review_required: boolean;
  status: "open" | "applied" | "dismissed";
  applied_at: string | null;
}

export interface RefreshAppliedIn {
  applied_at?: string;
  applied_text?: string;
}

export interface CreatorView {
  channel: ChannelRef;
  rank: number;
  subscriber_band: string;
  cadence_label: string;
  relevance: { matched_videos: number; recent_videos: number; topics: string[] };
  recent_performance_vs_niche: string;
  consistency: string;
  collaborations: { video_id: string; competitor_name: string; label: "mention" | "disclosed_paid_promotion"; date: string }[];
  reference_videos: ReferenceVideo[];
  recommendation_id: string | null;
}

// analytics (06 payloads, display fields only)

export interface OwnPerformanceReport {
  median_ratio_window: number | null;
  median_ratio_prev_window: number | null;
  comparison_age_days: number;
  videos_in_window: number;
  best: VideoMetricsView[];
  worst: VideoMetricsView[];
}

export interface OwnPerformance {
  youtube_public: OwnPerformanceReport;
  youtube_analytics: { subscribers_gained: number; top_search_terms: string[] } | null;
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
}

export interface ChannelScorecard {
  channel: ChannelRef;
  role: CompetitorRole;
  relation: CompetitorRelation;
  uploads_per_week: number;
  median_ratio: number | null;
  outliers_in_window: number;
  videos_in_window: number;
  days_since_last_upload: number;
  subscriber_hidden: boolean;
  top_outliers: VideoMetricsView[];
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
  top_outliers: VideoMetricsView[];
  trait_lift: TraitLiftView[];
  topic_coverage: { topic: string; videos: number; outliers: number; client_videos: number }[];
}

export interface TrendingReport {
  items: (VideoMetricsView & { velocity_per_hour: number })[];
  rising_topics: { label: string; uploads_prev: number; uploads_now: number; outliers_prev: number; outliers_now: number }[];
}

export interface TopicCoverage {
  topic_ref: { id: string; label: string };
  own_videos: number;
  competitor_videos: number;
  outliers: number;
  review_flagged: boolean;
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
