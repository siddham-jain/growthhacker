import type {
  AnswerCitations,
  BulkStatusIn,
  BusinessProfileView,
  BuyerQuestionItem,
  ChannelLandscape,
  ChannelResolveOut,
  ChannelScorecard,
  Client,
  ClientCreate,
  ClientCreated,
  ClientLifecycle,
  ClientPatch,
  CompetitorAddIn,
  CompetitorDetail,
  CompetitorRejectIn,
  CompetitorView,
  ConfirmSetupOut,
  FeedbackIn,
  FeedbackStatus,
  GapMatrixView,
  LlmSettings,
  LlmSettingsIn,
  LlmProviderId,
  LlmTestOut,
  NotificationSettingsPatch,
  BuyerQuestionCreate,
  CreatorsData,
  EarlySignalView,
  HygieneData,
  KeywordCreate,
  NewsSignal,
  TopicPatch,
  LoginIn,
  Me,
  NotificationSettings,
  CompetitorRole,
  KeywordItem,
  Lens,
  NewsOpportunityView,
  OpsQuotaOut,
  OutlierLabel,
  OwnPerformance,
  Page,
  PipelineRun,
  ProfilePatch,
  RecommendationOut,
  RecType,
  RefreshAppliedIn,
  RefreshSuggestionView,
  ResultEnvelope,
  ReviewRating,
  RunCreate,
  RunEventsPage,
  RunKind,
  RunStatus,
  TopicStatus,
  TraitLiftView,
  TrendingReport,
  TrendScan,
  VideoMetricsView,
  WhiteSpaceNote,
  WindowDays,
} from "./types";

export interface Paging {
  cursor?: string;
  limit?: number;
}

export interface RecommendationQuery extends Paging {
  type?: RecType;
  lens?: Lens;
  window_days?: WindowDays;
  buyer_question_id?: string;
  confidence?: "High" | "Medium" | "Low";
  status?: FeedbackStatus;
  format?: "contract" | "full";
}

export type RecommendationPage = ResultEnvelope<Page<RecommendationOut>> & { white_space?: WhiteSpaceNote[] };

// 200 returns cards; 202 means the window is still being written by `cards_run`
export type RecommendationsResult =
  | { state: "ready"; envelope: RecommendationPage }
  | { state: "writing"; cards_run: PipelineRun };

export type TopicSummary = import("./schema").components["schemas"]["TopicSummary"];

export interface TopicApi<T, C> {
  list(clientId: string, status?: TopicStatus): Promise<Page<T>>;
  create(clientId: string, input: C): Promise<T>;
  update(clientId: string, id: string, patch: TopicPatch): Promise<T>;
  remove(clientId: string, id: string): Promise<void>;
  bulkStatus(clientId: string, input: BulkStatusIn): Promise<{ updated: number }>;
}

