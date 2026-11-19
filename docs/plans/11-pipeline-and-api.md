# 11 — Pipeline orchestration, workers, API & progress streaming

Status: plan (no implementation). Consistent with `00-shared-context.md`; deviations are raised in §13.

---

## 1. Purpose & business value

This component turns the separate engines (02–10) into a product a business can use:

- **Time to first value.** Onboarding a website or channel starts a run straight away. The UI shows live, specific progress ("Reading /services/cardiology", "Found 6 competitor candidates", "3.4x outlier on Practo"). The business sees the engine studying *its* business, which builds trust before the first recommendation exists. Waiting becomes part of the product. For a hospital marketing lead or a dental clinic owner, seeing that the engine read their service pages is what makes the later "make this video" advice believable. That belief is what drives conversions.
- **Fresh, reliable recommendations every day.** Daily refresh runs, adaptive snapshots, news radar and the 30-day retention cron keep evidence inside the window, keep it fair (same-age views) and keep it within policy. When a niche moves (a frontier lab ships a model, a regulator changes a rule), the news radar turns that into a timely video idea while it still matters.
- **Speed where it matters.** Slow I/O (crawl, ingest) is separated from fast deterministic compute and the LLM card writer. That is how we hit **< 2 minutes per client to generate recommendations**. The run measures it and reports it to the ops report (12).
- **A clean, stable, versioned API.** `/api/v1` serves the output contract to the Spotlit UI (Mira) and to our Next.js frontend. It is idempotent, paginated and uses one error model, so the frontend and external integrations can rely on it.
- **Correct under failure.** Checkpointing, resume-from-failed-stage, dedupe, leases and honest partial results ("YouTube quota reached — showing data from 19 Nov; full refresh after 13:30 IST") mean users never see half-written or silently wrong results.

## 2. Scope / non-scope

**In scope**
- Pipeline framework: `Stage` Protocol, `StagePolicy`, `RunContext`, typed artifacts, `ArtifactStore`, DAG runner (parallel, checkpointed, resumable, cancellable, budgeted), `StageRegistry` and `PipelineRegistry`.
- Run kinds, run state machine, the client lifecycle transitions that runs drive, and the reuse matrix for daily refresh.
- Workers: arq `WorkerSettings` per queue, cron schedulers (daily refresh, snapshot tick, news radar tick, retention, run reconciler), dedupe, leases, distributed locks, global concurrency limiters, fairness, backpressure, graceful shutdown, poison-job handling.
- Progress: `RunEvent` schema, `ProgressReporter` adapter (DB persist + Redis pub/sub), the SSE endpoints with replay, and a JSON polling fallback.
- REST API `/api/v1`: all routers, DTO schemas, pagination, filtering, the problem+json error model, idempotency keys, auth dependencies, rate limiting, CORS, OpenAPI conventions and the deprecation policy.
- Tables: `pipeline_runs`, `stage_runs`, `run_artifacts`, `run_events`, `idempotency_keys`, `client_schedules`, `active_result_sets`, `recommendation_feedback`.

**Not in scope (owned elsewhere; consumed through ports and services)**
- What each stage computes: 02 (ingest/snapshots/quota/OAuth tokens), 03 (crawl/profile), 04 (competitors), 05 (topics), 06 (analytics), 07 (recommendations/quality gates), 08 (news), 09 (refresh), 10 (LLM/writer).
- Tenancy model, the users and api_keys tables, secret encryption, base settings, deployment manifests and the retention service implementation (01). 11 only *schedules* retention and covers its own tables.
- Evaluation logic (12). 11 exposes its endpoints and lets backtests reuse the runner with a frozen `as_of`.
- UI (13). 11 provides the event vocabulary the animations use.
- "Ask your analyst" chat (paused).

## 3. Inputs & outputs

### 3.1 Inputs consumed
| From | Object | Used by |
|---|---|---|
| 01 | `Settings`, `Container` (DI), `AsyncSessionFactory`, `RedisPool`, `Workspace`, `ApiPrincipal`, `RetentionService` | everything |
| 02 | `YouTubeGateway`, `QuotaManager` (reserve/commit/remaining, PT day), `IngestService`, `SnapshotScheduler`, `OwnAnalyticsService`, `ChannelResolver` | ingest/snapshot stages, ops endpoint, OAuth |
| 03 | `WebsiteIntelService` → `BusinessProfile`, crawled pages; `SiteCrawler` emits page callbacks | onboarding stages |
| 04 | `CompetitorDiscoveryService` → `list[CompetitorCandidate]` | onboarding, `competitors/suggest` |
| 05 | `TopicEngine` → `list[TopicMatch]` | `topics` stage |
| 06 | `AnalyticsEngine` → `VideoMetrics`, `VideoTraits`, `CommentSignals`, `TraitLift`, `ChannelScorecard`, `OwnPerformanceReport`, `TrendingReport` | `enrich`, `analytics` stages, analytics endpoints |
| 07 | `RecommendationEngine` → `list[RecommendationDraft]`; quality gates; `CreatorRanker` | `recommend` stage |
| 08 | `NewsRadar` → `list[NewsSignal]`, news_opportunity drafts | `news` stage, news cron |
| 09 | `RefreshEngine` → `list[DescriptionRefresh]` | `refresh` stage |
| 10 | `EvidenceWriter` → `Recommendation`; `LLMClient` usage callbacks (tokens, cost) | `write_cards` stage, budgets |

### 3.2 Domain objects owned (pydantic-style)

```
RunKind = Literal["onboarding", "analysis", "daily_refresh", "cards", "news_scan", "recrawl", "backtest"]
  # cards: write cards for a newly selected window; recrawl: deep/background crawl; backtest: 12, frozen as_of
RunTrigger = Literal["user", "schedule", "system", "resume", "evaluation"]
RunStatus = Literal["queued", "running", "cancelling", "succeeded", "partial", "failed", "cancelled", "superseded"]
  ACTIVE = {"queued", "running", "cancelling"}; TERMINAL = the rest
StageStatus = Literal["pending", "running", "succeeded", "reused", "degraded", "failed", "skipped", "cancelled", "interrupted"]
ClientLifecycle = Literal["created", "onboarding", "onboarding_failed", "awaiting_confirmation",
                          "analyzing", "active", "paused", "deleting"]   # column on clients (01), transitions driven here

RunConfigSnapshot:            # frozen at run creation; immutable for the run
  client_id: str; workspace_id: str
  config_version: int; config_hash: str           # sha256 of the canonical json below
  window_days: Literal[7, 30, 90]; windows_computed: list[int] = [7, 30, 90]
  own_channel_id: str | None; website_url: str | None
  competitor_channel_ids: list[str]               # confirmed only
  topic_refs: list[TopicRef]                      # active keywords + buyer questions (ids + text + version)
  topic_set_version: int
  freshness_profile: FreshnessProfile | None
  profile_version: int | None
  oauth_connected: bool
  market: Market                                  # country, language, timezone
  feature_flags: dict[str, bool]

StagePolicy:
  timeout_s: float                 # hard wall budget for one attempt
  soft_timeout_s: float | None     # stage is told to wrap up and return partial output
  max_attempts: int = 2            # in-process retries for transient errors
  backoff_s: tuple[float, ...] = (2, 8)
  retry_on: tuple[type[Exception], ...] = (TransientError,)
  critical: bool = True            # False → failure degrades the run instead of failing it
  idempotent: bool = True          # must be True for every stage (asserted at registration)
  reusable: bool = True            # may be skipped when the fingerprint matches a stored artifact
  budget: StageBudget

StageBudget:
  youtube_units: int | None; search_calls: int | None
  llm_usd: Decimal | None; llm_calls: int | None
  tavily_credits: int | None; pages: int | None

RunBudget: wall_s: float; youtube_units: int; search_calls: int; llm_usd: Decimal; tavily_credits: int

UsageLedger:                       # accumulated per stage and per run, persisted in stage_runs.usage / pipeline_runs.usage
  youtube_units: int; search_calls: int; llm_input_tokens: int; llm_output_tokens: int
  llm_cache_read_tokens: int; llm_usd: Decimal; tavily_credits: int; http_pages: int

ArtifactKey[T]:                    # typed handle; T is a pydantic model
  name: str; model: type[T]; schema_version: int; contains_api_data: bool

StageResult[T]:
  status: Literal["succeeded", "degraded", "skipped"]
  artifact: T | None
  counts: dict[str, int]           # e.g. {"videos_fetched": 412}
  notices: list[RunNotice]

RunNotice:                         # user-facing caveat attached to a run and propagated to result envelopes
  code: Literal["quota_limited", "llm_fallback", "partial_profile", "thin_data", "stale_config",
                "competitor_unavailable", "news_skipped", "oauth_revoked", "data_stale"]
  stage: str | None; message: str; detail: dict; suggest: dict | None   # e.g. {"widen_window_to": 30}

PipelineRun (read model):
  id: str ("run_" + ULID); workspace_id; client_id; kind: RunKind; trigger: RunTrigger
  status: RunStatus; attempt: int; resume_of_run_id: str | None
  window_days: int; as_of: datetime (UTC, DB clock); config_hash: str
  queue: str; priority: int
  progress_pct: float (0–100, monotonic); current_stages: list[str]
  stages: list[StageRunView]; notices: list[RunNotice]
  usage: UsageLedger; budget: RunBudget
  timings: RunTimings               # queued_ms, run_ms, reco_ms (see §5.15)
  error: RunError | None
  created_at; started_at; finished_at; cancel_requested_at

StageRunView: stage; status: StageStatus; attempt; started_at; finished_at; duration_ms; counts; usage; error_code: str | None; reused_from_run_id: str | None

RunError: code: str ("stage_failed" | "budget_exceeded" | "infra_unavailable" | "poison" | "timeout" | "client_deleted" | ...); stage: str | None; message: str; retryable: bool
```

### 3.3 RunEvent (progress stream; persisted and fanned out)

```
RunEventType = Literal[
  "run.queued", "run.started", "run.progress", "run.awaiting_confirmation",
  "run.succeeded", "run.partial", "run.failed", "run.cancelled", "run.superseded",
  "stage.started", "stage.step", "stage.progress", "stage.retrying", "stage.reused",
  "stage.succeeded", "stage.degraded", "stage.failed", "stage.skipped",
  "insight", "notice"]

RunEvent:
  run_id: str; client_id: str
  seq: int                          # per-run, strictly increasing, gap-free among persisted events; SSE `id`
  type: RunEventType
  stage: str | None                 # "crawl", "ingest", ...
  step: str | None                  # stable machine step id: "crawl.page", "discover.search", "write.card"
  status: StageStatus | RunStatus | None
  level: Literal["info", "warning", "error"] = "info"
  message: str                      # human text from code templates, never LLM; ≤ 200 chars
  counts: Counts | None             # {done: int, total: int | None, unit: "pages"|"videos"|"channels"|"cards"|...}
  percent: float | None             # run-level 0–100, monotonic
  stage_percent: float | None
  ts: datetime                      # server time at emit (display only; ordering uses seq)
  payload: EventPayload | None      # discriminated by `kind`, ≤ 4 KB serialized

EventPayload (discriminated union on `kind`; frontend switches on it for animations):
  page_read          {url, path, title, page_type: "service"|"about"|"location"|"pricing"|"blog"|"other"}
  page_skipped       {url, reason: "robots"|"timeout"|"non_html"|"duplicate"|"budget"}
  profile_field      {field: "offerings"|"value_props"|"locations"|"conversion_actions"|"buyer_questions", count, preview: list[str] (≤5)}
  channel_resolved   {channel_id, title, handle, thumbnail_url, subscriber_count, verified_by: "website_link"|"channel_link"|"oauth"|"manual"}
  competitor_candidate {candidate_id, name, channel_id|None, thumbnail_url|None, source: "web_search"|"youtube_search"|"profile", evidence_count, score}
  videos_fetched     {channel_id, channel_title, count, window_days}
  snapshot_progress  {videos_tracked, snapshots_taken}
  topic_summary      {topic_ref_id, label, matched_videos, review_flagged}
  outlier_found      {video_id, channel_title, title, outlier_ratio, views_day7}
  trait_lift         {trait, lift, outliers_with_trait, sample}
  news_found         {news_id, headline, source_domain, published_at}
  card_written       {recommendation_id, type, summary, lens: list[str], confidence_label}
  refresh_suggested  {video_id, title, insertions: int}
  quota              {youtube_units_used, search_calls_used, remaining_today}
  fallback           {component: "llm"|"tavily"|"crawler", reason, mode: "template"|"secondary_provider"|"skipped"}
```

Heartbeats are SSE comment lines and are not persisted. Throttled `stage.progress` events are coalesced before persistence (§5.11).

### 3.4 API resources (outputs; exact DTOs)

Shared shapes:
```
Page[T]: items: list[T]; next_cursor: str | None; limit: int; total: int | None   # total only when cheap
Problem (application/problem+json, RFC 9457):
  type: str (URI, "https://growthhacker.dev/problems/<code>"); title: str; status: int; detail: str
  instance: str (request path + request id); code: str (stable machine code)
  request_id: str; errors: list[FieldError] | None; retry_after_s: int | None; extra: dict | None
FieldError: loc: list[str|int]; msg: str; type: str
ResultEnvelope[T]:                       # every analytics/recommendation read
  data: T; window_days: int; as_of: datetime; run_id: str; generated_at: datetime
  freshness: {data_through: datetime, age_hours: float, stale: bool, stale_reasons: list[str]}
  notices: list[RunNotice]
```

