# 12 — Evaluation & operations

Status: plan (no code). Owner: component 12. Package: `backend/app/evaluation/` (+ `backend/app/core/logging.py` conventions, `backend/app/api/ops.py`, `backend/app/workers/` alert cron).
Consistent with `00-shared-context.md`. Deviations are listed in section 13.

---

## 1. Purpose & business value

The assignment's "Evaluation: prove it works" section is where a buyer decides whether GrowthHacker is real or just generic AI advice. This component produces the proof and keeps it true in production:

1. **Backtest.** Show that videos made the way we recommend outperform the niche, and outperform two naive strategies ("copy the most-viewed recent video", random). If a client follows our cards, that is the result they should expect. It is the core sales claim, so it has to survive a sceptical statistician.
2. **Outlier precision/recall.** Every card cites outliers. If the detector flags noise (a Short among long-form videos, a video that was just uploaded, one viral video inflating the mean), every downstream card inherits the error. Hand labels measure this and tune the threshold.
3. **Human review.** Mira and a marketer judge whether the cards are specific, evidenced, relevant, actionable and not obvious. These are the qualities that make a marketer actually brief a video, which is the step that leads to conversions. Low criteria scores map to concrete gate and critic knobs.
4. **Operations.** Quota units, time to recommendations, LLM cost, crawl time and failure rates per client. These determine how many clients one Google Cloud project can serve, what we charge, and whether we meet the "< 2 min per client" bar.
5. **Policy compliance matrix.** This is the Week 1 deliverable ("a list of every feature you compute with a policy check against the guardrails"). We keep it alive as a machine-checked registry so a new feature cannot ship without a policy row.
6. **Demo.** A 20-minute, reproducible demo on 3 ICP niches plus Apollo Hospitals and Runable, backed by the artefacts above.

Conversion angle: the backtest reports results separately for patterns tied to a buyer question (conversion lens) and for view-only patterns. The human review checks "relevant to the client" against the client's buyer questions. That way we prove the conversion lens produces videos that still get watched, and do not just assume it.

---

## 2. Scope / non-scope

**In scope**
- Point-in-time freeze of all inputs at date T (`FreezeBundle`) and replay of the analysis pipeline as of T through read-only repository adapters.
- A backtest harness with three arms (system, copy-top, random) plus the niche base rate. It covers pattern matching of post-T videos, metrics, a cluster bootstrap, power/MDE, and a markdown + JSON report.
- A secondary retrospective proxy backtest (clearly labelled "proxy, not point-in-time"), used only for sensitivity analysis.
- Outlier labelling: stratified sampler, self-contained HTML label pack, CSV import, agreement statistics, weighted precision/recall, threshold-tuning curve, comparison of detector variants.
- Human review: sampler, blinded review pack (same pack mechanism), import, aggregation, agreement, gate-tuning proposals.
- Ops: aggregation over the ledgers owned by 02/10/11 (quota, LLM usage, run stages), the ops report (CLI + read-only API), alert rules and the alert cron, structlog conventions, optional OpenTelemetry/Prometheus switches.
- Feature registry + generated feature-policy matrix + CI test that every computed feature is registered.
- Demo plan, demo data schedule, evaluation report, and design-doc answer assembly.

**Not in scope**
- Writing the quota ledger, LLM usage capture or run-event emission. Those belong to 02, 10 and 11. This component defines the fields it needs (section 3.6) and consumes them.
- Frontend admin screens. 13 may later wrap the read-only endpoints; v1 uses CLI + markdown + static HTML packs.
- An online A/B test of recommendations with real clients. That is impossible inside 4 weeks; listed as future work.
- Evaluating LLM wording quality automatically. Wording is covered by human review and by 10's grounding validator; this component only reports the validator's violation rate.
- Changing gates or thresholds automatically. The plan proposes config diffs; a human approves them.

---

## 3. Inputs & outputs

### 3.1 Consumed (from other components)
| From | Object / table | Fields this component relies on |
|---|---|---|
| 01 | repositories, settings, retention registry | `Clock` port (proposed, section 13), repository Protocols so a `Frozen*Repository` adapter can implement them |
| 02 | `ChannelRecord`, `VideoRecord`, `VideoSnapshot`, quota ledger | `video_id, channel_id, published_at, duration_s, title, description, thumbnail_url, live_broadcast_content, fetched_at`; snapshot `views, likes, comments, captured_at`; ledger `endpoint, units, bucket, cache_hit, client_id?, run_id?, scope(shared/client/evaluation), occurred_at` |
| 05 | `TopicMatch`, `TopicRef` (versioned) | `video_id, topic_id, score, method, review_flag, topic_ref_version`; adjudication cache key |
| 06 | `ViewsAtAge`, `ChannelBaseline`, `VideoMetrics`, `VideoTraits`, `ThumbnailTraits`, `TraitLift` | `views_at_age(video, age_days, as_of)`, `baseline(channel, format, as_of)`, `outlier_ratio`, `is_outlier`, `method` (interpolated / observed / proxy), `outlier_ratio_threshold` |
| 07 | `RecommendationDraft`, `Recommendation`, gate decisions | **`pattern_spec` (proposed, section 13)**, `type`, `lens`, `confidence`, `expected_range_vs_baseline` (numeric low/high), gate feature values and pass/fail per gate |
| 08 / 09 | `NewsSignal`, `DescriptionRefresh` | counts only (excluded from backtest, section 5.1.9) |
| 10 | LLM usage ledger | `provider, model, tier, purpose, input_tokens, output_tokens, cache_read_input_tokens, cache_creation_input_tokens, attempt, latency_ms, status, run_id, client_id` |
| 11 | runs, run stages, `RunEvent` | `run_id, run_kind (onboarding/analysis/daily_refresh/evaluation), client_id, stage, started_at, finished_at, status, error_type, queue_wait_ms` |
| 03 | crawl ledger | `pages_fetched, bytes, robots_blocked, errors, duration_ms` per crawl |
| 03/04/08 | web-search usage | `provider=tavily, endpoint, credits, client_id, run_id` (same `external_usage` shape, section 13) |

### 3.2 Backtest domain objects (owned)
```python
class EvalConfig(BaseModel):
    eval_id: str
    mode: Literal["forward", "retro_proxy"]
    client_ids: list[UUID]
    as_of: datetime                      # T
    window_days: Literal[7, 30, 90] = 30 # W, reference window before T
    horizon_days: int = 10               # H, publish window after T
    outcome_age_days: int = 7            # a
    age_tolerance_days: float = 1.0      # max distance to nearest snapshot bracket
    arms: list[Literal["system", "copy_top", "random", "base_rate"]]
    k_per_type: dict[str, int]           # recs per type per client, mirrors what the client would see
    random_replicates: int = 1000
    bootstrap_resamples: int = 10_000
    alpha: float = 0.05
    seed: int
    outlier_threshold: float | None      # None = 06's frozen value at T
    include_own_channel: bool = False    # own uploads may be influenced by our cards
    min_universe_videos_client: int = 20
    min_matched_videos_client: int = 5
    min_universe_videos_pooled: int = 100
    min_matched_videos_pooled: int = 30
    min_channels_pooled: int = 10
    max_usd: float = 5.0                 # hard cost cap per evaluation run
    max_quota_units: int = 1000
    config_hash: str                     # sha256 of canonical json, recorded before outcomes are read
    code_version: str                    # git sha

class FreezeManifest(BaseModel):
    freeze_id: str
    client_id: UUID
    as_of: datetime
    created_at: datetime
    channel_ids: list[str]               # universe frozen at T (own + confirmed competitors + niche creators known at T)
    competitor_set_version: int
    topic_ref_version: int
    business_profile_version: int
    gate_config_version: str
    analytics_config_hash: str
    record_counts: dict[str, int]
    oldest_api_fetched_at: datetime
    expires_at: datetime                 # oldest_api_fetched_at + 30d (policy), bundle deleted by retention cron
    pit_level: Literal["strict", "approximate"]
    bundle_uri: str                      # local path / object storage key of jsonl.gz

class PatternSpec(BaseModel):            # emitted by 07 per draft; also built by baselines
    spec_id: str
    rec_type: Literal["video_idea", "title_pattern", "thumbnail_pattern", "format", "creator"]
    topic_ids: list[str] = []            # any-of
    format: Literal["short", "long", "live"] | None = None
    length_band: str | None = None
    title_traits: dict[str, bool | str] = {}      # e.g. {"is_question": True, "has_number": True}
    thumbnail_traits: dict[str, bool | str] = {}  # e.g. {"text_present": True, "word_count_band": "1-3"}
    channel_id: str | None = None        # creator recs
    lens: list[Literal["views", "conversion"]]
    buyer_question_id: str | None = None
    specificity: int                     # number of constraints, used to match random specs
    source: Literal["system", "copy_top", "random"]
    source_ref: str | None               # draft id / seed video id / replicate idx

class OutcomeVideo(BaseModel):
    video_id: str
    channel_id: str
    client_id: UUID
    published_at: datetime
    format: Literal["short", "long", "live"]
    views_at_age: int | None
    views_at_age_method: Literal["observed", "interpolated", "missing"]
    baseline_as_of_t: float | None
    baseline_method: str                 # 06 method tag at T (tracked / proxy)
    ratio: float | None
    is_outlier: bool | None
    matched_spec_ids: dict[str, list[str]]   # arm -> spec ids
    status: Literal["complete", "pending", "missing_snapshot", "deleted", "no_baseline"]

class ArmMetrics(BaseModel):
    arm: str
    scope: str                           # client:<id> | niche:<slug> | pooled
    n_specs: int
    n_specs_matched: int                 # specs with >= 1 post-T match
    n_videos_matched: int
    n_channels_matched: int
    coverage: float                      # matched / universe complete videos
    outlier_rate: float | None
    outlier_rate_ci: tuple[float, float] | None
    geo_mean_ratio: float | None
    geo_mean_ratio_ci: tuple[float, float] | None
    spec_hit_rate: float | None          # share of matched specs with >= 1 outlier
    lift_vs_base: float | None
    lift_vs_base_ci: tuple[float, float] | None

class ArmComparison(BaseModel):
    scope: str
    a: str; b: str
    metric: Literal["outlier_rate_diff", "risk_ratio", "log_ratio_diff"]
    estimate: float
    ci: tuple[float, float]
    p_value: float | None
    p_adjusted: float | None
    method: str                          # "stratified cluster bootstrap" | "empirical (random arm)"

class BacktestReport(BaseModel):
    config: EvalConfig
    freezes: list[FreezeManifest]
    verdict: Literal["supported", "not_supported", "inconclusive_underpowered", "invalid"]
    arms: list[ArmMetrics]
    comparisons: list[ArmComparison]
    mde: dict[str, float]                # minimum detectable lift given realised n
    calibration: dict[str, Any]          # confidence-label monotonicity, range coverage
    missingness: dict[str, int]
    limitations: list[str]               # auto-filled from failed checks
    generated_at: datetime
```

### 3.3 Outlier labelling objects (owned)
```python
class LabelTask(BaseModel):
    task_id: str
    batch_id: str
    video_id: str
    channel_id: str
    stratum: str                         # "<size_band>|<ratio_band>"
    inclusion_prob: float                # for Horvitz-Thompson weighting
    detector_ratio: float                # hidden from labellers
    detector_label: bool                 # hidden
    detector_version: str                # 06 config hash
    observed_at: datetime                # data shown in the pack is as of this instant
    expires_at: datetime

class Label(BaseModel):
    task_id: str
    labeller_id: str
    round: int
    label: Literal["outlier", "not_outlier", "unsure"]
    reason_codes: list[Literal["format_mismatch", "too_young", "few_channel_videos", "viral_skews_channel",
                               "possible_paid_promotion", "live_or_premiere", "collab", "other"]]
    note: str | None
    seconds_spent: int | None
    labelled_at: datetime

class LabelAgreement(BaseModel):
    batch_id: str
    n_items: int
    percent_agreement: float
    cohen_kappa: float
    gwet_ac1: float
    n_unsure: dict[str, int]

class ThresholdPoint(BaseModel):
    threshold: float
    precision_w: float; recall_w: float; f05_w: float; f1_w: float
    precision_ci: tuple[float, float]; recall_ci: tuple[float, float]

class OutlierEvalResult(BaseModel):
    batch_id: str
    detector_version: str
    n_gold: int
    confusion_weighted: dict[str, float]
    confusion_raw: dict[str, int]
    precision_w: float; recall_w: float; f1_w: float
    precision_ci: tuple[float, float]; recall_ci: tuple[float, float]
    by_stratum: dict[str, dict[str, float]]
    curve: list[ThresholdPoint]
    recommended_threshold: float | None
    variants: dict[str, dict[str, float]]   # "median_baseline", "mean_baseline", "format_split", ...
```

