# 06 — Analytics engine

Deterministic metrics, same-age views, baselines, outliers, traits (title, format, timing, thumbnail, description), comment intent signals, pattern mining, scorecards and reports.

Owner component: `backend/app/services/analytics/`. Runs as the `analytics` stage of the analysis run and the daily refresh run (see 00 §4). No LLM calls. No YouTube API calls of its own; it reads the shared cache that 02 fills.

Live evidence used in this plan was collected on 2026-11-20 with one `playlistItems.list` and one `videos.list` call (2 quota units) on Apollo Hospitals' last 50 uploads (`UU0InVdvqNyNzKBl1-TL348A`):

| Observation (Apollo, last 50 uploads) | Value | Design consequence |
|---|---|---|
| Median views (48 non-upcoming) | 1,320 | the median is the baseline |
| Mean views | 51,415 (39x the median) | the mean is unusable as "normal" |
| 10% trimmed mean | 12,782 | trimming still leaves bias, so it is shown for display only |
| 6 videos with 188k–899k views | 0.06–0.89 likes per 1k views vs channel median 3.6 likes/1k | views spiking without engagement are suppressed as outliers (§5.7.4) |
| 1 video with 61,913 views | 57 likes/1k | a real organic outlier that must survive suppression |
| 2 `liveBroadcastContent=upcoming` items | duration 0, views 0 | excluded from everything |
| 2 live replays | durations 1,040 s and 6,945 s | live gets its own segment |
| "360° Stroke Care…" uploaded twice at the same minute, 31 s each | duplicate pair | duplicate collapsing (§5.5.3) |
| Many 10–90 s horizontal brand spots | not Shorts, but ≤ 180 s | "short-form" label, not "Short" (§5.5.1) |
| Most videos have 0–2 comments | — | comment signals must degrade gracefully |

---

## 1. Purpose & business value

The analytics engine turns cached YouTube API data into **fair, reproducible evidence**. It is the only producer of numbers in the product. Recommendation (07), the writer (10) and the UI (13) can only show what this component computes.

How it drives conversions for onboarded businesses:

1. **Fair comparisons stop the business chasing the wrong signal.** Same-age views judged against each channel's own median separate real breakouts from big-channel noise and ad-driven spikes. Apollo shows why: six ad-like spikes push the mean to 39x the median. A mean-based engine would call 90% of Apollo's organic videos "underperforming" and recommend copying paid spots.
2. **The conversion lens stays separate and honest.** Each video gets an answer-directness level for the client's buyer questions, buyer-intent comment signals (cost, location, booking, doctor, "does it work"), and description conversion hygiene (booking link, phone/WhatsApp, chapters). The client's own channel adds subscribers gained, traffic sources and search terms when OAuth is connected. These are always shown beside view metrics, never blended into them.
3. **Patterns come with honest confidence.** Lift is reported with minimum support, smoothing, Wilson/Katz intervals and false-discovery control. Recommendations then rest on "4 of 6 outliers across 4 channels" rather than on one lucky video.
4. **Competitor scorecards and the gap matrix** show which buyer questions competitors answer and the client does not. That is the most direct path from analytics to a conversion-oriented video idea.
5. **Thin data is reported as thin data.** Every output carries sample sizes, method labels and sufficiency flags. 07 uses them to say "Not enough outliers in 7 days. Widen to 30?" instead of guessing.

## 2. Scope / non-scope

**In scope**
- Age conventions, snapshot cleaning, `ViewsAtAge` at days 1/3/7/30 (observed, interpolated, owner-analytics, age-band proxy).
- Per-video metrics: views per day, velocity, engagement/like/comment rates.
- Format segmentation (short-form / long-form / live), duplicate collapsing.
- `ChannelBaseline` per channel × segment × metric × age × method, with a fallback ladder.
- Outlier assessment (ratio + robust z), tiers, suppression rules, and the validation plan for the 100-video labelling (12 runs it; this component defines the protocol and exposes the scoring functions).
- `VideoTraits`: topic (from 05), segment, length band, title traits, timing in client timezone, description conversion traits, `ThumbnailTraits` (local OCR + pixel statistics, no faces).
- `CommentSignals`: buyer-intent lexicon matching (English, romanised Hindi, Devanagari), question share, unanswered questions, PII handling.
- Pattern mining (`TraitLift`) per scope (competitor set, niche, own), with gated 2-way interactions.
- Reports: `OwnPerformanceReport`, `ChannelScorecard` (incl. gap matrix, collaborations, paid-placement share), `TrendingReport`, outlier list, `ConversionLens`.

**Not in scope (owner)**
- Fetching data, quota, snapshot scheduling, comment fetching, OAuth: **02** (§5.3.7 lists the snapshot cadence this component needs).
- Topic matching and its scores: **05**. 06 consumes `TopicMatch` and derives answer directness from it.
- Recommendation scoring, confidence labels, `expected_range_vs_baseline` wording, creator ranking: **07**. 06 supplies counts, quantiles and channel stats.
- Card wording: **10**. Evaluation harness, labelling UI, ops report: **12**. 06 exports the labelling sample and the PR functions.
- News, description refresh decisions: **08/09**. They consume `DescriptionTraits` and `TraitLift`.

## 3. Inputs & outputs

### 3.1 Inputs (consumed; defined by other components)

| Object | Fields 06 reads | From |
|---|---|---|
| `VideoRecord` | `video_id, channel_id, published_at, title, description, duration_s, live_broadcast_content, live_actual_start_at, live_actual_end_at, live_scheduled_start_at, has_live_details, view_count, like_count (None if hidden), comment_count (None if disabled/absent), thumbnails{key: url,w,h}, default_audio_language, made_for_kids, has_paid_product_placement, fetched_at, is_available` | 02 |
| `VideoSnapshot` | `video_id, captured_at, view_count, like_count?, comment_count?` | 02 |
| `ChannelRecord` | `channel_id, title, handle, subscriber_count?, hidden_subscriber_count, video_count, uploads_playlist_id, fetched_at` | 02 |
| `CommentRecord` (in-memory only for 06) | `comment_id, video_id, text_original, published_at, like_count, author_channel_id, total_reply_count, visible_reply_author_channel_ids[], fetched_at` | 02 |
| `OwnAnalytics` (OAuth only) | `daily_views[video_id][date_pt]`, `subscribers_gained[video_id]`, `traffic_sources[video_id]{source_type: views}`, `search_terms[video_id][(term, views)]`, `creator_content_type[video_id]`, `period, fetched_at` | 02 |
| `TopicMatch` | `video_id, topic_ref_id, kind (keyword/buyer_question), score 0..1, method, review_flag, matched_terms[]` | 05 |
| `TopicRef` / `BuyerQuestion` | `id, text, core_terms[], locale` | 03/05 |
| `BusinessProfile` | `timezone (IANA), country, languages[], website_domains[], locations[] (city/area names + aliases), conversion_actions[] (book, call, whatsapp…), niche_lexicon_overrides?` | 03 |
| `CompetitorCandidate` (confirmed) | `channel_id, website_domains[]` | 04 |
| `AnalysisScope` (run input) | `client_id, own_channel_id?, competitor_channel_ids[], niche_channel_ids[], niche_video_ids[] (from cached search), window_days ∈ {7,30,90}, as_of (UTC), oauth_connected` | 11 |

### 3.2 Outputs (owned by 06; pydantic-style)

```
class FormatSegment(StrEnum): short_form, long_form, live            # upcoming/unknown are excluded, not segments
class AgeMethod(StrEnum): observed, interpolated, owner_analytics, age_band_proxy, pending, unavailable
class OutlierTier(StrEnum): strong_outlier, outlier, breakout, normal, below, insufficient
class MetricFlag(StrEnum): likes_hidden, comments_disabled, count_decreased, count_frozen, too_new,
    low_views_for_rates, duplicate_of, live_or_premiere, shorts_counting_regime_pre_2025_03_31,
    published_at_changed, title_changed, thumbnail_changed, unavailable_now, views_without_engagement,
    non_trailing_baseline, widened_lookback

class ViewsAtAge:
    video_id: str
    age_days: int                       # target age: 1 | 3 | 7 | 30
    views: int | None
    likes: int | None
    comments: int | None
    method: AgeMethod
    observed_age_days: float | None     # actual age of the value; equals age_days ± tol unless proxy
    bracket_lo_at: datetime | None
    bracket_hi_at: datetime | None
    bracket_spread: float | None        # (V_hi - V_lo) / V
    source: Literal["data_api_snapshot", "analytics_api"]
    as_of: datetime
    source_oldest_at: datetime          # retention driver

class Velocity:
    views_per_day: float
    from_at: datetime
    to_at: datetime
    window_hours: float

class OutlierAssessment:
    metric: Literal["views_at_age"]
    age_days: int                       # comparison age actually used
    method: AgeMethod                   # same method for numerator and baseline, always
    value: int
    baseline_median: float
    baseline_n: int
    baseline_id: str
    ratio: float                        # value / max(baseline_median, min_baseline_views)
    log_robust_z: float
    tier: OutlierTier
    is_outlier: bool                    # tier in {outlier, strong_outlier} and not suppressed
    suppressed_reason: str | None       # e.g. "views_without_engagement", "duplicate"

class VideoMetrics:
    video_id: str
    channel_id: str
    as_of: datetime
    segment: FormatSegment
    segment_evidence: dict[str, bool]   # duration_rule, hashtag_shorts, pillarbox, owner_analytics
    age_anchor_at: datetime
    age_days: float
    views_now: int
    likes_now: int | None
    comments_now: int | None
    views_at_age: dict[int, ViewsAtAge]
    views_per_day_lifetime: float
    velocity_recent: Velocity | None
    engagement_rate: float | None
    like_rate: float | None
    comment_rate: float | None
    engagement_basis: Literal["likes+comments", "likes_only", "comments_only", "none"]
    outlier: OutlierAssessment | None
    duplicate_group_id: str | None
    flags: list[MetricFlag]
    metrics_version: str

class ChannelBaseline:
    baseline_id: str
    channel_id: str
    segment: FormatSegment
    metric: Literal["views_at_age", "like_rate", "engagement_rate", "velocity"]
    age_days: int | None
    method: AgeMethod
    age_band: tuple[float, float] | None    # proxy only
    as_of: datetime
    anchor_video_id: str | None             # trailing baselines are per video
    median: float
    mean: float                             # display only
    trimmed_mean_10: float                  # display only
    p25: float
    p75: float
    mad_log10: float
    n: int
    video_ids: list[str]
    fallback_level: Literal["trailing", "non_trailing", "widened", "none"]
    sufficient: bool

class TitleTraits:
    char_len: int
    char_len_band: str
    word_count: int
    is_question: bool
    has_number: bool
    has_listicle_number: bool
    is_how_to: bool
    has_year: bool
    year_is_current_or_next: bool
    has_brackets: bool
    has_parentheses: bool
    is_first_person: bool
    has_superlative: bool
    caps_ratio: float | None
    caps_band: str | None
    all_caps_word_count: int
    has_emoji: bool
    emoji_count: int
    separator: Literal["none", "colon", "pipe", "dash", "multiple"]
    has_price_cost: bool
    has_buyer_question_phrasing: bool
    mentions_doctor: bool
    script: Literal["latin", "devanagari", "mixed", "other"]
    # client-context (computed per client, never shared):
    has_client_location: bool
    has_niche_location: bool

class TimingTraits:                     # client-context
    client_timezone: str
    publish_weekday: int                # 0 = Monday
    publish_hour_local: int
    daypart: Literal["night", "morning", "afternoon", "evening", "late"]
    is_weekend: bool

class ThumbnailTraits:
    video_id: str
    source_key: Literal["maxres", "standard", "high", "medium", "default"]
    source_w: int
    source_h: int
    content_sha256: str
    letterbox_cropped: bool
    pillarbox_detected: bool
    text_present: bool
    word_count: int | None              # None = text detected but unreadable
    word_count_band: str | None
    text_box_count: int
    text_area_ratio: float
    largest_text_height_ratio: float
    brand_text_only: bool
    script_detected: Literal["latin", "devanagari", "mixed", "none", "unknown"]
    ocr_engine: str
    ocr_model: str
    ocr_confidence_mean: float | None
    brightness: float                   # 0..1
    brightness_band: str
    rms_contrast: float                 # 0..~0.5
    contrast_band: str
    colorfulness: float                 # Hasler–Süsstrunk M, 0..~150
    colorfulness_band: str
    dominant_colour_count: int
    traits_version: str
    computed_at: datetime

class DescriptionTraits:
    char_len: int
    links_count: int
    link_domains: list[str]
    has_external_link: bool
    has_own_site_link: bool             # the uploading channel's known site
    has_shortener_link: bool
    has_booking_cta: bool
    has_phone: bool
    has_whatsapp: bool
    has_chapters: bool
    chapter_count: int
    chapter_titles: list[str]
    hashtag_count: int
    hashtags_ignored: bool              # > 60 hashtags
    has_paid_disclosure_text: bool
    mentioned_handles: list[str]
    collab_markers: list[str]
    # client-context:
    has_client_site_link: bool
    first_two_lines_have_client_keyword: bool

class VideoTraits:
    video_id: str
    traits_version: str
    segment: FormatSegment
    length_band: str
    title: TitleTraits
    description: DescriptionTraits
    thumbnail: ThumbnailTraits | None
    topics: list[TopicTrait]            # (topic_ref_id, kind, score, review_flag, directness)
    timing: TimingTraits                # client-context

class IntentClass(StrEnum): cost, location, booking, doctor, efficacy, how_to, availability, comparison
class CommentSignals:
    video_id: str
    as_of: datetime
    comment_count_api: int | None
    sampled_n: int
    eligible_n: int                     # after spam/owner filtering
    sample_coverage: float | None       # sampled_n / comment_count_api
    question_n: int
    question_share: float | None
    intent_counts: dict[IntentClass, int]
    buyer_intent_n: int                 # comments with ≥1 intent class
    buyer_intent_share: float | None
    unanswered_question_n: int
    unanswered_question_share: float | None
    owner_reply_visible_n: int
    matched_buyer_questions: list[tuple[str, int]]   # (buyer_question_id, count)
    lexicon_version: str
    sufficient: bool
    excerpts: list[str]                 # redacted; empty unless enabled for niche

class TraitLift:
    scope: Literal["competitor_set", "niche", "own"]
    window_days: int
    as_of: datetime
    comparison_age_days: int
    comparison_method: AgeMethod
    trait_key: str                      # e.g. "title.is_how_to", "topic", "segment"
    trait_value: str
    interaction: tuple[str, str] | None
    n_with: int
    n_without: int
    outliers_with: int                  # a
    outliers_without: int               # c
    outlier_rate_with: float
    outlier_rate_without: float
    rate_with_wilson: tuple[float, float]
    lift: float | None
    lift_smoothed: float
    lift_ci: tuple[float, float] | None
    channel_balanced_lift: float | None
    p_value: float
    q_value: float
    outlier_channels_with: int
    max_channel_share: float
    ratio_quantiles_with: tuple[float, float, float]     # p25, p50, p75 (winsorised)
    reference_video_ids: list[str]      # outliers with the trait, sorted by ratio
    status: Literal["supported", "exploratory", "insufficient_support", "no_contrast", "dominated", "redundant"]
    redundant_with: str | None

class GapCell:
    n_videos: int
    n_outliers: int
    best_ratio: float | None
    latest_published_at: datetime | None
    best_directness: str | None

class ChannelScorecard:
    channel_id: str
    client_id: str
    window_days: int
    as_of: datetime
    uploads_in_window: int
    uploads_per_week: float
    gap_cv: float | None
    longest_gap_days: float | None
    days_since_last_upload: float | None
    segment_mix: dict[FormatSegment, float]
    median_views_at_age: dict[FormatSegment, tuple[int, AgeMethod, int]]   # value, method, n
    median_ratio: float | None
    outlier_hit_rate: tuple[int, int]   # outliers, eligible
    outlier_hit_rate_wilson: tuple[float, float]
    median_engagement_rate: float | None
    topic_coverage: dict[str, GapCell]  # topic_ref_id -> cell
    top_traits: list[TraitLift]         # channel-level, may be "exploratory"
    paid_placement_share: tuple[int, int] | None
    collaborations: list[CollabMention] # (video_id, handle_or_name, kind: mention | disclosed_paid)
    subscriber_count: int | None
    subscriber_hidden: bool
    size_vs_client: float | None        # display only; never used in scoring
    data_sufficiency: dict[str, bool]

class OwnPerformanceReport:
    client_id: str
    channel_id: str
    window_days: int
    as_of: datetime
    comparison_age_days: int
    method_mix: dict[AgeMethod, int]
    videos: list[VideoPerf]             # video_id, ratio, tier, method, segment, topics, published_at
    median_ratio_window: float | None
    median_ratio_prev_window: float | None
    best: list[str]
    worst: list[str]
    by_segment: dict[FormatSegment, SegmentPerf]   # n, median ratio, IQR
    by_topic: dict[str, SegmentPerf]
    by_length_band: dict[str, SegmentPerf]
    consistency: ConsistencyStats       # uploads/week, gap_cv, hit_rate_≥1x, log-ratio IQR
    baselines: list[ChannelBaseline]
    conversion: ConversionLens          # separate block
    sufficiency_messages: list[str]

class TrendingReport:
    client_id: str
    window_days: int
    as_of: datetime
    items: list[TrendingItem]           # video_id, channel_id, ratio, method, velocity_ratio, views_per_day, topics, segment
    rising_topics: list[TopicTrend]     # topic_ref_id, uploads now/prev, outliers now/prev
    chart_items: list[TrendingItem]     # mostPopular ∩ client topics
    sufficiency_messages: list[str]

class OutlierRecord:
    video_id: str
    channel_id: str
    scope: str
    published_at: datetime
    views_at_age: ViewsAtAge
    ratio: float
    tier: OutlierTier
    baseline_n: int
    topics: list[str]
    segment: FormatSegment
    url: str

class ConversionLens:
    answer_directness: dict[str, list[tuple[str, str]]]   # buyer_question_id -> [(video_id, level)]
    comment_intent: dict[str, CommentSignals]             # video_id -> signals
    description_cta_coverage: dict[str, tuple[int, int]]  # trait -> (videos with, eligible)
    own_channel: OwnConversionMetrics | None              # OAuth only
    website_side_by_side: None                            # placeholder; never merged, labelled "not from YouTube"

class OwnConversionMetrics:
    period: tuple[date, date]
    subscribers_gained_by_video: dict[str, int]
    subscribers_gained_per_1k_views: dict[str, float]     # both from Analytics API, same source
    traffic_source_share: dict[str, dict[str, float]]
    top_search_terms: dict[str, list[tuple[str, int]]]
    source_label: Literal["YouTube Analytics API (owner)"]

class AnalyticsResult:                  # stage output handed to 07/08/09
    run_id: str
    client_id: str
    as_of: datetime
    window_days: int
    metrics: dict[str, VideoMetrics]
    baselines: list[ChannelBaseline]
    traits: dict[str, VideoTraits]
    comment_signals: dict[str, CommentSignals]
    lifts: dict[str, list[TraitLift]]   # scope -> lifts
    outliers: dict[str, list[OutlierRecord]]
    scorecards: list[ChannelScorecard]
    own_report: OwnPerformanceReport | None
    trending: TrendingReport
    gap_matrix: dict[str, dict[str, GapCell]]   # topic_ref_id -> channel_id -> cell
    same_age_mode: dict[str, Literal["true", "proxy", "mixed_by_video"]]
    settings_hash: str
    versions: dict[str, str]
```