export interface GrowthHackerApi {
  auth: {
    login(input: LoginIn): Promise<{ status: string }>;
    logout(): Promise<void>;
    me(): Promise<Me>;
    switchWorkspace(workspaceId: string): Promise<{ status: string }>;
  };
  clients: {
    list(query?: Paging & { lifecycle?: ClientLifecycle; q?: string }): Promise<Page<Client>>;
    create(input: ClientCreate, idempotencyKey: string): Promise<ClientCreated>;
    get(clientId: string): Promise<Client>;
    update(clientId: string, patch: ClientPatch, version: number): Promise<Client>;
    attachChannel(clientId: string, channelId: string | null): Promise<Client>;
    remove(clientId: string): Promise<{ status: "deleting" }>;
    resolveChannel(clientId: string, channelRef: string): Promise<ChannelResolveOut>;
    restartOnboarding(clientId: string, input: { website_url?: string; channel_ref?: string }): Promise<PipelineRun>;
    confirmSetup(clientId: string, windowDays: WindowDays | undefined, idempotencyKey: string): Promise<ConfirmSetupOut>;
  };
  profile: {
    get(clientId: string): Promise<BusinessProfileView>;
    update(clientId: string, patch: ProfilePatch, version: number): Promise<BusinessProfileView>;
    recrawl(clientId: string): Promise<PipelineRun>;
  };
  keywords: TopicApi<KeywordItem, KeywordCreate>;
  buyerQuestions: TopicApi<BuyerQuestionItem, BuyerQuestionCreate>;
  competitors: {
    list(clientId: string, status?: CompetitorView["status"]): Promise<Page<CompetitorView>>;
    add(clientId: string, input: CompetitorAddIn, idempotencyKey: string): Promise<CompetitorView>;
    suggest(clientId: string): Promise<PipelineRun>;
    confirm(clientId: string, competitorId: string): Promise<CompetitorView>;
    reject(clientId: string, competitorId: string, input: CompetitorRejectIn): Promise<CompetitorView>;
    // proposed in plan 13 §13.4: move a channel between the competitor, niche and benchmark sets
    move(clientId: string, competitorId: string, role: CompetitorRole): Promise<CompetitorView>;
    remove(clientId: string, competitorId: string): Promise<void>;
  };
  runs: {
    create(clientId: string, input: RunCreate, idempotencyKey: string): Promise<PipelineRun>;
    list(clientId: string, query?: Paging & { kind?: RunKind; status?: RunStatus }): Promise<Page<PipelineRun>>;
    get(runId: string): Promise<PipelineRun>;
    cancel(runId: string, reason?: string): Promise<PipelineRun>;
    retry(runId: string, fromStage?: string): Promise<PipelineRun>;
    events(runId: string, afterSeq: number, limit?: number): Promise<RunEventsPage>;
    streamToken(runId: string): Promise<{ token: string; expires_at: string }>;
  };
  analytics: {
    ownPerformance(clientId: string, windowDays: WindowDays): Promise<ResultEnvelope<OwnPerformance>>;
    competitors(clientId: string, windowDays: WindowDays, sort?: string): Promise<ResultEnvelope<ChannelScorecard[]>>;
    competitor(clientId: string, channelId: string, windowDays: WindowDays): Promise<ResultEnvelope<CompetitorDetail>>;
    trending(clientId: string, windowDays: WindowDays): Promise<ResultEnvelope<TrendingReport>>;
    outliers(clientId: string, query: Paging & { scope: "own" | "competitors" | "niche"; window_days: WindowDays; min_ratio?: number }): Promise<ResultEnvelope<Page<VideoMetricsView>>>;
    patterns(clientId: string, scope: "competitors" | "niche", windowDays: WindowDays): Promise<ResultEnvelope<TraitLiftView[]>>;
    topics(clientId: string, windowDays: WindowDays): Promise<ResultEnvelope<TopicSummary[]>>;
    // buyer questions × tracked competitors, counts only
    gaps(clientId: string, windowDays: WindowDays): Promise<ResultEnvelope<GapMatrixView>>;
    // own channel and competitors side by side over a fixed 90 days, growth over 365
    landscape(clientId: string): Promise<ResultEnvelope<ChannelLandscape>>;
    // which buyer questions' web answers cite your videos, a competitor's, or neither
    answerCitations(clientId: string): Promise<AnswerCitations>;
  };
  trends: {
    // newest trend scan across runs: keywords, their recent videos and the ideas built on them
    latest(clientId: string): Promise<ResultEnvelope<TrendScan>>;
  };
  recommendations: {
    list(clientId: string, query: RecommendationQuery): Promise<RecommendationsResult>;
    get(recommendationId: string): Promise<RecommendationOut>;
    feedback(recommendationId: string, input: FeedbackIn, idempotencyKey: string): Promise<{ feedback: FeedbackIn }>;
  };
  news: {
    opportunities(clientId: string, query?: Paging & { status?: NewsOpportunityView["status"] }): Promise<ResultEnvelope<Page<NewsOpportunityView>>>;
    signals(clientId: string, query?: Paging): Promise<Page<NewsSignal>>;
    earlySignals(clientId: string, windowDays: WindowDays): Promise<ResultEnvelope<EarlySignalView[]>>;
    dismiss(opportunityId: string): Promise<NewsOpportunityView>;
  };
  refresh: {
    list(clientId: string, query?: Paging & { status?: RefreshSuggestionView["status"]; video_id?: string }): Promise<ResultEnvelope<Page<RefreshSuggestionView>>>;
    applied(suggestionId: string, input: RefreshAppliedIn): Promise<RefreshSuggestionView>;
    dismiss(suggestionId: string): Promise<RefreshSuggestionView>;
    hygiene(clientId: string): Promise<ResultEnvelope<HygieneData>>;
  };
  creators: {
    list(clientId: string, windowDays: WindowDays, query?: Paging & { sort?: string }): Promise<ResultEnvelope<CreatorsData>>;
  };
  settings: {
    llm(): Promise<LlmSettings>;
    updateLlm(input: LlmSettingsIn): Promise<LlmSettings>;
    // checks the saved key and model ids against the provider; spends no tokens
    testLlm(provider?: LlmProviderId): Promise<LlmTestOut>;
    notifications(clientId: string): Promise<NotificationSettings>;
    updateNotifications(clientId: string, patch: NotificationSettingsPatch): Promise<NotificationSettings>;
  };
  integrations: {
    youtubeConnect(clientId: string): Promise<{ authorization_url: string }>;
    youtubeDisconnect(clientId: string): Promise<void>;
  };
  evaluation: {
    startBacktest(input: { client_id: string; frozen_at: string; window_days: WindowDays }): Promise<PipelineRun>;
    outlierLabels(clientId: string, query?: Paging): Promise<Page<OutlierLabel>>;
    putOutlierLabel(videoId: string, label: Omit<OutlierLabel, "video_id">): Promise<OutlierLabel>;
    outlierPrecision(clientId: string): Promise<{ precision: number; recall: number; n: number; threshold: number }>;
    submitReview(rating: ReviewRating): Promise<void>;
  };
  ops: {
    quota(date?: string): Promise<OpsQuotaOut>;
  };
}