### 3.4 Human review objects (owned)
```python
class ReviewItem(BaseModel):
    item_id: str
    batch_id: str
    recommendation_id: str | None        # None for decoys
    client_id: UUID
    card_snapshot: dict                  # rendered contract object, contains API data -> expires
    is_decoy: bool                       # copy-top baseline card written by the same writer
    gate_features: dict[str, float]      # topic score, n_outliers, n_channels, min_ref_ratio, lift, critic score, novelty
    expires_at: datetime

class ReviewRating(BaseModel):
    item_id: str
    rater_id: str
    specific: conint(ge=1, le=5)
    evidenced: conint(ge=1, le=5)
    relevant: conint(ge=1, le=5)
    actionable: conint(ge=1, le=5)
    not_obvious: conint(ge=1, le=5)
    would_ship: bool
    comment: str | None
    rated_at: datetime

class ReviewSummary(BaseModel):
    batch_id: str
    n_items: int; n_raters: int
    overall_mean: float; overall_ci: tuple[float, float]
    by_criterion: dict[str, tuple[float, float, float]]   # mean, ci_low, ci_high
    share_items_ge4: float
    krippendorff_alpha_ordinal: dict[str, float]
    decoy_gap: dict[str, float] | None   # system mean - decoy mean per criterion
    by_type: dict[str, float]; by_client: dict[str, float]
    passed: bool                         # overall >= 4.0 and every criterion >= 3.5

class GateTuningProposal(BaseModel):
    feature: str
    spearman_rho: float
    current_threshold: float | None
    proposed_threshold: float | None
    retained_share: float
    retained_mean_rating: float
    config_diff: dict
    rationale: str
    status: Literal["proposed", "accepted", "rejected"]
```

### 3.5 Ops objects (owned)
```python
class StageTiming(BaseModel):
    stage: str; p50_ms: int; p95_ms: int; max_ms: int; n: int; failure_rate: float

class QuotaUsageSummary(BaseModel):
    day_pt: date                         # quota days are Pacific Time
    client_id: UUID | None               # None = project total
    units_marginal: int                  # units spent inside this client's runs
    units_amortised: float               # + share of shared-cache fills consumed by this client
    by_endpoint: dict[str, int]
    search_calls: int                    # separate bucket
    cache_hit_rate: float

class LlmUsageSummary(BaseModel):
    client_id: UUID | None
    period: str
    by_model_purpose: dict[str, dict[str, int]]   # tokens by kind
    usd: float
    usd_per_daily_refresh: float
    retries: int
    schema_failures: int
    grounding_violations: int

class OpsReport(BaseModel):
    period_from: datetime; period_to: datetime
    clients: int
    quota: list[QuotaUsageSummary]
    run_time: dict[str, list[StageTiming]]   # run_kind -> stages + "total"
    llm: list[LlmUsageSummary]
    web: dict[str, float]                # tavily credits, usd
    crawl: dict[str, float]              # pages, p95 duration, robots blocked, error rate
    failures: dict[str, float]           # per stage, per external provider, gate rejection rates
    retention: dict[str, int]            # rows with api data older than 29/30 days (must be 0)
    capacity_estimate: dict[str, float]  # clients per project at current units/client
    targets: dict[str, bool]             # "<2min per client", "quota/client <= budget", ...

class AlertRule(BaseModel):
    rule_id: str; severity: Literal["warn", "critical"]; query: str
    threshold: float; window: str; dedupe_minutes: int = 60

class AlertEvent(BaseModel):
    rule_id: str; severity: str; value: float; threshold: float
    fired_at: datetime; resolved_at: datetime | None; payload: dict
```

### 3.6 Feature registry (owned, consumed by every component)
```python
class FeatureSpec(BaseModel):
    key: str                             # "analytics.outlier_ratio"
    owner: str                           # "06"
    description: str
    sources: set[Literal["yt_data_public", "yt_analytics_oauth", "website", "web_search",
                         "client_input", "spotlit_ai_tracking", "llm", "internal"]]
    maths: Literal["raw", "count", "sum", "mean", "median", "ratio", "quantile", "interpolation",
                   "threshold_flag", "rank", "lexical_match", "embedding_similarity",
                   "llm_judgement", "image_simple", "text_generation"]
    scope: set[Literal["own", "competitor", "niche"]]
    displayed_as_metric: bool
    stored: bool
    retention_days: int | None
    checks: dict[str, Literal["OK", "COND", "FLAG", "n/a"]]   # per guardrail G1..G6
    notes: str
```
`FeatureRegistry.register(spec)` is called at import time in each owning module. `render_policy_matrix() -> str` produces the markdown in section 9. A CI test fails if any field of `VideoMetrics`, `VideoTraits`, `ThumbnailTraits`, `CommentSignals`, `ChannelScorecard`, `TraitLift`, `OwnPerformanceReport`, `TrendingReport`, `NewsSignal` or the recommendation scorer has no registered `FeatureSpec`, or if any spec has `FLAG` without a linked review note.

---

## 4. Internal design

```
backend/app/evaluation/
  __main__.py            cli: freeze | backtest | labels | review | ops | policy | report
  config.py              EvalConfig loading (yaml), canonical hashing
  freeze/
    bundle.py            FreezeJob, FreezeBundle writer/reader (jsonl.gz + manifest)
    frozen_repos.py      Frozen*Repository adapters implementing 01's repository Protocols, as_of filtering
    clock.py             FixedClock(as_of) implements Clock port
  backtest/
    arms.py              ArmGenerator Protocol + registry: SystemArm, CopyTopArm, RandomArm, BaseRateArm
    outcomes.py          OutcomeCollector (post-T universe, views at age, baseline as of T)
    matcher.py           PatternMatcher (spec x traits x topic matches)
    metrics.py           arm metrics, calibration checks
    runner.py            BacktestRunner (orchestrates; checkpoints; cost/quota caps)
    retro_proxy.py       RetroProxyBacktest (secondary, labelled)
  stats/
    bootstrap.py         stratified cluster bootstrap, percentile CIs, seeded
    proportions.py       wilson, newcombe, power/mde
    agreement.py         cohen kappa, gwet ac1, krippendorff alpha (ordinal), weighted kappa
    sampling.py          stratified sampler, inclusion probabilities, horvitz-thompson estimators
  labelling/
    sampler.py           OutlierSampleDesigner
    pack.py              PackRenderer (shared by labelling and review): jinja2 -> single html, csv export button
    importer.py          csv -> Label rows, validation
    scorer.py            agreement, gold resolution, weighted p/r, threshold curve, variant comparison
  review/
    sampler.py           ReviewSampleDesigner (stratified, decoys)
    importer.py, scorer.py, gate_tuning.py
  ops/
    report.py            OpsReportBuilder (sql over ledgers)
    alerts.py            AlertRule registry, AlertEvaluator, notifiers (log, webhook)
    capacity.py          clients-per-project estimate
  policy/
    registry.py          FeatureSpec, FeatureRegistry
    matrix.py            render_policy_matrix
  reporting/
    templates/*.md.j2    evaluation-report, ops-report, policy-matrix
    builder.py           ReportBuilder -> docs/eval/*.md + json
```

**SOLID extension points**
- `ArmGenerator` Protocol: `generate(ctx: FrozenContext, k_per_type, rng) -> list[PatternSpec]`. New baselines (e.g. "copy the trending chart") register without touching the runner.
- `MetricComputer` Protocol: `compute(outcomes, specs) -> dict[str, float]`. Adding a metric means registering one class.
- `PackRenderer` is generic over a `PackSchema` (columns, hidden fields, rubric). Labelling and review share it.
- `AlertNotifier` Protocol: `LogNotifier` (default), `WebhookNotifier` (Slack-compatible URL, optional).
- `UsageSource` Protocol per ledger, so ops reports do not hard-code table names owned by other components.
- The `Frozen*Repository` adapters implement the same Protocols as the live repositories, so the pipeline (11) runs unchanged in replay. Only the container wiring differs (`container.for_evaluation(freeze)`).

**Key signatures**
```python
class FreezeJob:
    async def run(self, client_id: UUID, as_of: datetime) -> FreezeManifest: ...

class BacktestRunner:
    async def run(self, config: EvalConfig) -> BacktestReport: ...
    async def replay_recommendations(self, freeze: FreezeManifest) -> list[RecommendationDraft]: ...  # writer disabled

class PatternMatcher:
    def matches(self, spec: PatternSpec, traits: VideoTraits, topics: list[TopicMatch]) -> bool: ...

def stratified_cluster_bootstrap(
    rows: Sequence[Row], stat: Callable[[Sequence[Row]], float], strata_key: str, cluster_key: str,
    resamples: int, rng: np.random.Generator,
) -> tuple[float, tuple[float, float], np.ndarray]: ...

def weighted_precision_recall(gold: Sequence[bool], pred: Sequence[bool], weights: Sequence[float]) -> tuple[float, float]: ...
```

---

## 5. Algorithms & logic

### 5.1 Backtest