## 4. Internal design

### 4.1 Module layout

```
services/analytics/
  settings.py             AnalyticsSettings (pydantic-settings, nested groups; §5.13)
  clock.py                AgeClock: anchor(), age_days(); no now() anywhere else
  snapshots.py            SnapshotCleaner (suffix-minimum correction, freeze detection)
  ages.py                 ViewsAtAgeResolver + AgeResolutionStrategy registry
  segments.py             SegmentClassifier, DuplicateCollapser
  rates.py                RateCalculator (engagement, like, comment, velocity)
  baselines.py            BaselineBuilder (trailing, fallback ladder), BaselineStore
  outliers.py             OutlierRule Protocol, DualGateRule, SuppressionRule registry
  traits/
    base.py               TraitExtractor Protocol, TraitRegistry
    title.py duration.py timing.py description.py topic.py thumbnail.py
    lexicons/             versioned YAML: title_patterns, intents (en, hi-Latn, hi-Deva), cta, collab
  thumbnails/
    fetch.py              ThumbnailFetcher (uses ImageSource port; size fallback)
    pixels.py             letterbox crop, brightness, RMS contrast, colourfulness, hue bins
    text.py               ThumbnailTextAnalyzer (filters, recurring-text suppression)
  comments/
    lexicon.py            IntentLexicon registry keyed by (locale, niche)
    signals.py            CommentSignalExtractor, SpamFilter, PiiRedactor
  patterns/
    contingency.py        counts, weights
    stats.py              wilson(), katz_rr_ci(), fisher_one_sided(), bh_qvalues(), shrink()
    miner.py              PatternMiner (scopes, gates, redundancy)
    interactions.py       pre-registered pairs
  reports/
    own_performance.py scorecard.py trending.py outliers.py conversion.py gap_matrix.py
  stage.py                AnalyticsStage (pipeline Stage implementation)
ports (in app/ports/):
  ThumbnailTextReader     read(image: ImageArray, lang_hint: ScriptHint) -> list[TextBox]
  ImageSource             fetch(url: str) -> ImageBytes | None
adapters/ocr/
  rapidocr_reader.py      default
  tesseract_reader.py     optional, behind the same port
```

### 4.2 Key protocols (signatures only)

```
class AgeResolutionStrategy(Protocol):
    method: AgeMethod
    def resolve(self, video: VideoRecord, snaps: Sequence[CleanSnapshot], target_age: int,
                ctx: AgeContext) -> ViewsAtAge | None: ...

class TraitExtractor(Protocol):
    key: str                      # "title", "thumbnail", ...
    scope: Literal["intrinsic", "client_context"]
    version: str
    def extract(self, video: VideoRecord, ctx: TraitContext) -> Mapping[str, TraitValue]: ...

class OutlierRule(Protocol):
    def assess(self, value: int, baseline: ChannelBaseline) -> tuple[OutlierTier, float, float]: ...

class SuppressionRule(Protocol):
    reason: str
    def applies(self, metrics: VideoMetrics, channel_ctx: ChannelContext) -> bool: ...

class ReportBuilder(Protocol[R]):
    kind: str
    def build(self, data: AnalyticsData, scope: AnalysisScope, settings: AnalyticsSettings) -> R: ...

class ThumbnailTextReader(Protocol):
    engine: str
    model: str
    def read(self, image: ImageArray, script_hint: ScriptHint) -> list[TextBox]: ...
```

### 4.3 SOLID / extension points
- **Registries, not switches.** Age strategies, trait extractors, suppression rules, intent lexicons and report builders are registered by key. A new title trait or a new niche lexicon (e.g. auto-dealer "on-road price") is a new registry entry with no edits to the miner.
- **Ports for I/O.** OCR and image fetching sit behind ports (`ThumbnailTextReader`, `ImageSource`). Repositories (01) give data access. Pure functions sit in `stats.py`, `pixels.py` and `rates.py`.
- **Purity and determinism.** Every public function takes `as_of` and `settings` explicitly. No wall-clock reads, no randomness. Sorting always has a full tie-break key: `(primary desc, published_at asc, video_id asc)`. Inputs come from repository reads filtered by `captured_at ≤ as_of` and `fetched_at ≤ as_of`, so backtests at frozen T cannot leak future data.
- **Versioning.** `metrics_version`, `traits_version` (per extractor) and `lexicon_version` are stamped on rows. A version bump invalidates only that cache.
- **Shared vs client-scoped.** Intrinsic traits, thumbnail traits, metrics, baselines and comment signals are keyed only by video/channel and shared across clients. Client-context traits (timing in client tz, client location/site/keyword) and everything in reports are computed per client run.

### 4.4 Stage flow (inside `AnalyticsStage.run(ctx)`)
1. Load the videos in scope: the window plus the baseline lookback for every channel in scope.
2. `SnapshotCleaner` → `ViewsAtAgeResolver` for ages {1, 3, 7, 30}.
3. `SegmentClassifier`, then `DuplicateCollapser`.
4. `RateCalculator`.
5. `BaselineBuilder` for each needed (channel, segment, metric, age, method).
6. `DualGateRule` and suppression rules → `OutlierAssessment`.
7. Traits: cached intrinsic traits plus client-context traits. Thumbnail traits are computed in the enrich stage beforehand; here they are only read.
8. `CommentSignalExtractor`, cached per (video, lexicon_version, comment fetch).
9. `PatternMiner` per scope.
10. Report builders, then persist, then emit `RunEvent`s (`analytics.baselines_done`, `analytics.patterns_done`, …).

## 5. Algorithms & logic

All thresholds are defaults in `AnalyticsSettings` (§5.13) and can be overridden per tenant/niche.

### 5.1 Time and age conventions
- Store and compute in UTC. Convert to client tz only for timing traits and display.
- **Age anchor**: `anchor(v) = live_actual_start_at if has_live_details and live_actual_start_at else published_at`.
  - For a scheduled stream, `published_at` can be the scheduling time, so the anchor uses the actual start.
  - `publishedAt` for a video that was private and later made public is the time it went public (videos resource docs). If a refetch shows a changed `published_at`, set `published_at_changed`, re-anchor and invalidate its `ViewsAtAge`.
- `age_days(v, t) = (t − anchor(v)).total_seconds() / 86400`.
- Snapshots with `captured_at < anchor` are discarded. They can occur for premieres and scheduled streams.

### 5.2 Snapshot cleaning
Input: snapshots `(t_i, V_i)` sorted by `t_i`, all with `t_i ≤ as_of`.
1. **Monotone correction (spam-filter decreases).** YouTube removes invalid views after the fact, so later counts are the audited ones. Set `V'_i = min(V_i, min_{j>i} V_j)` (suffix minimum). If any `V_i − V'_i > max(0.01·V_i, 10)`, flag `count_decreased`. Likes and comments get the same correction.
2. **Freeze detection.** If V is unchanged across ≥ 48 h while age < 7 days and the previous 48 h grew, flag `count_frozen`. The value stays valid, but velocity from that interval is excluded from velocity baselines.
3. **Duplicates within 5 minutes.** Keep the later snapshot.

### 5.3 Views at the same age (`ViewsAtAge`)

#### 5.3.1 Target ages and comparison age per window
- Computed ages: **1, 3, 7, 30 days**.
- Comparison age per window (`comparison_age_by_window`): **7 → day 3, 30 → day 7, 90 → day 7**, with day 30 shown as secondary for the 90-day window when available.
- Why day 3 for the 7-day window: most videos in a 7-day window are younger than 7 days, so a day-7 rule would leave almost nothing to compare. Day 1 is dominated by publish-hour effects.
- A video younger than the comparison age is `pending` and labelled "too new to judge". It is never extrapolated.

#### 5.3.2 Resolution order (first strategy that succeeds wins)

| # | Method | Applies when | Value |
|---|---|---|---|
| 1 | `owner_analytics` | own channel, OAuth connected, daily views available | `V_d = Σ_{k=0}^{d−1} views(date_PT(anchor)+k)`. Calendar-day basis; the YouTube Analytics day is a Pacific-time day (verify, §7). Labelled "YouTube Analytics". Never mixed with Data API counts in one ratio |
| 2 | `observed` | some cleaned snapshot has `|a_i − d| ≤ tol(d)` | V at the closest such snapshot. `tol = {1: 3h, 3: 6h, 7: 12h, 30: 48h}` |
| 3 | `interpolated` | snapshots bracket d (`a_lo < d < a_hi`), `a_hi − a_lo ≤ max_gap(d)`, and `bracket_spread ≤ 0.25` | `V = V_lo + (V_hi − V_lo)·(d − a_lo)/(a_hi − a_lo)`. `max_gap = {1: 12h, 3: 36h, 7: 72h, 30: 10d}` |
| 4 | `pending` | `age_now < d − tol(d)` | None |
| 5 | `age_band_proxy` | first cleaned snapshot age `a0 > d + tol(d)` (published before tracking began, or a tracking gap) | V at the latest snapshot ≤ as_of; `observed_age_days = a_now`; compared only against age-matched peers (§5.3.4) |
| 6 | `unavailable` | anything else (e.g. gap too wide and spread too large) | None |

- **Why linear interpolation is enough and stays honest.** Cumulative views are monotone and, after the first hours, concave. Linear interpolation between two observations is simple maths, and its worst-case error is bounded by `V_hi − V_lo`. We store `bracket_spread` and refuse to interpolate when the bracket is too loose. A monotone cubic would fit better but adds modelling for little gain.
- **No extrapolation, ever.** Forecasting a day-7 value is an estimate that "replaces API data with independently calculated data" (Developer Policies III.E.4.h). It also breaks "numbers are computed, not written".