Resource DTOs (prefixed ULID ids; `channel_id`/`video_id` stay raw YouTube ids):
```
ClientCreate: name: str (1–120); website_url: HttpUrl | None; channel_ref: str | None  # url, @handle or UC id
              market: {country: ISO-3166-1 alpha-2, language: BCP-47, timezone: IANA}
              window_days: 7|30|90 = 30; start_onboarding: bool = True
              # validator: at least one of website_url/channel_ref
Client: id; name; website_url; own_channel: ChannelRef | None; lifecycle: ClientLifecycle
        window_days; market; refresh_hour_local: int = 6; paused: bool; oauth_connected: bool
        active_run_id: str | None; last_success_at: datetime | None; created_at; updated_at; version: int
ClientPatch (JSON merge patch): name?, window_days?, market?, refresh_hour_local?, paused?
ChannelRef: channel_id; title; handle; thumbnail_url; subscriber_count | None; verified: bool; verified_by

ChannelResolveIn: channel_ref: str
ChannelResolveOut: candidates: list[{channel: ChannelRef, ownership_evidence: list[str], confidence: "high"|"medium"|"low"}]

BusinessProfileView: profile: BusinessProfile (machine); overrides: dict; effective: BusinessProfile
                     version: int; completeness: float (0–1); partial: bool; sources: list[{url, title, fetched_at}]
ProfilePatch: JSON merge patch over `overrides` (offerings, value_propositions, locations, conversion_actions,
              niche_keywords, freshness_profile); header If-Match: "<version>"

KeywordItem: id; text (1–80); status: "suggested"|"active"|"rejected"; source: "profile"|"user"|"spotlit"; priority: 1–5; created_at
BuyerQuestionItem: id; text (5–200); status; source: "profile"|"user"|"spotlit_ai_tracking"; intent_stage: "awareness"|"consideration"|"decision"|None
                   cited_video_ids: list[str]; priority; created_at
KeywordCreate / BuyerQuestionCreate: text; priority?; status? = "active"
BulkStatusIn: ids: list[str] (≤200); status: "active"|"rejected"

CompetitorView: id; status: "suggested"|"confirmed"|"rejected"; channel: ChannelRef | None; name
                source; score: float; evidence: list[{kind, url|None, text}]; reject_reason | None; added_by: "system"|"user"
CompetitorAddIn: channel_ref: str; name?: str
CompetitorRejectIn: reason?: "not_competitor"|"wrong_channel"|"too_big"|"too_small"|"other"; note?: str
ConfirmSetupIn: window_days?: 7|30|90
ConfirmSetupOut: run: PipelineRun; validation: {competitors_confirmed, keywords_active, buyer_questions_active}

RunCreate: kind: "analysis"|"daily_refresh"|"onboarding"|"news_scan"|"cards"|"recrawl"
           window_days?: 7|30|90; force: bool = False   # force = ignore reusable artifacts
           from_stage?: str                              # only with resume_of
           resume_of?: str
RunCancelIn: reason?: str

RecommendationOut: (exact contract fields)
  id; type: "video_idea"|"title_pattern"|"thumbnail_pattern"|"format"|"creator"|"news_opportunity"|"description_refresh"
  summary; lens: list["views"|"conversion"]; buyer_question: str | None; why_it_works: list[str]
  reference_videos: list[{video_id, url, channel, channel_id, title, published_at, views_day7, outlier_ratio}]
  fit_to_client; expected_range_vs_baseline; confidence: {label: "High"|"Medium"|"Low", outliers, channels}
  window_days; generated_at
  # extensions (ignored by strict-contract consumers; omitted when ?format=contract)
  stable_key; run_id; rendered_by: "llm"|"template"; score: float; topic_ref_ids: list[str]
  feedback: {rating|None, status|None}; news: NewsRef | None; refresh: RefreshRef | None
FeedbackIn: rating?: "up"|"down"; reasons?: list["not_relevant"|"too_generic"|"already_made"|"wrong_data"|"great"]
            status?: "saved"|"dismissed"|"done"; note?: str (≤1000)

NewsOpportunityView: id; news: {news_id, headline, url, source_domain, published_at}; recommendation_id | None
                     relevance: float; offering_refs: list[str]; demand_check: {youtube_results_7d, outliers} ; status: "new"|"dismissed"|"used"
RefreshSuggestionView: id; video: {video_id, title, published_at, url}; warranted_because: list[str]
                       original_description_hash; insertions: list[{anchor: "append"|"after_line", line_index|None, text, keyword, reason, evidence_refs}]
                       preview_diff: str (unified, additive only); status: "open"|"applied"|"dismissed"; applied_at | None
RefreshAppliedIn: applied_at?: datetime; applied_text?: str
CreatorView: channel: ChannelRef; rank; relevance; recent_performance_vs_niche; consistency
             collaborations: list[{video_id, competitor_channel_id, label: "mention"|"disclosed_paid_promotion"}]
             reference_videos: list[ReferenceVideo]; recommendation_id
OpsQuotaOut: youtube: {pt_day, units_used, units_limit, search_calls_used, search_calls_limit, reset_at, by_client: list[{client_id, units, search_calls}]}
             llm: {usd_today, by_model: list[{model, input_tokens, output_tokens, cache_read_tokens, usd}]}
             tavily: {credits_today}; queues: list[{name, depth, oldest_age_s, in_progress, workers_alive}]
```

Analytics payloads (`OwnPerformanceReport`, `ChannelScorecard`, `TrendingReport`, `TraitLift`, `VideoMetrics`) are owned by 06. The API wraps them in `ResultEnvelope` and never reshapes the numbers.

## 4. Internal design

### 4.1 Module layout
```
app/pipeline/
  contracts.py      Stage, StagePolicy, StageBudget, StageResult, ArtifactKey, RunNotice (Protocols + models)
  context.py        RunContext, CancellationToken, BudgetLedger
  artifacts.py      ArtifactStore (Protocol) + PgArtifactStore
  registry.py       StageRegistry, PipelineRegistry, PipelineDefinition
  runner.py         DagRunner (generic; knows no stage names)
  fingerprint.py    stage input fingerprints
  lifecycle.py      RunStateMachine, ClientLifecycleMachine (pure transition tables)
  runs.py           RunService (create/dedupe/cancel/resume/supersede; the only writer of pipeline_runs)
  progress.py       ProgressReporter adapter: buffered persister + Redis publisher + throttler
  publication.py    ResultPublisher (atomic active_result_sets swap)
  stages/           one module per stage: thin adapters that call services 02–10
    onboarding.py   resolve_inputs, crawl_site, build_profile, prefetch_own_channel, discover_competitors, seed_topic_refs
    analysis.py     snapshot_config, ingest, niche_discovery, own_analytics, enrich, topics, analytics,
                    news, recommend, write_cards, refresh, finalize
app/workers/
  settings.py       InteractiveWorker, BatchWorker, MaintenanceWorker (arq WorkerSettings classes)
  jobs.py           run_pipeline(ctx, run_id, attempt), purge_client(ctx, client_id)
  cron.py           schedule_daily_refreshes, snapshot_tick, news_radar_tick, retention_sweep, reconcile_runs, ops_rollup
  queue.py          JobQueue Protocol + ArqJobQueue adapter (enqueue, abort, status, depth)
  limits.py         RedisSemaphore, TokenBucket, RedisLock (lease + renew + token-checked release)
  scheduler.py      DueScheduler: computes due clients from client_schedules (DB clock)
app/api/
  app.py            create_app(): lifespan, middleware, exception handlers, routers
  deps.py           get_session, get_principal, require_role, get_client_scoped, idempotency, rate_limit
  errors.py         AppError hierarchy → Problem
  pagination.py     cursor encode/decode (opaque base64url of sort keys + filter hash)
  sse.py            RunEventStream (replay + live + poll fallback), PubSubHub (one Redis subscription per process)
  v1/ clients.py profile.py topic_refs.py competitors.py runs.py events.py analytics.py
      recommendations.py news.py refresh.py creators.py evaluation.py ops.py integrations.py health.py
```

### 4.2 Core Protocols (signatures only)
```
class Stage(Protocol[TOut]):
    name: ClassVar[str]
    version: ClassVar[int]                         # bump → fingerprint changes → recompute
    depends_on: ClassVar[tuple[str, ...]]
    optional_deps: ClassVar[tuple[str, ...]]       # consumed if present (degraded/skipped upstream is OK)
    produces: ClassVar[ArtifactKey[TOut]]
    policy: ClassVar[StagePolicy]
    weight: ClassVar[float]                        # share of run progress
    def enabled(self, snapshot: RunConfigSnapshot) -> bool: ...
    def fingerprint_inputs(self, ctx: RunContext) -> Mapping[str, Any]: ...   # extra inputs beyond upstream hashes
    async def run(self, ctx: RunContext) -> StageResult[TOut]: ...

class RunContext:                                  # one per run; read-only except artifacts/reporter/ledger
    run_id; kind; trigger; attempt; as_of: datetime; snapshot: RunConfigSnapshot
    deps: Container                                # typed accessors: deps.youtube, deps.llm, deps.topics, ...
    reporter: StageReporter                        # bound to the current stage via ctx.for_stage(name)
    artifacts: ArtifactReader                      # get(key) -> T; get_optional(key) -> T | None
    ledger: BudgetLedger                           # charge(kind, amount) raises BudgetExceeded
    cancel: CancellationToken                      # await ctx.checkpoint() raises RunCancelled
    session_factory: async_sessionmaker            # each stage opens its own AsyncSession
    clock: Clock                                   # returns as_of-anchored "now" for business logic
    def for_stage(self, name: str) -> "RunContext": ...

class StageReporter(Protocol):                     # implements port ProgressReporter for one stage
    async def step(self, step: str, message: str, payload: EventPayload | None = None) -> None
    async def progress(self, done: int, total: int | None, unit: str) -> None
    async def insight(self, message: str, payload: EventPayload) -> None
    async def notice(self, notice: RunNotice) -> None

class ArtifactStore(Protocol):
    async def put(self, run_id, key: ArtifactKey[T], value: T, fingerprint: str) -> None
    async def get(self, run_id, key: ArtifactKey[T]) -> T
    async def find_reusable(self, client_id, key: ArtifactKey[T], fingerprint: str, max_age: timedelta) -> tuple[str, T] | None

class JobQueue(Protocol):                          # wraps arq; lets us swap to another backend later (§13)
    async def enqueue_run(self, run_id, attempt, queue, defer_until: datetime | None = None) -> str | None
    async def abort(self, job_id, timeout_s: float) -> bool
    async def status(self, job_id) -> Literal["deferred","queued","in_progress","complete","not_found"]
    async def depth(self, queue) -> int
```