#### 5.1.1 The core difficulty: views at age without history
The Data API returns only **current lifetime** `viewCount`. Day-7 views exist only for videos we observed around their day 7. There is no API for historical view counts of public videos (Analytics API history requires owner OAuth). So:
- For videos published **after we started tracking**, `ViewsAtAge(7)` comes from snapshots: an observed value or a linear interpolation between two bracketing snapshots within ±`age_tolerance_days` (06 owns this). This is strictly point-in-time.
- For videos published **before tracking**, any day-7 number is a proxy (06's fallback). It must never be presented as observed.

Therefore the **primary backtest is forward (prospective) by construction**. T is set on or after the tracking start, so every outcome video (published in (T, T+H]) is tracked from birth and its day-7 views are observed, not reconstructed. The recommendation side at T may use 06's proxy baselines for pre-tracking videos. That is legitimate because it is exactly what the live system would have known at T. Leakage is about using information from after T, not about using imperfect information.

#### 5.1.2 Mode A — forward backtest (primary)
1. **Freeze at T** (`FreezeJob`, cron or CLI, runs at T):
   - Dump everything the pipeline reads, as of T: channels, videos (metadata as currently stored, `fetched_at <= T`), snapshots with `captured_at <= T`, TopicRefs/buyer questions (version at T), BusinessProfile version, confirmed competitor set, niche creator set, analytics and gate config hashes, 05's adjudication cache entries. Written to `eval_freeze` + `jsonl.gz` bundle.
   - `pit_level="strict"` when every row satisfies `fetched_at/captured_at <= T`. Otherwise `approximate`, with the offending tables listed.
   - Universe U_c(T) = own channel (only if `include_own_channel`) + confirmed competitors + niche creators known at T. Channels added after T are never part of this evaluation.
   - Freezing needs no recommender, so it can run in Week 2 before 07 exists (see the schedule in 5.6).
2. **Pre-register**: before any outcome is read, `EvalConfig` (θ, W, H, a, k, arms, seeds) is hashed and stored with `registered_at`. The outcome collector refuses to run if the config hash changed after registration. Any change needs a new `eval_id`, and the report lists every eval_id run (prevents forking-paths p-hacking).
3. **Replay at any time ≥ T**: build the container with `FixedClock(T)` + `Frozen*Repository`. Run ingest(no-op, cache only) → enrich → topics → analytics → recommend with **the writer disabled** (patterns come from deterministic code; wording is irrelevant to the backtest and costs money). Collect `RecommendationDraft.pattern_spec` for drafts that **pass gates**. Separately keep pre-gate candidates for an ablation ("do the gates help?").
   - Topic LLM adjudication: cache-only by default. Cache misses are resolved at temperature 0 with the pinned model and prompt version, and logged as `post_hoc_adjudications` (reported).
4. **Arms** (per client, k_per_type equal across arms so the comparison is fair):
   - `system`: top-k gated drafts per type, in the order 07 would display.
   - `copy_top` (naive baseline 1): rank videos in U_c(T) published in [T−W, T) by raw `viewCount` as of T (naive on purpose: raw views, not age-normalised). Excludes the client's own videos. Take top-k. For each, fill the same spec template as the system uses for that rec type from the video's own attributes (best TopicMatch, format, length band, title traits, thumbnail traits, channel_id for creator recs). Ties are broken by earlier publish date, then video_id.
   - `random` (naive baseline 2): for each system spec, draw R=1000 random specs of the **same type and same specificity**. Each constraint value is sampled uniformly from values observed among pre-T window videos, so the spec is feasible. topic_ids come from the client's TopicRefs. creator specs pick from channels in U_c(T). Seeded per replicate via `SeedSequence.spawn`.
   - `base_rate`: all complete post-T videos in the universe ("beat the niche baseline").
5. **Outcome collection** at E ≥ T + H + a + 1 day:
   - Post-T universe: videos in U_c(T) with `published_at ∈ (T, T+H]`, discovered through the normal ingest (uploads playlist). The snapshot cron (02) must schedule captures near ages 1, 3, 7 and 14 days.
   - `views_at_age(a)`: observed if a snapshot is within ±0.25 d of age a. Interpolated linearly if snapshots bracket age a within ±`age_tolerance_days`. Otherwise `missing_snapshot`. Missing values are **never extrapolated**.
   - Baseline: `ChannelBaseline(channel, format, as_of=T)` from the freeze, so post-T videos never contaminate their own baseline. The report also includes a sensitivity analysis with the baseline recomputed at E from tracked-from-birth videos only.
   - Ratio r = views_at_age / baseline. `is_outlier = r ≥ θ_T` (06's threshold frozen at T, default assumed 3.0; 06 owns the value).
   - Deleted/private videos (absent from `videos.list`) get status `deleted`, are excluded, and are counted. Survivorship caveat: deleted videos skew towards underperformers.
6. **Matching**: run 05 on post-T videos using TopicRefs at T. Compute 06 traits (title, length band, thumbnail traits). `PatternMatcher.matches` is true iff every constraint in the spec holds:
   - `topic_ids`: any TopicMatch with score ≥ 05's accept threshold at T.
   - `format`/`length_band`: equality.
   - title/thumbnail traits: equality on each listed key.
   - `channel_id`: equality.
   A video belongs to an arm if it matches ≥ 1 of the arm's specs; deduplicated at video level.
7. **Metrics** per arm and scope (client, niche, pooled):
   - **M1 outlier rate** (primary) = outliers among matched / matched complete videos.
   - **M2 geometric-mean ratio** = exp(mean(log r)) over matched. Robust to heavy tails, answers "views at day 7 vs channel average".
   - **M3 lift vs base** = M1_arm / M1_base_rate.
   - **M4 spec hit rate** = share of specs with ≥ 1 matched video that have ≥ 1 matched outlier. Specs with zero matches are "untestable", reported separately and not counted as failures.
   - **M5 coverage** = matched / universe. Needed to spot trivially broad or narrow patterns. Arm overlap is reported as Jaccard similarity of matched sets.
   - **Calibration**: (i) is M1 monotone across confidence labels (High > Medium > Low)? (ii) range coverage: the share of matched videos whose r falls within the spec's `expected_range_vs_baseline` (target 50–80% for an "indicative" interquartile-style range; much lower means overconfident).
   - **Lens split**: M1/M2 separately for specs with `conversion` in `lens` vs views-only specs.
   - **Within-channel contrast** (secondary): for channels with both matched and unmatched post-T videos, compare log r within channel. Mantel–Haenszel risk ratio stratified by channel for M1 (Mantel & Haenszel 1959). Removes channel-mix confounding.
   - **Gate ablation**: M1 for pre-gate candidates vs gated specs.

#### 5.1.3 Statistical inference
- Unit = video; videos cluster within channels, channels within clients. CIs use a **stratified cluster bootstrap**: within each client stratum, resample channels with replacement and keep all their videos. Recompute every arm's statistic on the same resample, so arm comparisons are paired. B = 10,000 resamples, percentile intervals (Efron & Tibshirani 1993; Davison & Hinkley 1997, §3.8 on clustered data; Field & Welsh 2007; Cameron & Miller 2015 on few-cluster inference).
- **Primary hypothesis (pre-registered)**: pooled M1(system) − M1(copy_top) > 0. Reported as the difference and the risk ratio, with the bootstrap 95% CI. One-sided bootstrap p = share of resamples ≤ 0.
- **Random arm**: empirical p = (1 + #{replicates with M1 ≥ M1_system}) / (R + 1) (Phipson & Smyth 2010). Also reports the system's percentile within the random distribution.
- **vs base rate**: bootstrap CI of M3. For small matched n, a Wilson score interval on M1 alone is also shown (Wilson 1927; Brown, Cai & DasGupta 2001).
- **Secondary comparisons** (M2, per niche, lens split, within-channel) use Holm's step-down adjustment (Holm 1979) and are labelled exploratory.
- **Few clusters**: if a scope has fewer than 5 channels, bootstrap CIs are unreliable (Cameron, Gelbach & Miller 2008). Show the Wilson interval and mark "few clusters".
- **Verdict**: `supported` if the primary CI lower bound is > 0 and the random-arm p < α. `not_supported` if the CI upper bound is ≤ 0. `inconclusive_underpowered` if minimum samples are unmet or the CI spans 0. `invalid` if leakage checks fail or missingness exceeds 20%.

#### 5.1.4 Minimum sample sizes and power
Two-proportion sample size per arm, α = 0.05 two-sided, power 0.8 (Fleiss, Levin & Paik 2003, ch. 4):
n = [z₀.₉₇₅·√(2·p̄q̄) + z₀.₈·√(p₁q₁ + p₂q₂)]² / (p₁ − p₂)²

| Base outlier rate p₁ | Target rate p₂ (lift) | n per arm |
|---|---|---|
| 0.10 | 0.20 (2x) | ~199 |
| 0.10 | 0.30 (3x) | ~62 |
| 0.15 | 0.30 (2x) | ~121 |
| vs. a known base rate 0.10 → 0.20 (one-sample) | | ~86 |

Expected demo volume: 5 clients × ~15 channels × ~1.5 uploads/week × H=10 days ≈ 160 post-T videos. Roughly 30–50% match the system arm, so ~50–80 matched. **Honest conclusion: the demo backtest can detect about a 3x lift, not a 2x lift.** The report always prints the minimum detectable lift (MDE) for the realised n. Hard floors (configurable):
- Per-client scope is shown only with ≥ 20 complete universe videos and ≥ 5 matched per arm. Otherwise it appears only inside the pooled result.
- The pooled verdict needs ≥ 100 complete universe videos, ≥ 30 matched per arm and ≥ 10 channels. Otherwise the verdict is `inconclusive_underpowered`.
- Power levers, in order of cost: track ~15 channels per demo client (5 competitors + 10 niche creators; this costs only a few quota units), add a second freeze T₂ = T + 7 d (rolling origin, Tashman 2000; overlapping horizons stay clustered by channel), lengthen H to 14 d if the schedule allows.

#### 5.1.5 Mode B — retrospective proxy backtest (secondary, labelled)
Purpose: more videos and more niches without waiting. It is **not point-in-time** and is shown only as a sensitivity check next to Mode A, never alone.
- Single fetch time F (one uploads crawl + `videos.list` per channel). Pick T = F − H − 28 d so every outcome video is ≥ 28 days old at F.
- Proxy ratio **CPR** (contemporaneous peer ratio): v_i(F) / median{ v_j(F) : same channel, same format, |published_j − published_i| ≤ 14 d, j ≠ i, ≥ 3 peers }. Peers of similar age have similar exposure time, which approximates a same-age comparison using only API counts and simple maths.
- The recommendation side at T uses only pre-T videos published ≤ T − 30 d, scored by CPR at F. Late growth after T for these videos is small relative to their first 30 days. This is an assumption, and Mode A data is used to measure it: report the Spearman correlation and Cohen's κ between CPR-based outlier labels and true day-7 labels for tracked videos.
- Known leakage, printed in the report: (a) stats include post-T growth; (b) titles/thumbnails may have been edited after success (YouTube "Test & Compare" replaces thumbnails); (c) the channel universe was discovered after T, which brings survivorship bias. Mode B never sets the verdict.

#### 5.1.6 Reporting format (`docs/eval/evaluation-report.md` + `eval_result` rows)
1. Headline: verdict, primary estimate with CI, MDE, and one sentence on limitations.
2. Setup table: clients, niches, T, W, H, a, θ, universe sizes, pit_level, config hash, git sha, eval_ids run.
3. Arm table (pooled and per niche): arm | specs | matched videos | channels | coverage | outlier rate [95% CI] | geo-mean ratio [CI] | lift vs base [CI] | spec hit rate.
4. Comparisons table: system vs copy_top, system vs random (percentile, p), system vs base. Raw and Holm-adjusted p.
5. Lens split, confidence calibration, range coverage, gate ablation, within-channel contrast.
6. Missingness and exclusions (deleted, missing snapshot, no baseline, post-hoc adjudications).
7. Mode B sensitivity (if run) with its leakage banner.
8. Limitations (auto-generated from failed checks plus the fixed list in 5.1.1/5.1.5).
9. Appendix: per-spec table (spec, type, matched, outliers, example post-T video ids with links). Video-level data expires per policy; the markdown keeps only aggregates after 30 days (section 6).

#### 5.1.7 Leakage controls (must all pass, else `invalid`)
- Every row read during replay has `fetched_at/captured_at ≤ T`. The frozen repository asserts this on read.
- Universe channels ⊆ channels known at T.
- TopicRefs, profile, gate and analytics config are the versions at T.
- θ is the value at T. A θ tuned in 5.2 is applied only to later freezes.
- Baselines are as of T.
- **Leakage canary test** (CI and per run): duplicate the freeze, randomly perturb every row with timestamp > T, replay, and assert that the specs are byte-identical.

#### 5.1.8 Creator recommendations
A `creator` spec matches the creator's post-T videos. M1/M2 compare the creator's post-T performance against the niche base. copy_top uses the channel of the most-viewed recent video. random draws a channel from the same universe. This answers "did the creators we ranked keep performing?" without any audience inference.

#### 5.1.9 Types excluded from the backtest
- `news_opportunity`: Tavily has no reproducible "as of T" search, so news inputs cannot be frozen retroactively. They are evaluated **prospectively only**: at each freeze, store the NewsSignals. At E, report how many post-T YouTube videos in the universe matched each news TopicRef and their M1/M2. Descriptive only, no verdict.
- `description_refresh`: needs the client to apply edits. If a client applies one, compare the refreshed video's views/day for the 14 days before and after against the client's untouched videos of similar age (difference-in-differences, descriptive). With OAuth, also compare YT_SEARCH traffic share. Not part of the demo verdict.

### 5.2 Outlier precision/recall

**Population**: complete videos (with a computed ratio) across the five demo clients' universes at the label snapshot instant.

**Sampling (n = 100, stratified)**
- Size bands from `subscriberCount`: S1 < 10k, S2 10k–100k, S3 100k–1M, S4 > 1M, plus S0 = hidden subscriber count.
- Ratio bands relative to the current θ: R1 r ≥ 2θ, R2 θ ≤ r < 2θ, R3 0.6θ ≤ r < θ (near misses, where false negatives live), R4 r < 0.6θ.
- Allocation across ratio bands: R1 15, R2 30, R3 25, R4 30. This oversamples positives and near misses, because outliers are rare (~5–10%) and simple random sampling would give too few positives to estimate recall. Within each ratio band, split as evenly as possible across size bands, with ≥ 2 per non-empty cell. Empty cells are reallocated to the neighbouring size band. Cap of 3 videos per channel. Formats are proportional (Shorts included and labelled).
- Record each video's inclusion probability π = n_cell / N_cell. Estimates use Horvitz–Thompson weights w = 1/π (Horvitz & Thompson 1952), as in stratified classifier evaluation (Bennett & Carvalho 2010). Unweighted sample metrics are also reported for transparency.

**Label pack (self-contained HTML, one per labeller)**
- Per item: video link and thumbnail (hotlinked from the API-provided URL, not stored). Title, format, duration, publish date, age at observation and views at observation. The channel's last 30 uploads as a table and a log-scale views-vs-publish-date chart with age shown, target highlighted. Subscriber band. Observation timestamp.
- **Hidden**: detector ratio, detector label, baseline value, stratum.
- Items appear in a seeded random order per labeller, in batches of 25 with save-progress (localStorage). An "Export CSV" button produces `task_id, label, reason_codes, note, seconds_spent`.
- Labellers are told to judge from the pack's numbers, not live YouTube counts, which have moved since the observation timestamp.

**Labeller guidelines (one page, shipped in the pack header)**
- Question: "Compared with this channel's typical video **of the same format** at a **similar age**, did this video clearly break out?" Answer outlier / not outlier / unsure.
- Rules:
  - Compare like with like: Shorts with Shorts, long-form with long-form, lives with lives.
  - Account for age. A 2-day-old video with 5k views can beat a 60-day-old video with 8k.
  - Ignore subscriber count and absolute size; judge only against the channel's own norm.
  - If the channel has fewer than ~8 comparable videos, answer unsure (reason `few_channel_videos`).
  - If one huge video dominates the channel, judge against the typical video, not that one (`viral_skews_channel`).
  - If the video is under ~3 days old, answer unsure (`too_young`).
  - If the counts look like ad-driven views (very low likes and comments relative to views compared with siblings), mark `possible_paid_promotion` and still give a label.
- Calibration: before labelling, both labellers label 10 anchor videos (outside the 100) with known answers, then discuss them in a 15-minute call.

**Agreement and gold labels**
- Two labellers (owner + Mira or the marketer) label all 100.
- Report percent agreement, Cohen's κ (Cohen 1960) and Gwet's AC1 (Gwet 2008). AC1 is included because κ is depressed when prevalence is skewed (the "kappa paradox").
- Interpretation follows Landis & Koch (1977), with a caveat.
- **Gate**: if κ < 0.6, revise the guidelines, recalibrate on new anchors and relabel a fresh 30 before scoring.
- Gold: agreed labels stand. Disagreements and unsure-vs-label pairs go to a third person (owner) who sees both notes. Items both labellers marked unsure are excluded and counted. If more than 15% are excluded, the guidelines are revised.

**Metrics**
- Weighted confusion matrix. Precision = Σw·TP / Σw·(TP+FP); recall = Σw·TP / Σw·(TP+FN); F1 and F0.5.
- 95% CIs from a stratified bootstrap of items (resampling within strata, B = 10,000). Wilson intervals on the unweighted counts as a cross-check.
- Breakdown by size band, format and reason code. Reason codes on false positives and false negatives point to the detector failure mode (for example, many FPs tagged `format_mismatch` → 06 should split baselines by format).

**Threshold-tuning curve**
- The detector's continuous ratio is stored, so sweep θ ∈ [1.5, 6.0] in steps of 0.25. Compute weighted precision, recall, F0.5 and F1 for each, and render the curve as a markdown table (no plotting dependency).
- Selection rule: maximise F0.5 (precision-weighted, because a false outlier pollutes cards more than a missed one does) subject to precision ≥ 0.80. Ties go to the higher θ.
- Overfitting guard: report the leave-one-channel-out optimism of the chosen θ. The new θ is a **proposal** to 06's config and takes effect only for freezes after approval.
- Detector variants are scored on the same gold labels: mean vs median baseline (the assignment says "channel average"; we report both), format-split vs pooled, baseline over the last 30 vs last 90 days, day-7 vs proxy. 06 picks the default from this table.
- The PR-curve presentation follows Saito & Rehmsmeier (2015): precision-recall rather than ROC under class imbalance.

### 5.3 Human review

**Sample (30 rated cards)**
- Source: the latest analysis run for each of the 5 demo clients, 6 cards each.
- Stratified by type so each of `video_idea`, `title_pattern`, `thumbnail_pattern`, `format`, `creator`, `news_opportunity`, `description_refresh` appears at least 3 times where available. Remaining slots go proportionally to how often each type is displayed.
- Covers both lenses and all confidence labels. Seeded.
- Plus **6 decoys** (not counted towards the 30): copy_top-arm specs turned into cards by the same writer and gates, so they look identical. Decoys measure whether raters discriminate. If decoys score within 0.3 of system cards, the ratings are flagged as uninformative.

**Pack**
- Same `PackRenderer`. A client context sheet comes first: business summary, offerings, tracked buyer questions, competitors, window. Then the cards rendered as the product shows them (summary, why_it_works, reference videos with links, fit_to_client, expected range, confidence).
- Hidden: ids, arm, gate features, model.
- Random order per rater. Raters do not see each other's scores.

**Rubric (1–5, anchored)**
| Criterion | 1 | 3 | 5 |
|---|---|---|---|
| Specific | generic advice ("make engaging videos") | topic named, but format/angle vague | names topic, angle, format/length and title/thumbnail direction |
| Evidenced | references missing, stale or unrelated | references valid but weakly connected to the claim | ≥ 3 recent, relevant references whose numbers clearly support the claim |
| Relevant | does not fit this business or its buyers | fits the niche, not this client's offerings | ties directly to a tracked buyer question or offering |
| Actionable | cannot brief a creator from it | needs significant extra thinking | could brief a creator today |
| Not obvious | something the client already does or anyone would say | somewhat new | a non-obvious, evidenced gap or angle |

Also collected: a "would you ship this card?" yes/no and an optional comment.

**Aggregation**
- Item score = mean over raters and criteria. Overall mean = mean of item scores.
- 95% CI from a bootstrap over items (B = 10,000). Averaging Likert items is common practice and robust in this use (Norman 2010). Medians and the share of items with mean ≥ 4 are also shown.
- Per-criterion means with CIs, plus per type, per client and per lens.
- **Pass** = overall mean ≥ 4.0 **and** every criterion mean ≥ 3.5. Also reported: the lower CI bound, and "would ship" ≥ 70%.
- Agreement: Krippendorff's α (ordinal) per criterion (Hayes & Krippendorff 2007; Krippendorff 2011), plus quadratic-weighted κ (Cohen 1968). If α < 0.667, Krippendorff's lower bound for tentative conclusions, run a calibration discussion and report per-rater means separately. A sensitivity view applies per-rater z-scores to correct acquiescence bias.

**Feeding results back (proposals, never automatic)**
- Criterion → knob mapping:
  - Evidenced low → raise `min_reference_videos`, `min_ref_outlier_ratio`, minimum confidence label; require the reference topic match score ≥ τ.
  - Specific low → the writer schema must fill format, length band and title direction (10). A gate rejects cards whose summary lacks a format/length token.
  - Relevant low → raise 05's topic acceptance threshold for recommendation evidence; require `buyer_question_id` for the conversion lens.
  - Actionable low → require a non-empty spec for format plus title or thumbnail pattern.
  - Not obvious low → add a novelty gate: reject if the client's own last 90 days contain ≥ 2 videos matching the spec, or if the spec equals the copy_top spec.
- `GateTuningReport`: for each gate feature, compute the Spearman ρ with the item score. Then compute the retention curve: for threshold t, the share of cards retained and the mean rating of retained cards. A threshold change is proposed when the retained mean is ≥ 4.0 with retention ≥ 70%.
- Proposals are emitted as YAML config diffs with a rationale. Once accepted they bump `gate_config_version`, and the next review batch (fresh cards) acts as the hold-out. With n = 30, all correlations are labelled exploratory.
- Comments are tagged by hand into an issue list for 07/10.

### 5.4 Operations

**Quota per client per daily refresh** (from 02's ledger; quota day = Pacific Time, matching Google's reset)
- `units_marginal(c, d)` = Σ units of ledger rows with `run_id ∈ daily_refresh runs of c on d`.
- `units_amortised(c, d)` = marginal + Σ over shared-scope rows on d of units × (1 / number of clients that read that cached object within 24 h). This needs 02 to log cache reads with `client_id` (cheap; Redis counter or ledger row with units = 0).
- Reported per endpoint (`channels.list`, `playlistItems.list`, `videos.list`, `commentThreads.list`, `search.list`, `videos.list chart=mostPopular`), with `search.list` calls also counted against its separate 100-calls/day bucket, and cache hit rate.
- Analytics API calls (OAuth, separate quota) are counted separately.

Expected budget (to be replaced by measurements): own + 5 competitors + ~10 niche channels.
- channels.list: 1
- playlistItems.list: ~16
- videos.list stats refresh: ~500 ids / 50 → ~10
- commentThreads.list for top outliers: ~20
- **≈ 50 units/client/day marginal**, plus amortised search ≈ 5–10 calls/week per niche. At 10,000 units/day that is ~150+ clients per project before snapshots of shared channels. Alert budget: 150 units/client/day.

**Time to generate recommendations**
- Defined as the wall time of a `daily_refresh` (or analysis) run from the first stage start to finalize, excluding queue wait (reported separately). Onboarding (crawl, profile, discovery) is reported separately and not counted against the 2-minute target, because the assignment's target is about generating recommendations.
- Read from 11's stage rows: p50/p95/max per stage and total, per client and pooled.
- Target: p95 total < 120 s with a warm cache.
- Suggested stage budget, to agree with 11: ingest delta 25 s, enrich/thumbnail OCR 25 s, topics 15 s, analytics 5 s, news 20 s (parallel with analytics), recommend 5 s, writer 30 s (parallel per card), finalize 2 s.

**LLM tokens and cost**
- From 10's usage ledger: tokens by kind (input, output, cache read, cache write) per model and purpose (profile, discovery, topic adjudication, news, writer, critic, refresh).
- Cost = tokens × a **versioned price table in config** (per provider/model/kind). It is never hard-coded. An unknown model gives a null cost and a warning.
- Each retry attempt is recorded, so retries cost what they cost.
- Prices verified 2026-11-20 on the Anthropic pricing page:

| Model | Input | Output | Cache read |
|---|---|---|---|
| claude-sonnet-5-5 | $2/MTok | $10/MTok | $0.10/MTok |
| claude-haiku-4-5 | $1/MTok | $5/MTok | $0.10/MTok |
| claude-haiku-5-5 | $0.10/MTok | $0.50/MTok | (prompts ≤ 100k tokens) |

  The Batch API gives a 50% discount.
- Estimated daily refresh per client:
  - Writer: ~12 cards × (2.5k in + 0.4k out) on Sonnet ≈ $0.11
  - Topic adjudication: ~100 ambiguous videos × 0.6k in + 50 out on Haiku 4.5 ≈ $0.09
  - Critic: Haiku ≈ $0.05
  - News: Sonnet ≈ $0.06
  - **Total ≈ $0.30/client/day** (≈ $0.03 on Haiku 5.5 for the Haiku tier)
- Onboarding: ~$0.5–1.5 one-off. Tavily: ~5 basic searches/day = 5 credits ≈ $0.04 at $0.008/credit.

**Crawl time**: from 03 — pages, bytes, p50/p95 duration, robots-blocked count, error rate per crawl.

**Failure rates**
- Per stage and run kind: failed/total runs, retries.
- Per external provider: error codes (YouTube `quotaExceeded`, `forbidden`, 404; Anthropic 429/529/overloaded; Tavily errors).
- LLM schema-validation failures and grounding violations (10).
- **Gate rejection rates per gate** (07). A sudden jump in any gate's rejection rate is an early quality signal.
- Snapshot cron lag: share of videos that missed their day-7 bracket.

**Retention compliance**: count rows of non-authorized API data with `fetched_at` older than 29 days. Must be 0 at all times; it is a critical alert.

**Surfaces (deliberately simple)**
- CLI: `python -m app.evaluation ops report --days 7 --out docs/eval/ops-report.md`.
- API (admin-only, read-only): `GET /api/ops/report?from=&to=&client_id=` → `OpsReport`; `GET /api/ops/alerts?active=true`; `GET /api/eval/runs`, `GET /api/eval/runs/{eval_id}`.
- Nightly `ops_daily_rollup` materialisation, so the report is a cheap query. 13 can add an admin page later.

**Alerting** (arq cron every 15 min; `AlertEvaluator` runs SQL rules; notifies via structured log at error level + optional webhook; dedupe 60 min; auto-resolve)

| Rule | Warn | Critical |
|---|---|---|
| project quota used today (PT) | > 70% before 18:00 PT | > 90% |
| search.list calls today | > 70 | > 90 |
| units per client per daily refresh (7-day median) | > 150 | > 300 |
| daily refresh p95 total | > 120 s | > 240 s |
| run failure rate (24 h) | > 5% | > 15% |
| LLM cost per client per day | > $0.75 | > $2 |
| grounding violations (24 h) | > 2% of cards | > 5% |
| API data older than 29 days | — | > 0 rows |
| retention cron last success | > 26 h | > 48 h |
| snapshot day-7 bracket miss rate (24 h) | > 5% | > 20% |
| any gate rejection rate jump | > 2x 7-day mean | — |
| crawl error rate | > 20% | > 50% |
| freeze bundle older than expiry | — | exists |

**Observability stack and why it is this small**
- **structlog** (already a dependency) is the only required piece.
  - JSON renderer in prod, console renderer in dev.
  - `merge_contextvars` binds `request_id, run_id, client_id, stage, job, eval_id`.
  - Standard event keys: `provider, endpoint, units, duration_ms, status, error_type`.
  - A redaction processor drops values of keys matching `key|token|secret|authorization|cookie` and never logs comment text or page bodies.
  - stdlib logging is routed through structlog so uvicorn, arq and httpx logs share the format.
- **Postgres ledgers** already hold quota, LLM usage and stage timings. SQL rollups answer every assignment metric with no new infrastructure: one API service, one worker pool, one DB.
- **OpenTelemetry**: optional, off by default (`OTEL_ENABLED`). When enabled: FastAPI/httpx/SQLAlchemy auto-instrumentation, manual spans in the pipeline runner (arq has no official instrumentation), and the Anthropic SDK's native OpenTelemetry option. Useful once runs span several workers; not needed for the demo.
- **Prometheus**: optional (`METRICS_ENABLED`), `prometheus_client` `/metrics` with a handful of counters and histograms (`gh_quota_units_total{endpoint,scope}`, `gh_run_duration_seconds{run_kind,stage}`, `gh_llm_tokens_total{model,kind}`, `gh_llm_cost_usd_total`, `gh_run_failures_total{stage,error_type}`). Only worth it when a Prometheus server exists in prod.

### 5.5 Policy compliance matrix
Generated from `FeatureRegistry` (section 9 holds the full matrix as of this plan). It is rebuilt by `python -m app.evaluation policy render` and committed as `docs/eval/feature-policy-matrix.md`. A CI test keeps it complete.

### 5.6 Demo plan

**Clients (5)**
| Client | Niche (ICP) | Channel | Notes |
|---|---|---|---|
| Apollo Hospitals | healthcare (India) | `UC0InVdvqNyNzKBl1-TL348A` (verified 2026-11-20: 1.12M subs, 4,328 videos) | owner's demo client. No OAuth, so public-data value only. Multilingual titles. Must not infer health status from comments |
| AutoNation | auto dealers | `UCSHJ1SCNy-QRaI4-tkSwHDQ` (verified: 28.8k subs, 908 videos) | candidate; confirm at kickoff |
| Dental clinic group | dental clinics | TBD via 04 discovery (handle `@AspenDental` did not resolve) | pick a clinic group with ≥ 50 uploads |
| Home services | home services | `@MrRooterPlumbing` → `UCNnT2M11NDQeKLYIir9PZ-w` resolves but has 17 videos / 21 subs | useful "tiny own channel" case. Prefer a larger HVAC/plumbing brand as the main demo, Mr. Rooter as the edge case |
| Runable | AI company (news radar) | `@runable` → `UC_ItvBBj4uzGQWbY-s0UQAw` resolves with 0 videos, ownership unverified | demonstrates "no own channel" + news_opportunity + description refresh on a competitor-informed plan. Verify ownership via the website link (02 rule) |

Each client gets 5 confirmed competitors (from 04, human-confirmed) + up to 10 niche creators for the evaluation universe.

**Schedule (4-week assignment, kickoff 2026-11-19)**
| When | Action |
|---|---|
| Week 2 day 1 (~2026-11-26) | onboard 5 demo clients, confirm competitors, start the snapshot cron. Freeze #1 at T₁ = day 1 23:59 UTC |
| Week 2 day 7 | Freeze #2 at T₂ = T₁ + 7 d (rolling origin, extra power) |
| Week 3 | build the recommender. Labelling pack exported from current data. Labelling done (2 × ~90 min) |
| Week 4 day 1 | outcomes complete for T₁ (H = 10 d + a = 7 d → T₁ + 18 d); register config; run backtest. Review pack from the latest run; human review (2 × ~60 min) |
| Week 4 day 3 | T₂ outcomes complete; final report; ops report over the last 7 days |
| Week 4 demo | 20-minute demo |
| ≤ T₁ + 30 d | freeze bundles and video-level eval rows purged (retention cron); aggregates remain |

**20-minute script**
| Time | Content |
|---|---|
| 0:00–2:00 | Problem, guardrails, what is deterministic vs LLM |
| 2:00–6:00 | Apollo: website → BusinessProfile → competitors (show ownership verification) → recommendations, with reference videos and honest confidence; a thin-data card showing "Widen to 30?" |
| 6:00–11:00 | Dental, auto and home services: own vs baseline, competitor cadence, trending, outliers, creator ranking; one card opened down to its evidence |
| 11:00–14:00 | Runable: news radar → news_opportunity; description refresh diff (additive keywords only) |
| 14:00–18:00 | Evaluation: backtest table and verdict with MDE, outlier P/R and threshold curve, human review scores, ops report (units/client, p95 time, $/client) |
| 18:00–20:00 | Policy matrix, limitations, Q&A |

Fallback: if live APIs fail, use a pre-run state from the last daily refresh (within 30 days). Never fake live progress; replay recorded SSE events labelled "recorded".

**Artefacts**
- `docs/eval/evaluation-report.md`: backtest + outlier P/R + human review + ops summary + limitations.
- `docs/eval/ops-report.md`.
- `docs/eval/feature-policy-matrix.md`.
- `docs/eval/labelling-guidelines.md` and `docs/eval/review-rubric.md`. These are shipped inside packs too; the markdown is the source.
- `docs/design-doc.md` answers (assembled by 12 from 02/05/06/07), each answer ≤ 1 page:
  1. *Same-age comparison when snapshots start at tracking*: tracked-from-birth videos use observed/interpolated ViewsAtAge. Pre-tracking videos use labelled proxies (contemporaneous-peer ratio) and are never shown as observed. Evaluation is forward so outcomes are always observed.
  2. *Sharing cached public data within policy*: one shared, client-agnostic cache of public channel/video data; per-client views only of the client's own config; no cross-owner aggregation shown as one owner's data; 30-day refresh/delete enforced by the retention cron with a compliance alert; consider the derived-metrics amendment (section 13).
  3. *Topics without inferring categories*: matching only against the client's own keywords and buyer questions; never free clustering; `categoryId` is not used to classify; LLM adjudication is constrained to the client's list; flagged for review.
  4. *Ranking creators without inferring audiences*: relevance = keyword/buyer-question match of their videos; performance = their views-at-age vs niche; consistency = dispersion of their own ratios; no audience, demographic or geography inference.
- Demo dataset manifest (freeze ids, eval ids, config hashes).

---

## 6. Data model

All tables live in schema `eval` (or the `eval_` prefix), are created via Alembic, and are tenant-agnostic (admin only). Rows holding **non-authorized API data** carry `expires_at` and are registered with 01's retention registry.

| Table | Key columns | Indexes | Retention |
|---|---|---|---|
| `eval_config` | `eval_id` PK, `mode`, `config jsonb`, `config_hash`, `code_version`, `seed`, `registered_at`, `status`, `started_at`, `finished_at`, `usd_spent`, `units_spent` | `(status)` | keep (no API data) |
| `eval_freeze` | `freeze_id` PK, `client_id`, `as_of`, manifest columns, `bundle_uri`, `pit_level`, `oldest_api_fetched_at`, `expires_at` | `(client_id, as_of)`, `(expires_at)` | row kept, bundle file deleted at `expires_at`; row marked `purged` |
| `eval_pattern_spec` | `eval_id`, `client_id`, `arm`, `replicate` (null except random), `spec jsonb`, `spec_id`, `specificity`, `source_ref` | `(eval_id, arm)` | keep (topic ids / traits are ours; creator `channel_id` → see note) |
| `eval_outcome_video` | `eval_id`, `video_id`, `channel_id`, `client_id`, `published_at`, `views_at_age`, `method`, `baseline`, `ratio`, `is_outlier`, `matched jsonb`, `status`, `expires_at` | `(eval_id)`, `(expires_at)` | **30 days** (API stats) |
| `eval_result` | `eval_id`, `scope`, `arm`, `metric`, `estimate`, `ci_low`, `ci_high`, `n_videos`, `n_channels`, `p_value`, `p_adj`, `notes` | `(eval_id, scope)` | keep (aggregates; see section 13 on derived data) |
| `label_task` | `task_id` PK, `batch_id`, `video_id`, `channel_id`, `stratum`, `inclusion_prob`, `detector_ratio`, `detector_label`, `detector_version`, `observed_at`, `expires_at` | `(batch_id)` | `detector_ratio` + ids 30 days; stratum/prob kept |
| `label` | `task_id`, `labeller_id`, `round`, `label`, `reason_codes text[]`, `note`, `seconds_spent`, `labelled_at` | `(task_id, labeller_id, round)` unique | keep (human judgement) |
| `label_gold` | `task_id` PK, `gold`, `method` (agree / adjudicated / excluded) | | keep |
| `outlier_eval_result` | `batch_id`, `detector_version`, metrics jsonb, curve jsonb | | keep |
| `review_item` | `item_id` PK, `batch_id`, `recommendation_id`, `client_id`, `card_snapshot jsonb`, `is_decoy`, `gate_features jsonb`, `display_seed`, `expires_at` | `(batch_id)` | `card_snapshot` nulled at 30 days |
| `review_rating` | `item_id`, `rater_id`, 5 criteria smallint CHECK 1..5, `would_ship`, `comment`, `rated_at` | `(item_id, rater_id)` unique | keep |
| `gate_tuning_proposal` | `id`, `batch_id`, `feature`, `rho`, thresholds, `config_diff jsonb`, `status`, `decided_by`, `decided_at` | | keep |
| `ops_daily_rollup` | `day` (PT for quota, UTC otherwise), `client_id` nullable, `metrics jsonb` | `(day, client_id)` unique | keep 400 days (internal telemetry, no API data) |
| `alert_event` | `id`, `rule_id`, `severity`, `value`, `threshold`, `fired_at`, `resolved_at`, `payload jsonb` | `(rule_id, fired_at)`, partial on `resolved_at is null` | 90 days |
| `feature_spec` | not a table: in-code registry; rendered to markdown | | — |

Notes:
- Storage of video/channel IDs beyond 30 days is not addressed explicitly by the Developer Policies (unverified; section 13). Until clarified, `eval_pattern_spec.channel_id` for creator specs and label/review video ids are nulled at 30 days, while aggregate results and human judgements remain.
- Freeze bundles: `jsonl.gz` per table, ~5–20 MB per client. Stored under `EVAL_BUNDLE_DIR` (local) or object storage in prod. They are deleted by the retention cron and the deletion is logged.

---

## 7. External APIs & libraries

| Item | Fact used | Source (verified 2026-11-20 unless marked) |
|---|---|---|
| YouTube Data API quota | channels.list, playlistItems.list, videos.list, commentThreads.list = 1 unit; **search.list = 1 unit with its own 100 calls/day bucket**; 10,000 units/day default; reset at midnight Pacific Time | https://developers.google.com/youtube/v3/determine_quota_cost |
| Developer Policies III.E.4.b/d | Non-authorized statistics must not be stored > 30 days; other non-authorized data stored temporarily ≤ 30 calendar days then deleted or refreshed; authorized statistics (Analytics, view counts with OAuth) may be stored longer with 30-day re-authorization checks | https://developers.google.com/youtube/terms/developer-policies |
| Developer Policies III.E.4.h | Must not use API data "to create new or derived data or metrics" without the amendment; independently calculated metrics must be disclosed | same |
| Developer Policies III.E.2 | No combining API data across content owners in aggregate views; no insights into YouTube's business | same |
| Derived Metrics Policy (Section L, from 2026-06-01) | Analytics use cases may apply for an amendment allowing custom ratios/averages, tagging (additive, disclosed), leaderboards, and **storage of statistics and derived metrics up to 36 months**; titles/descriptions/comment text still follow the 30-day rule | https://developers.google.com/youtube/terms/derived-metrics-policy |
| channels.list `forHandle` | used for 5 verification calls (5 units) | live call 2026-11-20 |
| Anthropic pricing | Sonnet 5.5 $2 in / $10 out / $0.10 cache read; Haiku 4.5 $1 / $5 / $0.10; Haiku 5.5 $0.10 / $0.50 (≤ 100k-token prompts); Batch −50% | https://platform.claude.com/docs/en/about-claude/pricing |
| Anthropic usage fields | `usage.input_tokens`, `output_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens` | Anthropic Python SDK docs (bundled claude-api skill, `python/claude-api/README.md`) |
| Anthropic SDK OpenTelemetry | native `open_telemetry=` client option | bundled skill `python/claude-api/opentelemetry.md` |
| Tavily credits | search basic 1 / advanced 2; extract 1 per 5 URLs (basic); map 1 per 10 pages; crawl = map + extract; $0.008/credit PAYG; 1,000 free/month | https://docs.tavily.com/documentation/api-credits |
| structlog contextvars | `structlog.contextvars.merge_contextvars`, `bind_contextvars` | https://www.structlog.org/en/stable/contextvars.html (not re-fetched; stable API) |
| OpenTelemetry Python | SDK + contrib instrumentations (FastAPI, httpx, SQLAlchemy) | https://opentelemetry.io/docs/languages/python/ (not re-fetched) |
| prometheus_client | Counter/Histogram, ASGI `make_asgi_app` | https://prometheus.github.io/client_python/ (not re-fetched) |
| NumPy RNG | `np.random.default_rng(SeedSequence)`, `SeedSequence.spawn` for independent replicate streams | https://numpy.org/doc/stable/reference/random/parallel.html (not re-fetched) |
| Jinja2 | pack/report templating (add as dependency; already a transitive dependency of many tools, confirm) | https://jinja.palletsprojects.com/ |
| SciPy (optional) | `scipy.stats.spearmanr`, cross-checks of the hand-rolled bootstrap | https://docs.scipy.org/doc/scipy/reference/stats.html — optional; stats are implemented on numpy, tested against reference values |
| Google Cloud Monitoring quota metric (optional reconciliation) | `serviceruntime.googleapis.com/quota/...` usage metrics for comparing our ledger with Google's count | **unverified**; manual console check is the fallback |
| `paidProductPlacementDetails` on videos.list (for 07's "disclosed paid promotion") | listed here only for the policy matrix | **unverified** in this plan; 07 must verify |

**Statistical references**
- Efron, B. & Tibshirani, R. (1993). *An Introduction to the Bootstrap*. Chapman & Hall.
- Davison, A. C. & Hinkley, D. V. (1997). *Bootstrap Methods and their Application*. Cambridge University Press.
- Field, C. A. & Welsh, A. H. (2007). Bootstrapping clustered data. *JRSS B* 69(3), 369–390.
- Cameron, A. C., Gelbach, J. B. & Miller, D. L. (2008). Bootstrap-based improvements for inference with clustered errors. *REStat* 90(3), 414–427.
- Cameron, A. C. & Miller, D. L. (2015). A practitioner's guide to cluster-robust inference. *J. Human Resources* 50(2), 317–372.
- Wilson, E. B. (1927). Probable inference, the law of succession, and statistical inference. *JASA* 22, 209–212.
- Brown, L. D., Cai, T. T. & DasGupta, A. (2001). Interval estimation for a binomial proportion. *Statistical Science* 16(2), 101–133.
- Newcombe, R. G. (1998). Interval estimation for the difference between independent proportions. *Stat. Med.* 17, 873–890.
- Fleiss, J. L., Levin, B. & Paik, M. C. (2003). *Statistical Methods for Rates and Proportions*, 3rd ed. Wiley (sample size, ch. 4).
- Mantel, N. & Haenszel, W. (1959). Statistical aspects of the analysis of data from retrospective studies of disease. *JNCI* 22, 719–748.
- Phipson, B. & Smyth, G. K. (2010). Permutation p-values should never be zero. *SAGMB* 9(1).
- Holm, S. (1979). A simple sequentially rejective multiple test procedure. *Scand. J. Statist.* 6, 65–70.
- Tashman, L. J. (2000). Out-of-sample tests of forecasting accuracy. *IJF* 16, 437–450.
- Kaufman, S., Rosset, S., Perlich, C. & Stitelman, O. (2012). Leakage in data mining: formulation, detection, and avoidance. *ACM TKDD* 6(4).
- Horvitz, D. G. & Thompson, D. J. (1952). A generalization of sampling without replacement from a finite universe. *JASA* 47, 663–685.
- Bennett, P. N. & Carvalho, V. R. (2010). Online stratified sampling: evaluating classifiers at web-scale. *CIKM '10*.
- Saito, T. & Rehmsmeier, M. (2015). The precision-recall plot is more informative than the ROC plot… *PLoS ONE* 10(3).
- Cohen, J. (1960). A coefficient of agreement for nominal scales. *EPM* 20, 37–46. Cohen, J. (1968). Weighted kappa. *Psych. Bull.* 70, 213–220.
- Landis, J. R. & Koch, G. G. (1977). The measurement of observer agreement for categorical data. *Biometrics* 33, 159–174.
- Gwet, K. L. (2008). Computing inter-rater reliability and its variance in the presence of high agreement. *BJMSP* 61, 29–48.
- Hayes, A. F. & Krippendorff, K. (2007). Answering the call for a standard reliability measure for coding data. *Comm. Methods & Measures* 1(1), 77–89. Krippendorff, K. (2011). *Computing Krippendorff's Alpha-Reliability*. UPenn.
- Norman, G. (2010). Likert scales, levels of measurement and the "laws" of statistics. *Adv. Health Sci. Educ.* 15, 625–632.

---

## 8. Edge cases & failure modes

| # | Case | Detection | Handling | User-facing message (admin/eval report unless noted) |
|---|---|---|---|---|
| 1 | Too few post-T uploads (universe < floors) | count complete outcome videos per scope | scope dropped to pooled only; verdict `inconclusive_underpowered`; MDE printed; suggest T₂/longer H/more niche channels | "Only 14 videos published after T in Dental; too few to judge. Pooled result shown; minimum detectable lift is 3.4x." |
| 2 | Post-T video missing its day-7 bracket (cron gap, quota) | no snapshot within ±tolerance | status `missing_snapshot`; never extrapolate; missingness reported; > 20% → verdict `invalid` | "9% of outcome videos lack a day-7 measurement and were excluded." |
| 3 | Outcome video deleted/private after T | absent from videos.list | status `deleted`, excluded, counted; survivorship caveat in limitations | "3 videos were removed by their owners and excluded (likely underperformers; results may be slightly optimistic for all arms)." |
| 4 | Premiere/scheduled/live video | `liveBroadcastContent`, live details | format `live` handled separately; baseline by format | — |
| 5 | Shorts mixed with long-form | duration/format flag | format-specific baselines; specs carry format | — |
| 6 | Channel has too few pre-T videos for a baseline | 06 returns `no_baseline` | outcome status `no_baseline`, excluded from ratio metrics, counted | "2 channels had too little history for a fair baseline." |
| 7 | Channel goes viral after T (baseline shift) | baseline at E vs T differ > 2x | primary uses T baseline; sensitivity with E baseline shown | — |
| 8 | Competitor/niche channel added after T | `channel.first_seen_at > T` | excluded from universe (selection leakage) | — |
| 9 | Snapshot captured after T read during replay | frozen repo assertion | hard fail of the run; canary test in CI | "Evaluation aborted: post-T data detected (leakage guard)." |
| 10 | Pre-T video metadata (title/thumbnail) edited after T | bundle captured at T; Mode B only: etag/thumbnail URL changed | Mode A immune (bundle at T); Mode B lists it as known leakage | Mode B banner |
| 11 | TopicRefs/buyer questions edited after T | version > freeze version | use the T version from the bundle | — |
| 12 | θ tuned on labels then applied to the same backtest | `outlier_threshold` source/time | θ always the frozen T value; tuned θ only for later freezes | — |
| 13 | LLM knows post-T events (training cutoff after T) during adjudication | post-hoc adjudication count | patterns come from deterministic code; adjudication cache from T; post-hoc ones reported; sensitivity excluding post-hoc matches | — |
| 14 | Post-T videos leak into baselines | baseline as_of check | baseline frozen at T | — |
| 15 | Config changed after seeing outcomes (p-hacking) | config hash vs registration | collector refuses; new eval_id; all eval_ids listed in report | "Config changed after registration; new evaluation id required." |
| 16 | Client follows our cards (own uploads not independent) | own channel in universe | own channel excluded from primary; reported separately | — |
| 17 | Niche with no pre-T outliers → system abstains ("Widen to 30?") | zero gated specs | counted as abstention; not a failure; abstention correctness = post-T base rate also low? reported | "Engine abstained for Home services at T (thin data); post-T outlier rate there was 2%." |
| 18 | Niche with no post-T outliers | M1 = 0 for all arms | fall back to M2 (geo-mean ratio); verdict from M2 flagged exploratory | — |
| 19 | Specs too broad (all arms match same videos) | Jaccard overlap > 0.8 | reported; comparisons labelled low-discrimination | — |
| 20 | System spec matches zero post-T videos | n matched = 0 | "untestable" count, excluded from hit rate | — |
| 21 | Random spec infeasible | no observed values for a constraint | sample only from observed values; else drop the constraint and record lower specificity | — |
| 22 | copy_top video is a Short/live or ties | format/ties | naive by design: no format filter; ties broken deterministically; variant "copy_top same format" as sensitivity | — |
| 23 | Duplicate specs across recs | spec hash equality | dedupe per arm; per-rec metrics still reported | — |
| 24 | Quota exhaustion during evaluation | quota manager budget scope `evaluation` | eval capped at `max_quota_units` (default 1,000/day); production always has priority; runner checkpoints and resumes next PT day; evaluation never calls search.list | "Evaluation paused: daily evaluation quota reached; resumes after midnight PT." |
| 25 | Quota exhaustion during tracking (affects outcomes) | 02 alerts; bracket miss rate | see #2; snapshot cron prioritises videos near day 7 (proposal to 02) | — |
| 26 | 30-day expiry reached before evaluation finishes | `expires_at` of freeze/outcomes | schedule E ≤ T + 29 d; if expired, the run cannot replay; report from stored aggregates only | "Freeze expired under the 30-day data policy; results are auditable but not re-runnable." |
| 27 | Retention cron deletes data needed by a running eval | retention runs first by design | compliance wins; eval marks the scope `invalid` | — |
| 28 | Labeller disagreement (κ < 0.6) | agreement score | revise guidelines, recalibrate, relabel fresh 30; report both rounds | "Labeller agreement 0.48 (moderate); guidelines revised and a second round run." |
| 29 | Many "unsure" labels (> 15%) | count | guidelines revision; unsure excluded from P/R and reported | — |
| 30 | Labeller fatigue/order effects | time per item, drift by position | seeded order per labeller, batches of 25, breaks; check κ by position half | — |
| 31 | Labeller unblinded (sees ratio, uses live YouTube counts) | pack hides fields; instruction | hidden fields; pack is the only data source; observation timestamp shown | — |
| 32 | Empty strata (no > 1M channels in demo niches) | N_cell = 0 | reallocate; realised π recorded; report "no S4 channels in sample" | — |
| 33 | One channel dominates sample | per-channel count | cap 3 per channel | — |
| 34 | Labelled video deleted before labelling | pack load check | replace from same stratum; π updated | — |
| 35 | Hidden subscriber count | `hiddenSubscriberCount` true | stratum S0 | — |
| 36 | Detector undefined for a video (no baseline) | ratio null | excluded from sampling frame; counted | — |
| 37 | Rater acquiescence (everything 5) or harshness | per-rater mean/sd; decoy gap | z-score sensitivity; decoy gap < 0.3 → ratings flagged uninformative | "Raters scored decoy cards as highly as real ones; ratings may not discriminate." |
| 38 | Rater skips items | missing cells | import rejects incomplete criteria rows; partial items excluded per criterion | — |
| 39 | Fewer than 30 eligible cards (thin niches) | pool count | fill from other clients/types; report actual n | — |
| 40 | Review pack opened after 30 days | `expires_at` | pack refuses to render (embedded expiry) and file deleted by cron | "This review pack has expired (data policy). Generate a new one." |
| 41 | Mira and marketer systematically differ | per-rater means, α | report both; calibration session | — |
| 42 | Overfitting gates to 30 cards | — | proposals only, human approval, next batch is the hold-out | — |
| 43 | Quota ledger misses calls (unwrapped client) | gateway is the only path (02); reconcile against Google console weekly | alert on units > 0 in console vs ledger mismatch > 5% (manual) | — |
| 44 | Shared cache attribution ambiguous | cache read log | report marginal and amortised; never one number | — |
| 45 | Cold cache inflates timings | run kind + cache hit rate | separate onboarding/first-analysis vs daily refresh | — |
| 46 | LLM retries/fallback provider | attempt rows, provider | every attempt costed; fallback priced from its own table; unknown → null + warn | — |
| 47 | Price table outdated | price table `verified_at` > 90 days | warn in ops report | "LLM prices last verified 2026-11-20." |
| 48 | Timezones (PT quota day vs UTC) | — | quota rollups in PT, everything else in UTC, labelled | — |
| 49 | Alert storms | dedupe window | dedupe 60 min per rule; auto-resolve | — |
| 50 | Secrets/PII in logs | redaction processor; test | keys/tokens redacted; no comment text or page bodies logged | — |
| 51 | Demo client with no own videos (Runable) | own video count = 0 | own-baseline metrics n/a; expected range vs niche median labelled; excluded from own-channel analyses | (product) "No published videos yet — ranges use the niche median." |
| 52 | Very large own channel (Apollo 4.3k videos) | video count | ingest limited to window + baseline horizon pages (02); eval universe unaffected | — |
| 53 | Multilingual titles (Apollo Hindi/English/Telugu) | language detection on titles (simple script check) | 05's matching languages; per-language coverage reported in eval | — |
| 54 | Healthcare comments reveal health conditions | domain flag | CommentSignals must not infer health status of commenters (policy, derived-metrics category 4); eval never exports comment text | — |
| 55 | Non-deterministic embeddings/ONNX threads | repeated run diff | pin model + version; round scores to 4 dp; set thread count; tolerance in tests | — |
| 56 | Evaluation cost runaway (writer on for random replicates) | `usd_spent` vs `max_usd` | writer disabled in backtest; hard cap aborts; random arm is compute only | "Evaluation stopped at the $5 cost cap." |
| 57 | Multiple niches/metrics → false positives | — | one primary hypothesis; Holm for secondaries | — |
| 58 | Heavy-tailed ratios | — | log scale/geo-mean, medians | — |
| 59 | Clustered dependence | — | cluster bootstrap by channel | — |
| 60 | < 5 clusters in a scope | channel count | Wilson only, "few clusters" mark | — |
| 61 | Video matches several specs/recs | — | video-level dedupe per arm; spec-level metrics separate | — |
| 62 | External shock (e.g. big news) lifts the whole niche | base rate jump | all arms share the universe, so comparisons hold; noted | — |
| 63 | W = 7 too thin for a meaningful system arm | abstention rate | primary W = 30; W = 7/90 as secondary runs | — |
| 64 | Demo-day API outage | health check | pre-run state within 30 days; recorded SSE labelled | — |
| 65 | Feature added without a policy row | registry CI test | build fails | — |
| 66 | A spec type 07 adds later (new rec type) | spec type not in matcher registry | matcher raises; type excluded with a report note until a matcher is registered | — |
| 67 | Freeze job runs while ingest is mid-write | run lock | freeze waits for the client's ingest lock (11) | — |
| 68 | Clock drift between worker and DB | DB `now()` used for `as_of` | all timestamps from DB clock | — |

---

## 9. Policy & guardrail check

Guardrails:
- **G1** API data + simple maths (sums, averages, medians, ratios)
- **G2** never merge YouTube data with other sources into one metric (side by side is fine)
- **G3** no revenue/monetisation estimates
- **G4** no audience demographics, no inferring a video's content category
- **G5** refresh/delete stored API data within 30 days
- **G6** thumbnails: simple traits only, no face recognition

Cells:
- **OK**: complies.
- **COND**: complies under the stated condition.
- **FLAG**: complies by our reading but needs reviewer sign-off.
- **n/a**: guardrail not applicable.

### 9.1 Feature-policy matrix

| # | Feature (owner) | Source · maths | G1 | G2 | G3 | G4 | G5 | G6 | Condition / mitigation |
|---|---|---|---|---|---|---|---|---|---|
| 1 | Channel stats: subscribers, views, video count (02) | YT public · raw | OK | OK | OK | OK | COND | n/a | refreshed by daily ingest; purge if untracked > 30 d |
| 2 | Video metadata: title, description, tags, duration, publishedAt, thumbnails URL, liveBroadcastContent (02) | YT public · raw | OK | OK | OK | COND | COND | n/a | `categoryId` stored but never used to classify; refresh ≤ 30 d |
| 3 | Video stats: views, likes, comments (02) | YT public · raw | OK | OK | OK | OK | COND | n/a | statistics must not be stored > 30 d (III.E.4.b) unless the derived-metrics amendment is accepted |
| 4 | VideoSnapshot time series (02) | YT public · raw over time | OK | OK | OK | OK | **FLAG** | n/a | historical stats > 30 d are not refreshable → delete at 30 d; baselines must be computable within 30 d or need the amendment (36 months) |
| 5 | Comment text (02) | YT public · raw | OK | OK | OK | COND | COND | n/a | used transiently for signals; text deleted ≤ 30 d |
| 6 | Own Analytics: watch time, retention, traffic sources, search terms, subscribers gained (02, OAuth) | YT Analytics · raw | OK | OK | OK | COND | COND | n/a | demographic reports (ageGroup/gender) never requested; authorized stats may persist with 30-day re-authorization checks |
| 7 | Quota ledger (02) | internal | n/a | n/a | n/a | n/a | n/a | n/a | no API data stored |
| 8 | Video age (06) | YT · subtraction | OK | OK | OK | OK | COND | n/a | derived from publishedAt |
| 9 | ViewsAtAge observed/interpolated (06) | YT · linear interpolation | COND | OK | OK | OK | COND | n/a | linear interpolation between two API observations only; labelled; no curve fitting |
| 10 | ViewsAtAge proxy for pre-tracking videos (06) | YT · peer median ratio | COND | OK | OK | OK | COND | n/a | labelled "proxy", never displayed as observed |
| 11 | View velocity (06) | YT · difference / days | OK | OK | OK | OK | COND | n/a | — |
| 12 | Engagement rate (likes+comments)/views (06) | YT · ratio | OK | OK | OK | OK | COND | n/a | derived metric → III.E.4.h (see 13) |
| 13 | ChannelBaseline median/mean views at age by format (06) | YT · median/mean | OK | OK | OK | OK | COND | n/a | per channel, never across owners presented as one owner's data |
| 14 | Outlier ratio and flag (06) | YT · ratio + threshold | OK | OK | OK | OK | COND | n/a | core assignment metric; derived metric → amendment recommended |
| 15 | Length band, Shorts flag (06) | YT duration · bucketing | OK | OK | OK | OK | COND | n/a | Shorts by duration rule (no visual inference) |
| 16 | Title traits: length, question, number, how-to, year, brackets (06) | YT title · string rules | OK | OK | OK | OK | COND | n/a | regex/lexical only |
| 17 | Thumbnail traits: text present, word count, contrast, brightness (06) | YT thumbnail · OCR + pixel stats | OK | OK | OK | OK | COND | OK | OCR text count and luminance contrast only; no face/object/person detection; image bytes not persisted |
| 18 | Publish day/hour (06) | YT · calendar | OK | OK | OK | OK | COND | n/a | UTC or client tz; no audience timezone inference |
| 19 | CommentSignals: buyer-question mentions, question count, purchase-intent keywords (06) | YT comments · lexical match to client list | OK | OK | OK | **FLAG** | COND | n/a | match against the client's buyer questions only; no commenter attributes; **no health-status inference (Apollo)**; aggregates only |
| 20 | TraitLift over outliers vs non-outliers (06) | YT · ratio of proportions, min sample | OK | OK | OK | OK | COND | n/a | — |
| 21 | ChannelScorecard: cadence, median ratio, outlier rate, consistency, Shorts share (06) | YT · counts/medians/dispersion | OK | OK | OK | OK | COND | n/a | leaderboard framing neutral (derived-metrics category 5) |
| 22 | OwnPerformanceReport: own vs own baseline; Analytics side panel (06) | YT public + YT Analytics · ratios | OK | COND | OK | OK | COND | n/a | public-stat metrics and OAuth metrics shown in separate labelled panels |
| 23 | TrendingReport incl. mostPopular chart membership (06/02) | YT · velocity, raw chart | OK | OK | OK | COND | COND | n/a | chart's `videoCategoryId` filter used only as YouTube's own label, never assigned by us |
| 24 | TopicMatch lexical score (05) | YT text vs client keywords · fuzzy match | OK | COND | OK | COND | COND | n/a | match to client's own TopicRefs, not categories; score internal |
| 25 | TopicMatch embedding similarity (05) | YT text vs client refs · cosine | **FLAG** | COND | OK | COND | COND | n/a | not "simple maths" on API numbers; it is text matching to client-defined refs; disclosed; flagged for review as the assignment asks |
| 26 | TopicMatch LLM adjudication (05) | LLM · constrained yes/no vs client list | **FLAG** | COND | OK | **FLAG** | COND | n/a | constrained to the client's list; logs and review flag; never free clustering |
| 27 | Topic coverage/gap counts (05/07) | YT · counts per client topic | OK | OK | OK | OK | COND | n/a | — |
| 28 | BusinessProfile: offerings, value props, locations, conversion actions (03) | website · LLM extraction | n/a | OK | OK | OK | n/a | n/a | non-YouTube source; never combined into YouTube metrics |
| 29 | Buyer questions (03 / Spotlit tracking) | website/internal | n/a | OK | OK | OK | n/a | n/a | — |
| 30 | FreshnessProfile (03/08) | web search · counts | n/a | OK | OK | OK | n/a | n/a | — |
| 31 | Competitor candidate evidence: web mentions, YouTube keyword overlap, ownership verification (04) | web + YT · separate scores | COND | **FLAG** | OK | OK | COND | n/a | web evidence and YouTube evidence kept as separate fields; ranking is lexicographic + human confirmation, **no blended score** |
| 32 | Recommendation priority score: evidence × gap × fit (07) | YT metrics + client buyer questions · internal ordering | COND | **FLAG** | OK | OK | COND | n/a | assignment prescribes "scored on evidence, fit and buyer intent"; internal ordering only, never displayed as a metric; displayed numbers individually labelled |
| 33 | Gap analysis "competitors cover, you don't" (07) | YT · counts | OK | OK | OK | OK | COND | n/a | — |
| 34 | expected_range_vs_baseline (07) | YT · quantiles of reference ratios × client baseline | OK | OK | OK | OK | COND | n/a | range, "indicative", never a promise; no website data |
| 35 | Confidence label (07) | YT · counts thresholds | OK | OK | OK | OK | COND | n/a | — |
| 36 | Creator ranker: relevance, performance vs niche, consistency (07) | YT · match + medians + dispersion | OK | OK | OK | OK | COND | n/a | no audience inference; no reach/demographic estimates |
| 37 | Collaboration labels: mention vs disclosed paid promotion (07) | YT text/fields · lexical + API flag | OK | OK | OK | OK | COND | n/a | uses the creator's own disclosure only (API field unverified); no revenue/deal value estimates |
| 38 | No-copy title similarity, novelty vs own uploads (07) | YT titles · fuzzy ratio | OK | OK | OK | OK | COND | n/a | internal guard |
| 39 | NewsSignal: recency, source count (08) | web search · counts | n/a | OK | OK | OK | n/a | n/a | — |
| 40 | YouTube demand check for a news keyword (08) | YT · counts/views | OK | COND | OK | OK | COND | n/a | shown beside the news evidence, never multiplied into one "opportunity score" displayed to users |
| 41 | news_opportunity ranking (08) | web + YT · internal ordering | COND | **FLAG** | OK | OK | COND | n/a | same treatment as #32 |
| 42 | Refresh-warranted decision (09) | web freshness + YT velocity · rule | COND | COND | OK | OK | COND | n/a | rule with each condition shown separately, no fused metric |
| 43 | Description keyword insertions (09) | LLM text from news + own search terms | n/a | OK | OK | OK | COND | n/a | additive diff only; search terms only for OAuth-connected own channel |
| 44 | Card wording + grounding validation (10) | LLM over evidence | n/a | OK | OK | OK | COND | n/a | LLM cannot add numbers/videos; card stores API data → refresh/expire ≤ 30 d |
| 45 | LLM critic/review score (10) | LLM judgement | n/a | OK | OK | OK | n/a | n/a | internal gate signal, never displayed |
| 46 | Backtest metrics (12) | YT · rates/ratios aggregates | OK | OK | OK | OK | COND | n/a | video-level rows deleted at 30 d; aggregates retained (13) |
| 47 | Outlier P/R and threshold curve (12) | human labels + YT ratios | OK | OK | OK | OK | COND | n/a | video-level detector values deleted at 30 d |
| 48 | Human review ratings (12) | human | n/a | n/a | OK | OK | COND | n/a | card snapshots nulled at 30 d |
| 49 | Ops metrics: units, latency, tokens, cost (12) | internal telemetry | n/a | n/a | n/a | n/a | n/a | n/a | no API data |

Summary of items requiring reviewer sign-off (FLAG): #4 snapshot retention vs same-age baselines, #19 comment signals in sensitive niches, #25/#26 embedding and LLM topic matching, #31/#32/#41 internal rankings that combine YouTube evidence with non-YouTube fit or news. The mitigation for all ranking flags: no blended number is ever displayed, every displayed figure is single-source and labelled, and the ordering rule is documented.

### 9.2 This component's own compliance
- Evaluation never calls `search.list`, and its quota is capped and scoped.
- Freeze bundles, outcome rows, label packs and review packs all expire ≤ 30 days from their oldest API datum and are registered with the retention cron.
- Retained artefacts contain only aggregates and human judgements.
- Packs hotlink thumbnails rather than storing them.
- No demographic or category data is ever generated.

---

## 10. Performance, cost & scaling

| Activity | Quota units | LLM / web cost | Wall time |
|---|---|---|---|
| Freeze per client | 0 (DB dump) | 0 | < 10 s, 5–20 MB |
| Replay (writer off) per client | 0 | ~$0.00–0.05 (cache-miss adjudications, Haiku) | ~30–60 s |
| Outcome collection per client | 0 normally; ≤ 20 if refetching deleted-status (videos.list batches of 50) | $0–0.05 (adjudication for post-T titles) | < 30 s |
| Random arm (1,000 replicates × k specs) | 0 | 0 | < 5 s (vectorised boolean matrix videos × specs) |
| Bootstrap 10,000 × arms | 0 | 0 | < 10 s with numpy (cluster index resampling) |
| Mode B retro per niche (15 channels, 200 uploads each) | ~60 playlistItems + ~60 videos.list ≈ 120 | ~$0.10 | ~1–2 min |
| Label pack (100) | 0 | 0 | seconds |
| Review pack (30 + 6 decoys) | 0 | ~$0.10 (decoy cards via writer) | < 1 min |
| Full evaluation (5 clients, 2 freezes, Mode A + B) | ≤ ~700 | ≲ $1.5 | < 15 min |
| Ops report (7 days) | 0 | 0 | < 2 s (rollups) |
| Alert cron | 0 | 0 | < 1 s per tick |

Production impact: evaluation runs on a separate arq queue (`evaluation`) with concurrency 1 and lower priority than client refreshes. Hard caps: `max_usd`, `max_quota_units`.

Scaling: rollups make ops queries O(days × clients). Ledgers are partitioned by month if they exceed ~10M rows (01).

---

## 11. Testing strategy

- **Stats unit tests** against published reference values:
  - Wilson interval (Brown, Cai & DasGupta 2001 examples).
  - Cohen's κ and weighted κ (textbook 2×2/ordinal examples).
  - Krippendorff's α (the worked example in Krippendorff 2011).
  - Gwet's AC1 (Gwet 2008 example).
  - Two-proportion sample size (values in 5.1.4).
  - Horvitz–Thompson weighted P/R on a hand-computed toy design.
  - Bootstrap determinism (same seed gives identical CI) and coverage, via a simulation test: nominal 95% CI covers the true rate in 93–97% of 500 simulated datasets (marked slow).
- **Synthetic world fixture** (`SyntheticNiche`): channels with lognormal views-at-age per channel and known baselines. Plant a pattern (e.g. topic A + question title triples outlier probability).
  - The backtest must recover lift ≈ 3x within its CI.
  - copy_top and random must not.
  - With no planted effect, the verdict must not be `supported` in ≥ 95% of seeds.
- **Leakage canary** (CI): perturb all rows with timestamp > T; replay specs must be byte-identical. Frozen repository assertion tests.
- **Matcher tests**: each spec type and constraint; any-of topics; untestable specs.
- **Golden tests**: report markdown rendered from a fixed `BacktestReport`/`OutlierEvalResult`/`ReviewSummary`/`OpsReport` (snapshot compare).
- **Pack tests**: hidden fields are absent from the HTML; CSV export schema round-trips through the importer; expiry is embedded; seeded order is reproducible.
- **Contract tests** with 02/10/11 ledgers: the required fields exist (pydantic models on read); the amortised attribution sums to project total units.
- **Registry test**: every field of the listed analytics/recommendation objects has a `FeatureSpec`; FLAG rows have notes.
- **Alert rule tests**: seeded ledger rows trigger and resolve each rule; dedupe works.
- **Logging test**: the redaction processor removes secrets; contextvars are bound inside pipeline stages.
- **Live smoke** (manual, staging): freeze one real client, replay, run an ops report for 1 day; ≤ 20 quota units.

---

## 12. Implementation task breakdown

Sizes: S ≤ 0.5 day, M ≈ 1 day, L ≈ 2–3 days.

| # | Task | Size | Depends on |
|---|---|---|---|
| 1 | `FeatureSpec`/`FeatureRegistry` + matrix renderer + CI completeness test; seed rows from section 9 (Week 1 deliverable) | M | 01 |
| 2 | structlog conventions in `core/logging.py` (contextvars, JSON/console, redaction) | S | 01 |
| 3 | `eval_*` tables + Alembic migration; retention registration | M | 01 |
| 4 | `Clock` port adoption check + `FixedClock`; `Frozen*Repository` adapters | M | 01, 02 (repo Protocols) |
| 5 | `FreezeJob` + bundle writer/reader + cron/CLI; **must ship in Week 2 day 1** | M | 02 ingest live, 4 |
| 6 | Stats module (bootstrap, proportions, power, agreement, HT weights) + reference tests | L | — |
| 7 | `PatternSpec` agreement with 07; `PatternMatcher` + tests | M | 05, 06, 07 |
| 8 | `OutcomeCollector` (views at age, baseline as of T, statuses) | M | 02 snapshots, 06 |
| 9 | Arms (system, copy_top, random, base_rate) | M | 7 |
| 10 | `BacktestRunner` (registration, caps, checkpoints, canary) + `SyntheticNiche` tests | L | 5–9, 11 (runner with writer off) |
| 11 | Report builder + templates (evaluation report) | M | 10 |
| 12 | Outlier sampler + `PackRenderer` + label pack + importer | M | 6, 06 metrics |
| 13 | Label scoring: gold, agreement, weighted P/R, threshold curve, variants | M | 12 |
| 14 | Review sampler (with decoys) + review pack + importer + scorer + gate tuning | M | 12, 07, 10 |
| 15 | Ops report builder + rollup job + API endpoints | M | 02/10/11 ledgers |
| 16 | Alert rules + evaluator cron + webhook notifier | M | 15 |
| 17 | Optional OTel/Prometheus switches (off by default) | S | 2 |
| 18 | `RetroProxyBacktest` (Mode B) | M | 10 |
| 19 | Demo runbook, design-doc assembly, artefact generation | M | all |

Critical path for the demo: 3 → 4 → 5 (by Week 2 day 1) → 6/7/8/9 (Week 3) → 10/11 (Week 4 day 1). Labelling (12–13) and review (14) run in parallel in Weeks 3–4.

---

## 13. Open questions / proposed changes to shared context

1. **search.list quota is wrong in §2.** The quota table now lists `search.list` at **1 unit** with its own 100 calls/day bucket (https://developers.google.com/youtube/v3/determine_quota_cost, verified 2026-11-20). §2's "search.list is 100 units/call" should be corrected. The real constraint is the 100-call bucket. The quota manager (02) must track two budgets (units and search calls).
2. **30-day rule vs same-age history.** Developer Policies III.E.4.b forbid storing non-authorized statistics (view counts) beyond 30 days, so day-7 snapshots of competitor videos cannot be kept longer. That limits baselines to videos observed within 30 days and makes backtests re-runnable only for 30 days. **Proposal:** apply for the YouTube **Derived Metrics Policy amendment** (Analytics & Reporting use case, effective 2026-06-01). It allows custom ratios/averages and storing statistics and derived metrics for up to 36 months; titles/descriptions still 30 days. Until accepted, every component must assume the 30-day limit. §2 should mention both.
3. **Derived metrics (III.E.4.h).** Without the amendment, the policy text prohibits creating "new or derived data or metrics" from API data. The assignment explicitly allows ratios, but our outlier ratio, engagement rate and scores are derived metrics in Google's terms. This strengthens proposal 2; add it to §2 as a known policy risk.
4. **Add `pattern_spec: PatternSpec` to `RecommendationDraft`** (07), persisted with every recommendation. It is the machine-readable pattern the card describes. The backtest, the novelty gate and future prospective tracking all need it. Add `PatternSpec` to the §5 shared vocabulary.
5. **Add a `Clock` port to §4 `ports/`** and require every service to read "now" from `RunContext.clock`. Repositories must expose `as_of`-aware reads (or be swappable for frozen adapters). Without this, point-in-time replay is impossible.
6. **Unified `external_usage` ledger** (or at least identical columns across 02/03/08/10): `provider, endpoint, units|credits|tokens by kind, client_id, run_id, scope (shared|client|evaluation), cache_hit, attempt, status, latency_ms, occurred_at`. Ops reporting then stays one query and per-client attribution is consistent. Cache *reads* need logging with `client_id` for amortised quota attribution.
7. **Run kind `evaluation`** and a separate low-priority arq queue (11). Quota budget scopes (02): production, evaluation (capped), with production priority.
8. **Demo data timeline is a hard dependency.** Demo clients must be onboarded and snapshotting by **Week 2 day 1**. Freezes T₁/T₂ must be taken then, before the recommender exists. Add to §3 build order. The snapshot cron (02) should guarantee captures bracketing day 7 for every new video in tracked universes, and should track ~15 channels per demo client.
9. **Model naming:** the bundled SDK docs list `claude-haiku-5-5` as the current Haiku, at $0.10/$0.50 per MTok vs $1/$5 for `claude-haiku-4-5`. Consider switching the low-reasoning tier (a ~10x cost cut for adjudication/critic) after a quick quality check. Keep the model configurable as already decided.
10. **Demo clients:** confirm a dental clinic group with an active channel (`@AspenDental` did not resolve), a larger home-services brand (`@MrRooterPlumbing` has 17 videos), and Runable's official channel (`@runable` resolves to a 0-video channel; ownership unverified). AutoNation (`UCSHJ1SCNy-QRaI4-tkSwHDQ`) is verified as an auto-dealer candidate.
11. **Retention of IDs and aggregates:** the policies do not explicitly address keeping video/channel IDs or aggregate evaluation results beyond 30 days. This plan nulls IDs at 30 days and keeps aggregates plus human judgements. Ask for a reviewer decision so 01's retention registry is consistent.
12. **Outlier threshold and baseline statistic defaults** (06): this plan assumes θ = 3.0 and a format-split median baseline. The labelled set (5.2) will produce the recommended θ and a mean-vs-median comparison (the assignment says "average"). 06 should treat both as config, with the eval result recorded as the rationale.
13. **Rankings that combine YouTube evidence with non-YouTube fit** (07 priority score, 08 news ranking, 04 competitor evidence) are internal orderings. Shared context should state the rule explicitly: "no blended number is ever displayed; every displayed figure is single-source and labelled". This makes G2 compliance auditable.