#### 5.3.3 Answer to the design-doc question: "How will you compare videos at the same age when snapshots only start the day we begin tracking?"

The short answer: **compare at whatever age both videos are actually observed. When that is impossible, compare against age-matched peers, label it as a proxy, and switch to true day-N values as tracking accumulates.**

1. **Own channel with OAuth: true same-age values from day 0.** The YouTube Analytics API returns per-video daily views (`dimensions=day`, `filters=video==ID`). Day-1/3/7/30 cumulative views are exact for the whole history (`owner_analytics`). This is authorized data. Its storage rules differ (III.E.4.b: it may be stored longer, with re-verification of authorization every 30 days).
2. **Shared cache: other clients' history.** Snapshots are keyed by video, not by client. A competitor already tracked for another client may already have true day-7 values on the day a new client onboards. No extra quota is needed.
3. **Videos published after tracking starts get true values.** 02 snapshots every tracked channel's new uploads on an adaptive cadence (§5.3.7). Every video published at or after T0 gets `observed`/`interpolated` day-1/3/7 values, and day-30 values once 30 days old.
4. **The backlog at onboarding uses an age-band proxy, not an estimate (§5.3.4).** Video v is seen first at age `a_v > d`. We do not invent its day-7 value. We compare its views at its observed age `a_v` with the same channel's same-segment videos observed at a similar age. Where a tracked peer's snapshots bracket `a_v`, the peer value is interpolated at exactly `a_v`, which makes that pair a true same-age comparison at a different age. The output says "views at 23 days: 4,100 (vs channel median 1,300 at 15–35 days)". It does not claim a day-7 figure.
5. **Never mix methods in one ratio.** Numerator and baseline always use the same method and the same age definition. A pattern-mining run uses one comparison method per scope (§5.3.5), so true and proxy ratios are never pooled into one lift.
6. **Proxy honesty is measured, not asserted (§5.3.6).** Videos with full tracked histories let us simulate "first seen at age a" and check how often the proxy agrees with the true day-7 outlier flag (Cohen's κ, Spearman ρ, bias). The agreement statistics appear in the evaluation report. If agreement is poor, the band width tightens and 07 downgrades proxy-based confidence.
7. **The retention policy decides how long true values survive (§5.3.8).** Under the strict 30-day mode, a true day-7 value lives about 37 days after publish. Older videos in a 90-day window revert to the proxy. This is stated in the UI, not hidden.

**Timeline for a newly onboarded client (no OAuth, no prior cache):**

| Day after T0 | 7-day window (compare day 3) | 30-day window (compare day 7) | 90-day window (compare day 7) |
|---|---|---|---|
| 0 | all proxy | all proxy | all proxy |
| 3 | videos published ≥ T0 and aged ≥ 3d are true; the rest proxy → `mixed_by_video` for display, proxy for mining | proxy | proxy |
| 7 | ≈ 100% true once T0 ≥ 7 days back covers the window → **true** | ≈ 0–20% true → proxy | proxy |
| 14 | true | ≈ 25–45% true → proxy | proxy |
| 30–37 | true | ≥ 80% coverage → **true** | ≈ 25–35% → proxy |
| > 37 (30-day retention mode) | true | true | the true share stays ≈ 30% (only videos ≤ 37d old) → proxy for mining; true values shown per video |
| > 97 (36-month mode, §5.3.8) | true | true | **true** |

#### 5.3.4 Age-band proxy (precise definition)
For video v of channel c, segment s, observed at age `a_v` (days) with views `V_v`:
1. **Band width κ(a_v)** grows with age because cumulative curves flatten:
   - `a < 3d: κ = 1.25`
   - `3–30d: κ = 1.5`
   - `30–180d: κ = 2.0`
   - `> 180d: κ = 3.0`
2. **Peer value for each candidate u** (same c, same s, u ≠ v, not a duplicate/upcoming/suppressed video):
   - if u's cleaned snapshots bracket `a_v` within `max_gap`: `P_u = interp(u, a_v)` → exact same age;
   - else if `age_now(u) ∈ [a_v/κ, a_v·κ]`: `P_u = V_u(now)`;
   - else u is not a peer.
3. If more than `proxy_max_peers = 30` peers qualify, keep the 30 nearest by `|ln a_u − ln a_v|`. Ties go to the more recent upload.
4. **Sufficiency.** Need `n ≥ min_baseline_n = 5`. If short, widen κ one step (×1.33) at most twice and flag `widened_lookback`. Otherwise the baseline is `insufficient`.
5. `proxy_ratio = V_v / max(median(P_u), min_baseline_views)`. The output carries `observed_age_days = a_v`, the peer age range, and `n`.

A growth-curve "calibrated proxy" (estimated day-7 = current views × f(age)) was **rejected for any contract field**. It is a modelled estimate, not "API data + simple maths". It sits behind `research.calibrated_proxy_enabled = false` for offline evaluation only.

#### 5.3.5 Switching rule (per scope, per run)
- Let `cov_true` = share of eligible window videos in the scope whose comparison-age value is `observed`, `interpolated` or `owner_analytics`.
- Let `ch_ok` = every channel contributing ≥ 1 outlier candidate has a sufficient **true-method** baseline.
- `same_age_mode[scope] = "true"` if `cov_true ≥ 0.80 and ch_ok`.
- Else `"proxy"`: every video in the scope is assessed by proxy, including those with true values, so lifts are internally consistent.
- Per-video display (`OutlierRecord`, reference videos) always uses the best available method, labelled. `"mixed_by_video"` is a display-only mode.

#### 5.3.6 Proxy validation (fed to 12)
- Take videos with tracked histories that have true day-7 values and are ≥ 30 days old.
- For simulated first-seen ages `a ∈ {10, 20, 30}` days, compute the proxy outlier flag at age `a` and compare it with the true day-7 flag.
- Report Cohen's κ on flags, Spearman ρ on ratios, and median `log10(proxy_ratio/true_ratio)` (bias), per segment.
- Targets: κ ≥ 0.6 and |bias| ≤ 0.1 (±26%).
- If missed: shrink κ bands and set `proxy_confidence_penalty = 1 level` (consumed by 07).
- Also publish the **proxy bias bound** `B(a, κ) = median_u V_u(κ·a)/V_u(a)` per segment, so the UI can say "proxy comparisons may be off by up to ~X%".

#### 5.3.7 Snapshot cadence 06 needs from 02 (requirement, not a decision)
| Age | Cadence | Why |
|---|---|---|
| 0–48 h | every 6 h | day-1 tol 3 h / max gap 12 h |
| 2–10 d | every 24 h | day-3 and day-7 brackets ≤ 36 h / 72 h |
| 10–35 d | every 72 h | day-30 bracket ≤ 10 d |
| 35 d – end of lookback | weekly | proxy peers, current views refresh (also satisfies the 30-day refresh) |

Snapshot cost is `videos.list` at 1 unit per 50 ids.

#### 5.3.8 Retention interplay (policy-driven; `policy.retention_mode`)
- `strict_30d` (default until approved):
  - Statistics from non-authorized data must not be stored more than 30 days (III.E.4.b).
  - Snapshots, `ViewsAtAge` and any stored derived statistic carry `source_oldest_at`. The retention cron (01) deletes them at 30 days.
  - A true day-7 value therefore expires at about publish + 37 days.
  - Current views are refreshed daily for in-scope videos, so proxy values are always fresh.
  - Owner-analytics values follow authorized-data rules (re-verify authorization every 30 days).
- `derived_metrics_36m`:
  - Only if Google accepts our "Analytics & Reporting" use case under Developer Policies III.L / the derived-metrics policy.
  - Statistics (views, likes, comment counts) and derived metrics may be stored for up to 36 months.
  - Titles, descriptions and comment text stay on the 30-day rule.
  - The switch is a config flag with no code change.

### 5.4 Rates and velocity
- `views_per_day_lifetime = V_now / max(age_days, 1)`. The `max` avoids inflating very new videos.
- **Velocity** between cleaned snapshots i<j: `vel = (V'_j − V'_i) / ((t_j − t_i)/86400)`, used only when `t_j − t_i ≥ 6 h`.
  - `velocity_recent` uses the latest pair spanning 20–30 h (the "last day"). If none exists, use the shortest pair ≥ 6 h ending at the latest snapshot, with `window_hours` recorded.
- **Velocity ratio** (trending):
  - `vel_recent(v) / median{vel_recent(u) : u same channel+segment, age_u within κ-band of age_v, velocity available}`.
  - Needs n ≥ 5, else none.
  - At T0 there are no velocities (one snapshot), so trending falls back to the ratio of `views_per_day_lifetime` against age-band peers, labelled proxy.
- **Engagement rates** require `V_at_measure ≥ min_views_for_rates = 100`, else None with flag `low_views_for_rates`:
  - `like_rate = L / V`
  - `comment_rate = C / V`
  - `engagement_rate = (L + C) / V` when both are visible. If likes are hidden, use `C/V` with `engagement_basis = "comments_only"`. If comments are disabled, use `L/V` with `"likes_only"`. With neither, the value is None.
  - Display per 1,000 views.
  - Comparisons only within the same `engagement_basis` and segment. Shorts views count every replay since 2025-03-31 (YouTube change; §7), which deflates Shorts rates.
- Rates are measured at the comparison age when snapshot likes/comments exist there, otherwise at `now`. `rate_age_days` is recorded.

### 5.5 Format segmentation and duplicates

#### 5.5.1 Segment rules (in order)
1. `live_broadcast_content ∈ {upcoming, live}` → **excluded** until it ends. Upcoming items have duration 0 and views 0 (Apollo evidence).
2. `has_live_details` (with `actualEndTime`) → **live**. This covers live replays and premieres; the API exposes no premiere flag, see §13.
3. Own channel with OAuth `creator_content_type == SHORTS` → **short_form**, with `segment_evidence.owner_analytics = true`, displayed as "Short (confirmed)".
4. `duration_s ≤ shorts_max_s` → **short_form**, where `shorts_max_s` is 180 s for `published_at ≥ 2024-10-15` and 60 s before (YouTube Help "Understand three-minute YouTube Shorts"). It is displayed as "short-form (≤ 3 min)" unless `pillarbox_detected` (§5.8.6) or `#shorts` in the title/description adds evidence. Apollo's 10–90 s horizontal brand spots land here, honestly labelled.
5. `duration_s == 0 or None` → excluded, flag `unavailable_now` (processing).
6. Otherwise → **long_form**.

Shorts views counted under the pre-2025-03-31 definition are never used in a Shorts baseline. The Shorts lookback is clamped to `published_at ≥ 2025-03-31`; relevant only for 365-day lookbacks crossing that date.

#### 5.5.2 Length bands (segment-aware)
- short_form: `≤60s`, `61–180s`.
- long_form: `3–8m`, `8–15m`, `15–30m`, `30–60m`, `>60m`.
- live: `<30m`, `30–90m`, `>90m`.

#### 5.5.3 Duplicate collapsing
- Same channel, normalised titles (casefold, strip punctuation/emoji/whitespace) with `rapidfuzz.ratio ≥ 95`, `|Δduration| ≤ 2 s`, and `|Δpublished| ≤ 30 days` → one duplicate group. The Apollo "360° Stroke Care" pair qualifies.
- The member with the highest comparison-age views is the **primary**. Others get `duplicate_of` and are excluded from baselines, outlier lists and pattern mining.
- Cross-channel re-uploads (same rule across channels, duration ±2 s, ratio ≥ 95) only get the flag `cross_channel_duplicate`. They stay in the data, but neither copy can be the sole reference for a pattern.

### 5.6 Channel baselines

**Definition.** For video v (channel c, segment s, comparison age d, method m):

```
L(v) = up to N=30 most recent videos u of channel c with:
       segment(u) = s, u ≠ v, published_at(u) < published_at(v)        # trailing, leakage-free
       published_at(u) ≥ published_at(v) − 365 days
       u not duplicate-secondary, not excluded
       X_u(d, m) available                                             # same age, same method
B(v) = median{X_u}      spread = 1.4826 · median|log10 X_u − log10 B|  (MAD_log)
```

For the proxy method, the age band (§5.3.4) replaces "same age d".

**Median vs mean.** The median is robust: its breakdown point is 50%, versus 0% for the mean (Rousseeuw & Croux 1993; Leys et al. 2013 recommend median/MAD over mean/SD for outlier detection). Apollo's last 48 videos: median 1,320 vs mean 51,415. The assignment says "own channel average", so the mean and 10% trimmed mean are shown beside the median, labelled "average (skewed by N videos above 10x)". Decisions use the median only. Spread is computed in log space because view counts are heavy-tailed and roughly multiplicative.

**Lookback size.** `N = 30` balances recency against noise. The median's standard error is about `1.2533·σ/√n`, so n=30 halves the standard error of n≈8. The 365-day cap keeps a channel's distant past from defining "normal".

**Separate baselines.**
- **Segment**: short_form / long_form / live are never cross-compared. Shorts views count replays; live replays include live-chat viewing.
- **Method**: true vs proxy.
- **Age**: day 1/3/7/30 baselines are separate.
- **Language split (optional)**: if a channel has ≥ 2 `default_audio_language` groups each with ≥ `min_baseline_n` videos and medians differing ≥ 2x, the baseline is split by language (`baseline_split_by_language = true`); otherwise the baseline is pooled and flagged.

**Minimum sample and fallback ladder.** `min_baseline_n = 5`. The ladder:
1. **trailing** (above).
2. **non_trailing**: allow videos published after v (leave-one-out), still within ±365 days. Flag `non_trailing_baseline`. Used for brand-new tracked channels and for the first videos of a channel.
3. **widened**: N = 50, lookback 730 days. Flag `widened_lookback`.
4. **Method fallback**: true → proxy, only if the proxy baseline is sufficient.
5. **none**: `tier = insufficient`; the UI says "Not enough videos to define this channel's normal".

There is **no cross-segment fallback**: a Shorts baseline never stands in for long-form.

**Floor.** `B_eff = max(B, min_baseline_views = 10)` avoids divide-by-zero and absurd ratios on near-dead channels.

**Also computed per channel/segment** (display + scorecards): like_rate median, engagement_rate median (per basis), velocity median by age band, uploads/week.

### 5.7 Outliers

#### 5.7.1 Assessment
```
ratio   = X_v / B_eff
z_log   = (log10 X_v − log10 B) / max(MAD_log, mad_floor = 0.05)
```

| Tier | Rule |
|---|---|
| `strong_outlier` | ratio ≥ 5.0 and z_log ≥ 2.0 and X_v ≥ min_outlier_views |
| `outlier` | ratio ≥ 3.0 and z_log ≥ 2.0 and X_v ≥ min_outlier_views (100 at comparison age) |
| `breakout` | ratio ≥ 2.0 (shown; never counted as an outlier; usable only as labelled thin-data context) |
| `normal` | 0.67 ≤ ratio < 2.0 |
| `below` | ratio < 0.67 |
| `insufficient` | no sufficient baseline or value pending/unavailable |

`is_outlier = tier ∈ {outlier, strong_outlier} and not suppressed`.

#### 5.7.2 Why ≥ 3x plus a z gate
- **Ratio gate.** "Far above their own channel's normal" needs a plain-language threshold. 3x is the smallest round multiple a marketer reads as "far above", and it matches the assignment's example outlier (3.6x). 2x happens too often in volatile channels.
- **Volatility gate.** A fixed ratio treats a steady channel (MAD_log 0.1, so 3x is about 4.8 robust σ) and a volatile one (MAD_log 0.5, so 3x is about 1 σ, routine) the same. The z_log ≥ 2 gate (about the top 2.3% under log-normality) raises the bar automatically for volatile channels. It uses the modified-z idea of Iglewicz & Hoaglin (1993) on log views.
- **Absolute floor.** 100 views keeps "12 → 40 views" from being an outlier. It is low enough not to exclude small but real channels.
- **Final values come from validation (§5.7.5).** Thresholds are grid-searched; the defaults above are the starting point.

#### 5.7.3 Keeping big channels from drowning small ones
1. Ratios are scale-free: each video is judged only against its own channel's median.
2. Pattern mining also computes a **channel-balanced lift**: each video is weighted `1/n_c`, where `n_c` is its channel's video count in scope, so every channel has equal total weight. A trait is `supported` only if both the raw and balanced lifts clear the gate (§5.10).
3. **Dominance cap**: if one channel contributes > 50% of a trait's outliers, the status is `dominated`.
4. Confidence counts distinct channels (07 requires 3+ for High).
5. Trending and outlier lists rank by ratio, not raw views, with at most `max_per_channel_in_lists = 2` items per channel in top-N lists.
6. Raw views and subscriber counts appear only as context (`size_vs_client`), never as a score input.

#### 5.7.4 Suppression rules (registry; each records `suppressed_reason`)

| Rule | Condition | Rationale |
|---|---|---|
| `views_without_engagement` | tier ≥ outlier and like_rate visible and `like_rate_v ≤ channel_median_like_rate / 5` | Apollo: 0.06–0.89 likes/1k on 188k–899k views vs 3.6/1k median. These spikes look externally driven (e.g. ads), and copying them would mislead. The UI says "views far above normal but engagement far below the channel's norm", and never asserts ads. The 61,913-view video (57 likes/1k) survives |
| `duplicate` | duplicate secondary | count once |
| `pending_or_excluded` | upcoming/live-now/too new | not judgeable |
| `cross_channel_duplicate_only_evidence` | applied in mining, not here | no single re-upload drives a pattern |

Suppressed videos stay visible in the outlier list under "Unusual spikes (not used as evidence)".

#### 5.7.5 Validation plan (precision/recall on 100 hand-labelled videos; run by 12)
1. **Sampling (stratified, seeded, reproducible).** From the 3 demo niches (5 competitors each plus the client), draw 100 eligible long-form videos plus a separate 30-video Shorts check:
   - 35 from ratio ≥ 3,
   - 35 from 1.5 ≤ ratio < 3,
   - 30 from ratio < 1.5.

   Strata are over-sampled near the threshold, where errors happen.
2. **Labelling.** Two labellers (Mira + one marketer) independently answer "Is this video far above this channel's normal?" They see:
   - the channel's last 30 same-segment videos with views at the same age, sorted by date,
   - the target's same-age views.

   They do **not** see our ratio or tier. Disagreements are adjudicated. Report Cohen's κ (target ≥ 0.6).
3. **Metrics.**
   - Precision and recall of `is_outlier` vs the adjudicated label, with **inverse-probability stratum weights** so the estimates reflect the population rather than the over-sampled strata.
   - 95% Wilson intervals on both.
   - Report separately for true-method and proxy-method videos.
4. **Grid.** ratio ∈ {2, 2.5, 3, 4, 5} × z ∈ {0, 1.5, 2, 2.5} × floor ∈ {50, 100, 250}.
   - Pick the setting maximising **F0.5**: precision matters more because false outliers become false evidence.
   - Subject to recall ≥ 0.6.
   - Ties go to the simpler rule (z = 0 beats z > 0 if equal).
5. **Suppression check.** Labellers also mark "spike looks externally driven". Report the precision of `views_without_engagement`.
6. **Freeze.** Record the chosen thresholds in settings with the evaluation date. Re-run quarterly or after a lexicon/threshold change.

### 5.8 Traits per video

Intrinsic traits are computed once per (video, traits_version, title/description hash) and shared. Client-context traits are computed per client run. Every trait is a surface feature of API fields (title, description, duration, thumbnail pixels) or a match to the client's own topics. None infers a content category or audience.

#### 5.8.1 Topic (from 05)
- `topics = [TopicTrait(topic_ref_id, kind, score, review_flag, directness)]` for matches with `score ≥ topic_min_score = 0.5`.
- Multi-label: a video can match several topics, and each counts in mining. Non-independence is noted in docs.
- Matches with `review_flag = true` are mined into a separate status (`exploratory`) until reviewed.
- `directness` (conversion lens) for buyer-question matches:
  - `coverage = |core_terms(BQ) ∩ tokens(title ∪ chapter_titles)| / |core_terms(BQ)|`, using 05's normalised tokens.
  - **direct**: coverage ≥ 0.8 and (`title.is_question` or `title.is_how_to` or a chapter title covers ≥ 0.8) and score ≥ 0.75.
  - **partial**: coverage ≥ 0.5 or score ≥ 0.6.
  - **tangential**: otherwise.

#### 5.8.2 Format and length
`segment` and `length_band` (§5.5).

#### 5.8.3 Title traits
Text normalisation: NFKC, collapse whitespace. Matching is case-insensitive except for caps traits. Lexicons are versioned YAML.

| Trait | Rule (default) |
|---|---|
| `char_len_band` | `≤30`, `31–50`, `51–70`, `>70` chars (YouTube truncates long titles in many surfaces; bands are descriptive) |
| `word_count` | whitespace tokens |
| `is_question` | contains `?` or `？`, or the first token is in the interrogatives list: en `what, why, how, when, where, which, who, is, are, can, does, do, should, will, could`; hi-Latn `kya, kaise, kyun, kyon, kab, kahan, kaha, kaun, kitna, kitne, kitni`; hi-Deva `क्या, कैसे, क्यों, कब, कहाँ, कहां, कौन, कितना, कितने, कितनी` (also matched anywhere for Hindi, which is often verb-final) |
| `has_number` | any digit run not part of a recognised year |
| `has_listicle_number` | `^\s*\d{1,3}\s+\w` or `\b(top|best)\s+\d{1,3}\b` |
| `is_how_to` | `\bhow to\b`, `\bhow do (i|you)\b`, `\bstep[- ]by[- ]step\b`, `\btutorial\b`, `\bguide\b`; `\bkaise\s+(kare|karein|karen|banaye)\b`; `कैसे करें`, `कैसे बनाएं` |
| `has_year` | `(?<!\d)20[0-3]\d(?!\d)` (2000–2039 only; "1066", Apollo's emergency number, is not a year) |
| `year_is_current_or_next` | year ∈ {year(as_of), year(as_of)+1} |
| `has_brackets` / `has_parentheses` | `[`…`]` / `(`…`)` present |
| `is_first_person` | `\b(i|i'm|i've|my|me|we|our|us)\b`; `\b(main|mera|meri|hum|hamara)\b`; `मैं|मेरा|मेरी|हम|हमारा` |
| `has_superlative` | lexicon: `best, worst, top, most, least, ultimate, greatest, biggest, fastest, cheapest, easiest, #1, number one, sabse` (`सबसे`) |
| `caps_ratio` | uppercase letters / cased letters; None if there are no cased letters (Devanagari-only); bands `<0.3`, `0.3–0.6`, `>0.6` |
| `all_caps_word_count` | words of ≥ 3 cased letters entirely uppercase, excluding an acronym allowlist (`ICU, MRI, CT, ECG, IVF, AI, GPT, EMI, OPD, …`, niche-extendable) |
| `has_emoji` / `emoji_count` | `\p{Extended_Pictographic}` (needs the `regex` library; §7) |
| `separator` | `:` → colon; ` | ` → pipe; ` - ` / ` – ` → dash; more than one kind → multiple (Apollo uses pipes heavily) |
| `has_price_cost` | `\b(cost|price|prices|pricing|fee|fees|charges|rate|rates|budget|cheap|affordable|emi|kharcha|kitne ka|paisa)\b`, `₹`, `\brs\.?\s?\d`, `\binr\b`; `कीमत|खर्च|फीस|दाम|पैसे` |
| `has_buyer_question_phrasing` | `\b(should i|is it safe|does it work|is it worth|worth it|vs\.?|versus|side effects?|recovery time|how long|which is better|near me|cost of|before and after|myths?)\b` + hi equivalents (`kya safe hai`, `kitna time`, `side effect`) |
| `mentions_doctor` | `\bdr\.?\s+[A-Z][a-z]+` (case-sensitive name start) or `\bdoctor\b` + capitalised name; `डॉ\.?\s` |
| `script` | share of letters in U+0900–U+097F: ≥ 0.8 devanagari, 0.2–0.8 mixed, < 0.2 latin; other scripts → other |
| `has_client_location` (client) | word-boundary match against `BusinessProfile.locations` + aliases (no NER, no gazetteer inference) |
| `has_niche_location` (client) | same lexicon applied to competitor titles; "near me" counts |

#### 5.8.4 Timing (client-context)
- `local = published_at.astimezone(ZoneInfo(profile.timezone))`. If tz is missing: derive from `profile.country` when it has a single zone (IN → Asia/Kolkata), else UTC with the label "UTC (client timezone unknown)".
- Traits: weekday, hour, `daypart = night 0–5, morning 6–11, afternoon 12–16, evening 17–21, late 22–23`, `is_weekend` (Sat/Sun; configurable for markets with other weekends).
- Competitor times are converted to the **client's** timezone as a common frame. The UI labels this; it makes no claim about the competitor audience's location.
- DST is handled by zoneinfo (the `fold` attribute disambiguates repeated hours; non-existent local times cannot arise when converting from UTC).

#### 5.8.5 Description traits (conversion hygiene)

| Trait | Rule |
|---|---|
| `links_count`, `link_domains` | URL regex `https?://\S+` and bare `www\.\S+`; registrable domain via a small public-suffix-aware normaliser; youtube.com/youtu.be counted separately |
| `has_external_link` | any non-YouTube, non-social link |
| `has_shortener_link` | domain ∈ {bit.ly, tinyurl.com, t.co, goo.gl, rb.gy, cutt.ly, …}; **not resolved**, no network call |
| `has_own_site_link` | domain ∈ the uploading channel's known domains (`CompetitorCandidate.website_domains`; client: `profile.website_domains`) |
| `has_client_site_link` (client) | domain ∈ `profile.website_domains` |
| `has_booking_cta` | `\b(book|booking|appointment|consult(ation)?|schedule|register|enquire|enquiry|call now|visit us|get a quote|test drive)\b` + hi (`अपॉइंटमेंट`, `संपर्क करें`, `appointment book karein`), niche-extendable from `ConversionAction` |
| `has_phone` | `(\+91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}`, `\b1800[\s-]?\d{3}[\s-]?\d{4}\b`, E.164 `\+\d{8,15}`, and niche short codes from the profile (e.g. 1066). **Only the boolean is stored**, never the number |
| `has_whatsapp` | `wa\.me/`, `api\.whatsapp\.com`, `\bwhatsapp\b` |
| `has_chapters`, `chapter_count`, `chapter_titles` | lines matching `^\s*(\d{1,2}:)?\d{1,2}:\d{2}\s+.+`. Valid only if the first timestamp is 0:00/00:00, there are ≥ 3 timestamps, they ascend, and each gap ≥ 10 s (YouTube chapter rules) |
| `hashtag_count`, `hashtags_ignored` | `(?<!\w)#[\p{L}\p{N}_]+`; ignored = count > 60 (YouTube ignores all hashtags beyond 60) |
| `has_paid_disclosure_text` | `#ad\b`, `#sponsored`, `paid partnership`, `sponsored by`, `in partnership with`, `#collab` (collab is a mention, not paid) |
| `mentioned_handles` | `@[\w.\-]{3,30}`, resolved against cached `ChannelRecord.handle` (no API call) |
| `collab_markers` | `\b(ft\.?|feat\.?|featuring|with|x)\s+@?\w+`, `in conversation with`, `collab` |
| `first_two_lines_have_client_keyword` (client) | first 2 non-empty lines contain a client keyword/topic term (feeds 09 refresh) |

#### 5.8.6 Thumbnail traits (no face detection, no person/object recognition)

**Fetch (enrich stage; `ImageSource` port).**
- URL is taken from the API's `snippet.thumbnails`, trying `maxres` (1280×720) → `standard` (640×480) → `high` (480×360) → `medium` (320×180).
- Key by `(video_id, url)` plus `content_sha256`. Recompute only when the hash changes (thumbnails can be swapped or A/B tested).
- Images are processed in memory and **not persisted**; only traits are stored.

**Preprocessing.**
- `standard` and `high` are 4:3 frames with letterbox bars for 16:9 videos.
- Crop rows from the top/bottom while the row mean luma < 0.06 and the row std < 0.02 (max 25% per side). Then do the same for columns.
- `pillarbox_detected` = after the letterbox crop, both side bands are uniform (std < 0.03) and each is ≥ 18% of the width. This is evidence of a vertical source (Shorts).
- The pillarbox signal is experimental: validate on 50 known Shorts and 50 known long-form videos before using it in segmentation (`pillarbox_in_segmentation = false` by default).

**Pixel statistics** (on the cropped image downsampled to ≤ 320 px wide; Pillow `L` luma = 0.299R + 0.587G + 0.114B, values in [0,1]).
- **brightness** = mean(L). Bands: `dark < 0.35 ≤ mid ≤ 0.65 < bright`.
- **rms_contrast** = std(L) (Peli 1990, RMS contrast). Bands: `low < 0.18 ≤ mid ≤ 0.28 < high`. These are starting values; task T-14 recalibrates them to niche tertiles and freezes them per traits_version.
- **colorfulness** (Hasler & Süsstrunk 2003), on 0–255 RGB:
  - `rg = R − G`, `yb = 0.5(R + G) − B`
  - `σ = sqrt(σ_rg² + σ_yb²)`, `μ = sqrt(μ_rg² + μ_yb²)`
  - `M = σ + 0.3·μ`
  - Bands, collapsing the paper's perceptual scale (thresholds as reported in the paper; verify): `low < 33 ≤ medium < 59 ≤ high`.
- **dominant_colour_count**: convert to HSV.
  - Chromatic pixels (S ≥ 0.2 and V ≥ 0.2) go into 12 hue bins of 30°.
  - Achromatic pixels go into black (V < 0.2), white (V > 0.85, S < 0.2) or grey.
  - Count = bins holding ≥ 5% of pixels.

**Text (OCR) via `ThumbnailTextReader`** on the cropped image upscaled so its height is ≥ 720 px.
1. Detect + recognise. Keep boxes with `rec_confidence ≥ 0.6` and `box_height ≥ 0.04 × image_height`. This drops tiny watermarks and timestamps.
2. **Recurring-text suppression (logos/watermarks).**
   - Normalise box text (casefold, alphanumerics) and note its 3×3 grid cell.
   - Text recurring in ≥ 40% of the channel's last 30 thumbnails in the same cell is a watermark and is excluded. Stored in `channel_recurring_text`, refreshed weekly.
   - Separately, text matching the channel title/brand tokens is `brand_text`. If it is the only text, `brand_text_only = true`.
3. `text_present` = any detected box survives the geometry filters, **even if recognition failed**. Detection is script-agnostic (DB detector).
4. `word_count` = tokens with ≥ 2 alphanumerics (Latin) or ≥ 1 Devanagari letter cluster in the kept, non-brand boxes. If boxes exist but mean confidence < 0.5 after the script pass, `word_count = None` and `script_detected = "unknown"`.
5. Hindi.
   - The Devanagari recognition model runs as a second pass when any of these holds: the channel has ≥ 10% Devanagari titles; the profile languages include `hi`; or first-pass boxes have confidence < 0.5.
   - Both passes are kept per box, and the higher-confidence reading wins.
   - Romanised Hindi needs nothing special (Latin model).
6. Bands: `word_count_band = 0, 1–3, 4–6, 7+`; `text_area_ratio = Σ box area / image area`; `largest_text_height_ratio`.

**OCR engine evaluation (rapidocr vs tesseract).**

| Criterion | RapidOCR (`rapidocr` 3.x + onnxruntime) | Tesseract 5 (`pytesseract` + system binary) |
|---|---|---|
| Model type | PaddleOCR DB detector + recogniser in ONNX, built for scene text | LSTM line recogniser, designed for documents; docs focus on scanned-page preprocessing (binarisation, 300 DPI, borders) |
| Stylised text on images | detector finds text regions on busy backgrounds | needs binarisation and `--psm 11` (sparse text); struggles with outlined/gradient text |
| Devanagari | PP-OCRv4/v5 `devanagari` mobile rec models listed in the RapidOCR model list (verify accuracy) | `hin` traineddata available in tessdata |
| Packaging | pip only; models auto-download (must be pre-baked into the Docker image) | apt/brew binary + traineddata; heavier image |
| Licence | Apache-2.0 (code); models Apache-2.0 (PaddleOCR) | Apache-2.0 |
| Python | `rapidocr` 3.10.0 (2026-10-08) supports ≥3.8 incl. 3.12/3.13; legacy `rapidocr-onnxruntime` 1.4.4 (2025-01-17) requires <3.13 | any |

- **Decision**: RapidOCR (`rapidocr` 3.x) is the default adapter. Tesseract is an optional adapter behind the same port for A/B evaluation.
- **Evaluation protocol** (task T-13):
  - 200 thumbnails: 100 English, 50 Hindi/Hinglish, 50 no-text or logo-only, drawn from the demo niches.
  - Hand-label `text_present` and word count.
  - Metrics: text-present F1, word-count MAE, false positives on logo-only images, CPU p50/p95 latency.
  - Choose by F1, then MAE, then latency.
  - Expect RapidOCR to win on scene text; this is not verified, which is why we run the test.

### 5.9 Comment intent signals (deterministic conversion proxy)

**Input.** `CommentRecord`s fetched by 02: top-level `commentThreads` with `order=relevance`, ≤ 100 per video, newest window videos first. They are processed in memory. **06 never persists comment text.**

**Filtering (SpamFilter).** Drop comments that:
- are authored by the video's channel (owner);
- have < 3 letters after stripping emoji;
- contain a URL;
- match the same normalised text in ≥ 3 different videos of the scope (copy-paste spam);
- match the giveaway/crypto spam lexicon.

The rest is `eligible_n`.

**Question detection.** `?`/`？` or an interrogative starter (title lexicon, §5.8.3) or a Hindi interrogative anywhere.

**Intent lexicon** (registry `(locale, niche)`; base + niche overrides; healthcare shown):

| IntentClass | en | hi-Latn | hi-Deva |
|---|---|---|---|
| cost | cost, price, fees, charges, how much, rate, package, emi, insurance cover, ₹, rs | kitna, kitne, kitni, kharcha, kharch, paisa, paise, fees kya | कितना, कितने, खर्च, कीमत, फीस, पैसे |
| location | where, location, address, branch, near me, which city, in <client location> | kahan, kaha, kidhar, address bhejo | कहाँ, कहां, पता, शाखा |
| booking | appointment, book, booking, consult, slot, opd timing, contact number, phone number, how to contact, whatsapp number | appointment kaise, milna hai, contact karna, number do | अपॉइंटमेंट, संपर्क, मिलना है, नंबर |
| doctor | which doctor, doctor name, `dr <Name>`, specialist name | doctor ka naam, kaun se doctor | डॉक्टर का नाम, कौन से डॉक्टर |
| efficacy | does it work, is it effective, success rate, results, is it safe, side effects, risk, permanent | kya ye kaam karta, asar, safe hai kya, side effect | असर, सुरक्षित, साइड इफेक्ट |
| how_to | how to, how do i, steps, what should i do | kaise kare, kya kare | कैसे करें, क्या करें |
| availability | available in, cashless, insurance accepted, sunday open, emergency | milega kya, available hai | उपलब्ध |
| comparison | vs, better than, which is better, difference between | konsa better, farak | कौन सा बेहतर, अंतर |

- Niche packs (auto dealers: on-road price, test drive, EMI, mileage; home services: visit charge, same day, warranty) are additional registry entries.
- Buyer questions extend the lexicon automatically: core terms of each `BuyerQuestion` are matched with `rapidfuzz.partial_token_set_ratio ≥ 85` → `matched_buyer_questions`.

**Signals.**
- `question_share = question_n / eligible_n`.
- `buyer_intent_share = buyer_intent_n / eligible_n`.
- Unanswered question: `total_reply_count == 0`, or no visible reply authored by the video's channel. Replies in `commentThreads` are a partial list (API docs), so the label says "no visible owner reply".
- `sufficient = eligible_n ≥ min_comments_for_signals (10)`. Below that, counts are shown but shares are None, with the message "Too few comments to measure".
- `sample_coverage = sampled_n / comment_count_api`. Shares are always "of sampled comments".

**PII and sensitive data.**
- Comment text and author ids are never stored by 06 and never appear in analytics outputs. Outputs hold only counts and buyer-question ids.
- Optional excerpts (`comment_excerpts_enabled`, default **false** for healthcare/finance niches, true otherwise):
  - max 3 per video, ≤ 140 chars;
  - phone/email/URL/`@handle` redacted to `[redacted]`;
  - no author names;
  - kept only inside the run report (30-day retention).
- **No health-condition classification.** No intent class describes a person's condition or other protected attributes. The derived-metrics policy forbids inferring protected attributes such as health status in sentiment-style analysis. Commenters are aggregated, never profiled.

### 5.10 Pattern mining (`TraitLift`)

**Population.** In scope S and window W: videos with `published_at ∈ [as_of − W, as_of]`, not excluded, not duplicate-secondary, `tier ≠ insufficient`, assessed under `same_age_mode[S]` at the comparison age. Outcome: `is_outlier`.

**Scopes (never pooled).**
- `competitor_set`: the client's confirmed competitors.
- `niche`: competitors + discovered niche channels (04) + channels of cached niche search results with sufficient baselines.
- `own`: the client channel; outliers vs own baseline; usually small and mostly descriptive.

**Trait values mined.**
- Every categorical/boolean trait value: topic ids, segment, length band, title booleans and bands, separator, timing weekday/daypart, thumbnail bands/text bands, description booleans.
- Numeric traits only through their bands.
- Thumbnail traits only over videos with `thumbnail != None`. A missing thumbnail is not "no text".

**Counts for trait value t.**

|  | outlier | not outlier |
|---|---|---|
| has t | a | b |
| lacks t | c | d |

- `p1 = a/(a+b)`, `p0 = c/(c+d)`, `p̄ = (a+c)/N`.
- **Lift** (relative risk; "videos with t are k× as likely to be outliers") = `p1/p0`. The share-ratio form "over-represented among outliers" = `(a/(a+c)) / (b/(b+d))`; it is also stored for 07's wording.
- **Smoothed lift** (Bayesian shrinkage toward the scope rate; Beta prior with strength α = 5 pseudo-videos centred on p̄): `p1~ = (a + α·p̄)/(a + b + α)`, `p0~ = (c + α·p̄)/(c + d + α)`, `lift_smoothed = p1~/p0~`. Used for **ranking**. With small a+b it falls toward 1.
- **Wilson 95% interval** on p1 (Wilson 1927; recommended over Wald by Brown, Cai & DasGupta 2001):
  `(p̂ + z²/2n ± z·sqrt(p̂(1−p̂)/n + z²/4n²)) / (1 + z²/n)`.
- **Lift CI** (Katz et al. 1978, log method): `exp(ln(p1/p0) ± z·sqrt(1/a − 1/(a+b) + 1/c − 1/(c+d)))`. Add 0.5 to every cell if any cell is 0 (Haldane–Anscombe).
- **Over-representation test**: one-sided Fisher exact, `p = Σ_{x=a}^{min(K,n)} C(K,x)·C(N−K, n−x) / C(N,n)` with `K = a+c`, `n = a+b`. Exact integer arithmetic with `math.comb`; no scipy.
- **Multiple comparisons**: Benjamini–Hochberg FDR within (run, scope), counting main effects and interactions together. `q_(i) = min_{j≥i} p_(j)·m/j`. With ~60 trait values × 3 scopes, uncorrected p < 0.05 would yield several false patterns per run.
- **Channel-balanced lift**: the same rates with weights `w_u = 1/n_c(u)`.

**Gates → `status`** (evaluated in order):
1. `no_contrast`: `n_with < 1` or `n_without < 1`.
2. `insufficient_support`: `a < min_outliers_with (3)` or `outlier_channels_with < 2` or `n_with < 8` or `n_without < 8`.
3. `dominated`: `max_channel_share > 0.5`.
4. `supported`: `lift_ci.low > 1.0` and `q ≤ 0.10` and `channel_balanced_lift ≥ 1.2` and `review_flag` false (topic traits).
5. `exploratory`: everything else with lift_smoothed > 1. 07 may show it only with the "early signal" label and never at High confidence.

**Redundancy.** If two supported values have Jaccard(video sets with trait) ≥ 0.8 (e.g. `has_year` and `year_is_current_or_next`), keep the higher `lift_smoothed` and mark the other `redundant`.

**Interactions (2-way, pre-registered only).**
- Pairs: `topic × segment`, `topic × length_band`, `topic × title.is_question`, `segment × title.is_how_to`. This caps the test count.
- Computed only if both main effects have `n_with ≥ 8`, the cell has `a ≥ 3` across ≥ 2 channels, and `N ≥ 40`.
- Reported as `supported` only if it passes all gates and the cell's Wilson lower bound on p1 exceeds both main effects' p1. In other words, the interaction must add over its parts, not restate them.
- No 3-way interactions.

**Outputs for 07.**
- `ratio_quantiles_with` = p25/p50/p75 of outlier ratios among videos with t. Ratios are winsorised at `ratio_winsor_cap = 50` so one ad spike cannot set the range.
- `reference_video_ids` = outliers with t, sorted by ratio desc and then the tie-break key.
- 07 combines these with the client's own segment/topic median ratio into `expected_range_vs_baseline`.

**Language rule.** Lifts are associations, never causes. 06 exposes `claim_strength ∈ {association}` only.

### 5.11 Reports

**OwnPerformanceReport** (client channel, window W):
- Per video: ratio, tier and method vs own trailing baseline, segment, topics, length band.
- Window median ratio vs previous window (same length) median ratio.
- Best/worst: top/bottom 3 by ratio, excluding insufficient; ties broken by the key.
- `by_segment`, `by_topic`, `by_length_band`: n, median ratio, IQR. Shown only when n ≥ 3, otherwise "1–2 videos, not enough".
- Consistency:
  - uploads/week;
  - `gap_cv = std(gaps)/mean(gaps)` over inter-upload gaps in the last 90 days (needs ≥ 4 uploads);
  - `hit_rate_≥1x` = share of videos ≥ own baseline (+ Wilson CI);
  - IQR of log10 ratios (lower = steadier).
- Baselines used: each with n, method and fallback level.
- `conversion` block (§5.12), shown separately.

**ChannelScorecard** (per competitor, window W):
- **Cadence**:
  - `uploads_per_week = uploads_in_window / (W/7)`;
  - `gap_cv`, `longest_gap_days`, `days_since_last_upload`;
  - computed over a 90-day lookback even when W = 7 (labelled), because 7 days of gaps is meaningless.
- **Performance**:
  - median views at the comparison age per segment (value, method, n), labelled "views at day 7 (true)" or "views at ~N days (proxy)";
  - median ratio vs own baseline;
  - **outlier hit rate** = outliers / eligible, with Wilson CI;
  - median engagement per basis.
- **Topic coverage → gap matrix**:
  - for every client TopicRef/BuyerQuestion: the competitor's `GapCell`;
  - the full matrix (rows = topics, columns = client + competitors) lives in `AnalyticsResult.gap_matrix`;
  - **gap** = ≥ 2 competitors have ≥ 1 matching video in the last 365 days and the client has 0 in the last 365 days;
  - **opportunity** = a gap where ≥ 1 competitor video is an outlier;
  - client coverage uses 365 days, not the window, to honour "don't repeat what the client already published".
- **Format mix**: share of uploads per segment and length band.
- **Title/thumbnail patterns working for them**: channel-scope mining (§5.10 maths, scope = this channel). Its gates are relaxed to `a ≥ 2` and the result is always labelled `exploratory`. Single-channel lifts never qualify as `supported`.
- **Paid promotion disclosure share**: `count(has_paid_product_placement) / count(field present)`, plus a separate count of `has_paid_disclosure_text`. Labelled "declared by the creator", never inferred.
- **Collaborations**: `CollabMention(video_id, handle_or_name, kind)`. `kind = disclosed_paid` if `has_paid_product_placement` or paid disclosure text is present, else `mention` (handles, ft./feat./with). Feeds 07's creator ranker.
- **Size context** (display only): subscriber count (or "hidden"), `size_vs_client = competitor median same-age views / client median`. Never a ranking input.

**TrendingReport** (niche scope, window W):
- Candidates: niche-scope window videos with a topic match ≥ `topic_min_score`, not suppressed, comparison value ≥ `trending_min_views (500)`.
- Sort by `(ratio desc, velocity_ratio desc, published_at desc, video_id)`, max 2 per channel, top 20.
- `rising_topics`: per topic, uploads and outliers in this window vs the previous window of the same length; flagged rising if outliers_now ≥ 2 and ≥ 2× outliers_prev.
- `chart_items`: `videos.list?chart=mostPopular&regionCode={profile.country}` (category per niche config; default all), fetched daily by 02 and shared across clients. Shown only if a video matches a client topic. Charts are API-provided; we only filter by the client's own topics.

**Outliers list**: `OutlierRecord`s per scope, sorted by ratio. Suppressed spikes are listed separately.

### 5.12 Conversion lens in analytics
The conversion lens is always a separate block. Its numbers are never added to, multiplied with, or weighted into view metrics by 06. 07 may use them as separate ranking inputs and must display them separately.

1. **Answer directness** per buyer question: list of (video, direct/partial/tangential) across client and competitors (§5.8.1). This is the assignment's definition of conversion potential.
2. **Comment intent**: `CommentSignals` per video, plus per-topic totals (sum of intent counts across matching videos, with the number of videos with sufficient comments).
3. **Description CTA coverage**: per channel, share of window videos with booking CTA, own-site link, phone/WhatsApp, chapters. For the client this feeds 09.
4. **Own channel (OAuth only)**: `OwnConversionMetrics`.
   - Subscribers gained per video, plus per 1k views where both numbers come from the Analytics API.
   - Traffic source shares (`insightTrafficSourceType`: YT_SEARCH, SUGGESTED, BROWSE, SHORTS, EXT_URL, …) and top search terms (`insightTrafficSourceDetail` where source = YT_SEARCH).
   - Labelled "YouTube Analytics (your channel only)". Data API counts are never mixed into these ratios.
5. **Website data** (future): rendered side by side with the label "not from YouTube" (III.E.4.h disclosure). Never merged.

### 5.13 Defaults (`AnalyticsSettings`)

| Group | Key | Default |
|---|---|---|
| ages | `target_ages_days` | [1, 3, 7, 30] |
| ages | `comparison_age_by_window` | {7: 3, 30: 7, 90: 7} |
| ages | `tol_hours` | {1: 3, 3: 6, 7: 12, 30: 48} |
| ages | `max_gap_hours` | {1: 12, 3: 36, 7: 72, 30: 240} |
| ages | `max_bracket_spread` | 0.25 |
| proxy | `kappa_by_age` | [(3, 1.25), (30, 1.5), (180, 2.0), (inf, 3.0)] |
| proxy | `proxy_max_peers`, `proxy_max_widenings` | 30, 2 |
| proxy | `true_mode_min_coverage` | 0.80 |
| segments | `shorts_max_s_after_2024_10_15`, `shorts_max_s_before` | 180, 60 |
| segments | `pillarbox_in_segmentation` | false |
| segments | `dup_title_ratio`, `dup_duration_tol_s`, `dup_window_days` | 95, 2, 30 |
| baselines | `lookback_videos`, `lookback_days` | 30, 365 |
| baselines | `widened_lookback_videos`, `widened_lookback_days` | 50, 730 |
| baselines | `min_baseline_n`, `min_baseline_views`, `mad_floor` | 5, 10, 0.05 |
| baselines | `baseline_split_by_language` | true |
| outliers | `outlier_ratio`, `strong_ratio`, `breakout_ratio`, `below_ratio` | 3.0, 5.0, 2.0, 0.67 |
| outliers | `outlier_min_z`, `min_outlier_views` | 2.0, 100 |
| outliers | `engagement_spike_divisor` | 5 |
| rates | `min_views_for_rates`, `min_velocity_interval_h` | 100, 6 |
| traits | `topic_min_score` | 0.5 |
| thumbnail | `ocr_min_conf`, `ocr_min_box_height_ratio`, `recurring_text_share` | 0.6, 0.04, 0.40 |
| thumbnail | band edges (brightness, contrast, colourfulness) | §5.8.6 |
| thumbnail | `dominant_colour_min_share` | 0.05 |
| comments | `max_comments_per_video`, `min_comments_for_signals` | 100, 10 |
| comments | `comment_excerpts_enabled` | false for healthcare/finance; true otherwise |
| patterns | `min_outliers_with`, `min_outlier_channels`, `min_n_with`, `min_n_without` | 3, 2, 8, 8 |
| patterns | `max_channel_share`, `fdr_q`, `prior_strength_alpha` | 0.5, 0.10, 5 |
| patterns | `min_balanced_lift`, `redundancy_jaccard`, `ratio_winsor_cap` | 1.2, 0.8, 50 |
| patterns | `interaction_pairs`, `interaction_min_N` | §5.10, 40 |
| lists | `max_per_channel_in_lists`, `trending_min_views`, `trending_top_n` | 2, 500, 20 |
| policy | `retention_mode` | strict_30d |
| research | `calibrated_proxy_enabled` | false |

## 6. Data model

All tables are created by 01's migrations from this spec. Column types are PostgreSQL. Tables marked **shared** are keyed by public ids and reused across clients. Every row derived from API statistics carries `source_oldest_at`, which the retention cron uses.

| Table | Scope | Columns | Keys / indexes |
|---|---|---|---|
| `video_age_values` | shared | `video_id text, age_days smallint, views bigint, likes bigint null, comments bigint null, method text, observed_age_days real, bracket_lo_at timestamptz, bracket_hi_at timestamptz, bracket_spread real, source text, computed_at timestamptz, source_oldest_at timestamptz` | PK (video_id, age_days, source); idx (source_oldest_at) |
| `video_metrics` | shared | `video_id text PK, channel_id text, as_of timestamptz, segment text, segment_evidence jsonb, age_anchor_at timestamptz, views_now bigint, likes_now bigint null, comments_now bigint null, views_per_day_lifetime real, velocity_recent jsonb, like_rate real, comment_rate real, engagement_rate real, engagement_basis text, duplicate_group_id text, flags text[], metrics_version text, source_oldest_at timestamptz` | idx (channel_id, segment); idx (source_oldest_at) |
| `channel_baselines` | shared | `baseline_id text PK, channel_id, segment, metric, age_days smallint null, method, age_band numrange null, anchor_video_id text null, as_of, median real, mean real, trimmed_mean_10 real, p25 real, p75 real, mad_log10 real, n int, video_ids text[], fallback_level text, sufficient bool, source_oldest_at` | idx (channel_id, segment, metric, age_days, method, as_of desc) |
| `video_outliers` | shared, per as_of date | `video_id, as_of_date date, age_days, method, value bigint, baseline_id, ratio real, log_robust_z real, tier text, is_outlier bool, suppressed_reason text null, source_oldest_at` | PK (video_id, as_of_date, age_days, method); idx (tier, as_of_date) |
| `video_traits` | shared | `video_id PK, traits jsonb (intrinsic only), traits_versions jsonb, title_hash text, description_hash text, computed_at, source_oldest_at` | GIN (traits jsonb_path_ops) |
| `thumbnail_traits` | shared | all `ThumbnailTraits` fields as columns; `content_sha256` | PK (video_id); idx (content_sha256) to reuse across re-uploads |
| `channel_recurring_text` | shared | `channel_id, normalized_text, grid_cell smallint, share real, as_of` | PK (channel_id, normalized_text, grid_cell) |
| `comment_signals` | shared | `video_id PK, as_of, comment_count_api, sampled_n, eligible_n, question_n, intent_counts jsonb, buyer_intent_n, unanswered_question_n, owner_reply_visible_n, lexicon_version, sufficient, source_oldest_at` (no text; `matched_buyer_questions` is client-scoped and lives in reports) | — |
| `trait_lifts` | client run | `run_id, client_id, scope, window_days, comparison_age_days, comparison_method, trait_key, trait_value, interaction text null, a,b,c,d int, rates real…, lift real, lift_smoothed real, lift_ci_low real, lift_ci_high real, balanced_lift real, p_value double, q_value double, outlier_channels_with int, max_channel_share real, ratio_q jsonb, reference_video_ids text[], status text, redundant_with text null, source_oldest_at` | PK (run_id, scope, trait_key, trait_value, interaction); idx (client_id, status) |
| `analytics_reports` | client run | `run_id, client_id, kind (own_performance, scorecard, trending, outliers, gap_matrix, conversion), channel_id null, window_days, as_of, payload jsonb, schema_version, source_oldest_at` | idx (client_id, kind, as_of desc) |

**Retention.**
- `strict_30d`: delete rows whose `source_oldest_at < now − 30 d` unless recomputed from refreshed inputs.
  - Shared current-state rows (`video_metrics`, `video_traits`, `thumbnail_traits`, `comment_signals`) are recomputed daily for in-scope videos, which keeps them fresh.
  - Historic `video_age_values` and `video_outliers` age out.
  - Run reports older than 30 days are deleted. A future history feature stores only non-API facts, or needs `derived_metrics_36m`.
- `derived_metrics_36m`: statistics and derived rows are kept for 36 months. Text-bearing rows (titles in reports, chapter titles, excerpts) still follow the 30-day refresh.
- Comment text is never stored by 06. Thumbnail images are never stored.

## 7. External APIs & libraries

06 makes **no YouTube API calls**. It reads data 02 fetched. Facts it depends on (verified 2026-11-20 unless marked):

| Fact | Source |
|---|---|
| `statistics.viewCount/likeCount/commentCount`; `dislikeCount` owner-only; duration ISO 8601, may differ by ~1 s | https://developers.google.com/youtube/v3/docs/videos |
| `snippet.publishedAt` = time made public for private→public videos; `liveBroadcastContent ∈ {live, none, upcoming}`; `liveStreamingDetails.actualStartTime/actualEndTime/scheduledStartTime/concurrentViewers` | same |
| Thumbnail keys/sizes: default 120×90, medium 320×180, high 480×360, standard 640×480, maxres 1280×720 (standard+ "available for some videos") | same |
| `paidProductPlacementDetails.hasPaidProductPlacement` boolean (live-checked: present on Apollo videos) | same + live call |
| Hidden like count → `likeCount` absent (observed behaviour; docs silent; handle absent ≠ 0) | live/unverified in docs |
| `videos.list` 1 unit; `chart=mostPopular` with `regionCode`, `videoCategoryId` (default 0 = all); `videoChartNotFound` 400 | https://developers.google.com/youtube/v3/docs/videos/list |
| `commentThreads` replies are a limited subset; compare with `snippet.totalReplyCount` | https://developers.google.com/youtube/v3/docs/commentThreads |
| `commentsDisabled` 403 on `commentThreads.list` (02 maps it to `comments_disabled`) | https://developers.google.com/youtube/v3/docs/commentThreads/list#errors (not re-fetched) |
| Analytics dimensions: `creatorContentType` (SHORTS, VIDEO_ON_DEMAND, LIVE_STREAM, STORY, UNSPECIFIED), `insightTrafficSourceType` (YT_SEARCH, SHORTS, …), `insightTrafficSourceDetail` = search term for YT_SEARCH, `liveOrOnDemand` | https://developers.google.com/youtube/analytics/dimensions |
| Analytics day boundaries in Pacific Time | **unverified**; 02 to confirm |
| Shorts up to 3 min for square/vertical uploads on/after 2024-10-15 | https://support.google.com/youtube/answer/15424877 |
| Shorts views count each start/replay from 2025-03-31; old metric renamed "engaged views" (Analytics only) | https://techcrunch.com/2025/03/26/youtube-is-changing-how-youtube-shorts-views-are-counted ; https://www.tubefilter.com/2025/03/26/youtube-shorts-views-counting-stats/ |
| Chapters: first timestamp 00:00, ≥ 3 ascending timestamps, each ≥ 10 s | https://support.google.com/youtube/answer/9884579 |
| > 60 hashtags → all ignored | https://support.google.com/youtube/answer/6390658 |
| Developer Policies III.E.4.b (non-authorized statistics ≤ 30 days), III.E.4.h (no derived metrics/no replacing API data; disclose non-YouTube data), III.E.2 (aggregation across content owners), III.L (derived metrics for approved analytics use cases) | https://developers.google.com/youtube/terms/developer-policies |
| Derived-metrics policy: averages/sums/ratios allowed; must be distinguished from API metrics; no inferring protected attributes (e.g. health status); statistics + derived metrics storable 36 months if accepted; text still 30 days | https://developers.google.com/youtube/terms/derived-metrics-policy |

**Libraries**

| Library | Use | Notes |
|---|---|---|
| numpy (present) | vectorised medians, MAD, pixel stats | — |
| Pillow (present) | decode, crop, `L` luma, HSV, resize | — |
| `rapidocr` 3.x + `onnxruntime` | thumbnail OCR (det + rec; `devanagari` rec model) | https://github.com/RapidAI/RapidOCR, https://pypi.org/project/rapidocr/ (3.10.0, 2026-10-08), model list https://rapidai.github.io/RapidOCRDocs/main/model_list/. **pyproject currently pins legacy `rapidocr-onnxruntime>=1.4`** (1.4.4, 2025-01-17, Python <3.13; https://pypi.org/project/rapidocr-onnxruntime/), see §13. Pulls OpenCV: use `opencv-python-headless` in Docker. Pre-download models at image build |
| Tesseract 5 + `pytesseract` (optional adapter) | OCR A/B | https://github.com/tesseract-ocr/tesseract ; preprocessing guidance https://tesseract-ocr.github.io/tessdoc/ImproveQuality.html (300 DPI, binarisation, `--psm 11` sparse text) |
| `regex` (proposed) | `\p{Extended_Pictographic}`, `\p{Devanagari}`, `\p{L}` | stdlib `re` lacks Unicode properties |
| rapidfuzz (present) | duplicate titles, buyer-question matching in comments | — |
| zoneinfo (stdlib) + `tzdata` (proposed for slim images) | client timezone | — |
| isodate (present) | duration parsing | — |

No scipy: Wilson, Katz and BH are closed-form; Fisher uses `math.comb`.

**Statistics and imaging references**
- Wilson, E. B. (1927). Probable inference, the law of succession, and statistical inference. *JASA* 22(158):209–212.
- Brown, L. D., Cai, T. T., DasGupta, A. (2001). Interval estimation for a binomial proportion. *Statistical Science* 16(2):101–133.
- Agresti, A., Coull, B. A. (1998). Approximate is better than "exact" for interval estimation of binomial proportions. *The American Statistician* 52(2):119–126.
- Katz, D., Baptista, J., Azen, S. P., Pike, M. C. (1978). Obtaining confidence intervals for the risk ratio in cohort studies. *Biometrics* 34(3):469–474.
- Benjamini, Y., Hochberg, Y. (1995). Controlling the false discovery rate. *JRSS B* 57(1):289–300.
- Fisher, R. A. (1922/1935) exact test; hypergeometric formulation in any standard text.
- Iglewicz, B., Hoaglin, D. C. (1993). *How to Detect and Handle Outliers*. ASQC (modified z-score).
- Rousseeuw, P. J., Croux, C. (1993). Alternatives to the median absolute deviation. *JASA* 88(424):1273–1283.
- Leys, C. et al. (2013). Detecting outliers: Do not use standard deviation around the mean, use absolute deviation around the median. *J. Exp. Soc. Psychol.* 49(4):764–766.
- Efron, B., Morris, C. (1975). Data analysis using Stein's estimator and its generalizations. *JASA* 70(350):311–319 (shrinkage rationale).
- Hasler, D., Süsstrunk, S. (2003). Measuring colourfulness in natural images. *Proc. SPIE 5007*, Human Vision and Electronic Imaging VIII (formula verified via https://pyimagesearch.com/2017/06/05/computing-image-colorfulness-with-opencv-and-python/ ; band thresholds from the paper's table, to verify against the PDF).
- Peli, E. (1990). Contrast in complex images. *JOSA A* 7(10):2032–2040 (RMS contrast).
- Cohen, J. (1960). A coefficient of agreement for nominal scales. *Educ. Psychol. Meas.* 20:37–46.

## 8. Edge cases & failure modes

| # | Case | Detection | Handling | User-facing message |
|---|---|---|---|---|
| 1 | Channel has < 5 eligible videos in a segment | baseline n < `min_baseline_n` after the fallback ladder | tier `insufficient`; excluded from mining; scorecard shows raw counts only | "Only 3 long-form videos. Not enough to define this channel's normal." |
| 2 | Client channel brand-new / no uploads | 0 eligible videos | no own baseline; `expected_range_vs_baseline` unavailable (07 says niche-relative only) | "We can't compare you with yourself yet. Results are shown relative to the niche." |
| 3 | All of a channel's videos are short-form | no long_form segment | Shorts-only baseline; no cross-segment comparisons; scorecard format mix 100% short | "This channel publishes only short-form videos. Compared with its own Shorts." |
| 4 | Hidden like count | `like_count` None while views present | like/engagement rates None or `comments_only`; `views_without_engagement` cannot fire; excluded from like-based traits | "Likes hidden by creator." |
| 5 | Like count genuinely 0 | `like_count == 0` | valid zero; rate = 0 if views ≥ 100 | — |
| 6 | Comments disabled / made for kids | `comment_count` None, 403 `commentsDisabled`, or `made_for_kids` | comment signals `sufficient=false`, basis `likes_only` | "Comments are turned off on this video." |
| 7 | Very few comments (Apollo norm: 0–2) | `eligible_n < 10` | counts shown, shares None | "Too few comments to measure buyer questions." |
| 8 | Viral video skewing the mean | max/median > 10 in lookback | median baseline (robust); mean shown labelled; ratio winsorised at 50 in quantiles | "Average skewed by 1 video above 10x normal. We use the median." |
| 9 | Views spike without engagement (ad-like) | §5.7.4 rule | not counted as an outlier; listed under "unusual spikes" | "Views far above normal but likes far below this channel's norm. Not used as evidence." |
| 10 | Brand-new video (< 24 h, or < comparison age) | `age_days < d − tol` | `pending`; excluded from outliers/mining; may appear in trending with velocity only, labelled | "Too new to judge (published 9 hours ago)." |
| 11 | Upcoming live/premiere | `liveBroadcastContent=upcoming` | excluded entirely | — (not shown) |
| 12 | Currently live | `liveBroadcastContent=live` | excluded until ended; re-assessed next refresh | "Live now. Assessed after the stream ends." |
| 13 | Live replay with inflated counts (live viewers + replay) | `has_live_details` with end time | live segment only; anchor = actualStartTime | "Compared only with this channel's other live streams." |
| 14 | Premiere (indistinguishable from live in API) | live details present | treated as live (conservative); §13 | same as #13 |
| 15 | Live channel with < 5 lives | n < 5 | live videos `insufficient` | "Not enough live streams to compare." |
| 16 | Re-upload / duplicate within channel | §5.5.3 | primary kept, others flagged and excluded | "Duplicate of another upload. Counted once." |
| 17 | Cross-channel re-upload | title+duration match across channels | flagged; never sole evidence | "Also uploaded by another channel." |
| 18 | View count decreases (spam filtering) | suffix-minimum correction changes values | corrected values used; flag | tooltip "YouTube later removed some views; we use the audited count." |
| 19 | View count frozen | unchanged ≥ 48 h at young age | value kept; velocity from that interval excluded | tooltip "View count was not updating for 2 days." |
| 20 | Video deleted/private between snapshots | absent from `videos.list` (02 sets `is_available=false`) | excluded from current reports and evidence; stored stats deleted per policy; baselines recomputed | "This video is no longer public." (only in own report history) |
| 21 | Private → public (publishedAt changes) | `published_at` differs from the stored value | re-anchor, drop old age values | — |
| 22 | Title changed after publish | `title_hash` change | traits recomputed; outcome may reflect the old title | "Title changed after publishing; performance may reflect the earlier title." |
| 23 | Thumbnail changed / A/B test | `content_sha256` change | recompute; same caveat | "Thumbnail changed after publishing." |
| 24 | Window with zero uploads (competitor) | `uploads_in_window = 0` | cadence 0; last-upload date; excluded from window lifts | "No uploads in the last 30 days (last: 12 Sep)." |
| 25 | Window with zero outliers in scope | `Σ is_outlier = 0` | no lifts; sufficiency message for 07 | "Not enough outliers in the last 7 days. Widen to 30?" |
| 26 | Window with < 3 outliers for every trait | all `insufficient_support` | same; 07 must not emit pattern cards | same |
| 27 | Competitor 10x client size | `size_vs_client ≥ 10` | ratios only; size shown as context; client expected range from the client's own baseline | "Much larger channel. Compared against its own normal, not yours." |
| 28 | One channel dominates outliers | `max_channel_share > 0.5` | status `dominated` | "Mostly driven by one channel." |
| 29 | Timezone missing | profile tz null | country single-zone fallback, else UTC labelled | "Times shown in UTC. Set your timezone for local publish times." |
| 30 | DST transitions | zoneinfo | UTC storage; conversion correct by construction | — |
| 31 | Ties in rankings | equal sort keys | deterministic tie-break (published_at asc, video_id asc) | — |
| 32 | Divide by zero: baseline median 0 / views 0 | B = 0 or V = 0 | `B_eff = max(B, 10)`; rates None if V < 100 | tooltip "Very low view counts; ratios are approximate." |
| 33 | Lift with c = 0 or a = 0 | zero cell | Haldane 0.5 for CI; RR undefined → `lift=None`, smoothed lift used | — |
| 34 | Trait present on all/none videos | `n_without = 0` | `no_contrast` | — |
| 35 | Missing thumbnail / all sizes 404 | fetch fails | `thumbnail=None`; excluded from thumbnail lifts (not "no text") | "Thumbnail unavailable." |
| 36 | `maxres` absent | key missing | fall back to standard/high; letterbox crop | — |
| 37 | OCR false positive on logo/watermark | recurring text in ≥ 40% of channel thumbnails in the same cell, or tiny box | excluded; `brand_text_only` | — |
| 38 | Hindi/Devanagari text in thumbnail | channel/profile hints, low first-pass confidence | Devanagari second pass; if still unreadable `word_count=None`, `text_present=true` | "Text detected (not readable by our OCR)." |
| 39 | Stylised/curved/outlined text | low rec confidence, det boxes present | `text_present=true`, word count None | same |
| 40 | Thumbnail is a near-uniform frame (auto-generated, black) | brightness < 0.05 or contrast < 0.02 | traits stored; flagged `degenerate_frame`, excluded from thumbnail lifts | — |
| 41 | Extreme outliers (ratio ≥ 50) | ratio ≥ cap | kept as outliers if not suppressed; winsorised in quantiles; 07 cannot build a range from a single such video | "Exceptional (>50x). Excluded from typical-range estimates." |
| 42 | Duration ≤ 180 s but horizontal (ads/promos) | no Shorts evidence | `short_form` label "≤ 3 min", not "Short" | "Short-form (under 3 min)" |
| 43 | Shorts across the 2025-03-31 view-count change | published before the cutoff | excluded from Shorts baselines | — |
| 44 | Duration 0 / missing (processing) | `duration_s` 0/None, not upcoming | excluded until available | — |
| 45 | Very long videos (> 3 h replays) | duration | live/long bands `> 90m` / `> 60m` | — |
| 46 | Snapshot gap (cron missed) | bracket gap > max_gap | method falls to proxy/unavailable | label "proxy" on the value |
| 47 | Snapshot before publish (premiere) | captured_at < anchor | dropped | — |
| 48 | Mixed-language channel | `default_audio_language` groups | split baseline if both are sufficient and differ ≥ 2x | "Compared with this channel's other Hindi videos." |
| 49 | Hinglish titles | romanised lexicon | traits use hi-Latn lists | — |
| 50 | Emoji-only / non-cased script titles | no cased letters | `caps_ratio=None`; emoji traits work | — |
| 51 | Year false positives (1066, model numbers) | 2000–2039 + no adjacent digits | excluded | — |
| 52 | Location false positives (city names as common words) | profile lexicon only, word-boundary | aliases curated in profile; manual override | — |
| 53 | Price false positives ("rate" in "heart rate") | phrase exclusions list (`heart rate, success rate`) | excluded | — |
| 54 | Phone numbers in descriptions | regex | boolean only; number never stored | — |
| 55 | PII / health disclosures in comments | any comment | never stored; no condition classes; excerpts off for health niches; redaction when enabled | — |
| 56 | Comment spam / bot giveaways | spam filter | dropped from eligible | — |
| 57 | Partial replies list → "unanswered" uncertain | `len(replies) < totalReplyCount` | labelled "no visible owner reply" | "No visible reply from the channel." |
| 58 | Topic match flagged for review | `review_flag` | topic lifts `exploratory` only | "Topic match pending review." |
| 59 | Video matches multiple topics | multi-label | counted in each; noted | — |
| 60 | Correlated traits | Jaccard ≥ 0.8 | redundant marked | — |
| 61 | Many traits tested | m tests | BH FDR q ≤ 0.10 | "Early signal" label for exploratory |
| 62 | Owner analytics vs Data API mismatch | both exist | never in one ratio; source labelled | "Source: YouTube Analytics (your channel)" |
| 63 | OAuth revoked / expired | 02 reports auth failure | own conversion block hidden; owner-analytics age values deleted per policy | "Reconnect your channel to see subscribers gained and traffic sources." |
| 64 | `mostPopular` unavailable for region/category | `videoChartNotFound` | chart_items empty | "No trending chart available for this region." |
| 65 | Hidden subscriber count | `hidden_subscriber_count` | "hidden"; size ratio from views only | "Subscriber count hidden." |
| 66 | Competitor channel terminated/unavailable | channels.list empty | scorecard `unavailable`; excluded from mining | "Channel no longer available." |
| 67 | Infrequent channel (30 videos span > 1 yr) | lookback hits 365 d first | fewer samples; widened ladder | "Few uploads this year; baseline uses older videos." |
| 68 | Backtest at frozen T | `as_of = T` | all reads filtered ≤ T; trailing baselines; no future snapshots | — |
| 69 | Retention expiry of true day-7 values (strict mode) | `source_oldest_at` > 30 d | video reverts to proxy | "Older videos are compared at their current age (true day-7 data expires after 30 days)." |
| 70 | Same video in several clients' scopes | shared keys | shared metrics; client-context traits per client | — |
| 71 | Seasonal/awareness-day spikes (World Heart Day) | not detected | counted normally; shows up as a topic/timing pattern; no special-casing | — |
| 72 | Paid placement flag absent on old videos | field missing | excluded from the share denominator | "Disclosure data not available for some videos." |
| 73 | Velocity at T0 (single snapshot) | < 2 snapshots | lifetime views/day vs age-band peers (proxy) | label "proxy" |
| 74 | Statistics missing entirely on a video | no `statistics` | excluded | — |
| 75 | Engagement basis differs between video and baseline | basis mismatch | compare only like-for-like; else None | — |

## 9. Policy & guardrail check

| Feature | Guardrail | Status |
|---|---|---|
| Views at age, velocity, rates, medians, ratios, lifts | "API data + simple maths: sums, averages, ratios" | Compliant: medians/ratios/counts. Wilson/Katz/Fisher/BH are confidence statistics about our own counts, not new YouTube metrics; disclosed as "GrowthHacker-calculated" (derived-metrics policy requires distinguishing derived from API metrics). **Risk**: Developer Policies III.E.4.h forbids derived metrics (example: likes-based) unless accepted under III.L → §13 |
| Age-band proxy | No estimates; no replacing API data | Compliant: compares observed counts only; labelled; no extrapolation; calibrated estimate disabled |
| Owner analytics | Own channel only, shown separately | Compliant: separate block; never mixed with Data API counts |
| Baselines across competitors / niche lifts | Aggregation across content owners (III.E.2) | **Risk to flag**: the assignment requires cross-channel comparison. Mitigation: per-client, per-run outputs viewable only by that client; no product-wide YouTube "insights" dashboards; seek III.L analytics acceptance |
| Topic trait | No inferring content category; match to client's keywords/buyer questions | Compliant: consumes 05 matches against client TopicRefs only; tags disclosed as ours ("Matches your topic: X"), never presented as a YouTube category |
| Title/description traits | No category inference | Compliant: surface lexical features of public text, not a category |
| Format segment | — | Compliant: from duration/live fields; "short-form" vs "Short" labelled honestly |
| Thumbnail traits | Simple visual traits only, no face recognition | Compliant: text present/word count/contrast/brightness/colourfulness/colour count/bars. No face, person, object or logo-identity recognition. Recurring-text suppression is string frequency, not logo recognition |
| Comment intent | No demographics, no protected-attribute inference | Compliant: intent of the text (cost/location/booking), not who the commenter is; no health-condition classes; no author storage |
| Comment text storage | 30-day rule; PII | Compliant: 06 stores no text; optional redacted excerpts ≤ 30 days |
| Retention | Refresh/delete within 30 days | Compliant by `source_oldest_at` + cron; `derived_metrics_36m` only after acceptance |
| Website data | Never merged | Compliant: side-by-side slot only, labelled "not from YouTube" |
| Revenue/monetisation | Never estimated | Compliant: none computed; paid-placement share is creator-declared |
| Audience demographics / creator audiences | Never inferred | Compliant: creator stats are channel performance only; timing in client tz is labelled as a reference frame |
| LLM | Not in analytics | Compliant: zero LLM calls |
| Determinism | Numbers from code | Compliant: pure functions, explicit as_of, versioned settings hash |

## 10. Performance, cost & scaling

**Quota.** 06 itself uses 0 units. Indirect needs placed on 02:
- snapshots for in-scope uploads (`videos.list`, 1 unit/50 ids; ≈ 10–30 units/client/day for ~11 channels, shared across clients);
- comments for new window videos (`commentThreads.list` 1 unit/video/fetch; cap at 1 fetch at age ≈ 3 d and 1 at ≈ 10 d → ≈ 20–60 units/client/day);
- `mostPopular` 1 unit per (region, category)/day, shared.

Thumbnails are fetched from i.ytimg URLs returned by the API, so no quota.

**LLM.** 0 tokens.

**Latency budget** (daily refresh, one client: ~1 client + 10 competitors + ~30 niche channels; ~40 channels × ~60 videos in lookback ≈ 2.5k videos, ~600 in a 90-day window):

| Step | Budget |
|---|---|
| Load metrics/snapshots (2 indexed queries, ~20k snapshot rows) | ≤ 1.5 s |
| Snapshot clean + ViewsAtAge (numpy per channel) | ≤ 1 s |
| Baselines (trailing medians, ~2.5k videos × 30) | ≤ 1 s |
| Outliers + suppression | ≤ 0.2 s |
| Client-context traits (regex over ~600 titles/descriptions) | ≤ 0.5 s |
| Comment signals (cached; new videos only, ~50 × 100 comments) | ≤ 1 s |
| Pattern mining (3 scopes × ~80 values + ~60 interactions; exact Fisher) | ≤ 1 s |
| Reports + persist | ≤ 2 s |
| **Total analytics stage** | **≤ 10 s** (of the < 2 min client budget) |

**Thumbnail OCR** runs in the enrich stage, outside the 2-minute path, and is incremental and shared.
- Onboarding a new niche: ~600 window thumbnails plus last-30 per channel for watermark stats (~1.2k images).
- At an assumed 150–300 ms CPU per image (RapidOCR mobile models; **to be measured** in T-13), that is ~3–6 min of CPU, parallelised across 4 arq worker processes to ~1–1.5 min.
- Daily refresh: ~20 new thumbnails per client → seconds.
- Pixel stats take ~5 ms/image.

**Scaling.** Shared tables mean the N-th client in an existing niche costs only client-context traits and mining. Mining is O(values × videos) and stays well below 1 s up to ~20k window videos. Beyond that, move contingency counting into SQL (`GROUP BY trait_value, is_outlier`).

## 11. Testing strategy

**Unit (pure functions; pytest, table-driven)**
- `AgeClock`: anchor selection (live vs VOD; private→public change).
- `SnapshotCleaner`:
  - suffix-minimum on `[100, 150, 140, 160]` → `[100, 140, 140, 160]` + flag;
  - freeze detection;
  - pre-anchor drop.
- `ViewsAtAgeResolver`:
  - observed within tol;
  - interpolation (snapshots at 6.5d/7.5d, 1000/1200 → 1100);
  - gap too wide → proxy;
  - spread > 0.25 → refuse;
  - pending;
  - owner analytics sum;
  - never extrapolates (property: result ≤ max observed V at age ≥ d when not pending).
- Proxy peers: κ bands, widening, interpolation of peers at exactly `a_v`, nearest-30 cap.
- Segment rules: 2024-10-15 boundary; upcoming exclusion; duplicate grouping with the Apollo pair fixture.
- Baselines:
  - trailing vs non-trailing ladder;
  - min n;
  - no cross-segment;
  - MAD floor;
  - Apollo fixture: median 1,320 vs mean 51,415, and the six spikes are `views_without_engagement` while the 61,913-view video is an outlier.
- Stats against reference values:
  - Wilson(3/10) = [0.108, 0.603];
  - Katz CI on a textbook 2×2;
  - Fisher one-sided vs hand-computed hypergeometric sums;
  - BH vs a worked example from Benjamini & Hochberg (1995);
  - shrinkage reduces to raw p when α = 0.
- Title traits: a lexicon golden file of ~150 titles (English, Hinglish, Devanagari, Apollo titles with pipes, "1066", "Heart Rate") with expected trait vectors.
- Description traits: chapter validity (missing 00:00, < 3 stamps, < 10 s gap), > 60 hashtags, shortener, phone regex (Indian mobile, toll-free, short code), WhatsApp.
- Pixel stats on synthetic images:
  - uniform grey: contrast 0, colourfulness 0, 1 dominant colour;
  - half black/white: RMS 0.5;
  - pure red: colourfulness known by formula;
  - letterboxed 4:3 → crop correct;
  - pillarboxed → detected.
- Comment signals: spam filter, owner exclusion, intent classes per language, redaction, `sufficient` threshold.

**Golden / integration**
- A frozen Apollo + 3 competitor fixture (sanitised JSON of videos, snapshots and comments, with comment text synthetic) → full `AnalyticsResult` compared with a golden JSON. Any diff requires a reviewed fixture update.
- **Determinism test**: run twice, and with shuffled input order → identical `AnalyticsResult` hash.
- **Leakage test**: `as_of = T` with snapshots after T present → identical to a run where they are absent.
- **Retention test**: rows past 30 d deleted in `strict_30d`; true values revert to proxy.

**Contract tests**
- `AnalyticsResult` pydantic schema snapshot (consumed by 07/08/09).
- `ReferenceVideo` fields derivable for every outlier (`views_at_age`, method, ratio).

**OCR evaluation (T-13)**: the 200-image labelled set is stored as hashes + labels (images re-fetched at test time, not committed). The CI subset is 20 images cached under a 30-day TTL.

**Live smoke (manual / nightly, ≤ 5 quota units)**: fetch 50 Apollo videos → build metrics → assert segment counts are plausible, upcoming excluded, at least 1 suppression on the known spikes, runtime < 10 s.

**Performance**: a synthetic 20k-video benchmark → the analytics stage stays under 10 s on a dev laptop.

## 12. Implementation task breakdown

Sizes: S ≤ 0.5 d, M ≈ 1–2 d, L ≈ 3–4 d.

| # | Task | Size | Depends on |
|---|---|---|---|
| T-1 | `AnalyticsSettings`, enums, domain models (§3.2) in `domain/` | M | 01 (settings/container) |
| T-2 | Table specs → 01 migration (§6), repositories for analytics tables | M | 01 |
| T-3 | `AgeClock`, `SnapshotCleaner` + tests | S | 02 snapshot repo |
| T-4 | `ViewsAtAgeResolver` (observed/interpolated/pending/owner analytics) + tests | M | T-3, 02 OwnAnalytics |
| T-5 | Age-band proxy + switching rule + proxy validation export | M | T-4 |
| T-6 | `SegmentClassifier`, `DuplicateCollapser`, length bands | S | T-1 |
| T-7 | `RateCalculator` (rates, velocity, velocity ratio) | S | T-3 |
| T-8 | `BaselineBuilder` (trailing, ladder, language split) | M | T-4, T-6 |
| T-9 | Outlier rule + suppression registry + validation sampler/scorer (for 12) | M | T-8 |
| T-10 | Title + timing + duration trait extractors, lexicons (en/hi-Latn/hi-Deva), golden file | M | T-1 |
| T-11 | Description traits (links, CTA, phone/WhatsApp, chapters, hashtags, collabs) | M | T-1 |
| T-12 | Thumbnail pixel pipeline (fetch port, crop, stats, bars) | M | 02 (thumbnail URLs) |
| T-13 | OCR port + RapidOCR adapter (+ optional Tesseract), recurring-text suppression, 200-image evaluation | L | T-12 |
| T-14 | Thumbnail band calibration (niche tertiles → frozen defaults) | S | T-13 |
| T-15 | Comment signals (spam filter, lexicon registry, redaction, buyer-question matching) | M | 02 comments, 05 BuyerQuestion terms |
| T-16 | Topic trait + answer directness | S | 05 TopicMatch |
| T-17 | `stats.py` (Wilson, Katz, Fisher, BH, shrinkage) + reference tests | S | — |
| T-18 | `PatternMiner` (scopes, gates, balanced lift, redundancy, interactions) | L | T-9, T-10–T-17 |
| T-19 | Reports: own performance, scorecard + gap matrix + collaborations, trending (+ chart filter), outliers, conversion lens | L | T-18, 02 OAuth analytics |
| T-20 | `AnalyticsStage` wiring, progress events, persistence, retention hooks | M | T-19, 11 Stage protocol |
| T-21 | Golden fixture (Apollo + competitors), determinism/leakage/retention tests, perf benchmark | M | T-20 |

Critical path: T-1 → T-3 → T-4 → T-8 → T-9 → T-18 → T-19 → T-20 (≈ 2 weeks of one engineer). T-12/13 run in parallel.

## 13. Open questions / proposed changes to shared context

1. **Output contract: `views_day7` cannot always be a true day-7 value.** Proposal: keep `views_day7` (null when not observed) and add `views_at_age: {age_days, value, method: observed|interpolated|owner_analytics|age_band_proxy}` and `outlier_ratio_method` to `ReferenceVideo`. Without this, the card would mislabel proxy values. For the 7-day window the comparison age is day 3 (`views_day3`); the contract should not hard-code day 7.
2. **Policy: derived metrics and storage (Developer Policies III.E.4.h, III.E.4.b, III.L).** By default, likes-based ratios and other derived metrics are not allowed and non-authorized statistics expire at 30 days. Google's derived-metrics policy (accepted "Analytics & Reporting" use case) allows averages/sums/ratios and 36-month statistic storage. Proposal: add to shared context (a) "apply for derived-metrics acceptance before production" and (b) `policy.retention_mode ∈ {strict_30d, derived_metrics_36m}`. Under strict mode, true day-7 values survive only ~37 days after publish; the 90-day window relies on the labelled proxy for older videos.
3. **Policy: III.E.2 aggregation across content owners.** Niche-wide medians and lifts combine data from many channels. The assignment requires this; flag it for owner/legal review and keep outputs per-client and per-run. Also add a shared rule: all derived numbers are labelled "GrowthHacker-calculated" in UI and API (the derived-metrics policy requires distinguishing them from API metrics).
4. **Baseline wording.** The shared context and contract say "own channel average". Proposal: state "own-channel **median** at the same age (mean shown for reference)". Apollo evidence: median 1,320 vs mean 51,415.
5. **Shorts identification.** The Data API has no Shorts flag. Proposal: the shared vocabulary uses "short-form (≤ 3 min)" unless confirmed by OAuth `creatorContentType` or validated thumbnail pillarbox evidence. Do **not** use the undocumented `UUSH…` playlist convention (an unofficial endpoint by convention). Premieres cannot be told apart from live streams via the API; both are treated as live. Confirm this is acceptable.
6. **Dependencies (pyproject).**
   - Replace `rapidocr-onnxruntime>=1.4` (legacy 1.4.4, Python < 3.13) with `rapidocr>=3.10` + `onnxruntime`. It includes the `devanagari` recognition models.
   - Add `regex`, `tzdata`, `opencv-python-headless`.
   - Pre-bake OCR models in the Docker image.
   - Tesseract stays optional.
7. **Snapshot cadence requirement for 02** (§5.3.7): every 6 h to 48 h, daily to 10 d, every 3 d to 35 d, then weekly. 02 should confirm quota and also confirm Analytics API day boundaries (Pacific time).
8. **BusinessProfile (03) must expose** `timezone` (IANA), `country`, `languages[]`, `website_domains[]`, `locations[]` with aliases, `conversion_actions[]` and optional niche lexicon overrides. `CompetitorCandidate` (04) must expose `website_domains[]`.
9. **Topic engine (05) must expose** `core_terms[]` per BuyerQuestion and `matched_terms[]` per TopicMatch so 06 can compute answer directness deterministically. Alternative: 05 owns directness. Decide one owner.
10. **Confidence (07).**
    - Downgrade one level when reference evidence relies on `age_band_proxy`, or when 06's proxy validation misses its targets.
    - `exploratory` lifts can never reach High.
    - Thin-data messages come from `sufficiency_messages`.
11. **Comment text handling.** Proposal for the shared context:
    - `CommentRecord.text` is transient (processed and discarded, or ≤ 30 days in 02's cache);
    - analytics never persists text;
    - excerpts are off by default for healthcare/finance niches (India DPDP Act 2023 treats health data sensitively; legal review recommended, not verified here).
12. **Wording of the `views_without_engagement` suppression** (e.g. "possibly promoted") needs owner sign-off. The UI must not assert that a competitor ran ads; it only states the two numbers.
13. **Component map row for 06** should also list `ConversionLens`, `OutlierRecord`, `GapCell`/gap matrix and `AnalyticsResult` among its outputs, and "proxy validation statistics" as an output to 12.