### 4.3 Registries (open for extension, closed for modification)
- `StageRegistry`: maps `name → Stage factory`. Stages register at import time with `@stage_registry.register`. Duplicate names raise at startup.
- `PipelineRegistry`: maps `RunKind → PipelineDefinition{stages: list[str], run_budget: RunBudget, queue: str, priority: int}`. Definitions are declared in `pipeline/definitions.py`. Settings can disable stages per environment (`PIPELINE__DISABLED_STAGES`), but cannot reorder them.
- Startup validation (in the worker `on_startup` and in API lifespan): every name in a definition exists, every `depends_on` resolves inside the definition, the graph is acyclic (Kahn's algorithm), weights are > 0, and `policy.idempotent` is True. Any failure aborts the process. A bad deploy should not run.
- Adding a stage (for example `ai_citations` from Spotlit tracking) means one new module plus one line in the definition. `DagRunner`, the API and the reporter do not change. Optional consumers use `optional_deps`, so a new producer never breaks existing stages.
- `EventPayload` kinds are also a registry, so new insight types are additive. The frontend ignores unknown kinds (documented contract).

### 4.4 DI and sessions
- The 01 `Container` exposes ports. Stages receive the container, never concrete adapters.
- SQLAlchemy: "a single AsyncSession is not safe for use in multiple, concurrent tasks", so each stage (and each parallel sub-task inside a stage) opens its own session from `async_sessionmaker(engine, expire_on_commit=False)` (SQLAlchemy asyncio docs). No lazy loading (`lazy="raise"` on relationships used by the pipeline). The engine is disposed in worker `on_shutdown` and API lifespan exit.

### 4.5 Worker topology (arq)
arq binds one `Worker` to one `queue_name` (docs list a single `queue_name` setting), and a queue has no per-job priority. Priority therefore comes from **separate queues with dedicated worker capacity**:

| Queue | WorkerSettings | Jobs | max_jobs/proc | job_timeout | Notes |
|---|---|---|---|---|---|
| `gh:interactive` | `InteractiveWorker` | onboarding, user-triggered analysis, `cards`, `news_scan` when the user asks | 4 | 1500 s | Never starved by batch work. Autoscale on depth > 2 or oldest age > 20 s. |
| `gh:batch` | `BatchWorker` | daily_refresh, scheduled news_scan, recrawl, backtest | 6 | 1500 s | Cron scheduler throttles enqueues. |
| `gh:maintenance` | `MaintenanceWorker` | cron ticks, purge_client, snapshot batches, retention, reconcile, ops rollup | 4 | 900 s | Hosts every `cron_jobs`. Run ≥ 2 replicas for availability; arq `cron(unique=True)` runs each slot once "even if multiple workers are running". |

Common settings:
- `allow_abort_jobs=True`, so `Job.abort()` works. The default is False.
- `max_tries=3` covers crash/shutdown recovery only. Stage-level retries happen in process.
- `keep_result=3600`. `job_completion_wait=25` (the worker stops taking jobs, then waits before cancelling; keep it below the orchestrator's termination grace of 30 s).
- `health_check_interval=30`. The probe is `arq --check` (exit 0/1).
- `on_startup`: build the container, warm the embedding model (fastembed) and validate registries. `on_shutdown`: dispose the engine and close Redis.
- `on_job_start`/`after_job_end`: structlog context (run_id, client_id) and metrics.
- Job payloads carry only primitives (`run_id`, `attempt`). Everything else is loaded from the DB, so pickled payloads survive deploys.

### 4.6 SSE architecture
- `PubSubHub`, one per API process: a single Redis connection subscribed with `PSUBSCRIBE gh:{env}:run:*` and `gh:{env}:client:*`. Incoming messages fan out to in-process `asyncio.Queue`s (maxsize 256) registered per SSE connection. This avoids one Redis connection per browser tab. If a slow consumer's queue overflows, it is marked `lagging` and backfills from the DB (§5.12).
- `RunEventStream(run_id, last_seq)`: an async generator handed to sse-starlette's `EventSourceResponse` with `ping=15`, `send_timeout=30`, `shutdown_event` plus `shutdown_grace_period=3`, and headers `Cache-Control: no-cache`, `X-Accel-Buffering: no`. The SSE route is excluded from GZip middleware.

### 4.7 API layering
Router → application service (use case; owns the transaction and authorization of the target client) → repositories (01) / domain services (02–10). Routers contain no SQL. Middleware order, outermost first: request-id → structured access log → CORS → body-size limit (1 MiB; SSE/GET exempt) → GZip (≥ 1 KiB; SSE excluded) → rate limit (dependency, so it can see the principal) → routes. Exception handlers turn `AppError`, `StarletteHTTPException` and `RequestValidationError` into problem+json, following FastAPI's handler-override pattern.

## 5. Algorithms & logic

### 5.1 DAG execution (`DagRunner.execute(run)`)
```
load run + snapshot; build ctx; graph = PipelineRegistry[run.kind] filtered by stage.enabled(snapshot)
states = load stage_runs (resume) or init all "pending"
ready = {s | all deps terminal-success(s.deps) and s pending}
async with TaskGroup-like supervisor (bounded by Semaphore(max_parallel_stages=3)):
  while unfinished stages:
     for s in ready: spawn run_stage(s)
     await first completion
     on success/reused/degraded: mark; add newly-ready dependents
     on failure:
        if s.policy.critical: stop scheduling new stages; let in-flight siblings finish
                              (their artifacts are checkpointed for resume); run → failed
        else: mark failed; dependents whose dep is in optional_deps proceed; required dependents → skipped
     on RunCancelled: cancel in-flight tasks; mark them "cancelled"; run → cancelled
finalize run status: failed | cancelled | partial (any degraded/failed-noncritical/notice of severity warn) | succeeded
```
`run_stage(s)`:
1. `fp = fingerprint(s, ctx)` (§5.2). If `s.policy.reusable and not run.force`, look for a matching artifact in this run (resume) or, within `reuse_max_age`, in a previous successful run of the same client (cross-run reuse). On a hit, emit `stage.reused`, link `reused_from_run_id` and return.
2. Emit `stage.started`. Then `for attempt in 1..max_attempts`: `async with asyncio.timeout(policy.timeout_s)`, call `s.run(ctx.for_stage(s.name))`. `TransientError` → emit `stage.retrying`, sleep the backoff (honour `retry_after` when the error carries one). `BudgetExceeded` → treated as degraded if the stage returns partial output, otherwise as failure. Other exceptions → failure with an error fingerprint (`type + top frame + message template`).
3. Persist the artifact plus the `stage_runs` row in one transaction, then emit the terminal stage event.
4. `soft_timeout_s`: the stage sees `ctx.deadline_soft` and must wrap up and return `degraded` with what it has. The crawler and ingest both support this.

### 5.2 Fingerprints, checkpoints, resume
- `fingerprint(stage) = sha256(stage.name, stage.version, artifact.schema_version, sorted upstream artifact fingerprints, stage.fingerprint_inputs(ctx), as_of_bucket)`.
- `as_of_bucket` depends on the stage: ingest/snapshot = PT quota day + hour bucket (6 h); analytics/recommend = the upstream hashes only (pure functions); news = freshness cadence slot; crawl = 7 days.
- Typical `fingerprint_inputs`: topics = `topic_set_version` and video-id set hash; recommend = `config_hash` subset (window, competitors, topic refs); write_cards = draft evidence hashes plus the writer prompt version from 10.
- Artifacts hold **summaries and pointers** (ids, counts, hashes), not bulk data. Bulk domain data lives in the owners' tables (02 video tables, 06 metrics tables, 07 recommendations). Hard cap: 2 MiB serialized per artifact (`ArtifactTooLarge` is a programming error and fails tests).
- **Resume** (`POST /runs/{id}/retry`, or automatic after a crash): creates attempt N+1 *on the same run row* when the run is still `running`/`queued` (crash recovery). It creates a new run with `resume_of_run_id` when the old one is terminal (`failed`/`partial`/`cancelled`). In the second case the new run gets a new snapshot and fingerprints decide what is reused. Stages whose inputs did not change are skipped as `reused`, so a run that failed at `write_cards` resumes at `write_cards`.
- Resume is refused (a fresh run starts instead) when the source run is older than `resume_max_age_h=24`, so stale evidence is not stitched together.

### 5.3 Cancellation
- `POST /runs/{id}/cancel`: in one transaction, set `cancel_requested_at` and `status=cancelling` (if running) or `cancelled` (if queued). Then `SET gh:{env}:run:{id}:cancel 1 EX 3600`, publish a control event, and call `JobQueue.abort(job_id)` for queued jobs.
- Cooperative path: `ctx.checkpoint()` is called by stages between steps (every page, channel or card). It checks an in-memory flag, which a watcher refreshes every 2 s from the Redis key (and from DB `cancel_requested_at` on the 15 s heartbeat as a fallback when Redis is down). If set, it raises `RunCancelled`.
- Hard path: if the run is still running `cancel_grace_s=30` after the request, the API's cancel handler (or the reconciler) calls `Job.abort()` → `CancelledError` in the job. The runner catches it, records in-flight stages as `cancelled`, flushes events and re-raises.
- Results are never published from a cancelled run. The previous active set stays visible.

### 5.4 Budgets
- `BudgetLedger` holds per-stage `StageBudget` and per-run `RunBudget`.
- YouTube units: the stage calls `QuotaManager.reserve(units, client_id, run_id)` (02) before each batch. The ledger refuses when the stage or run budget would be exceeded. `QuotaManager` refuses when the global day budget or the client fair share (§5.10) is exhausted → `QuotaExhausted`.
- LLM: 10's client reports `usage` after each call. The ledger compares cumulative `llm_usd` (price table from 10's settings) before each call: `ledger.ensure("llm_usd", estimate)`. Over budget → `BudgetExceeded` → writer uses the template fallback for the remaining cards (`rendered_by="template"`, notice `llm_fallback`).
- Wall time: the run wall budget is enforced by the runner (`asyncio.timeout` around the DAG) and by arq `job_timeout = run_wall + 120 s`.
- Defaults (settings `PIPELINE__BUDGETS__<KIND>`):

| Kind | wall | yt units | search calls | llm USD | tavily credits |
|---|---|---|---|---|---|
| onboarding | 600 s | 150 | 6 | 0.60 | 40 |
| analysis (cold) | 1200 s | 900 | 4 | 1.00 | 20 |
| daily_refresh | 600 s | 120 | 1 | 0.30 | 10 |
| cards | 300 s | 0 | 0 | 0.40 | 0 |
| news_scan | 300 s | 30 | 1 | 0.20 | 20 |

### 5.5 Run state machine
```
queued ──start──▶ running ──all ok──▶ succeeded
  │                 │──noncritical failure / degraded / notices──▶ partial
  │                 │──critical failure / wall timeout / poison──▶ failed
  │                 │──cancel requested──▶ cancelling ──▶ cancelled
  │                 │──crash (lease expired)──▶ running (attempt+1, via reconciler)   # same row
  │──cancel──▶ cancelled
  └──newer equivalent run requested (coalesce)──▶ superseded
```
Terminal statuses never change. Allowed transitions live in a pure table that has its own tests. `RunService` writes status with `UPDATE … WHERE id=:id AND status = ANY(:allowed_from)` and treats 0 rows as a conflict. This is optimistic and survives duplicate workers.

Client lifecycle transitions (driven by runs and the API):
```
created ─onboarding run starts─▶ onboarding ─succeeded/partial─▶ awaiting_confirmation
onboarding ─failed─▶ onboarding_failed ─retry─▶ onboarding
awaiting_confirmation ─confirm-setup─▶ analyzing ─analysis succeeded/partial─▶ active
analyzing ─analysis failed─▶ awaiting_confirmation (results none; user can retry)
active ─PATCH paused─▶ paused ─unpause─▶ active        # paused: no scheduled runs; data ages out at 30 d
any ─DELETE─▶ deleting ─purge done─▶ (row deleted)
```

### 5.6 Run kinds and DAGs

**Onboarding** (`gh:interactive`, weight in parentheses):
```
resolve_inputs(5) ─┬─▶ crawl_site(35) ─▶ build_profile(20) ─┬─▶ discover_competitors(25) ─┐
                   │                                       └─▶ seed_topic_refs(5) ───────┼─▶ finish_onboarding(2)
                   └─▶ prefetch_own_channel(8, non-critical) ─────────────────────────────┘
```
- `resolve_inputs`: normalise the URL; resolve `channel_ref` through 02 `ChannelResolver` with **ownership verification**. The channel must link to the website, or the website must link to the channel, or the user must confirm manually. The `@Apollo247` case therefore yields "unverified", not silent acceptance. With only a channel given, the website is taken from the channel's links where available.
- `crawl_site`: 03 crawler. Each page emits `page_read`/`page_skipped`. Soft timeout 60 s, hard 120 s, page budget 40. A website-less client skips this stage (`skipped`) and the profile is built from the channel description and video titles.
- `prefetch_own_channel`: warms 02's cache (channel + uploads in 90 d) while the crawl runs. That is the onboarding/ingest parallelism. It is non-critical.
- `build_profile`: 03 → `BusinessProfile` + `FreshnessProfile`. Emits `profile_field` insights. With fewer than 3 pages it sets `partial=true` (`partial_profile` notice).
- `discover_competitors`: 04 emits `competitor_candidate` per candidate, upserts suggestions, and **never auto-confirms**.
- `seed_topic_refs`: stores suggested keywords and buyer questions (status `suggested`). It merges with Spotlit-tracked questions where available.
- `finish_onboarding`: client → `awaiting_confirmation`; emit `run.awaiting_confirmation` with counts.

**Analysis / daily_refresh** (same DAG, different stage modes; `gh:interactive` or `gh:batch`):
```
snapshot_config(1)
 ├─▶ ingest(25)  ─────────────┬─▶ enrich(10) ─┐
 ├─▶ niche_discovery(8) ──────┘               ├─▶ topics(10) ─▶ analytics(10) ─┬─▶ recommend(5) ─▶ write_cards(15) ─┐
 ├─▶ own_analytics(4, non-critical, if oauth) ─────────────────▶ analytics      │                                   ├─▶ finalize(2)
 └─▶ news(8, non-critical) ───────────────────────────────────────────────────▶ ├─▶ refresh(4, non-critical) ───────┘
                                                                                 └─(news is optional_dep of recommend & refresh)
```
- `snapshot_config`: freezes `RunConfigSnapshot` (taken at run creation, re-validated here: ≥ 3 confirmed competitors and ≥ 1 active topic ref, otherwise the run fails with `config_invalid`).
- `ingest`: 02. Own and competitor uploads, batched `videos.list` stats, today's snapshot alignment. Mode `full` (analysis) or `incremental` (daily: new uploads since cursor plus stats refresh for in-window videos only). Emits `videos_fetched` per channel.
- `niche_discovery`: 02/07 shared, cached niche searches (search.list bucket, shared across clients by query hash) feeding creators and trending. On a cache hit no search call is made.
- `news`: 08, gated by FreshnessProfile cadence. When not due, the stage returns `skipped` and the last `NewsSignal` set is consumed through `optional_deps` from the last news artifact (cross-run reuse, max age = cadence × 2).
- `enrich`: 06 thumbnail traits (new videos only) and comment signals (outliers only).
- `topics`: 05 hybrid matching restricted to the snapshot's `topic_refs`. Only unseen (video, topic_set_version) pairs are matched.
- `analytics`: 06 for **all three windows** (7/30/90). It is cheap deterministic SQL/numpy and lets the UI switch windows without a run.
- `recommend`: 07 drafts and quality gates for all three windows, deterministic.
- `write_cards`: 10 writer for the client's **chosen window only** (`cards` run does other windows lazily). Concurrency 6. Cards whose draft evidence hash is unchanged since the last published set are copied, not rewritten. Emits `card_written`.
- `refresh`: 09 description refresh suggestions (own channel only).
- `finalize`: `ResultPublisher.publish(run)` (§5.7a); client lifecycle → `active`; schedule bookkeeping.

**Daily refresh reuse matrix**

| Item | Daily refresh behaviour |
|---|---|
| BusinessProfile, competitors, topic refs | reused (part of snapshot); never recomputed |
| Channel metadata | refreshed if > 7 d old (02 cache) |
| Uploads list | incremental since `uploads_cursor` |
| Video stats | refreshed for videos inside the 90 d window (batched 50/call); outside window → no |
| Snapshots | taken by snapshot cron, not by the run; the run reads the latest |
| Thumbnail traits | only new videos (traits are immutable per thumbnail URL hash) |
| Comment signals | only videos that newly became outliers |
| Topic matches | only new videos, or all if `topic_set_version` changed |
| Embeddings | cached by text hash (05) |
| Analytics / baselines / outliers / lift | recomputed (cheap, depends on fresh stats) |
| Niche search results | reused while the cache TTL holds (02/07 policy, e.g. 72 h) |
| News signals | only if cadence slot due |
| Drafts | recomputed |
| Cards (LLM) | rewritten only when the draft evidence hash changed |
| Refresh suggestions | recomputed; LLM phrasing reused by hash |

**cards** run: `load_drafts(window) → write_cards → finalize(window only)`. **news_scan** run: `news → recommend(news-only drafts) → write_cards → finalize(merge news cards)`. **recrawl**: `crawl_site(deep, 200 pages, 10 min) → build_profile(merge as suggestions, never overriding overrides)`. **backtest** (12): the analysis DAG with `as_of = T`, ingest replaced by `frozen_corpus` (reads stored data ≤ T), `finalize` writing to evaluation tables instead of `active_result_sets`.

### 5.7 Start-run / dedupe (`RunService.request_run`)
```
txn:
  lock client row (SELECT … FOR UPDATE)            # serialises run creation per client
  reject if client.lifecycle in {deleting} → 409 client_deleting; {paused} and trigger=schedule → skip
  active = SELECT run WHERE client_id=:c AND status IN ACTIVE
  if active:
     if active.kind == requested.kind or (requested.kind == daily_refresh and active.kind == analysis):
         return active (HTTP 200, header `X-Run-Deduplicated: true`)       # join existing
     if requested.kind == analysis and active.kind == daily_refresh and active.status == queued:
         mark active superseded; continue                                     # user intent wins
     if requested.kind == analysis and active is running daily_refresh/analysis and config_hash differs:
         set active.follow_up_requested = true; return active + notice "will re-run with your changes"
     else → 409 run_in_progress (Problem includes active run id)
  insert run (status queued, snapshot, budget, queue, priority)
  -- partial unique index uq_active_run_per_client guarantees one ACTIVE run even if two txns race
after commit: JobQueue.enqueue_run(run_id, attempt=1, queue)  (arq _job_id = f"run:{run_id}:{attempt}")
if enqueue fails (Redis down): run stays queued; reconciler enqueues later (§5.9e)
```
Every enqueue path dedupes on the DB index and on the arq `_job_id`. arq guarantees "a job with a particular ID cannot be enqueued again until its execution has finished and its result has cleared" and `enqueue_job` returns `None` for a duplicate.

### 5.7a Result publication (atomic)
`finalize` runs one transaction:
1. Verify `client.lifecycle != deleting` and that the run is still `running` with this attempt (fencing).
2. For each window computed, upsert `active_result_sets(client_id, window_days) → run_id, published_at`.
3. Mark previous sets `superseded_at`.
4. Compare `snapshot.config_hash` with the client's current config hash. If they differ, set `stale_reasons=["config_changed"]` and, if `follow_up_requested`, request a coalesced analysis run after commit.

Readers always resolve `active_result_sets` first, so readers never observe a half-published run. Partial runs publish only windows/sections whose stages succeeded; the rest keep the previous set and get `stale_reasons`.

### 5.8 Leases, heartbeats, duplicate execution
arq warns "Jobs may be called more than once!" and favours "multiple times over zero times", so:
- On job start: `UPDATE pipeline_runs SET lease_owner=:worker_job, lease_expires_at=now()+45s, attempt=:attempt, status='running' WHERE id=:id AND status IN ('queued','running') AND (lease_expires_at IS NULL OR lease_expires_at < now() OR lease_owner=:worker_job) AND attempt <= :attempt`. 0 rows → another live owner exists or this attempt was superseded → exit quietly.
- A heartbeat every 15 s extends the lease using **DB `now()`**, so worker clock skew does not matter. A failed heartbeat (DB down) is retried; after 2 failed heartbeats the runner stops scheduling new stages and lets in-flight work stop at its next checkpoint.
- All state writes from the runner include `AND lease_owner=:me` (fencing). Domain writes are idempotent upserts keyed on natural keys, so a zombie worker's late writes are harmless.

### 5.9 Schedulers (cron "ticks" on `gh:maintenance`; ticks only decide and enqueue)
Each tick is `cron(..., unique=True)`. It also takes `RedisLock("cron:{name}", ttl=interval-5s)` and uses DB-clock due times, so overlapping ticks (slow tick, skewed workers) cannot double-enqueue.

a. **schedule_daily_refreshes** (every 10 min). Clients with `lifecycle=active`, not paused, circuit closed, and `next_due_at <= now()`. `next_due_at` = the client's `refresh_hour_local` in its timezone, plus a deterministic jitter `hash(client_id) % 40 min`, which spreads load and avoids thundering herds. Enqueue `daily_refresh` (trigger schedule) via `request_run`, in **round-robin across workspaces** (sorted by workspace's last-enqueued time). Stop when batch queue depth ≥ `batch_max_depth=50`, or when the quota forecast (`QuotaManager.remaining_today() - reserved_for_snapshots - interactive_reserve`) < the per-client estimate. Clients not enqueued stay due and are retried next tick. If a client could not run by `due + 20 h`, emit an ops alert and show the `data_stale` notice.

b. **snapshot_tick** (every 30 min). Calls 02 `SnapshotScheduler.due_batch(limit_units)`. The adaptive cadence (02's rule, e.g. 0–2 d every 6 h, 3–14 d daily, 15–90 d every 3 d, > 90 d weekly while referenced) produces video id batches of 50, enqueued as `snapshot_batch` jobs with `_job_id=f"snap:{pt_day}:{slot}:{batch_hash}"`. Budget: `snapshot_units_share=35%` of the daily quota, carved out before refreshes.

c. **news_radar_tick** (hourly). For clients with a FreshnessProfile, `cadence_hours` comes from the profile: `high` (AI, tech) = 6 h, `medium` = 24 h, `low` (dental) = 168 h, and `none` = disabled. 08 can refine this. If the client is due and no news-capable run is active, enqueue `news_scan` with `_job_id=f"news:{client_id}:{slot}"`. Niche queries are shared across clients by query hash (08 cache), so 10 AI clients cost one Tavily search per query per slot.

d. **retention_sweep** (daily 03:30 UTC). Calls 01 `RetentionService.sweep()` for YouTube API data older than 29 days (one day of margin under the 30-day rule), then purges 11's tables: `run_events` and `run_artifacts` older than 29 d (both may contain titles and stats), `idempotency_keys` past expiry, superseded `active_result_sets` history older than 29 d. Active recommendations whose reference videos were purged/refreshed are revalidated by 07 on the next refresh. For paused clients whose data aged out, the API returns `data_stale` and hides recommendations.

e. **reconcile_runs** (every 2 min). (i) Runs `running` with `lease_expires_at < now() - 15 s` → attempt+1, re-enqueue `run:{id}:{attempt+1}`, emit `stage.retrying` on interrupted stages. Max attempts 3 → `failed(poison)`. (ii) Runs `queued` for more than 60 s where `JobQueue.status(job_id) == not_found` (Redis lost the job) → re-enqueue. (iii) `cancelling` older than the grace period → `Job.abort`, then mark `cancelled` when the lease expires. (iv) Clients in `deleting` with no active run → enqueue `purge_client`.

f. **ops_rollup** (hourly). Aggregates `pipeline_runs.usage`/timings into 12's ops tables.

### 5.10 Concurrency limits, fairness, backpressure
- **Global limiters** (`RedisSemaphore`: a sorted set of lease tokens with expiry scores, acquired atomically by Lua, lease 60 s auto-renewed):
  - `sem:youtube` = 8 concurrent HTTP calls, so we stay polite and avoid 403 bursts.
  - `sem:llm:{model}` = 8. On top of that, a `TokenBucket` per model fed from the Anthropic `anthropic-ratelimit-*-remaining`/`-reset` headers. It honours `retry-after` on 429. Anthropic limits are a token bucket per model class across RPM/ITPM/OTPM, cache reads don't count toward ITPM, and ramp limits can 429 on sudden bursts, so batch work starts slowly.
  - `sem:tavily` = 4. `sem:crawl:{domain}` = 2.
- **Per-tenant fairness**: at most `1` active run per client (DB index) and at most `max_active_runs_per_workspace=3` (checked in `request_run` for schedule triggers; user triggers go over this to a max of 5). Each client's YouTube share = `max(min_share=60 units, (daily_budget - reserved) / active_clients)`, enforced by 02's `QuotaManager` using the `client_id` passed through the ledger. Interactive onboarding is always protected by `interactive_reserve=1500` units/day that batch runs cannot reserve.
- **Shared channel single-flight**: before refreshing channel X, ingest takes `RedisLock("lock:channel:{id}", ttl=60s, renew)`. Waiters poll up to 30 s, then read whatever is cached (fresh enough → skip the fetch). After acquiring, the stage re-checks `fetched_at` (another worker may have just refreshed it). The lock exists for efficiency only. Correctness comes from upserts with `WHERE excluded.fetched_at > table.fetched_at`.
- **Backpressure**: when `gh:interactive` depth > 20 or oldest > 120 s, `POST /runs` and onboarding still accept (202) but include `queue_position` and `eta_s`. When batch depth ≥ 50, the scheduler stops enqueueing. When DB pool wait > 2 s (p95), the API returns 503 `overloaded` with `Retry-After: 5` on write endpoints. Per-process SSE connection cap is 1,000. A new stream over the cap → 503, and the client falls back to polling.
- **Horizontal scaling**: API replicas are stateless (PubSubHub per replica). Workers scale per queue. DB connections are budgeted: `api_replicas × (pool 10 + overflow 5) + Σ workers × max_jobs × max_parallel_stages(3) ≤ 0.8 × max_connections`. In prod, put PgBouncer in front in transaction mode and set asyncpg `statement_cache_size=0` (general knowledge; verify with 01).
- **Graceful shutdown**: on SIGTERM arq stops picking jobs, waits `job_completion_wait`, then cancels tasks. The runner treats `CancelledError` *without* a cancel request as an **interruption**: in-flight stages are marked `interrupted`, events are flushed, and the lease is released (`lease_expires_at=now()`). arq will re-run the job ("will be run again"), and the reconciler also covers it. The new attempt resumes from checkpoints. API: uvicorn `--timeout-graceful-shutdown 20`. sse-starlette `shutdown_event` sends `event: reconnect` with `retry: 1000` to every stream before closing.
- **Poison jobs**: an error fingerprint recurring across 3 attempts, or 3 crash recoveries (lease expiry with no terminal event, e.g. OOM), → run `failed(code=poison)`. The client's daily schedule circuit opens for 24 h (`client_schedules.circuit_open_until`), with an ops alert and a "We couldn't refresh your data; last good results from <date>" notice. Non-retryable errors (`ConfigInvalid`, `ValidationError`, `ArtifactSchemaMismatch`) fail on the first attempt.

### 5.11 Progress emission
- `ProgressReporter` (per run) holds an in-memory `seq` counter, seeded at attempt start from `SELECT coalesce(max(seq),0) FROM run_events WHERE run_id=:id`. This is safe because the lease guarantees a single writer.
- Throttling: `stage.progress` is coalesced to ≤ 4/s per stage. `insight` events are capped at 30/stage/run (the rest are summarised in the stage completion counts) so a 4,000-video channel doesn't flood the stream.
- Persistence: events are buffered and flushed every 200 ms or 50 events with a bulk `INSERT … ON CONFLICT (run_id, seq) DO NOTHING`. **After commit**, each event is `PUBLISH`ed to `gh:{env}:run:{run_id}` and lifecycle events also to `gh:{env}:client:{client_id}`. Publishing after commit guarantees that any seq a subscriber sees is already replayable from the DB.
- Terminal and lifecycle events (`stage.*` terminal, `run.*`) flush immediately.
- If the DB is unavailable, events stay buffered (max 2,000; oldest progress events dropped first, lifecycle never dropped) and are retried. Live publish waits for the DB to keep the replay invariant.
- Percent: `run_pct = 100 × Σ_s w_s·f_s / Σ_s w_s` over enabled stages, where `f_s` = 1 for terminal statuses (including reused/skipped), `done/total` when known, and otherwise an asymptotic `1 - e^(-t/τ_s)` capped at 0.9 with τ_s = the stage's p50 duration from ops history (default timeout/4). Emitted value = `max(previous, computed)` (monotonic). A `run.progress` event is emitted on change ≥ 1 point.
- Messages come from code templates in `progress_messages.py` (e.g. `"Reading {path}"`, `"Found {n} competitor candidates"`, `"{ratio:.1f}x outlier on {channel}"`). Numbers in them come from code. No LLM text appears in events.

### 5.12 SSE replay algorithm (`GET /api/v1/runs/{run_id}/events`)
```
authz(run.client in principal.workspace)
last = int(Last-Event-ID header) or int(?after_seq) or 0
if run terminal and last >= run.last_seq: return 204          # spec: 204 stops EventSource reconnecting
queue = hub.register(run_channel)                           # SUBSCRIBE first (no gap window)
yield retry: 3000
for ev in db.events(run_id, seq > last, order seq, batches of 500): yield sse(ev); last = ev.seq
loop:
   ev = await queue.get() with timeout 5 s
   on timeout or hub degraded (Redis down): poll db for seq > last (fallback); yield those
   on ev: if ev.seq <= last: continue (dupe)
          if ev.seq > last + 1: backfill db (last, ev.seq) first      # gap: missed pubsub message
          yield sse(ev); last = ev.seq
   if ev is terminal run event: yield "event: end"; break
finally: hub.unregister(queue)                               # on disconnect (CancelledError) too
```
Frame: `id: <seq>`, `event: <type>`, `data: <RunEvent json>`. Keep-alive: sse-starlette `ping=15` comment frames, within proxy idle timeouts (HAProxy/ALB ≥ 60 s). Required headers on the route: `Content-Type: text/event-stream` (library), `Cache-Control: no-cache`, `X-Accel-Buffering: no`; and for nginx `proxy_buffering off`.

Also exposed:
- `GET /api/v1/clients/{id}/events` (SSE): client-scope lifecycle (runs started by cron, run terminal events, `results.published`). The frontend opens one stream per client view instead of one per run, which matters for the browser's 6-connections-per-domain limit on HTTP/1.1. Serve over HTTP/2 in prod.
- `GET /api/v1/runs/{run_id}/events.json?after_seq=&limit=200`: polling fallback with the same schema.

Auth for SSE: `EventSource` cannot set an `Authorization` header. The frontend uses a same-site session cookie (via the Next.js BFF). Third parties call `POST /runs/{id}/stream-token`, which returns a 5-min HMAC token scoped to `run_id`, passed as `?token=`. The token is redacted from access logs.

### 5.13 Idempotency keys (POST endpoints that create things: clients, runs, onboarding, confirm-setup, competitors add, feedback)
Header `Idempotency-Key: "<uuid>"` (IETF draft-08 style: sf-string). Required for `POST /clients/{id}/runs` and `POST /clients` from API-key principals, optional for session principals (the BFF generates one per user action).
```
fp = sha256(method, route template, principal.workspace_id, canonical body)
INSERT idempotency_keys(workspace_id, key, fp, status='in_progress', expires_at=now()+24h) ON CONFLICT DO NOTHING
if conflict: row = select
   row.fp != fp → 422 idempotency_key_reused
   row.status == in_progress → 409 idempotency_request_in_progress (Retry-After: 2)
   completed → replay stored status + body + Location (header Idempotent-Replayed: true)
else execute; store response (≤ 64 KB) status=completed
missing when required → 400 idempotency_key_required
```
Run creation is *also* idempotent through dedupe (§5.7), so a retried POST without a key never creates two active runs.

### 5.14 Rate limiting
Token bucket in Redis (Lua; key = principal + bucket):
- `read` 600/min
- `write` 120/min
- `run_start` 20/hour per client and 100/hour per workspace
- `sse_open` 60/min
- `oauth` 10/min

Responses carry `RateLimit-Policy` and `RateLimit` (IETF draft-11 structure), plus `Retry-After` on 429 (`code=rate_limited`). If Redis is unavailable, a local per-process bucket at 50% of limits applies (fail-safe, not fail-open).

### 5.15 Meeting and measuring "< 2 minutes per client to generate recommendations"
- **Definition** (agreed with 12): `reco_ms` = wall time from the moment all inputs for analysis exist in storage (end of `ingest` ∧ `enrich` ∧ `niche_discovery`) to `finalize` commit. That is the critical path `topics → analytics → recommend → write_cards → finalize`. Also tracked: `refresh_total_ms` for daily refreshes, and `onboarding_to_first_card_ms`.
- **Targets**: p95 `reco_ms` < 120 s; warm daily refresh p95 `refresh_total_ms` < 120 s.
- **Design levers**:
  - Slow I/O (crawl, cold ingest, snapshots) runs separately, ahead of time, or in parallel.
  - Analytics is SQL/numpy only.
  - Cards are written for one window only, with 6 in parallel and copy-forward of unchanged cards.
  - The deterministic template fallback has a time box: `write_cards` soft timeout of 60 s, after which the remaining cards render from the template and an LLM rewrite is scheduled in the background `cards` run.
- **Measurement**: `stage_runs.duration_ms`, `pipeline_runs.timings`. Ops rollup computes p50/p95 per kind. A regression test with recorded fixtures asserts the deterministic portion is < 20 s for a 10-competitor corpus.

## 6. Data model (all owned by 11; Alembic migrations; UTC timestamps `timestamptz`)

**pipeline_runs**
| column | type | notes |
|---|---|---|
| id | text pk | `run_` + ULID |
| workspace_id | text fk | |
| client_id | text fk ON DELETE CASCADE | |
| kind | text | RunKind |
| trigger | text | |
| status | text | RunStatus, CHECK constraint |
| attempt | smallint default 1 | |
| resume_of_run_id | text null fk | |
| follow_up_requested | bool default false | |
| window_days | smallint | |
| as_of | timestamptz | DB `now()` at creation (or T for backtests) |
| config_snapshot | jsonb | RunConfigSnapshot |
| config_hash | text | |
| queue, priority | text, smallint | |
| arq_job_id | text | |
| lease_owner | text null; lease_expires_at timestamptz null | |
| event_seq | int default 0 | last persisted seq (updated on flush) |
| progress_pct | real default 0 | |
| current_stages | text[] | |
| notices | jsonb default '[]' | |
| usage, budget, timings | jsonb | |
| error | jsonb null | |
| idempotency_key | text null | |
| requested_by | text null | user id / "scheduler" |
| created_at, queued_at, started_at, finished_at, cancel_requested_at | timestamptz | |

Indexes:
- `uq_active_run_per_client UNIQUE (client_id) WHERE status IN ('queued','running','cancelling')`
- `ix_runs_client_created (client_id, created_at DESC)`
- `ix_runs_status_lease (status, lease_expires_at) WHERE status IN ('queued','running','cancelling')`
- `ix_runs_workspace_active (workspace_id) WHERE status IN (...)`

Retention: rows kept 180 d (no API data, as long as `config_snapshot` holds only ids and client-authored text); `notices` must not embed video titles.

**stage_runs**: PK `(run_id, stage)`; `stage_version int`, `status`, `attempt`, `input_fingerprint text`, `reused_from_run_id`, `started_at`, `finished_at`, `duration_ms int`, `counts jsonb`, `usage jsonb`, `error jsonb`, `warnings jsonb`. Index `(stage, finished_at)` for ops percentiles. Retention 180 d.

**run_artifacts**: `id bigserial pk`, `run_id fk cascade`, `client_id`, `key text`, `schema_version int`, `fingerprint text`, `payload jsonb` (CHECK `pg_column_size(payload) < 2 MiB`), `contains_api_data bool`, `created_at`, `expires_at` (= created + 29 d if contains_api_data else 90 d). Indexes: `UNIQUE (run_id, key)`, `(client_id, key, fingerprint, created_at DESC)` for cross-run reuse.

**run_events**: `id bigserial pk`, `run_id fk cascade`, `client_id`, `seq int`, `type text`, `stage text`, `step text`, `status text`, `level text`, `message text`, `counts jsonb`, `percent real`, `stage_percent real`, `payload jsonb`, `created_at timestamptz default clock_timestamp()`. `UNIQUE (run_id, seq)` (replay range scan). Retention: 29 d (payloads contain titles/stats), and a hard cap of 5,000 events/run (reporter enforces it). Optional monthly partitioning by `created_at` once volume > 10 M rows.

**idempotency_keys**: PK `(workspace_id, key)`; `fingerprint`, `method`, `path`, `status`, `response_status smallint`, `response_headers jsonb`, `response_body jsonb`, `resource_id text`, `created_at`, `expires_at` (24 h). Index `(expires_at)`.

**client_schedules**: PK `(client_id, kind)` where kind ∈ {daily_refresh, news_scan}; `cadence_hours`, `next_due_at`, `last_enqueued_at`, `last_run_id`, `last_success_at`, `failure_streak smallint`, `circuit_open_until`, `paused bool`. Index `(kind, next_due_at) WHERE NOT paused`.

**active_result_sets**: PK `(client_id, window_days)`; `run_id fk`, `published_at`, `stale_reasons text[]`, `superseded_history jsonb` (last 5 run ids for rollback). Rollback = repoint to a previous run_id (ops endpoint, admin only).

**recommendation_feedback**: `id`, `workspace_id`, `client_id`, `recommendation_id`, `stable_key` (feedback carries across runs for the same idea), `rating`, `reasons text[]`, `status`, `note`, `user_id`, `created_at`, `updated_at`. `UNIQUE (recommendation_id, user_id)`; index `(client_id, stable_key)`. No API data stored; retained while the client exists. Consumed by 07 (suppress dismissed `stable_key`s) and 12.

Recommendations, news, refresh suggestions and analytics tables are owned by 07/08/09/06. Their rows carry `run_id` so `active_result_sets` can resolve them.

## 7. External APIs & libraries (verified 2026-11-20)

| Library / API | Facts used | Source |
|---|---|---|
| arq 0.28.0 (PyPI latest, 2026-04-16) | `_job_id` uniqueness ("cannot be enqueued again until its execution has finished and its result has cleared"; `enqueue_job` returns None on duplicate); `_queue_name`, `_defer_until`, `_defer_by`, `_expires` (default 24 h + defer); `Retry(defer=)`; `max_tries` default 5; `job_timeout` default 300 s; `keep_result` default 3600; `allow_abort_jobs` default False; `Job.abort()`; `JobStatus` deferred/queued/in_progress/complete/not_found; shutdown cancels jobs immediately and they "will be run again"; `job_completion_wait` default 0; "Jobs may be called more than once!"; `cron(unique=True)` runs once per slot across workers; `health_check_interval` + `arq --check`; one `queue_name` per worker. **README: "In maintenance only mode" (issue #510).** | https://arq-docs.helpmanual.io/ , https://github.com/python-arq/arq , https://pypi.org/project/arq/ |
| arq in-progress key TTL after a hard crash (job held until timeout) | from source knowledge, **not on docs page — unverified**; mitigated by the reconciler re-enqueueing under a new `_job_id` | — |
| sse-starlette 3.7.0 | `EventSourceResponse(content, ping=15 default, sep, send_timeout, headers, ping_message_factory, shutdown_event, shutdown_grace_period, cleanup_timeout)`; `ServerSentEvent(data, event, id, retry, comment)`; recommends `X-Accel-Buffering: no` / nginx `proxy_buffering off`; Cloudflare buffers ~100 KB; `AppStatus.should_exit` reset needed in tests | https://github.com/sysid/sse-starlette , https://pypi.org/project/sse-starlette/ |
| SSE protocol | `id`, `event`, `data`, `retry` (ms) fields; comment lines as keep-alive; HTTP/1.1 limit 6 connections per browser+domain, HTTP/2 negotiated (default 100 streams); 204 stops reconnection and `Last-Event-ID` resent on reconnect per HTML spec (WHATWG page unreachable during research — **verify**) | https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events/Using_server-sent_events , https://html.spec.whatwg.org/multipage/server-sent-events.html |
| FastAPI 0.165.0 | lifespan context manager (on_event deprecated; lifespan not run for mounted sub-apps); override `StarletteHTTPException` and `RequestValidationError` handlers; CORSMiddleware: no `*` with `allow_credentials=True`, default `max_age=600` | https://fastapi.tiangolo.com/advanced/events/ , https://fastapi.tiangolo.com/tutorial/handling-errors/ , https://fastapi.tiangolo.com/tutorial/cors/ |
| SQLAlchemy 2 asyncio | AsyncSession not safe across concurrent tasks; one session per task with `asyncio.gather`; `expire_on_commit=False`; avoid lazy loads (`selectinload`, `lazy="raise"`, `AsyncAttrs`); `await engine.dispose()` | https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html |
| PostgreSQL | partial unique indexes; advisory locks session vs xact (session locks survive rollback, released at session end) — we use row locks + partial unique index instead of advisory locks because of pooling | https://www.postgresql.org/docs/current/explicit-locking.html |
| Redis Pub/Sub | **at-most-once** ("if the subscriber is unable to handle the message … forever lost"); Streams for at-least-once; channels not scoped by db number → prefix with env | https://redis.io/docs/latest/develop/pubsub/ |
| RFC 9457 | `application/problem+json`; members type/title/status/detail/instance; extensions ignored if unknown; `about:blank` default; obsoletes RFC 7807 | https://www.rfc-editor.org/rfc/rfc9457.html |
| Idempotency-Key draft-08 (2026-10-22) | sf-string key; 400 missing, 409 in-flight, 422 reused with different payload; fingerprint optional; expiry documented | https://datatracker.ietf.org/doc/draft-ietf-httpapi-idempotency-key-header/ |
| RateLimit headers draft-11 (May 2026) | `RateLimit-Policy`, `RateLimit`; still a draft | https://datatracker.ietf.org/doc/html/draft-ietf-httpapi-ratelimit-headers |
| RFC 9745 + RFC 8594 | `Deprecation` and `Sunset` response headers for API deprecation | https://www.rfc-editor.org/rfc/rfc9745 |
| Anthropic rate limits | RPM/ITPM/OTPM per model class, token bucket; 429 with `retry-after`; `anthropic-ratelimit-{requests,tokens,input-tokens,output-tokens}-{limit,remaining,reset}` headers; cache reads excluded from ITPM; ramp limits (`ramp_limit_exceeded`); spend-cap 429 has **no** retry-after (`enforced_spend_limit_reached`) → treat as outage, not retry | https://platform.claude.com/docs/en/api/rate-limits |
| YouTube quota | channels/videos/playlistItems/commentThreads.list = 1 unit; "`search.list` and `videos.insert` methods have their own quota buckets", default 100 search.list calls/day, "Each call costs 1 quota" in that bucket; reset at midnight PT | https://developers.google.com/youtube/v3/determine_quota_cost |
| Google OAuth (web server) | auth endpoint `https://accounts.google.com/o/oauth2/v2/auth`, `access_type=offline`, `state` for CSRF, `prompt=consent` to re-obtain refresh token, token endpoint `https://oauth2.googleapis.com/token`, `invalid_grant` → re-consent. YouTube Analytics scope names (`yt-analytics.readonly`, `youtube.readonly`) not on that page — owned/verified by 02 | https://developers.google.com/youtube/reporting/guides/authorization/server-side-web-apps |
| uvicorn | `--timeout-graceful-shutdown`, `--proxy-headers` (trusts `--forwarded-allow-ips`), `--timeout-keep-alive` 5 s | https://uvicorn.dev/settings/ |

## 8. Edge cases & failure modes

| # | Case | Detection | Handling | User-facing message |
|---|---|---|---|---|
| 1 | User edits competitors/keywords/questions mid-run | `client.config_hash != run.snapshot.config_hash` at finalize | Run continues on its immutable snapshot. Edits bump config version. Finalize publishes with `stale_reasons=["config_changed"]`. If the user pressed "Re-run" (or confirmed changes), `follow_up_requested` → one coalesced analysis run starts right after, reusing ingest artifacts. | "You changed competitors while we were analysing. Showing results for the previous set — updating with your changes now." |
| 2 | User rejects a competitor that the running analysis is using | same as 1 | Results referencing it are marked stale. The follow-up run excludes it. No mid-run mutation (determinism). | as above |
| 3 | Two runs triggered at once (double click, two tabs, cron + user) | client row lock + `uq_active_run_per_client` | Same kind → return the existing run (200, `X-Run-Deduplicated`). Analysis vs queued daily → supersede daily. Otherwise 409 with the active run id. | "An analysis is already running — following its progress." |
| 4 | Concurrent create races past the row lock (different transactions/isolation) | unique violation on partial index | Catch `IntegrityError` → re-read the active run → same as 3. | same |
| 5 | Same POST retried by network/proxy | Idempotency-Key match | Replay the stored response. Mismatched body → 422. | none (transparent) |
| 6 | Worker crash (OOM/SIGKILL) mid-stage | lease expiry (45 s) found by reconciler; arq re-run later | Attempt+1 re-enqueued under a new job id. Completed stages reused by fingerprint. The interrupted stage restarts (stages are idempotent). Zombie writes fenced. | Progress shows "Resuming from Topic matching…" (`stage.retrying`). |
| 7 | Same job executed twice concurrently by arq | lease UPDATE affects 0 rows | Second executor exits without side effects. | none |
| 8 | Worker SIGTERM during deploy | `CancelledError` without cancel flag | Mark `interrupted`, release lease, arq re-runs the job; the new code version may change stage versions → those stages recompute. | "Resuming after a brief interruption." |
| 9 | Stage version or artifact schema changed between attempts | fingerprint mismatch / `schema_version` mismatch | Recompute the stage; never deserialize an old schema (`ArtifactSchemaMismatch` → treat as missing). | none |
| 10 | Redis restart: pub/sub messages lost | hub reconnect event, or seq gap in a subscriber | SSE backfills from the DB on gap; 5 s poll fallback while the hub is down. DB is the source of truth. | none (maybe a brief pause in the animation) |
| 11 | Redis restart: arq queue/jobs lost (no persistence) | reconciler: `queued` run with job `not_found` > 60 s | Re-enqueue. Prod Redis must run AOF (`appendonly yes`) to limit loss. | none |
| 12 | Redis restart: cancel flags, locks, rate counters lost | — | Cancel falls back to DB `cancel_requested_at` on the heartbeat. Locks are efficiency only (idempotent upserts). Rate limiter falls back to a local bucket at 50%. Quota ledger must be DB-authoritative (02; §13). | none |
| 13 | Redis down entirely | health check / connection errors | API: reads OK. Run creation persists `queued` and returns 202 (enqueue deferred to reconciler). SSE → DB polling. Workers can't take jobs → alert. | "Live updates are delayed; your run is queued." |
| 14 | DB failover / brief outage | `OperationalError`, `pool_pre_ping` failures | Stage-level transient retry with backoff. Events buffered. Heartbeat failures stop new stages. After 60 s unavailable → run `failed(infra_unavailable, retryable)` and the reconciler auto-resumes when the DB returns. API returns 503 problem with Retry-After. | "We hit a temporary infrastructure issue and will resume automatically." |
| 15 | Very slow website crawl | soft timeout 60 s / hard 120 s / page budget | Return the pages read so far. Profile built partially (`partial=true`, completeness score). Low-priority `recrawl` run enqueued on batch. Its findings become *suggestions* and never override user edits. | "Your site was slow to respond — we read 14 pages. You can add details now; we'll keep reading in the background." |
| 16 | Website blocks crawling (robots.txt, 403, Cloudflare challenge) | crawler result codes | Respect robots. Fall back to channel description, Tavily `extract` on allowed pages (03), and user input. Profile partial. | "Your website doesn't allow automated reading of most pages. Add your services below to improve recommendations." |
| 17 | Website-only onboarding (no channel) | snapshot `own_channel_id` None | Channel candidates from the site's links (03/02). If none, the client operates competitor-only (own-performance section shows an empty state). Analysis still runs. | "We couldn't find your YouTube channel. Add it to compare against your own baseline." |
| 18 | Channel ref resolves to the wrong/unrelated channel (e.g. `@Apollo247`) | ownership verification fails | Never auto-attach. Return candidates with evidence and require confirmation. | "This channel doesn't link to apollohospitals.com — is it yours?" |
| 19 | Quota exhausted mid-run | `QuotaExhausted` from 02 | Stage degrades: uses cached data (≤ 29 d old), counts what's missing, adds a `quota_limited` notice. Run ends `partial`. Continuation run (`resume_of`) is deferred with `_defer_until = next PT midnight + jitter`. Quality gates (07) hide recommendations lacking evidence. | "YouTube's daily data limit was reached. Showing data from 19 Nov; full refresh after 13:30 IST." |
| 20 | Search bucket (100 calls/day) exhausted | 02 search-bucket counter | Niche discovery/creators use cached results. If none, creators section empty with a notice. Competitor discovery falls back to Tavily web search + channel lookups by handle (02). | "Creator suggestions will update tomorrow." |
| 21 | LLM outage / 5xx / 529 / spend cap | 10 raises `LLMUnavailable` (spend cap = 429 without retry-after) | Fallback chain (10): secondary provider → deterministic template cards (`rendered_by=template`). Profile: rule-based extraction (partial). Competitor discovery: search-only candidates (lower score). News: skip relevance adjudication → `news_skipped`. Background `cards` run re-renders when healthy. | "Some cards use a simpler format while our writing service recovers." |
| 22 | LLM rate limited (429 with retry-after / ramp limit) | headers | Token bucket waits up to the stage soft deadline. Beyond it → template fallback for the remainder. Batch work starts at a low rate. | none, unless fallback |
| 23 | LLM cost budget exceeded | `BudgetExceeded` | Template fallback for the remaining cards. Ops alert if frequent. | as 21 |
| 24 | Tavily outage / credits exhausted | adapter error | News stage → `skipped` with notice (non-critical). Competitor discovery uses YouTube-only candidates. | "News radar is temporarily unavailable." |
| 25 | Client deleted during run | `lifecycle=deleting` at checkpoints; finalize guard | Cancel the run (reason client_deleted). Finalize refuses to publish. `purge_client` waits for no active run, then cascades the client's rows. Shared public cache untouched (02 handles orphan cleanup). | (client gone) DELETE returns 202 `{"status":"deleting"}` |
| 26 | Client paused mid-run | `paused` flag | Current run finishes. No new scheduled runs. Data ages out at 29 d and recommendations are hidden with `data_stale`. | "Paused — recommendations will expire on 18 Dec unless you resume." |
| 27 | Window changed (30 → 7) | PATCH `window_days` | Analytics/drafts already exist for all windows (instant). If cards for that window aren't written → enqueue `cards` run (interactive) and return 202 with run id. UI streams card writing. | "Writing your 7-day recommendations…" then thin-data notice if applicable. |
| 28 | Thin data in chosen window | 07 gates produce 0–2 cards; `thin_data` notice with `suggest.widen_window_to` | Show the notice and the computed alternate window counts. | "Not enough outliers in the last 7 days. Widen to 30? (9 recommendations available)" |
| 29 | All recommendations fail quality gates | 0 published cards | Publish an empty set with notices (honest), not the previous set mixed in. | "No recommendation met our evidence bar this time. Here's why: …" |
| 30 | Stale artifacts (old run reused) | `reuse_max_age` per stage, `as_of_bucket` in fingerprint, `resume_max_age_h=24` | Recompute beyond the age; fresh run instead of resume. | none |
| 31 | Results older than expected (refresh failing for days) | `freshness.age_hours > 36` | Badge plus `data_stale`. After 29 d, hide. Circuit breaker opens after poison. | "Last updated 3 days ago — we're retrying." |
| 32 | Poison run (deterministic crash) | same error fingerprint ×3 or 3 crash recoveries | Fail `poison`, open the circuit 24 h, alert ops with the run id and fingerprint. | "We couldn't refresh your data. Our team has been notified." |
| 33 | Run exceeds wall budget / arq job_timeout | `asyncio.timeout` / arq timeout | Runner timeout fires first (wall < job_timeout): mark the remaining stages `cancelled`, publish completed sections if finalize-eligible (`partial`), else `failed(timeout)`. | "Analysis took too long; showing what finished." |
| 34 | Cancel requested while finalize is committing | status check inside the finalize txn | Finalize txn checks `status='running'`. If `cancelling`, roll back and mark `cancelled`. Cancel endpoint on a terminal run → 409 `run_not_cancellable`. | "This run already finished." |
| 35 | User cancels then immediately restarts | cancelling run still holds the active index | New request returns 409 `run_cancelling` with `retry_after_s=5`; the UI auto-retries. Cancel grace is ≤ 30 s. | "Stopping the previous run…" |
| 36 | SSE client disconnects | generator `CancelledError` | Unregister the queue. The run is unaffected. Reconnect resumes with Last-Event-ID. | none |
| 37 | SSE behind a buffering proxy/CDN | events arrive in bursts | `X-Accel-Buffering: no`, nginx `proxy_buffering off`, no gzip on the route, 15 s pings; documented deployment checklist (Cloudflare buffers ~100 KB → bypass cache for `/events`). | none |
| 38 | Too many tabs (HTTP/1.1 six-connection limit) | — | One client-scope stream per page; HTTP/2 in prod; JSON polling fallback when SSE fails 3×. | none |
| 39 | Reconnect after run finished | Last-Event-ID ≥ last seq and run terminal | 204 (stops reconnection); otherwise replay + `end`. | none |
| 40 | Slow SSE consumer (queue overflow) | queue full | Mark lagging, drain, backfill from the DB in batches; drop progress-only frames, never lifecycle frames. | none |
| 41 | Cron overlap (slow tick, two maintenance replicas, skew) | arq `unique=True`, `RedisLock(cron:name)`, DB-clock `next_due_at` | Ticks only enqueue and are idempotent via `_job_id` slots + dedupe. | none |
| 42 | Clock skew between workers/API | — | Leases, due times, as_of and quota day all use the DB clock (`now()`). Event ordering uses seq, not ts. arq cron slot ids are minute-based so a small skew still dedupes. | none |
| 43 | Quota day boundary (midnight PT) during a run | 02 QuotaManager day key | Reservations are tagged with the PT day; the continuation run uses the new day. Ops report attributes units by PT day. | none |
| 44 | DST change for `refresh_hour_local` | zoneinfo | `next_due_at` computed with zoneinfo each time (never fixed offsets); a skipped/repeated hour runs once. | none |
| 45 | Large payloads (4k-video channel, long lists) | page size/limits | Cursor pagination (max 100); artifacts as pointers (≤ 2 MiB); event payload ≤ 4 KB (truncate lists to 5 previews); insight cap 30/stage; request body ≤ 1 MiB (413 problem); response GZip. | "Showing 100 of 4,312 videos" |
| 46 | Enormous description in refresh suggestion | 09 limits | Return a diff, not the full text twice; `preview_diff` ≤ 20 KB. | none |
| 47 | Competitor channel deleted/private/terminated | 02 returns not found | Mark competitor `unavailable` (notice). Exclude from scorecards. If confirmed < 3 → `config_invalid` for analysis; daily refresh continues with notice. | "Competitor X is no longer available on YouTube. Add another to keep 3+." |
| 48 | Confirm setup with < 3 or > 10 competitors / no topic refs | validation | 422 problem with field errors (`competitors_out_of_range`, `topic_refs_required`). | "Choose 3–10 competitors." |
| 49 | Own channel has zero/very few videos | 06 baseline undefined | Own-baseline sections show an empty state. `expected_range_vs_baseline` uses the niche baseline label (07 decision); notice. | "Your channel is new — comparing against your competitors' baseline." |
| 50 | OAuth token revoked/expired mid-run | `invalid_grant` from 02 | `own_analytics` stage degrades (non-critical); client `oauth_connected=false`; notice. | "Reconnect YouTube Analytics to see traffic sources." |
| 51 | OAuth callback with mismatched/expired `state` | Redis state lookup (10 min TTL, single use) | 400 problem `oauth_state_invalid`; nothing stored. | "The connection link expired. Try again." |
| 52 | OAuth connects a channel that isn't the client's | channel id from the token ≠ client's own channel | Refuse to store the token (revoke it); 409 `oauth_channel_mismatch`. | "You connected @X, but this client's channel is @Apollo… Use the right Google account." |
| 53 | Profile edited concurrently | `If-Match` version mismatch | 412 `precondition_failed` with the current version. | "Someone else updated this profile. Reload to see changes." |
| 54 | Feedback on a superseded recommendation | rec id belongs to an old run | Accepted and keyed by `stable_key`, so it carries forward. | none |
| 55 | Retention purges videos referenced by the active set | 07 revalidation on refresh / API join returns missing | API omits purged reference videos. If < min refs → hide the card with notice until the next refresh. | "Some cards are being refreshed with newer evidence." |
| 56 | Embedding model load slow on cold worker | startup timing | Preload in `on_startup`; readiness only after load; topics stage timeout accounts for it. | none |
| 57 | Worker memory pressure (models × jobs) | RSS metric | `max_jobs` sized per worker type; models shared per process; heavy stages (thumbnail OCR) limited by a semaphore of 2. | none |
| 58 | Misconfigured pipeline (cycle, unknown stage) | startup validation | Process exits non-zero; deploy fails health checks. | none |
| 59 | Unknown enum / new event kind seen by an old frontend | contract | API documents "clients must ignore unknown enum values / payload kinds"; never remove/rename within v1. | none |
| 60 | Backwards-incompatible API change needed | OpenAPI diff in CI | Add a new field/endpoint; deprecate the old one with `Deprecation` + `Sunset` headers ≥ 90 d; breaking → `/api/v2` router side by side. | none |
| 61 | Rate limit exceeded | token bucket | 429 problem + Retry-After + RateLimit headers. | "Too many requests — retrying in 10 s." |
| 62 | Interactive queue backlog (onboarding spike) | depth/age metrics | 202 with `queue_position`/`eta_s`; autoscale interactive workers; batch never consumes the interactive reserve. | "You're #3 in line — starting in about a minute." |
| 63 | Daily refresh cannot fit in today's quota for all clients | scheduler forecast | Round-robin fairness; unrun clients roll to the next tick or day; oldest-data-first ordering; ops alert. | stale badge if > 36 h |
| 64 | News radar due for many AI clients at once | cadence slots | Shared query cache (08) + jittered slot per client + `sem:tavily`. | none |
| 65 | Backtest run (frozen T) touches live data | `kind=backtest` | `frozen_corpus` ingest only reads ≤ T; finalize writes to evaluation tables; never publishes. | n/a |
| 66 | Huge onboarding site (10k pages) | page budget | 40 pages interactive (priority: services, about, locations, pricing via sitemap/nav heuristics in 03); rest via recrawl ≤ 200. | none |
| 67 | Run row stuck `cancelling` (worker gone) | reconciler | After grace + lease expiry → `cancelled`. | none |
| 68 | Event table write fails but stage succeeded | flush error | Retry with buffer. Stage status is authoritative in `stage_runs`. On final failure, a synthetic `stage.succeeded` is emitted on next flush (seq continuity preserved). | none |
| 69 | Request from another workspace for a client id | authz dependency | 404 (not 403) to avoid leaking existence. | "Not found" |
| 70 | Expired/rotated API key | auth | 401 problem `invalid_credentials`; `WWW-Authenticate: Bearer`. | — |

## 9. Policy & guardrail check

| Guardrail | How this component complies |
|---|---|
| Official APIs only | Stages call only 02's gateway (Data API v3 / Analytics API via OAuth). No YouTube scraping in any stage; crawler stages target websites only and respect robots (03). |
| Quota 10k/day; search bucket; batching; shared cache | Budgets per stage/run; `QuotaManager` reservations with interactive reserve and fair share; single-flight channel locks; daily refresh is incremental; niche searches shared by query hash; the ops endpoint exposes usage per client. |
| Metrics = API data + simple maths | The pipeline only orchestrates. The API returns 06's numbers unchanged inside `ResultEnvelope`. Event messages are code templates whose numbers come from code. |
| Never merge YouTube with other sources | `own-performance` returns `youtube_public`, `youtube_analytics` (OAuth) and `website` as **separate sibling sections**; no combined fields; labels included. |
| No revenue/monetisation, no demographics | No endpoint or event field carries such data; schema review in contract tests (a forbidden-field list: revenue, cpm, rpm, earnings, age, gender, demographic). |
| No content-category inference | `topics` stage passes only the client's own `topic_refs`. The run fails `config_invalid` without refs. The API exposes `review_flag` from 05. |
| Refresh/delete API data within 30 days | `run_events` and `run_artifacts` (which hold titles/stats) expire at 29 d; the retention cron runs daily; the active set is hidden when data ages out; paused clients age out. |
| Thumbnails: simple traits only | `enrich` calls 06's trait extractor only; no face features are in any schema. |
| Conversion lens honest | Recommendation `lens`/`buyer_question` come from 07; own-channel subscribers/traffic sources shown separately when OAuth is connected. |
| LLM only phrases evidence | `write_cards` is the only LLM step in the analysis core (plus bounded edges: profile, competitor discovery, news). Fallback templates exist; `rendered_by` exposed. |
| Quality bar (≥ 3 refs, honest confidence, thin data → say so, no copying) | Enforced by 07 gates before `finalize`; the API never shows ungated drafts; thin-data notices surface `suggest.widen_window_to`. |
| OAuth data privacy | Tokens encrypted (01), scoped to one client; the callback verifies channel ownership; own analytics are never shared across clients or used in niche aggregates. |
| Cross-client caching within policy | Shared cache holds public data only; per-client artifacts are tenant-scoped; client deletion purges tenant rows, not public cache (02 handles TTL). |

## 10. Performance, cost & scaling

**Latency budget: warm daily refresh** (own + 5 competitors; critical path in bold)

| Stage | p50 | p95 | Notes |
|---|---|---|---|
| **snapshot_config** | 0.2 s | 0.5 s | |
| **ingest (incremental)** | 8 s | 25 s | ~6 playlistItems + ~6 videos.list calls, 4 parallel |
| niche_discovery | 0.5 s | 5 s | cache hit typical |
| news (if due) | 15 s | 40 s | off critical path |
| **enrich** | 3 s | 15 s | new thumbnails only |
| **topics** | 3 s | 15 s | new videos only; borderline LLM adjudication via haiku |
| **analytics (3 windows)** | 3 s | 8 s | SQL + numpy |
| **recommend (3 windows)** | 2 s | 5 s | |
| **write_cards** | 15 s | 50 s | changed cards only (typically 3–8), 6 parallel; 60 s soft cap |
| refresh | 5 s | 15 s | parallel |
| **finalize** | 0.5 s | 2 s | |
| **Total** | ~35 s | ~110 s | target p95 < 120 s; `reco_ms` p95 ≈ 25–80 s |

Cold first analysis (10 competitors × 90 d): ingest 60–150 s (cache-dependent), cards for all ~15–25 drafts 40–70 s. `reco_ms` target p95 < 120 s holds; total wall ≈ 2–4 min, streamed live. Onboarding: crawl ≤ 120 s ∥ prefetch; profile 20–40 s; discovery 30–60 s → ≈ 2–4 min to `awaiting_confirmation`.

**Quota per client per daily refresh** (estimate; 02 owns the authoritative plan): playlistItems ~6, videos.list ~6–10 (≤ 50 ids each), channels.list 1, commentThreads ~5–10 (new outliers only) → **≈ 20–30 units/day/client**, plus snapshots (02) and ~0–1 search.list calls (separate bucket, shared). 10,000 units minus 35% snapshots and 1,500 interactive reserve supports roughly **150–200 clients/day** on one project. Exact figures come from the ops report (12).

**LLM per client per daily refresh**: cards ~3–8 rewritten × (~2.5k input incl. cached system prompt, ~500 output); topic adjudication ~0–20 haiku calls × (~800 in / 50 out); news relevance per due scan ~5–15 haiku calls. Prices come from 10's configurable price table. The per-run USD budget caps spend (0.30 USD default), so a mispriced loop cannot run away.

**Scaling**: API p95 < 150 ms for reads (indexed active-set lookups plus 06 tables). SSE: ~1,000 streams per API process (each ~a few KB plus a queue). Redis load = 1 pubsub connection per process. Workers: interactive 2 procs × 4 jobs; batch 2–N procs × 6; maintenance 2 × 4. Scale batch by queue depth/age. DB connection budget per §5.10.

**Observability**: structlog JSON with run_id/stage. Metrics:
- `run_duration_seconds{kind,status}`, `stage_duration_seconds{stage,status}`, `reco_seconds`
- `queue_depth{queue}`, `queue_oldest_age_seconds`
- `quota_units_total{client}`, `llm_usd_total{model}`
- `sse_connections`, `sse_backfills_total`, `pubsub_gaps_total`, `reconciler_requeues_total`

Alerts: poison runs, reco p95 > 120 s, interactive oldest > 120 s, workers missing (arq health key absent).

## 11. Testing strategy

- **Unit**
  - Run/client state-machine tables: every allowed and forbidden transition, plus property tests with hypothesis-style generated sequences.
  - Fingerprint stability (same inputs → same hash; version bump changes it).
  - Percent monotonicity; cursor encode/decode tamper detection (HMAC); problem mapping for each `AppError`; idempotency fingerprint canonicalisation; rate limiter Lua via fakeredis or a real Redis container.
- **Runner (fake stages)**
  - Linear, diamond and parallel DAGs; cycle/unknown-dep detection.
  - Critical vs non-critical failure; optional deps; soft/hard timeout; retries with `retry_after`.
  - Budget exceeded; cancellation at a checkpoint and via abort; resume after a failure skips succeeded stages; cross-run reuse with max age.
  - Lease fencing with two concurrent executors (asyncio).
  - `CancelledError` without a cancel flag → `interrupted`.
- **Integration (real Postgres 18 + Redis, arq `burst=True` worker)**
  - Enqueue → run → events persisted with gap-free seq.
  - Duplicate enqueue returns None; partial unique index race (two concurrent `request_run`).
  - Kill the worker process mid-stage → reconciler resumes.
  - Redis `FLUSHALL` mid-run → re-enqueue, SSE poll fallback.
  - DB restart → retry/resume.
  - Cron `unique` with two maintenance workers.
- **SSE**
  - httpx AsyncClient streaming against the app: replay from `Last-Event-ID`.
  - Live + replay dedupe; gap backfill (drop a pubsub message deliberately); 204 on a terminal run.
  - Ping cadence (time-mocked); disconnect cleanup (no leaked queues); shutdown event sends `reconnect`.
  - Header assertions (`X-Accel-Buffering`, no gzip). Reset `AppStatus.should_exit` in an autouse fixture (sse-starlette note).
- **API contract**
  - Snapshot the OpenAPI document and fail CI on breaking diffs; `operationId` stability.
  - A golden JSON for the strict recommendation contract (`?format=contract` equals the assignment's shape exactly).
  - Forbidden-field scan; problem+json shape on every error path.
  - Idempotency 400/409/422/replay; pagination round-trips; authz cross-workspace 404.
- **Golden runs**: recorded fixtures (YouTube/Tavily/LLM responses via respx) for Apollo Hospitals + one dental + one auto dealer. Full onboarding→analysis produces a deterministic event sequence (types and stages) and published set; the LLM stubbed via 10's fake.
- **Performance**: a timed test over the fixture corpus (deterministic portion < 20 s, end-to-end with a fake 300 ms LLM < 60 s). A load test of 500 concurrent SSE streams per process with replay. Daily scheduler simulation with 300 clients versus the quota forecast.
- **Live smoke (manual, gated)**: one onboarding of Apollo with real keys (respecting quota: no search.list unless cache empty), verifying the stream visually in the frontend and ops quota accounting.

## 12. Implementation task breakdown (ordered; S ≤ 0.5 d, M ≈ 1–2 d, L ≈ 3 d)

| # | Task | Size | Depends on |
|---|---|---|---|
| 1 | Pipeline contracts (`Stage`, `StagePolicy`, `StageResult`, `ArtifactKey`, `RunNotice`, `RunEvent` + payload union) | S | 01 settings/container |
| 2 | Alembic migrations for §6 tables + partial indexes | M | 01 base models (clients, workspaces) |
| 3 | `RunStateMachine` / `ClientLifecycleMachine` + tests | S | 1 |
| 4 | `PgArtifactStore`, fingerprints | M | 2 |
| 5 | `ProgressReporter` (buffer, seq, throttle, persist→publish) | M | 2 |
| 6 | `DagRunner` (parallel, retries, timeouts, budgets, cancel, resume, reuse) + fake-stage tests | L | 1, 3, 4, 5 |
| 7 | `RunService` (request/dedupe/supersede/cancel/resume) + `ResultPublisher` | M | 2, 3 |
| 8 | `JobQueue` arq adapter, WorkerSettings ×3, `run_pipeline` job, lease/heartbeat | M | 6, 7 |
| 9 | Limits: `RedisLock`, `RedisSemaphore`, `TokenBucket`; wire into 02/10 adapters | M | 02 gateway, 10 client |
| 10 | Onboarding stages (thin adapters) + definition | M | 02 resolver, 03, 04 |
| 11 | Analysis/daily stages + definitions + reuse modes | L | 02, 05, 06, 07, 08, 09, 10 |
| 12 | Cron ticks: daily scheduler, snapshot tick, news tick, retention, reconciler, ops rollup | M | 8, 02 SnapshotScheduler, 01 RetentionService |
| 13 | API foundation: app factory, lifespan, error model, auth deps, pagination, idempotency, rate limit, CORS, request id | M | 01 security |
| 14 | SSE: `PubSubHub`, `RunEventStream`, polling endpoint, stream tokens | M | 5, 13 |
| 15 | Routers: clients, onboarding, channel resolve, profile, topic refs, competitors, confirm-setup | M | 13, 7, 03/04 repos |
| 16 | Routers: runs, analytics, recommendations (+contract format), feedback, news, refresh, creators | M | 13, 06–09 repos |
| 17 | Routers: OAuth connect/callback/disconnect, ops, evaluation, health | M | 02 OAuth, 12 |
| 18 | OpenAPI conventions (operationIds, tags, examples) + CI breaking-change diff + TS client generation hook | S | 15–17 |
| 19 | Integration/chaos/SSE/perf test suites | L | all above |
| 20 | Deployment notes: Redis AOF, nginx SSE config, worker/termination grace, PgBouncer settings (handed to 01) | S | 8, 14 |

Critical path for a Week-2/3 demo: 1→2→3→4→5→6→7→8→11 (ingest/analytics first) →13→16 (recommendations endpoint returning the contract) →14 (SSE).

### 12.1 Endpoint catalogue (`/api/v1`; all JSON unless noted; auth = API key `Authorization: Bearer gh_<env>_<id>.<secret>` or session cookie; roles owner/editor/viewer; viewer = GET only)

| Method & path | Request | Response | Notes |
|---|---|---|---|
| POST `/clients` | `ClientCreate`; Idempotency-Key | 201 `Client` (+ `onboarding_run: PipelineRun` if started); `Location` | 409 `client_exists` on same website/channel in workspace |
| GET `/clients` | `?lifecycle=&q=&cursor=&limit=` | `Page[Client]` | |
| GET/PATCH/DELETE `/clients/{client_id}` | PATCH: `ClientPatch` (`If-Match`) | `Client` / 202 `{status:"deleting"}` | window change may return `cards_run` |
| POST `/clients/{id}/channel/resolve` | `ChannelResolveIn` | `ChannelResolveOut` | sync, ≤ 3 API units |
| POST `/clients/{id}/onboarding` | `{website_url?, channel_ref?}` | 202 `PipelineRun` | restart/redo onboarding |
| GET `/clients/{id}/profile` | — | `BusinessProfileView`; `ETag` | |
| PATCH `/clients/{id}/profile` | `ProfilePatch` (`application/merge-patch+json`), `If-Match` | `BusinessProfileView` | bumps config version |
| POST `/clients/{id}/profile/recrawl` | — | 202 `PipelineRun` (kind recrawl) | |
| GET/POST `/clients/{id}/keywords` | `?status=` / `KeywordCreate` | `Page[KeywordItem]` / 201 | |
| PATCH/DELETE `/clients/{id}/keywords/{kid}` | `{text?, status?, priority?}` | `KeywordItem` / 204 | |
| POST `/clients/{id}/keywords/bulk-status` | `BulkStatusIn` | `{updated: int}` | accept/reject suggestions |
| same five for `/buyer-questions` | `BuyerQuestionCreate` | `BuyerQuestionItem` | |
| GET `/clients/{id}/competitors` | `?status=` | `Page[CompetitorView]` | |
| POST `/clients/{id}/competitors` | `CompetitorAddIn` | 201 `CompetitorView` (status confirmed) / 422 `channel_unresolvable` | verifies the channel exists |
| POST `/clients/{id}/competitors/suggest` | — | 202 `PipelineRun` (onboarding subset: discover only) | |
| POST `/clients/{id}/competitors/{cid}/confirm` · `/reject` | — · `CompetitorRejectIn` | `CompetitorView` | ≤ 10 confirmed (422) |
| DELETE `/clients/{id}/competitors/{cid}` | — | 204 | |
| POST `/clients/{id}/confirm-setup` | `ConfirmSetupIn`; Idempotency-Key | 202 `ConfirmSetupOut` | validates 3–10 competitors, ≥ 1 topic ref |
| POST `/clients/{id}/runs` | `RunCreate`; Idempotency-Key | 202 `PipelineRun` + `Location: /api/v1/runs/{id}` (200 if deduplicated) | |
| GET `/clients/{id}/runs` | `?kind=&status=&cursor=` | `Page[PipelineRun]` (stages omitted) | |
| GET `/runs/{run_id}` | — | `PipelineRun` | |
| POST `/runs/{run_id}/cancel` | `RunCancelIn` | 202 `PipelineRun` / 409 | |
| POST `/runs/{run_id}/retry` | `{from_stage?}` | 202 `PipelineRun` (new, `resume_of`) | |
| GET `/runs/{run_id}/events` | `Last-Event-ID` / `?after_seq=&token=` | `text/event-stream` | §5.12 |
| GET `/runs/{run_id}/events.json` | `?after_seq=&limit=` | `{items: RunEvent[], last_seq, run_status}` | polling |
| POST `/runs/{run_id}/stream-token` | — | `{token, expires_at}` | |
| GET `/clients/{id}/events` | `Last-Event-ID` | `text/event-stream` | client-scope lifecycle events; seq per client stream from `run_events.id` |
| GET `/clients/{id}/analytics/own-performance` | `?window_days=&lens=` | `ResultEnvelope[{youtube_public: OwnPerformanceReport, youtube_analytics: OwnAnalytics \| null, website: WebsiteSignals \| null}]` | sections separate by policy |
| GET `/clients/{id}/analytics/competitors` | `?window_days=&sort=` | `ResultEnvelope[list[ChannelScorecard]]` | folder grid |
| GET `/clients/{id}/analytics/competitors/{channel_id}` | `?window_days=` | `ResultEnvelope[{scorecard, cadence_series, top_outliers: VideoMetrics[≤10], trait_lift: TraitLift[], topic_coverage: [{topic_ref_id, videos, outliers}], collaborations}]` | folder open view |
| GET `/clients/{id}/analytics/competitors/{channel_id}/videos` | `?window_days=&sort=-outlier_ratio&topic_ref_id=&cursor=&limit=` | `Page[VideoMetrics+traits]` | |
| GET `/clients/{id}/analytics/trending` | `?window_days=` | `ResultEnvelope[TrendingReport]` | |
| GET `/clients/{id}/analytics/outliers` | `?scope=own\|competitors\|niche&window_days=&min_ratio=&cursor=` | `ResultEnvelope[Page[VideoMetrics]]` | |
| GET `/clients/{id}/analytics/patterns` | `?scope=competitors\|niche&window_days=` | `ResultEnvelope[list[TraitLift]]` | |
| GET `/clients/{id}/analytics/topics` | `?window_days=` | `ResultEnvelope[list[{topic_ref, own_videos, competitor_videos, outliers, review_flagged}]]` | |
| GET `/clients/{id}/recommendations` | `?type=&lens=&window_days=&buyer_question_id=&confidence=&status=&format=contract\|full&cursor=&limit=` | `ResultEnvelope[Page[RecommendationOut]]`; `ETag` | 202 + `cards_run` if the window isn't written yet |
| GET `/recommendations/{rec_id}` | — | `RecommendationOut` | |
| PUT `/recommendations/{rec_id}/feedback` | `FeedbackIn` | `{feedback}` | upsert per user |
| GET `/clients/{id}/news/opportunities` | `?status=&cursor=` | `ResultEnvelope[Page[NewsOpportunityView]]` | |
| GET `/clients/{id}/news/signals` | `?cursor=` | `Page[NewsSignal]` | |
| POST `/news/opportunities/{id}/dismiss` | — | `NewsOpportunityView` | |
| GET `/clients/{id}/refresh-suggestions` | `?status=&video_id=&cursor=` | `ResultEnvelope[Page[RefreshSuggestionView]]` | |
| POST `/refresh-suggestions/{id}/applied` · `/dismiss` | `RefreshAppliedIn` · — | `RefreshSuggestionView` | applied → 09 tracks post-change performance |
| GET `/clients/{id}/creators` | `?window_days=&cursor=` | `ResultEnvelope[Page[CreatorView]]` | |
| GET `/integrations/youtube/connect` | `?client_id=` | `{authorization_url}` (or 302 with `?redirect=1`) | state in Redis 10 min, single use |
| GET `/integrations/youtube/callback` | `?code&state` | 302 to frontend `…/clients/{id}?oauth=connected\|error` | ownership check |
| DELETE `/clients/{id}/integrations/youtube` | — | 204 | revokes the token |
| POST `/evaluation/backtests` | `{client_id, frozen_at, window_days}` | 202 `PipelineRun` (kind backtest) | owner only |
| GET `/evaluation/backtests/{run_id}` | — | 12's `BacktestReport` | |
| GET `/evaluation/outlier-labels` · PUT `/evaluation/outlier-labels/{video_id}` | `?client_id=&cursor=` · `{is_outlier, labeller, note?}` | `Page[Label]` · `Label` | |
| GET `/evaluation/outlier-precision` | `?client_id=` | `{precision, recall, n, threshold}` | |
| GET `/evaluation/reviews/queue` · POST `/evaluation/reviews` · GET `/evaluation/reviews/summary` | rubric `{recommendation_id, reviewer, specific, evidenced, relevant, actionable, not_obvious (1–5), comment?}` | — | target avg ≥ 4 |
| GET `/ops/quota` | `?date=` | `OpsQuotaOut` | owner/admin |
| GET `/ops/report` | `?from=&to=` | 12's `OpsReport` (units per client per refresh, reco p50/p95) | |
| GET `/ops/queues` | — | queue depths, oldest age, arq health | admin |
| GET `/healthz` · `/readyz` | — | 200 / 503 (db, redis, migrations at head) | unauthenticated, no details |

Conventions:
- **Pagination**: opaque `cursor` (base64url JSON of last sort keys + filter hash, HMAC-signed). `limit` default 25, max 100. A cursor used with different filters → 400 `cursor_mismatch`.
- **Filtering and sorting**: whitelisted fields only. `sort=field` or `sort=-field`. Unknown params → 400 (strict, so typos aren't silently ignored).
- **Timestamps**: RFC 3339 UTC. **IDs**: opaque strings.
- **Enums**: clients must tolerate unknown values.
- **Caching**: `ETag` on recommendation/profile reads (hash of run_id + filters/version); `If-None-Match` → 304.
- **Errors**: problem codes (stable): `validation_failed`, `not_found`, `invalid_credentials`, `forbidden`, `rate_limited`, `idempotency_key_required`, `idempotency_key_reused`, `idempotency_request_in_progress`, `run_in_progress`, `run_cancelling`, `run_not_cancellable`, `config_invalid`, `competitors_out_of_range`, `topic_refs_required`, `channel_unresolvable`, `channel_ownership_unverified`, `oauth_state_invalid`, `oauth_channel_mismatch`, `precondition_failed`, `client_deleting`, `cursor_mismatch`, `payload_too_large`, `overloaded`, `internal_error` (no stack traces; `request_id` for support).
- **CORS**: `allow_origins` from settings (frontend origins, exact), `allow_credentials=True`, explicit `allow_methods` (GET, POST, PUT, PATCH, DELETE, OPTIONS) and `allow_headers` (Authorization, Content-Type, Idempotency-Key, If-Match, If-None-Match, Last-Event-ID, X-Request-ID); `expose_headers` (Location, ETag, Retry-After, RateLimit, RateLimit-Policy, X-Request-ID, X-Run-Deduplicated, Idempotent-Replayed, Deprecation, Sunset); `max_age=600`.
- **OpenAPI**: served at `/api/v1/openapi.json`; docs UI disabled in prod or behind auth. Every route has an `operationId` (`clients_create`, `runs_events_stream`, …), tags per router, response models for all codes including `Problem`, and examples from the Apollo fixture. The frontend generates its TS client from it.
- **Versioning**: the path prefix `/api/v1` is mounted as an `APIRouter` (not a sub-app, so lifespan applies). Additive-only changes within v1. Deprecations announced with `Deprecation`/`Sunset` headers ≥ 90 days before removal in v2.

## 13. Open questions / proposed changes to shared context

1. **search.list quota (correction)**: the official quota page now says "`search.list` and `videos.insert` methods have their own quota buckets", a default of 100 search.list calls/day, "Each call costs 1 quota". Shared context §2 says "search.list is 100 units/call". Propose rewording to "search.list draws from its own bucket of ~100 calls/day (not from the 10k units)". This changes 02's quota maths: search no longer competes with videos.list units, but the 100-call bucket stays the binding constraint. Cache/share discovery as already decided.
2. **arq is in maintenance-only mode** (README; issue #510; latest 0.28.0, Apr 2026). It still meets our needs. Keep it, but **only behind the `JobQueue` port** (§4.2) so we can swap backends without touching the runner. Add this to §3 owner decisions.
3. **Run kinds**: add `cards` (lazy card writing for a newly selected window), `news_scan`, `recrawl` and `backtest` to §4's run model. Add run status `partial`, and the client `ClientLifecycle` enum (stored on 01's `clients` table, transitions owned by 11).
4. **Analysis DAG naming**: propose the shared skeleton list the stages as `snapshot_config → {ingest ∥ niche_discovery ∥ news ∥ own_analytics} → enrich → topics → analytics → {recommend → write_cards ∥ refresh} → finalize`. News runs in parallel, not sequentially. `write_cards` (10) is a separate stage from `recommend` (07).
5. **Compute all three windows deterministically; write LLM cards only for the chosen window** (others lazily). 07 and 06 must accept `windows: list[int]` per call. Please confirm.
6. **Retention scope**: `run_events` and `run_artifacts` contain API-derived titles and stats, so 01's retention policy must include them (29-day TTL). Rule for all plans: no API data in long-lived tables outside the owners' refreshed stores.
7. **Quota ledger durability**: 02's `QuotaManager` must be DB-authoritative (Redis as cache). Otherwise a Redis restart forgets the day's spend and we overrun 10k.
8. **Recommendation identity**: propose 07 emit a deterministic `stable_key` (hash of type + topic_ref + pattern/trait + window), so feedback, dedupe of "already dismissed" and backtests survive across runs. 11 owns `recommendation_feedback` keyed by it.
9. **Ownership verification for channel refs** belongs to 02's `ChannelResolver`. 11 only blocks auto-attach. Confirm 02 exposes `ownership_evidence`.
10. **Auth**: 01 should own `users`, `api_keys` (HMAC-SHA256 hashed with a server pepper; prefix `gh_<env>_`), sessions and roles. 11 consumes `ApiPrincipal{workspace_id, user_id?, role, key_id?}`.
11. **pyproject pins**: `sse-starlette>=2.1` → `>=3.0,<4` (3.x has `shutdown_event`/`shutdown_grace_period`); `arq>=0.28,<0.29`; `fastapi>=0.115` fine (0.165 current). Add a `hypothesis` dev dep for state-machine property tests (optional).
12. **Prod infra requirements** for 01's compose/deploy: Redis with AOF; nginx/ingress `proxy_buffering off` for `/api/v1/**/events`; HTTP/2 at the edge; worker termination grace ≥ 30 s; PgBouncer transaction mode with asyncpg `statement_cache_size=0` (verify with 01).
13. **Unverified**: the WHATWG SSE page was unreachable during research. The 204-stops-reconnect and `Last-Event-ID` resend behaviours come from the HTML spec as commonly implemented; verify against the spec before implementation. The arq in-progress-key TTL after a hard crash is from source knowledge; the reconciler design makes us independent of it.
14. **Spotlit AI-answer tracking** (buyer questions + cited videos) is an internal source with no adapter yet. Propose a `BuyerQuestionSource` port implementation stub and an optional `ai_citations` stage that plugs into the registry later without runner changes.
