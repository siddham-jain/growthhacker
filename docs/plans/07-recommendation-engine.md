# 07 — Recommendation engine

Owner component: recommenders, gap analysis, creator ranker, scoring, expected range, confidence, quality gates, output contract assembly.
Consumes: 03 (BusinessProfile), 05 (TopicRef, TopicMatch), 06 (VideoMetrics, ViewsAtAge, ChannelBaseline, VideoTraits, TraitLift, ChannelScorecard, OwnPerformanceReport, TrendingReport), 02 (VideoRecord, ChannelRecord, cached niche channels), 04 (confirmed competitors, CompetitorCandidate classifications).
Produces: RecommendationDraft → Recommendation (contract), RecommendationSet, GapReport, SuppressedRecommendation, InsufficiencyNotice, WhiteSpaceNote, the shared gate framework used by 08 and 09.

---

## 1. Purpose & business value

This is stage 4 of the assignment pipeline ("Patterns x gaps x fit to this client, scored on evidence, fit and buyer intent"). It answers question 5, *what should I make next*, as a short ranked list of cards. Every card:

- names a concrete thing to do (topic + format, a title or thumbnail pattern, a format, or a creator to work with);
- is backed by at least 3 recent reference videos that the user can click and check;
- says why it fits **this** business: it answers one of their tracked buyer questions, it uses a format their own channel already does well with, or it fills a gap that competitors cover and they don't;
- gives an honest confidence label and an indicative range relative to the client's own baseline.

Conversions come from the conversion lens. When the business's buyer questions ("How much does a knee replacement cost at Apollo?", "Which hospital is best for angioplasty in Chennai?") get answered on YouTube by videos built in the formats that are currently winning, those videos sit at the decision stage of the funnel. The engine pushes those questions up the list (intent-stage weighting) and marks gaps where competitors answer a buyer question and the client doesn't. The views lens keeps the channel growing so those answers reach people.

The "no AI slop" bar is enforced in code. A deterministic gate chain removes any card that breaks a rule before a user sees it, and logs why. That log is what makes the system trustworthy and lets us measure it.

## 2. Scope / non-scope

In scope
- Recommender registry and the five assignment recommenders: `video_idea`, `title_pattern`, `thumbnail_pattern`, `format`, `creator`.
- Gap analysis: a coverage matrix of TopicRefs × channels, gap typing and gap size.
- Creator ranker, including collaboration detection (mention vs sponsor-language vs disclosed paid promotion).
- Candidate merging/deduplication, scoring, MMR diversity rerank, per-window card counts.
- Expected-range estimator, confidence policy.
- **Shared quality gate framework** (registry, chain, policies, suppression log). 08 (`news_opportunity`) and 09 (`description_refresh`) emit `RecommendationDraft`s into it and register their own extra gates.
- The writer brief (evidence JSON handed to 10) and the deterministic fallback renderer.
- Contract assembly (assignment contract + backward-compatible `extras`), stable ids, staleness.
- Feedback capture and the deterministic effects of feedback. Outcome tracking for calibration.
- Thin-data notices with projected counts for wider windows. White-space notes.

Not in scope
- Computing metrics, outliers, baselines, traits, lift (06). This component never recomputes them; it only selects and combines them.
- Topic matching (05). Buyer question extraction (03). Competitor discovery and entity classification (04).
- Any YouTube API call. Creator uploads are fetched by 02 on our request (a list of channel ids); 07 only reads the cache.
- The LLM call, prompt, retry loop and model routing (10). 07 defines what goes in and what may come out.
- News detection (08) and refresh diff generation (09), except for the shared contract and gates.
- HTTP routes and SSE (11). Evaluation harness (12) — 07 exposes `as_of` and suppression logs for it.
- UI (13).

## 3. Inputs & outputs

### 3.1 Consumed (fields 07 relies on; owners in brackets)

```
BusinessProfile [03]: client_id, name, offerings[Offering], value_props[ValueProposition],
    locations[str], niche_keywords[TopicRef], buyer_questions[BuyerQuestion], brand_aliases[str], domains[str]
BuyerQuestion [03]: id, text, intent_stage: awareness|consideration|decision, source: crawler|user|spotlit, tracked: bool,
    ai_cited_video_ids: list[str] | None        # only if Spotlit AI tracking connected
TopicRef [05]: id, kind: keyword|buyer_question, text, offering_id|None, intent_stage|None, active: bool
TopicMatch [05]: video_id, topic_id, score: 0..1, method: lexical|embedding|llm, accepted: bool, review_flag: bool
VideoRecord [02]: video_id, channel_id, title, description, tags, published_at, duration_s, live_broadcast_content,
    default_audio_language|None, thumbnail_url, has_paid_product_placement: bool|None
ChannelRecord [02]: channel_id, title, handle (customUrl), country|None, subscriber_count|None, hidden_subscriber_count,
    made_for_kids|None, uploads_playlist_id
VideoMetrics [06]: video_id, comparison_age_days, views_at_age: ViewsAtAge, outlier_ratio (for EVERY video, not only outliers),
    is_outlier, channel_baseline_views_at_age, baseline_n, engagement_rate
ViewsAtAge [06]: age_days, views, basis: observed|interpolated|backfilled|unknown
VideoTraits [06]: video_id, format_key, length_band, title_traits{is_question, has_number, is_howto, has_year, length_bucket,
    has_location}, thumbnail_traits{text_present, word_count_band, contrast_band}, publish_dow
TraitLift [06]: scope: competitors|niche, trait_key, trait_value, n_with, n_outliers_with, n_total, n_outliers_total,
    lift, ci_low, ci_high, min_sample_met, supporting_video_ids
ChannelScorecard [06]: channel_id, role, uploads_per_week, gap_days_cv, median_outlier_ratio, median_views_at_age, topic_coverage{topic_id: n}
OwnPerformanceReport [06]: baseline_views_at_age{age: views}, baseline_n, by_format{format_key: (median_ratio, n)},
    by_trait{trait_key=value: (median_ratio, n, usage_share)}, own_analytics|None {subs_gained_per_1k_views by format, ...}
TrendingReport [06]: window_days, outliers[video_id], niche_median_views_at_age{age: views}
Competitors [04/01]: confirmed channel ids + CompetitorCandidate{channel_id, name, brand_aliases, domains, entity_kind}
```

`entity_kind` (business | creator | media | unknown) on niche channels and `brand_aliases`/`domains` for competitors are requested from 04 (see §13).

### 3.2 Owned domain objects (`domain/recommendation.py`)

```
class RecType(StrEnum): video_idea, title_pattern, thumbnail_pattern, format, creator, news_opportunity, description_refresh
class Lens(StrEnum): views, conversion
class ConfidenceLabel(StrEnum): High, Medium, Low
class EvidenceSource(StrEnum): youtube_public, youtube_own_analytics, spotlit_ai_tracking, website

class ReferenceVideo:
    video_id: str; url: str; title: str
    channel_id: str; channel_title: str; channel_role: competitor|niche|client|creator
    published_at: datetime
    comparison_age_days: int; views_at_age: int; views_basis: observed|interpolated|backfilled
    views_day7: int | None              # only when comparison_age_days == 7
    channel_baseline_views_at_age: int
    outlier_ratio: float; is_outlier: bool
    format_key: str; length_band: str; matched_topic_ids: list[str]
    thumbnail_url: str; has_paid_product_placement: bool | None

class Reason:                            # structured, never prose
    reason_id: str                       # "r1", "r2" … stable within a draft
    code: ReasonCode                     # see §5.12
    params: dict[str, int | float | str | date]
    ref_video_ids: list[str]
    source: EvidenceSource

class LensAssignment: lens: Lens; reasons: list[Reason]

class GapEvidence:
    topic_id: str
    gap_type: uncovered | stale | underperforming | none
    competitors_covering: int; competitors_total: int
    niche_outliers_matching: int; window_outliers_total: int
    client_videos_matching: int; client_last_covered_at: date | None; client_best_ratio: float | None
    gap_size: float                      # 0..1, internal

class PatternSpec:                       # title / thumbnail / format subjects
    kind: title | thumbnail | format
    traits: dict[str, str]               # e.g. {"is_question": "true", "has_year": "true"}
    scope: competitors | niche
    lift: float; ci_low: float; ci_high: float
    n_with: int; n_outliers_with: int; n_total: int; n_outliers_total: int
    client_usage_share: float | None; client_ratio_with: float | None; client_n_with: int

class VideoIdeaSubject:
    topic_id: str; topic_text: str
    format_key: str | None               # None when no format has a majority
    length_band: str | None
    title_hint_traits: dict[str, str]; thumbnail_hint_traits: dict[str, str]
    placeholders: list[str]              # e.g. ["[city]"] when location trait lifts and client has locations

class CollaborationEvidence:
    kind: mentioned_by_competitor | mentions_competitor | sponsor_language | disclosed_paid_promotion
    competitor_channel_id: str; competitor_name: str
    video_id: str; published_at: datetime
    matched_on: handle | channel_url | custom_url | website_domain | brand_name
    matched_token: str

class CreatorSubject:
    channel_id: str; title: str; handle: str | None; url: str
    subscriber_band: lt_10k | 10k_100k | 100k_1m | gt_1m | hidden
    declared_country: str | None         # creator-declared, never inferred
    uploads_90d: int; uploads_checked: int
    relevance: {match_share: float, topics_covered: int}
    performance: {median_outlier_ratio: float, median_views_at_age: int, niche_median_views_at_age: int, comparison_age_days: int}
    consistency: {median_gap_days: float, gap_cv: float, hit_rate: float, last_upload_at: date}
    collaborations: list[CollaborationEvidence]

class ExpectedRange:
    low: float | None; high: float | None           # multiples of client baseline at comparison age
    basis: peer | blended | own | peer_only_no_baseline | insufficient | not_applicable
    n_peer: int; n_peer_effective: float; n_own: int
    comparison_age_days: int
    size_note: peers_much_smaller | peers_much_larger | None
    display: str                                     # "1.2x-2.0x (indicative)" or a fixed n/a string

class Confidence:
    label: ConfidenceLabel
    outliers: int; channels: int                     # assignment fields
    matching_videos: int; rule_id: str; caps_applied: list[str]

class ScoreBreakdown: components: dict[str, float]; weights: dict[str, float]; penalties: dict[str, float]; total: float

class RecommendationDraft:
    draft_id: str; stable_key: str; client_id: str
    type: RecType; window_days: int; as_of: datetime
    produced_by: str                                 # "video_idea@1"
    subject: VideoIdeaSubject | PatternSpec | CreatorSubject | NewsSubject | RefreshSubject   # discriminated by type
    topic_ids: list[str]; buyer_question_id: str | None; related_buyer_question_ids: list[str]
    reference_pool: list[ReferenceVideo]             # ranked, up to 12
    references: list[ReferenceVideo]                 # selected for display, 3..5
    reasons: list[Reason]; fit_reasons: list[Reason]
    lenses: list[LensAssignment]
    gap: GapEvidence | None
    expected_range: ExpectedRange | None
    confidence: Confidence | None
    score: ScoreBreakdown | None
    flags: set[DraftFlag]                            # event_driven, contradictory, single_channel, review_flagged_topics, size_mismatch, ...

class GateOutcome: gate: str; action: pass | repair | downgrade | suppress; reason_code: str; details: dict

class SuppressedRecommendation:
    draft: RecommendationDraft; phase: pre_writer | post_writer | set_level
    gate: str; reason_code: str; details: dict; created_at: datetime

class InsufficiencyNotice:
    kind: thin_window | type_thin | no_buyer_questions | no_client_baseline | few_competitors | stale_data
    window_days: int; rec_type: RecType | None
    found: {outliers: int, channels: int, videos: int, cards: int}
    required: {outliers: int, channels: int, cards: int}
    suggestion: {action: widen_window | add_competitors | add_keywords | add_buyer_questions | connect_oauth | wait,
                 widen_to: int | None, projected: {outliers: int, channels: int, cards: int} | None}
    reason_code: str                                 # rendered by template, not LLM

class WhiteSpaceNote:
    buyer_question_id: str; text: str; window_days: int
    videos_checked: int; channels_checked: int; widest_window_checked: int
    ai_answer_citations: int | None                 # from Spotlit tracking, labelled separately

class GapReport:                                     # also consumed by 09 and 13
    window_days: int; as_of: datetime
    rows: list[GapEvidence]
    cells: dict[(topic_id, channel_id), {videos: int, outliers: int, best_ratio: float | None, latest: date | None}]

class RecommendationSet:
    client_id; window_days; as_of; generated_at; contract_version: "1.1"
    recommendations: list[Recommendation]
    notices: list[InsufficiencyNotice]; white_space: list[WhiteSpaceNote]
    suppressed_counts: dict[gate, int]; candidate_count: int

class RecommendationFeedback:
    id; client_id; stable_key; recommendation_row_id
    action: done | irrelevant | save | snooze | more_like_this
    reason: not_our_business | already_covered | cannot_produce_format | creator_not_suitable | evidence_wrong | other | None
    linked_video_id: str | None; note: str | None (≤280 chars); user_id; created_at
```

`Recommendation` is the contract object, defined in §5.14.

## 4. Internal design

```
backend/app/services/recommend/
  service.py              RecommendationService (facade used by pipeline stage + API)
  context.py              RecommendContext (immutable per run: client, window, as_of, config, EvidenceIndex, GapReport)
  evidence/index.py       EvidenceIndex: lookups by topic, format, trait, channel, role, outlier; window slicing by as_of
  evidence/references.py  ReferenceSelector: rank pool, pick 3..5, reuse-aware swap
  gaps/coverage.py        CoverageMatrixBuilder → GapReport; GapClassifier
  recommenders/base.py    Recommender Protocol, RecommenderRegistry
  recommenders/video_idea.py, title_pattern.py, thumbnail_pattern.py, format.py, creator.py
  creators/ranker.py      CreatorRanker (eligibility, signals, score)
  creators/collabs.py     CollaborationDetector (token builder + scanners)
  merge.py                CandidateMerger
  lens.py                 LensAssigner
  scoring/components.py   ScoreComponent Protocol + EvidenceStrength, ClientFit, GapSize, IntentStage, Recency
  scoring/penalties.py    Penalty Protocol + FeedbackPenalty, StalenessPenalty, ContradictionPenalty
  scoring/scorer.py       Scorer (weighted sum × penalties, lens-mode weights)
  scoring/diversity.py    MmrSelector (per-type caps, reference reuse caps)
  expectation.py          ExpectedRangeEstimator (strategies: PeerStrategy, OwnHistoryStrategy, NoBaselineStrategy)
  confidence.py           ConfidencePolicy registry (default_v1, creator_v1; 08/09 may add)
  gates/base.py           QualityGate Protocol, GateRegistry, GateChain, GatePolicy
  gates/structural.py     PolicyGuardrailsStructural, EvidenceCompleteness, MinReferenceVideos, FitToClient,
                          NoRepeatClient, HonestConfidence
  gates/text.py           NumbersFromEvidence, NoCopyingText, PolicyGuardrailsText, CertaintyLanguage, ReasonIntegrity
  gates/set_level.py      ThinData, ReferenceReuse
  thin_data.py            ThinDataAdvisor (projects counts for wider windows from cached data)
  white_space.py          WhiteSpaceDetector
  writer_brief.py         WriterBriefBuilder (→ 10), WrittenCard schema
  fallback_renderer.py    DeterministicCardRenderer (template per ReasonCode)
  assembly.py             ContractAssembler, StableIdFactory
  feedback.py             FeedbackService, OutcomeTracker
  repository.py           RecommendationRepository (01 tables below)
backend/app/pipeline/stages/recommend.py   RecommendStage (calls service.generate)
```

### 4.1 Key Protocols

```
class Recommender(Protocol):
    rec_type: ClassVar[RecType]
    version: ClassVar[int]
    def generate(self, ctx: RecommendContext) -> list[RecommendationDraft]: ...

class RecommenderRegistry:
    def register(self, recommender: Recommender) -> None
    def enabled_for(self, client_config) -> list[Recommender]      # per-client on/off by type

class ScoreComponent(Protocol):
    name: ClassVar[str]
    def value(self, draft, ctx) -> float                            # 0..1

class Penalty(Protocol):
    name: ClassVar[str]
    def factor(self, draft, ctx) -> float                           # 0..1 multiplier

class QualityGate(Protocol):
    name: ClassVar[str]
    phase: ClassVar[Phase]                                          # pre_writer | post_writer | set_level
    applies_to: ClassVar[frozenset[RecType]]
    order: ClassVar[int]
    def check(self, draft, ctx, written: WrittenCard | None) -> GateOutcome

class SetGate(Protocol):
    def check_set(self, drafts, ctx) -> tuple[list[RecommendationDraft], list[GateOutcome], list[InsufficiencyNotice]]

class ConfidenceRule(Protocol):
    rule_id: ClassVar[str]
    def label(self, draft, policy) -> Confidence

class ExpectedRangeStrategy(Protocol):
    def applies(self, draft, ctx) -> bool
    def estimate(self, draft, ctx) -> ExpectedRange
```

### 4.2 Open/closed rules

- Adding a recommendation type = one `Recommender` class + optional `ConfidenceRule`, `ScoreComponent`s and gates, registered in the DI container (01). No edits to scorer, chain, assembler or other recommenders.
- 08 registers `NewsOpportunityRecommender` plus gates such as `NewsSourceCorroboration`. 09 registers `DescriptionRefreshRecommender` plus `AdditiveDiffOnly` and `MinimalEdit`. Both reuse `MinReferenceVideos`, `EvidenceCompleteness`, `NumbersFromEvidence`, `NoCopyingText`, `PolicyGuardrails*`, `HonestConfidence` through `applies_to`.
- `GatePolicy` holds per-type thresholds, so a type can set `min_reference_videos=2` (news) without touching gate code. The policy is validated at startup: no type may set `min_reference_videos < 1`, and the assignment types are locked at `>= 3` unless an admin flag is set.
- Every recommender is a pure function of `RecommendContext`. No I/O, no wall clock: `as_of` is injected, so a backtest at frozen date T (12) runs the same code.

### 4.3 Run flow (inside `RecommendStage`)

```
generate(client_id, window_days, as_of, lens_mode="both") -> RecommendationSet
 1. ctx = build_context(...)                 # EvidenceIndex sliced to [as_of - window, as_of]; GapReport
 2. drafts = ⋃ r.generate(ctx) for r in registry.enabled_for(client)   (08/09 drafts injected here from their stages)
 3. drafts = CandidateMerger.merge(drafts)
 4. for d: LensAssigner, ExpectedRangeEstimator, ConfidencePolicy, ReferenceSelector
 5. pre-writer GateChain (repair/downgrade/suppress) → suppressed → log
 6. Scorer.score(d); MmrSelector.select(drafts, n = cards_per_window[window])
 7. writer: 10.write_cards(briefs) → WrittenCard per draft (fallback renderer if 10 fails)
 8. post-writer GateChain → on failure: one rewrite request with violations (10) → else fallback renderer → re-gate
 9. set-level gates: ReferenceReuse, ThinData (+ WhiteSpaceDetector) → notices
10. if a slot was freed in 8/9: backfill from the MMR queue (max 1 backfill round)
11. ContractAssembler → RecommendationSet → repository (shown + suppressed + notices)
```

Steps 1–6 also run, without writing, for the other two windows (cheap, < 1 s). This lets `ThinData` project what widening would give, and makes a "widen to 30" click fast, since only the writer then has to run.

## 5. Algorithms & logic

All thresholds live in `RecommendSettings` (pydantic-settings, section `recommend.*`). Each one can be overridden per client in `client_config.recommend_overrides`. The defaults are below.

### 5.1 Evidence universe

- Window set `W`: videos from competitor and niche channels with `published_at ∈ [as_of − window_days, as_of]` and `age_days ≥ min_ref_age_days (2)`.
- Excluded from `W`: `live_broadcast_content != "none"`; `duration_s == 0` (upcoming or processing — seen live on 2026-11-20, Apollo video `beZTMveD180` returned `P0D`, 0 views); `views_basis == unknown`; channels with `baseline_n < 5` or `channel_baseline_views_at_age < min_baseline_views (100)` (stops tiny channels from producing 10× "outliers" off 50-view baselines).
- Client set `O`: the client's own videos over `client_lookback_days (365)` (refreshed daily by 02, so retention holds).
- Topic match: a video matches topic `t` iff `TopicMatch.accepted` and `score ≥ topic_match_min (0.6)`. If `review_flag` is set, the match counts toward candidate generation but sets the `review_flagged_topics` flag (confidence cap, §5.9).
- Topic breadth guard: `breadth(t) = |W matching t| / |W|`. If `breadth(t) > broad_topic_share (0.4)`, `t` is "broad". It cannot seed a video_idea by itself and gets fit weight `1 − breadth(t)`. This stops a keyword like "health" from matching everything.

### 5.2 Gap analysis (GapReport)

Coverage matrix: rows are active TopicRefs; columns are the client, each competitor, and "niche" (all niche channels together). Each cell holds `{videos, outliers, best_ratio, latest}` in the window (client column: in `client_lookback_days`).

For each topic `t`:

```
comp_share     = competitors_covering(t) / max(1, competitors_total)
outlier_share  = niche_outliers_matching(t) / max(1, window_outliers_total)     # niche+competitor outliers
demand         = 0.5 * comp_share + 0.5 * min(1, outlier_share / 0.2)
presence       = max over client videos v matching t of 2^(-age_days(v) / client_presence_half_life (180))   # 0 if none
if client covered t and client_best_ratio < 0.8: presence *= 0.5              # underperforming counts as half-covered
gap_size       = demand * (1 - presence)

gap_type = uncovered       if client_videos_matching == 0 and demand > 0
         = stale           if latest client coverage older than stale_days (365) and demand > 0
         = underperforming if covered, client_best_ratio < 0.8 and niche outliers exist
         = none            otherwise
```

A buyer question with `demand == 0` and zero matching videos anywhere becomes white space (§5.11), not a recommendation.

### 5.3 Recommenders — candidate generation

Each recommender emits drafts that carry structured `reasons`/`fit_reasons` (codes in §5.12) and a `reference_pool`. None of them writes text.

**video_idea** (topic × winning format × gap)

```
for t in active TopicRefs (buyer questions first, then keywords), t not broad:
    T = {v in W matching t}
    T_out = {v in T : is_outlier}
    skip unless |T_out| >= min_outliers_for_candidate (2) and |{v in T : outlier_ratio >= 1.0}| >= min_reference_videos (3)
    by_format = group T_out by format_key
    formats = top 2 formats f by (count, then niche TraitLift(format_key=f).ci_low)
    if no format holds >= 50% of T_out: emit one draft with format_key=None (topic-only idea)
    for f in formats:
        subject.length_band = mode of length_band over T_out with format f
        subject.title_hint_traits = traits with share(T_out) - share(W) >= 0.2 AND niche/competitor TraitLift ci_low > 1
        subject.thumbnail_hint_traits = same rule on thumbnail traits
        subject.placeholders = ["[city]"] if has_location lifts and BusinessProfile.locations non-empty
        reference_pool = T sorted by (format == f desc, is_outlier desc, outlier_ratio desc, published_at desc)[:12]
        reasons: OUTLIER_SHARE(k=|T_out|, n=window outliers, window), MEDIAN_RATIO(refs),
                 COMPETITORS_COVER_CLIENT_DOESNT (if gap uncovered and competitors_covering >= 1),
                 FORMAT_LIFT (if TraitLift for f has ci_low > 1), AI_CITES_COMPETITOR (if Spotlit data)
        fit_reasons: BUYER_QUESTION_MATCH / KEYWORD_MATCH, OWN_FORMAT_RESULT (if client n_with >= 3),
                     CLIENT_STALE_COVERAGE / CLIENT_UNDERPERFORMING (gap type)
        gap = GapEvidence(t)
```

Fit is built in: `t` is the client's own keyword or buyer question, so topic overlap always holds. Gap is not required, but it carries a lot of weight in scoring (§5.7). A topic the client already covers well can still produce an idea for a different buyer question or format. The repeat guard (§5.10 NoRepeatClient) decides whether it is a repeat.

**title_pattern**

```
for each TraitLift L over title traits (single traits, plus 2-trait conjunctions if 06 emits them):
    require L.min_sample_met and L.ci_low > 1.0 and L.n_outliers_with >= 3
    prefer scope=competitors if its sample is met, else niche
    pool = {v in W with trait, is_outlier} ∩ {v matching any client TopicRef}   # topic-relevant support
    require |pool| >= 3 and distinct channels(pool) >= min_distinct_channels[title_pattern] (2)
    subject = PatternSpec(kind=title, traits, lift…, client_usage_share, client_ratio_with, client_n_with from OwnPerformanceReport)
    reasons: TRAIT_LIFT(n_outliers_with, n_outliers_total, n_with, n_total, lift, ci_low), MEDIAN_RATIO
    fit_reasons: CLIENT_TRAIT_USAGE (e.g. 2 of last 20 titles are questions), OWN_TRAIT_RESULT (if n >= 3),
                 TOPIC_SUPPORT (k of refs match client buyer questions)
```

The pattern is shown as a slot template built by code from the traits, e.g. `[Question] … [Number] … [Year]`. Example titles are optional writer output and must pass NoCopyingText.

**thumbnail_pattern** works the same way over `thumbnail_traits` (text_present, word_count_band, contrast_band). Thumbnail URLs of the refs go in the brief so the UI can show them side by side. Fit adds `CLIENT_TRAIT_USAGE` from the client's own thumbnails (e.g. "6 of your last 10 thumbnails have no text"). No face, person or object traits exist anywhere in the input, by design.

**format**

```
for each TraitLift over format_key and length_band with ci_low > 1, sample met:
    pool as for title_pattern; min_distinct_channels 2
    fit_reasons: OWN_FORMAT_RESULT (client median ratio in this format, n), CLIENT_FORMAT_ABSENT (client n_with == 0),
                 UNDERPERFORMING_TOPICS (client topics where client used another format with ratio < 0.8 while refs use f)
```

`format_key` is a structural label: duration band × title structure (how_to | list | question | comparison | explainer). It comes from duration and title regexes. It is not a content category (§9).

**creator** — see §5.5. One draft per eligible creator in the top `creator_candidates_to_gate (10)`.

### 5.4 Lens assignment (`LensAssigner`)

The rules are deterministic. Each lens on a card carries the reasons that put it there, and those go to `extras.lens_reasons`.

| Lens | Assigned when | Reason codes |
|---|---|---|
| conversion | draft has a `buyer_question_id` and `directness ≥ 0.6`, where directness = share of displayed refs whose match to that buyer question has `score ≥ direct_match_min (0.75)` and `method ∈ {lexical, llm}` | `ANSWERS_BUYER_QUESTION(directness_k, n, stage)` |
| conversion | Spotlit tracking shows AI answers to the question cite competitor videos and none of the client's | `AI_CITES_COMPETITOR` (source spotlit_ai_tracking) |
| conversion (own data) | OAuth connected and the client's own videos in this format/topic gained subscribers per 1k views ≥ client median | `OWN_SUBS_PER_1K` (source youtube_own_analytics, shown separately) |
| views | median outlier_ratio of displayed refs ≥ `views_lens_min_ratio (2.0)` and outliers ≥ 3 | `MEDIAN_RATIO` |
| views | pattern types: TraitLift ci_low > 1 (lift is measured on outlier status, which is a views measure) | `TRAIT_LIFT` |
| creator → conversion | creator's matching uploads include ≥ 2 buyer-question matches with directness ≥ 0.6 | `CREATOR_ANSWERS_BQ` |
| creator → views | creator performance score ≥ 0.5 | `CREATOR_PERFORMANCE` |

A draft with no lens is dropped as `no_lens` (logged). Website data is never used to assign a lens. If it appears at all, it appears in the UI next to the card, labelled (§9).

### 5.5 Creator ranker — "rank creators without inferring audiences"

**Answer for the design doc:** we rank creators only on what they publish and how those public videos perform. We never estimate who watches. The inputs are (a) whether their recent uploads match the client's own keywords and buyer questions, using the same constrained topic matcher; (b) their views at the same age relative to their own baseline and to the niche median; (c) how regularly they publish; (d) public, labelled collaboration evidence. We use no demographics, no audience geography, no interest profiles and no commenter profiling. Channel-level fields the creator declared themselves (`snippet.country`, `madeForKids`) are shown as "declared by creator" and used only as optional filters.

**Candidates**: channels seen in the shared niche-search cache for the client's keywords (02) plus channels of niche outliers in the 90-day window. Removed from the set:
- the client channel and any channel whose description links to a client domain (client's secondary channels);
- confirmed competitors, and any `CompetitorCandidate` with `entity_kind == business` (including candidates the user rejected; they are still businesses);
- channels whose description links to a competitor domain (competitor's own secondary channels);
- `made_for_kids == true` (configurable), and channels with no upload in `creator_max_inactive_days (30)`.

Pre-filter to the top 40 by niche-search frequency × outlier count. Ask 02 to make sure we have their last 50 uploads (playlistItems.list + videos.list, both 1 unit per 50 ids, shared cache).

**Signals** (all from public API data plus simple maths):

```
U90 = creator uploads in last 90 days (cap 50); w(kind) = 1.0 buyer_question, 0.7 keyword
match_share   = Σ_{v∈U90} max_t w(kind_t)·[v matches t] / |U90|
topics_cov    = distinct client TopicRefs matched in U90
relevance     = 0.7·match_share + 0.3·min(1, topics_cov / 5)

M = matching uploads in U90
momentum      = clip01( (ln(median outlier_ratio(M)) − ln 0.5) / (ln 3 − ln 0.5) )
reach         = clip01( (ln(median views_at_age_7(M) / niche_median_views_at_age_7) − ln 0.25) / (ln 4 − ln 0.25) )
performance   = 0.5·momentum + 0.5·reach

gaps          = days between consecutive uploads in U90 (needs ≥ 4 uploads)
cadence       = 1 / (1 + cv(gaps))
hit_rate      = share of U90 with outlier_ratio ≥ 0.7
consistency   = (0.5·cadence + 0.5·hit_rate) × (0.5 if last upload > 21 days ago else 1)

brand_exp     = 1.0 if any U90 video has has_paid_product_placement, 0.5 if only sponsor_language, else 0

creator_score = 0.40·relevance + 0.30·performance + 0.20·consistency + 0.10·brand_exp
if disclosed_paid_promotion mentioning a competitor within competitor_promo_penalty_days (60): × competitor_promo_penalty (0.8)
```

Eligibility: `match_share ≥ 0.2` and at least 3 matching uploads inside the client's window (these become the refs). For a 7-day window most creators fail this, and that is the honest outcome (§8).

**Collaboration detection** (`CollaborationDetector`), from cached titles and descriptions only:

1. Token sets. Creator: `@handle` (customUrl), `youtube.com/@handle`, `youtube.com/channel/UC…`, legacy `/c/` URLs. Competitor: the same, plus website domains and brand aliases from 04.
2. `mentioned_by_competitor`: a competitor upload whose title or description contains a creator token. Handle and URL matches only. The creator's display name alone gives no label, because names collide.
3. `mentions_competitor`: a creator upload that contains a competitor handle, URL or domain, or a brand alias **plus** at least one client niche keyword in the same text. The co-occurrence rule exists because brand names are ambiguous: "Apollo" also matches Apollo Tyres.
4. `disclosed_paid_promotion`: case 3 **and** `paidProductPlacementDetails.hasPaidProductPlacement == true` on that video. The label reads "disclosed paid promotion; mentions {competitor}". The flag does not name the sponsor, so we never say "sponsored by {competitor}".
5. `sponsor_language`: case 3 with no disclosure flag, where sponsor phrasing ("sponsored by", "in partnership with", "#ad", "paid partnership", plus a configurable regional list) appears on the same line as the token. The label is "self-described sponsorship text", and the card states that it is not YouTube's disclosure flag.
6. Every creator card shows coverage: "checked N recent uploads of {creator} and M of each competitor". Finding nothing does not mean there was no collaboration.

Scoring uses only `brand_exp`, plus the optional competitor-promo penalty. Labels are always shown and never hidden.

**Creator refs and confidence (rule `creator_v1`)**: refs = matching uploads in the window, sorted by outlier ratio; show 3–5.
- High: ≥ 5 matching uploads in window, ≥ 2 outliers, and `gap_cv ≤ 0.5`.
- Medium: ≥ 3 matching uploads and ≥ 1 outlier.
- Low: otherwise.

`channels` is always 1, and `extras.confidence.rule_id = "creator_v1"` so the UI can explain the rule. The default 3-channel rule cannot apply to a single creator.

### 5.6 Merging and deduplication (`CandidateMerger`)

Run in order:
1. **Exact key**: `stable_key = sha1(client_id | type | canonical_subject_key | window_days)`. Canonical subject keys: video_idea `topic_id|format_key`; patterns `kind|sorted(traits)|scope`; creator `channel_id`; news `news_signal_id`; refresh `video_id`. Duplicates by key keep the one with more refs.
2. **Same type, overlapping evidence**: Jaccard(reference_pool ids) ≥ 0.6, **or** (05 topic similarity ≥ 0.85 **and** Jaccard ≥ 0.4) → merge. The survivor keeps the higher-intent buyer question. The others' questions go to `related_buyer_question_ids`. Pools are unioned and re-ranked, and reasons are recomputed from the union, never concatenated.
3. **Pattern subsumption**: a 2-trait conjunction is dropped if a single trait's lift ci covers it (|Δlift| < 0.2) and its own n is smaller.
4. **Cross-component**: a `news_opportunity` (08) whose refs overlap a `video_idea` by Jaccard ≥ 0.5 on the same topic replaces the idea, because news is the more specific claim. Video ideas flagged `event_driven` (≥ 70% of refs published within 72 h of each other) are handed to 08 and do not stand alone. A `description_refresh` (09) never merges with an idea. When their topics match, the idea links to it in `extras.related` ("or refresh your video from {date}").
5. Contradiction marking: if lift for the same trait has `ci_low > 1` in one scope and `ci_high < 1` in the other, both with sample met, we keep the competitor-scope pattern (closer to the client), flag it `contradictory`, and add `MIXED_EVIDENCE`. The same applies to mutually exclusive values both lifted (e.g. short and long length bands). If both scopes have large samples (`n_outliers_with ≥ 10` each), we suppress with `contradictory_evidence`.

### 5.7 Scoring

The score is internal, used only for ranking. It is never shown as a number (§9).

```
E  evidence   = 0.35·(1 − e^(−outliers/5)) + 0.25·min(1, channels/4)
              + 0.20·min(1, ln(median_ratio_refs)/ln 5)⁺ + 0.20·min(1, ln(ci_low)/ln 3)⁺     # lift term: pattern types; video_idea uses its format lift or 0
F  fit        = 0.6·topic_overlap + 0.4·own_signal
               topic_overlap = max over matched client topics of w(kind)·(1 − breadth(t))·share_of_refs_matching
               own_signal    = clip01((own_ratio − 0.5) / 1.5) if client_n_with ≥ 3 else 0.5 (neutral)
G  gap        = gap_size (0 for pattern types unless client_usage_share < 0.2 → 1 − client_usage_share/0.2 scaled by 0.6)
I  intent     = decision 1.0 · consideration 0.7 · awareness 0.4 · keyword-only 0.3 (pattern/creator: mean over refs' matched topics)
R  recency    = mean over refs of 2^(−age_days / (window_days / 3))

base = Σ w_i·component_i
penalties: feedback (§5.15) · staleness 0.85^k (k = days shown unchanged beyond 14, no action) · contradiction 0.7 · single_channel 0.8
total = base × Π penalties
```

Weights by `lens_mode` (the client chooses; default `both`):

| mode | E | F | G | I | R |
|---|---|---|---|---|---|
| both | .30 | .25 | .20 | .15 | .10 |
| views | .40 | .20 | .15 | .05 | .20 |
| conversion | .20 | .30 | .20 | .25 | .05 |

If the client has no videos, `G` is uniform and tells us nothing, so its weight is split equally between E and I and `own_signal` is fixed at 0.5. Drafts with `total < min_score (0.35)` are not selected.

**Ties** are broken deterministically, in order: `round(total, 6)` desc → confidence label rank → distinct channels → outliers → newest median ref date → `stable_key` asc. The result never depends on input order, which a test checks (§11).

### 5.8 Diversity and card counts (`MmrSelector`)

Greedy MMR (Carbonell & Goldstein 1998):

```
select argmax_c  λ·total(c) − (1 − λ)·max_{s∈S} sim(c, s)          λ = 0.7
sim(c, s) = 0.4·Jaccard(ref ids) + 0.3·[shared topic_id or topic similarity ≥ 0.85] + 0.2·[same format/trait key]
          + 0.1·[same type] (+0.2 for creator pairs in same subscriber_band)
```

Hard constraints while selecting:
- per-type caps: video_idea 4, title_pattern 2, thumbnail_pattern 2, format 2, creator 3, news_opportunity 2 (08 may override). description_refresh is outside this list and gets its own surface with cap 5.
- reference reuse: one video may appear in at most `max_ref_reuse (2)` selected cards. On conflict, `ReferenceSelector` swaps in the next pool video that keeps min refs. If no swap is possible, the candidate is skipped this round.

Cards per window: `{7: 5, 30: 8, 90: 10}`. The UI shows the top 3 first and the rest behind "more" (progressive disclosure, 13). Fewer cards than the target is fine. We never pad.

### 5.9 Confidence (`ConfidencePolicy`, rule `default_v1`)

Counted over the **displayed** refs and the wider matching set:
- `outliers` = distinct `is_outlier` videos in reference_pool ∩ window; `channels` = distinct channels among those outliers; `matching_videos` = |matching set| used for the expected range.
- **High**: outliers ≥ 5 **and** channels ≥ 3 (assignment rule) **and**, for pattern types, ci_low > 1.
- **Medium**: outliers ≥ 3 **and** channels ≥ 2.
- **Low**: anything else that passes MinReferenceVideos.

Caps, each recorded in `caps_applied`:
- `contradictory` → max Medium.
- one channel supplies > 60% of refs → max Medium.
- > 50% of refs with `views_basis ≠ observed` → max Medium.
- `review_flagged_topics` on > 1 ref → max Medium.
- expected range `insufficient` → max Low.
- client baseline provisional (`baseline_n < 5`) → no cap on the label, but the range basis says so.

Low cards are shown and ranked lower; `show_low_confidence` defaults to true. The rule is computed in one place and re-checked by the HonestConfidence gate.

### 5.10 Expected range vs client baseline (`ExpectedRangeEstimator`)

Indicative only, deterministic, and never a promise. The key choice: **we compute it from every matching video, not only the outliers.** Using only outliers would bias the range upward (selection bias).

```
matching set M(c):
  video_idea: videos in W matching topic t with format f   (fallback: matching t, any format, if |M| < 8)
  patterns:   videos in W with the trait(s) AND matching any client TopicRef (fallback: all with the trait if |M| < 8)
  creator:    not applicable → basis=not_applicable

for v in M: x_v = ln(clip(outlier_ratio_v, 0.1, 20))     # outlier_ratio = views_at_age / own-channel baseline at same age (06)

size weights (client baseline B_c exists):  w_v = exp(−|ln(B_ch(v) / B_c)| / ln 10)
                                             else w_v = 1
n_eff = (Σw)² / Σw²
if n_eff < n_min_range (5): basis=insufficient, display "not enough comparable videos for a range"
q25, q75 = weighted quantiles of x
s = n_eff / (n_eff + k_peer (8))                 # shrink toward ratio 1.0 (x = 0) by sample size
lo_p, hi_p = s·q25, s·q75

own history (client has n_o ≥ 3 own videos matching the same pattern within client_lookback_days):
  so = n_o / (n_o + k_own (5)); lo_o, hi_o = so·q25(own), so·q75(own)
  α = n_o / (n_o + k_blend (5))
  lo, hi = α·lo_o + (1 − α)·lo_p, α·hi_o + (1 − α)·hi_p     basis = blended (own if n_o ≥ 20 → α forced 1)

caps: lo ≥ ln 0.5; hi ≤ ln cap_hi (3.0; 2.0 if size_mismatch)
min width: if hi − lo < ln 1.15, widen symmetrically
display: floor(e^lo, 1dp) "x-" ceil(e^hi, 1dp) "x (indicative)"     e.g. "1.2x-2.0x (indicative)"
```

- `size_mismatch`: the weighted share of peers within 10× of the client's baseline is < 0.3. `size_note` = `peers_much_smaller` or `peers_much_larger`. The range is capped at 2.0× and the card says so.
- Client with no baseline (new or empty channel, or `baseline_n < 3`): basis = `peer_only_no_baseline`. `expected_range_vs_baseline` reads `"no baseline yet; similar videos ran 1.2x-2.0x their own channel's baseline (indicative)"`. `low`/`high` hold the peer values so the UI can still show them, labelled as peer-relative.
- `hi < 1.0`: the views lens is removed (we don't recommend for views something that runs below baseline). If the conversion lens remains, the card stays and shows the range.
- Optional absolute translation (simple maths, same source): `views ≈ e^lo·B_c … e^hi·B_c at day {age}`, in `extras.expected_range.views_at_age_range` and labelled indicative.

Why IQR plus shrinkage: view counts are heavy-tailed. The middle half is robust, and shrinking by `n/(n+k)` (pseudo-count / empirical-Bayes style, after Efron & Morris 1975) keeps small samples close to "about your normal" instead of overclaiming. Component 12 calibrates `k_peer` and `k_own` from outcomes: a 50% interval should contain about 50% of outcomes.

### 5.11 White space (buyer question with zero coverage)

When a tracked buyer question has zero accepted matches in W at the widest window we have data for (90), across all competitor and niche channels:
- It is **not** a recommendation. It cannot meet the ≥ 3 reference rule, and inventing evidence is exactly the slop we forbid.
- We emit a `WhiteSpaceNote` with: question, videos checked, channels checked, window, and (if Spotlit tracking is connected) how many AI answers cite any video for it. That count is a separate source, labelled.
- The UI shows it as "Unanswered on YouTube (no reference videos)". The user can pair it with a proven format from the format cards. We make no expected range and no confidence claim for it.

### 5.12 Reason codes (closed catalogue; the writer phrases them, the fallback renders them)

| code | params | fallback template |
|---|---|---|
| OUTLIER_SHARE | k, n, window, topic | "{k} of {n} outliers in the last {window} days cover {topic}" |
| MEDIAN_RATIO | median_ratio, n | "Reference videos ran a median {median_ratio}x their channel's normal" |
| COMPETITORS_COVER_CLIENT_DOESNT | covering, total | "{covering} of {total} competitors cover this; you haven't" |
| FORMAT_LIFT / TRAIT_LIFT | trait_label, n_outliers_with, n_outliers_total, n_with, n_total, lift | "{trait_label}: {n_outliers_with} of {n_outliers_total} outliers vs {n_with} of {n_total} videos overall" |
| ANSWERS_BUYER_QUESTION | k, n, stage | "{k} of {n} reference videos directly answer this buyer question" |
| BUYER_QUESTION_MATCH / KEYWORD_MATCH | count, texts | "Matches {count} of your tracked buyer questions" |
| OWN_FORMAT_RESULT / OWN_TRAIT_RESULT | label, ratio, n | "Your {label} videos average {ratio}x your baseline ({n} videos)" |
| CLIENT_TRAIT_USAGE | k, n, label | "{k} of your last {n} videos use {label}" |
| CLIENT_STALE_COVERAGE | date | "You last covered this on {date}" |
| CLIENT_UNDERPERFORMING | ratio, n | "Your {n} videos on this ran {ratio}x your baseline" |
| AI_CITES_COMPETITOR | competitor_citations, client_citations | "AI answers to this question cite {competitor_citations} competitor videos and {client_citations} of yours (Spotlit AI tracking)" |
| OWN_SUBS_PER_1K | value, median | "Your similar videos gained {value} subscribers per 1k views vs {median} median (your YouTube Analytics)" |
| CREATOR_RELEVANCE / CREATOR_PERFORMANCE / CREATOR_CONSISTENCY / CREATOR_COLLAB | see §5.5 | … |
| MIXED_EVIDENCE | scope_a, scope_b | "Evidence is mixed: holds for {scope_a}, not for {scope_b}" |

Code chooses at most 3 reasons and 2 fit reasons per card (priority: gap > buyer question > lift > ratio > own). 08 and 09 add their own codes to the catalogue through registration.

### 5.13 Quality gate framework

**Chain of responsibility.** `GateChain` runs the gates registered for `(phase, draft.type)` in `order`. Each gate returns `pass`, `repair` (the draft was modified, e.g. invalid refs dropped, and the chain continues), `downgrade` (confidence or lens changed, continue) or `suppress` (stop, log a `SuppressedRecommendation`). Every non-pass outcome is persisted with its reason code, so 12 can report suppression rates by gate.

| order | gate | phase | applies to | rule (defaults in `GatePolicy`) | action |
|---|---|---|---|---|---|
| 10 | PolicyGuardrailsStructural | pre | all | draft holds no field outside the allow-list (no revenue, CPM, earnings, demographic, audience-geo or category fields; `categoryId` is never read) | suppress `policy_field` |
| 20 | EvidenceCompleteness | pre | all | each ref has url, channel, published_at, comparison_age_days, views_at_age with basis ≠ unknown, outlier_ratio, and is inside the window and `as_of`; drop failures from the pool and re-select | repair; suppress if the pool is empty |
| 30 | MinReferenceVideos | pre | all (per-type min) | ≥ `min_reference_videos[type]` (3; news per 08; refresh per 09) complete refs inside the window; ≥ `min_distinct_channels[type]` (patterns 2, video_idea 1, creator 1) | suppress `too_few_refs` / `single_channel_pattern` |
| 40 | FitToClient | pre | all | ≥ 1 fit reason **and**, except creator/refresh, ≥ 2 refs match a client TopicRef; for creators match_share ≥ 0.2 | suppress `no_fit` |
| 50 | NoRepeatClient | pre | video_idea, format, news | client has a video matching the same topic **and** the same format published within `max(window_days, repeat_guard_days 30)` → suppress `client_already_published`. Same topic, < 7 days old, any format → suppress `client_recent_pending` ("we'll judge it at day 7"). Same topic but different format and older → pass with fit reason CLIENT_COVERED_DIFFERENT_FORMAT | suppress / annotate |
| 60 | NoCopyingStructural | pre | patterns, video_idea | the subject's template or topic text vs titles of competitor, niche and client-window videos (rules below) | suppress `copy_risk` |
| 70 | HonestConfidence | pre | all | recompute the Confidence with the type's rule; fix any mismatch in label, counts or caps | downgrade |
| 80 | ThinData (per draft) | pre | all | expected range `insufficient` → confidence cap Low (already applied); flag | downgrade |
| 110 | ReasonIntegrity | post | all | each `why_it_works` item maps to a `reason_id` in the brief; no extra items; fit text cites only `fit_reasons`; no video ids or URLs in text | reject → rewrite / fallback |
| 120 | NumbersFromEvidence | post | all | every number in the written text exists in the brief's `allowed_numbers` (rules below) | reject → rewrite / fallback |
| 130 | NoCopyingText | post | all | summary and example titles vs competitor, niche and client titles (rules below) | reject → rewrite / fallback |
| 140 | PolicyGuardrailsText | post | all | lexicon: channel revenue/earnings/CPM/RPM/monetisation statements; audience demographics ("viewers are mostly…", age ranges, gender); category labels; medical-outcome promises for healthcare clients (domain extension) | reject → rewrite / fallback |
| 150 | CertaintyLanguage | post | all | no "guaranteed", "will get", "definitely", "proven to", "viral" claims; range wording must include "indicative" | reject → rewrite / fallback |
| 210 | ReferenceReuse | set | all | no video in > `max_ref_reuse` cards; swap or drop | repair / suppress `ref_overused` |
| 220 | ThinData (set) | set | all | see §5.13.4 | emit notices |

The text gates run after the writer. The fallback renderer's output goes through them too. A fallback that still fails (in practice only NoCopyingText, when a competitor title equals the bare topic phrase) is suppressed as `copy_risk_unresolvable`.

**5.13.1 NumbersFromEvidence**
- Extraction regex over the written text: integers, decimals, thousands separators (`18,400`, `18.4k`, `1.2M`, Indian `1.2 lakh`), percentages, multipliers (`3.6x`, `3.6×`), ranges (`1.2–2.0`), ordinals, dates and years, and number words one–twenty.
- `allowed_numbers`: every numeric leaf in the brief (reason params, ref fields, counts, window, range bounds, length-band bounds, `as_of` year), expanded with display forms: rounding at 0/1/2 dp, k/M/lakh abbreviations, fraction↔percent (`4 of 6` ↔ `67%` only if the brief contains both 4 and 6), and `x` multipliers.
- A number passes only on an exact match after display normalisation, at the precision written. "3.6x" passes for a 3.62 evidence value. "4x" does not pass for 3.6.
- Unmatched numbers are listed in the rewrite feedback sent to 10.

**5.13.2 NoCopying (rapidfuzz, `processor=utils.default_process`)**
Comparison corpus: titles of every competitor and niche video in the cache (all windows), plus the client's titles in `client_lookback_days`. A candidate string (summary, example title, pattern template, topic phrase) fails if, against any corpus title:
- normalised exact equality; or
- `fuzz.ratio ≥ 85`; or
- `fuzz.token_sort_ratio ≥ 88` **and** the length ratio is in [0.75, 1.33]; or
- `fuzz.partial_ratio ≥ 95` **and** the shorter string has ≥ 6 tokens (a verbatim chunk lifted from a long title).

`token_set_ratio` is deliberately not used. It scores 100 whenever one token set is a subset of the other ("Dental implant cost" vs "Dental implant cost in Delhi | full breakdown"), which would block every short topic phrase. Thresholds are configurable and calibrated by 12 on 200 labelled pairs. Against the client's own titles the threshold is `fuzz.ratio ≥ 80`, which catches "you already made this". Search uses `rapidfuzz.process.extract` with `score_cutoff` over a per-run cached, preprocessed corpus.

**5.13.3 PolicyGuardrails lexicon** is a configurable list of regexes per locale. Each hit is logged with its pattern id so false positives can be tuned. Prices in buyer questions ("implant cost ₹…") are allowed. The rule targets channel income statements, not product prices: it matches money terms whose subject is a channel or creator.

**5.13.4 ThinData (set level) and `ThinDataAdvisor`**
- Trigger: after all gates, `cards < min_cards (3)` **or** window outliers `< min_window_outliers (5)` **or** distinct outlier channels `< 2`.
- The advisor re-runs steps 1–6 at the next wider windows (7→30→90). That is pure computation over cached data, with no quota.
- Notice: `{found: {outliers, channels, cards}, suggestion: widen_to=30, projected: {outliers: 14, channels: 5, cards: 6}}`. It renders as "Not enough outliers in the last 7 days (2 across 1 channel). Widen to 30? That gives 14 outliers across 5 channels."
- If even 90 days is thin, the suggestion becomes `add_competitors` (if fewer than 3 confirmed), `add_keywords` (if fewer than 5 TopicRefs match anything) or `wait`.
- Per-type thin notices (`type_thin`), e.g. "No creator suggestions for 7 days. Creators need 3 matching uploads in the window. Widen to 30?".
- Other notices: `no_buyer_questions` → conversion lens unavailable; `no_client_baseline`; `stale_data` when the newest snapshot is older than 48 h.

### 5.14 Writer boundary and contract assembly

**What 07 hands to 10 (`WriterBrief`, JSON, ≤ ~2k tokens):**

```
{
  "brief_version": "1",
  "rec_type": "video_idea", "lens": ["views","conversion"], "window_days": 30,
  "client": {"name": "...", "offerings_matched": ["Knee replacement"], "value_props_matched": ["..."],
             "buyer_question": {"id": "bq_12", "text": "...", "stage": "decision"}, "keywords_matched": ["..."],
             "locations": ["Chennai"]},
  "subject": {... VideoIdeaSubject | PatternSpec | CreatorSubject, labels resolved to human words ...},
  "reasons": [{"reason_id": "r1", "code": "OUTLIER_SHARE", "params": {"k": 4, "n": 6, "window": 30, "topic": "..."}}],
  "fit_reasons": [{"reason_id": "f1", "code": "OWN_FORMAT_RESULT", "params": {"label": "how-to", "ratio": 1.4, "n": 7}}],
  "reference_videos": [{"ref": "v1", "title": "...", "channel": "...", "published_at": "2026-11-02",
                         "age_days": 7, "views_at_age": 18400, "outlier_ratio": 3.6}],
  "confidence": {"label": "High", "outliers": 6, "channels": 4},
  "expected_range": {"display": "1.2x-2.0x (indicative)", "basis": "blended"},
  "constraints": {"summary_max_chars": 110, "why_max_items": 3, "fit_max_chars": 160,
                  "example_titles_allowed": true, "max_example_titles": 2,
                  "allowed_numbers": [...], "banned_phrases": [...],
                  "do_not_copy": "never reuse a reference title or close paraphrase"}
}
```

Video ids, URLs and channel ids are left out of the brief. The writer cannot cite them, so it cannot invent them.

**What 10 returns (`WrittenCard`, validated by pydantic):**
`summary: str`, `why_it_works: [{reason_id, text}]`, `fit_to_client: {reason_ids: [..], text}`, `example_titles: [str] | None` (video_idea and title_pattern only), `thumbnail_text_idea: str | None` (thumbnail_pattern only).

**What code fills (never the LLM):** `id`, `type`, `lens`, `buyer_question` (verbatim from BusinessProfile), `reference_videos`, `expected_range_vs_baseline`, `confidence`, `window_days`, `generated_at`, and everything in `extras`.

**Fallback renderer:** when 10 is down, times out or fails twice, `DeterministicCardRenderer` renders `summary` from subject slots, e.g. "{format label}: {topic}, {length band}" or "Title pattern: {slot template}". `why_it_works` and `fit_to_client` come from the §5.12 templates. Fallback cards set `extras.writer = "template"`. They are plainer but still pass every gate.

**Contract (`Recommendation`, `contract_version` 1.1)** — every assignment field is present with its assignment type. Additions go only in `extras`, plus extra keys inside `reference_videos[]` and `confidence`, which JSON consumers ignore.

```
{
  "id": "rec_7k2m9q4x8w1p",                       # "rec_" + base32(sha1(stable_key))[:12]; stable across daily runs
  "type": "video_idea",
  "summary": "...",
  "lens": ["views", "conversion"],
  "buyer_question": "..." | null,                  # null for pattern/creator cards without a single question
  "why_it_works": ["...", "..."],
  "reference_videos": [{
      "video_id": "...", "channel": "<channel title>", "published_at": "2026-11-02",
      "views_day7": 18400 | null,                  # null when the comparison age is not 7 (e.g. 7-day window, video 4 days old)
      "outlier_ratio": 3.6,
      "url": "https://www.youtube.com/watch?v=...", "title": "...", "channel_id": "UC...",
      "channel_role": "competitor", "views_at_age": {"age_days": 7, "views": 18400, "basis": "observed"},
      "channel_baseline_views_at_age": 5100, "is_outlier": true, "thumbnail_url": "..."
  }],
  "fit_to_client": "...",
  "expected_range_vs_baseline": "1.2x-2.0x (indicative)" | "<n/a string>" | null,
  "confidence": {"label": "High", "outliers": 6, "channels": 4, "matching_videos": 23, "rule_id": "default_v1", "caps_applied": []},
  "window_days": 30,
  "generated_at": "2026-11-20T09:00:00Z",
  "extras": {
      "contract_version": "1.1",
      "status": "new | ongoing | updated",
      "first_seen_at": "...", "expires_at": "...",
      "rank": 1,
      "lens_reasons": [{"lens": "conversion", "code": "ANSWERS_BUYER_QUESTION", "params": {...}}],
      "why_it_works_structured": [{"reason_id": "r1", "code": "...", "params": {...}, "ref_video_ids": [...], "source": "youtube_public"}],
      "fit_structured": [...],
      "expected_range": {"low": 1.2, "high": 2.0, "basis": "blended", "n_peer": 23, "n_peer_effective": 17.4,
                         "n_own": 7, "comparison_age_days": 7, "size_note": null, "views_at_age_range": [6100, 10200]},
      "gap": {GapEvidence…},
      "subject": {VideoIdeaSubject | PatternSpec | CreatorSubject …},
      "related_buyer_questions": ["..."],
      "example_titles": ["..."],
      "related": [{"type": "description_refresh", "id": "rec_..."}],
      "flags": ["event_driven"],
      "writer": "llm | template",
      "data_disclosure": "Metrics computed by GrowthHacker from public YouTube data"
  }
}
```

Backward-compatibility test: remove `extras` and the added keys, and the object must validate against a JSON Schema transcribed from the assignment example (§11). In that schema `views_day7` and `expected_range_vs_baseline` are nullable. This is called out in §13.

### 5.15 Feedback loop

- **Capture** (`POST` via 11 → `FeedbackService.record`): `done` (optionally with the client's video URL or id), `irrelevant` + reason chip, `save`, `snooze` (14 days), `more_like_this`. Keyed by `stable_key`, so feedback survives daily regeneration.
- **Deterministic effects now:**
  - `irrelevant` → that stable key is hidden for 90 days. `FeedbackPenalty` multiplies drafts sharing the topic or pattern key by 0.5 for 60 days.
  - reason `not_our_business` → 11 prompts "Stop tracking '{topic}'?". This sets `TopicRef.active = false` (05/03 own the flag; 07 only proposes).
  - `cannot_produce_format` → `client_config.excluded_formats += format_key`. Recommenders skip it.
  - `creator_not_suitable` → that creator is excluded for 180 days.
  - `evidence_wrong` → creates a review item for 12 holding the full draft. If the ref was a topic mismatch, it also raises a TopicMatch review flag in 05.
  - `more_like_this` → ×1.2 on the same topic or pattern for 30 days (capped so it can't beat evidence rules).
  - `done` → `OutcomeTracker` links the video. If no link is given, a client upload whose topic and format match the rec within 30 days is linked automatically as `done (detected)`.
- **Outcome tracking**: when a linked video reaches `comparison_age_days`, store its ratio vs the client baseline, whether it fell inside the predicted range, and whether it became an outlier. This feeds the range calibration (k constants), confidence calibration (was High more reliable than Medium?) and 12's "did recommendations work" report.
- **Later learning signals** (not built now; data is stored for them): per-client weight tuning from accept/dismiss rates (logistic regression or a bandit over weight vectors), global shrinkage constants from outcomes, NoCopying threshold tuning from `evidence_wrong`/`copy` reports.
- **Staleness and rotation**:
  - A shown rec gets `expires_at = generated_at + max(3, window_days/3)` days. Each daily run recomputes it.
  - If the rec still passes, it keeps its id with status `ongoing`, or `updated` if refs or numbers changed.
  - If it now fails, it expires with the gate reason. A saved rec shows "No longer supported by recent data ({reason})".
  - Unchanged and unacted for more than 14 days → `StalenessPenalty` 0.85^k, so the list rotates.

## 6. Data model

All tables are tenant-scoped (`client_id`, row-level policy per 01).

| table | columns | indexes | retention |
|---|---|---|---|
| `recommendation_runs` | id (uuid), client_id, pipeline_run_id, window_days, as_of, generated_at, lens_mode, config_hash, contract_version, candidate_count, shown_count, suppressed_counts jsonb, duration_ms, writer_tokens_in/out, writer_fallbacks | (client_id, window_days, generated_at desc) | metadata kept; it holds no API data |
| `recommendations` | id (uuid row), rec_id (public `rec_…`), stable_key, run_id FK, client_id, type, rank, status (shown/expired/hidden), lens text[], buyer_question_id, score_total, score_components jsonb, payload jsonb (contract), draft jsonb (evidence), writer_meta jsonb, first_seen_at, last_seen_at, expires_at | (client_id, window_days, status), (client_id, stable_key), (run_id) | `payload`/`draft` contain API data → nulled or rebuilt at 30 days by the retention cron; row kept with ids + feedback linkage |
| `recommendation_references` | recommendation_id FK, video_id, channel_id, role, ordinal | (video_id), (recommendation_id) | delete with payload at 30 days |
| `recommendation_suppressions` | id, run_id, client_id, type, stable_key, phase, gate, reason_code, details jsonb, draft jsonb, created_at | (client_id, created_at), (gate, reason_code) | draft jsonb deleted at 30 days; (gate, reason_code, type, counts) kept for evaluation |
| `recommendation_notices` | id, run_id, client_id, window_days, kind, rec_type, payload jsonb | (run_id) | 30 days |
| `white_space_notes` | id, run_id, client_id, buyer_question_id, payload jsonb | (client_id, run_id) | 30 days |
| `recommendation_feedback` | id, client_id, stable_key, recommendation_id, action, reason, linked_video_id, note, user_id, created_at | (client_id, stable_key), (client_id, created_at) | kept (user-generated) |
| `recommendation_outcomes` | id, client_id, stable_key, video_id, matched_by (user/auto), comparison_age_days, predicted_low/high, observed_ratio, within_range, became_outlier, measured_at | (client_id), (stable_key) | observed_ratio is derived from API data → recomputed or deleted at 30 days; within_range/became_outlier booleans kept (flagged for policy review, §13) |
| `creator_collaborations` (shared, not tenant-scoped) | creator_channel_id, other_channel_id, video_id, kind, matched_on, matched_token, video_published_at, detected_at | (creator_channel_id), (other_channel_id) | 30 days (derived from titles and descriptions) |
| `client_recommend_config` | client_id, lens_mode, recommend_overrides jsonb, excluded_formats text[], excluded_creators jsonb | pk client_id | kept |

Migrations are delivered via Alembic under 01's conventions. Because `payload` holds the full contract, the API reads are a single query.

## 7. External APIs & libraries

07 makes **no** external calls itself. Dependencies on other components' calls and on libraries:

| item | use | verified |
|---|---|---|
| YouTube Data API `videos.list` part `paidProductPlacementDetails` | disclosed paid promotion flag for collaboration labels. Must be requested by 02 in every videos.list call; quota cost is per call (1 unit), not per part | Field documented at https://developers.google.com/youtube/v3/docs/videos ("Set to true if the content uses paid product placement"). **Verified live 2026-11-20** with an API key: `part=paidProductPlacementDetails,snippet,statistics,contentDetails` on 3 Apollo videos returned `{"hasPaidProductPlacement": false}` (3 units total used for this plan). |
| YouTube paid promotion disclosure | meaning of the flag: creators must tick "paid promotion" for sponsorships/endorsements; viewers see a disclosure label | https://support.google.com/youtube/answer/154235 |
| Quota costs | videos.list / playlistItems.list / channels.list = 1 unit; search.list = 100 | https://developers.google.com/youtube/v3/determine_quota_cost (well known; not re-fetched for this plan) |
| YouTube Developer Policies | 30-day refresh/delete (III.E.4), derived-metrics restriction and disclosure (III.E.4.h), aggregation across owners (III.E.2) | https://developers.google.com/youtube/terms/developer-policies (fetched 2026-11-20; see §9 and §13) |
| rapidfuzz ≥ 3.10 | `fuzz.ratio`, `fuzz.token_sort_ratio`, `fuzz.partial_ratio`, `process.extract`, `utils.default_process`; all scores 0–100 with `score_cutoff` | https://rapidfuzz.github.io/RapidFuzz/Usage/fuzz.html |
| numpy | weighted quantiles (own implementation over sorted cumulative weights), log maths | https://numpy.org/doc/stable/reference/generated/numpy.quantile.html |
| MMR | diversity rerank | Carbonell & Goldstein, "The use of MMR, diversity-based reranking for reordering documents and producing summaries", SIGIR 1998 — https://www.cs.cmu.edu/~jgc/publication/The_Use_MMR_Diversity_Based_LTMIR_1998.pdf |
| Shrinkage | pseudo-count shrinkage toward 1.0 | Efron & Morris, "Data analysis using Stein's estimator and its generalizations", JASA 1975 |
| LLM (via 10) | card wording | Model ids and prices from the bundled claude-api skill docs (`shared/models.md`): `claude-sonnet-5-5` $2/$10 per MTok; `claude-haiku-5-5` $0.10/$0.50 per MTok (≤100k prompt). `claude-haiku-4-5` price not listed there; assumed $1/$5 per MTok (**unverified**). |

## 8. Edge cases & failure modes

| case | detection | handling | user-facing message |
|---|---|---|---|
| No outliers in a 7-day window | window outliers == 0 | no drafts; ThinDataAdvisor projects 30/90 from cache; the rest of the dashboard (own performance, competitors) is unaffected | "Not enough outliers in the last 7 days (0 across 8 channels). Widen to 30? That gives 11 outliers across 5 channels." |
| Wider window also thin | projection at 90 still < thresholds | suggest add_competitors / add_keywords / wait | "Even over 90 days your niche has few standout videos (3 across 2 channels). Adding 2 more competitors would give us more to learn from." |
| Wider-window data not ingested yet | cache lacks 30/90-day uploads | suggestion without projection; clicking schedules ingest (02) and re-run | "Widen to 30? We'll fetch the extra videos (about 1 minute)." |
| All evidence from one channel | distinct channels among refs == 1 | patterns: suppress `single_channel_pattern`; video_idea: allowed, confidence max Low via cap, penalty 0.8, reason states it | "Only one channel has done this so far (3 videos), so treat it as a lead, not a trend." |
| One channel dominates (> 60% refs) | channel share | confidence capped Medium; ReferenceSelector prefers other channels when the pool allows | Confidence chip tooltip: "Mostly one channel" |
| Client has no channel / no videos | no client channel or `O` empty | no own baseline; gap weight redistributed; NoRepeatClient skipped; range basis `peer_only_no_baseline`; notice `no_client_baseline` | "No baseline yet: similar videos ran 1.2x-2.0x their own channel's normal (indicative)." |
| Client has few videos (baseline_n < 5) | 06 baseline_n | range basis peer-only, flagged provisional | "Your baseline is based on 3 videos, so ranges are rough." |
| Client far bigger than competitors | weighted share of peers within 10× baseline < 0.3 | size-weighted quantiles; cap 2.0×; `size_note` | "Peers are much smaller than you; range capped at 2.0x." |
| Client far smaller than competitors | same, other direction | same weighting, `peers_much_larger` | "Peers are much larger; results relative to your own baseline." |
| Tiny channels producing extreme ratios | baseline < min_baseline_views or ratio > 20 | channel excluded from W; ratios clipped at 20 in scoring and range | — |
| Contradictory patterns across scopes | §5.6 rule 5 | keep competitor scope, flag, Medium cap, MIXED_EVIDENCE; suppress if both large | "Mixed evidence: works for your competitors, not the wider niche." |
| Client's own history contradicts niche pattern | own ratio < 0.8 with n ≥ 3 | fit reason shows it; range blends own → lower; views lens dropped if hi < 1 | "Your question titles averaged 0.7x your baseline (5 videos), so test before switching." |
| Same video supports many recs | reference count per video across selected cards | `max_ref_reuse` 2 with swap from pool; MMR similarity penalises overlap | — |
| Client published the same topic 2 days ago | NoRepeatClient: same topic, age < 7 days | suppress `client_recent_pending`; re-evaluated when that video reaches day 7 | Notice: "You posted on '{topic}' on 18 Nov; we'll judge it at day 7 before suggesting more." |
| Client published same topic + format inside guard window | NoRepeatClient | suppress `client_already_published` | — (logged) |
| Client covered topic long ago | CLIENT_STALE_COVERAGE | allowed; fit cites date; link to 09 refresh if the old video exists | "You last covered this on 3 Feb 2025. A new take, or refresh that video." |
| Competitor title near-duplicate in written text | NoCopyingText | one rewrite with the offending title listed → template fallback → suppress if still failing | — |
| Writer invents a number | NumbersFromEvidence | rewrite → fallback | — |
| Writer adds a reason or extra bullet | ReasonIntegrity | rewrite → fallback | — |
| LLM unavailable / timeout / refusal | 10 returns error | fallback renderer for all cards; run continues; `writer=template` logged | Cards shown in plainer wording (no banner needed) |
| Buyer question with zero coverage anywhere (white space) | no matches at widest window | WhiteSpaceNote, not a card (§5.11) | "Nobody in your niche has answered '{question}' on YouTube in 90 days (checked 640 videos, 14 channels). There are no reference videos, so we can't estimate results." |
| No buyer questions tracked | profile has 0 active buyer questions | conversion lens unavailable; keyword topics only; notice | "Add buyer questions to get conversion-focused ideas." |
| Overly broad keyword | breadth > 0.4 | cannot seed video_idea alone; fit down-weighted | Settings hint: "'health' matches 62% of niche videos; add a narrower keyword." |
| Topic matches flagged for review | TopicMatch.review_flag | counts, cap Medium; card offers "wrong topic?" feedback | Tooltip: "Topic match pending review" |
| Event spike (news-driven outliers) | ≥ 70% refs within 72 h | flagged event_driven, routed to 08 merge; not shown as an evergreen idea | — |
| Duplicate across 07 and 08 | Jaccard ≥ 0.5 same topic | news_opportunity wins | — |
| Creator is a competitor or competitor sub-channel | id in competitor set; entity_kind business; description links competitor domain | excluded before ranking | — |
| Creator is client's own secondary channel | description links client domain | excluded | — |
| Creator with disclosed paid promotion mentioning a competitor | CollaborationDetector | labelled; optional ×0.8 penalty within 60 days; never "sponsored by X" | "Disclosed paid promotion on 12 Oct in a video that mentions {competitor}." |
| Creator mentions competitor with sponsor language but no flag | sponsor_language | labelled as self-described text | "Mentions {competitor} with 'sponsored by' text (not YouTube's disclosure flag)." |
| Brand name ambiguous ("Apollo") | brand alias without domain/handle and no niche keyword co-occurrence | not counted | — |
| Creator only uploads monthly (7-day window) | < 3 matching uploads in window | suppressed; type_thin notice | "Creator suggestions need 30+ days of uploads. Widen to 30?" |
| Creator hidden subscriber count | `hiddenSubscriberCount` | band `hidden`; no size-based diversity | "Subscriber count hidden by creator" |
| Creator inactive | last upload > 30 days | excluded | — |
| Made-for-kids channel | declared `madeForKids` | excluded by default (config) | — |
| Reference video deleted/private after generation | 02 refresh returns missing | drop ref, re-gate on next run; if below min → expire | Saved card: "A reference video is no longer available; this suggestion was withdrawn." |
| Live / upcoming / zero-duration videos | liveBroadcastContent, duration 0 | excluded from W | — |
| Views at age not observed (tracking started late) | basis ≠ observed | allowed if interpolated/backfilled; > 50% → Medium cap; `unknown` dropped | Ref row shows "estimated at day 7" |
| 7-day window: no video is 7 days old | comparison_age_days < 7 | `views_day7` null; `views_at_age` at actual age vs same-age baseline | "Views at day 4 vs that channel's typical day 4." |
| Shorts vs long-form mixed | format_key | separate formats; baselines per format (06, §13) | — |
| Multilingual niche (Hindi + English titles) | default_audio_language / script detection | title patterns computed per language when both sides ≥ min sample; NoCopying uses unicode-normalised text; refs language shown | "Pattern seen in Hindi-language titles." |
| Recommendations going stale | expires_at, daily re-gate | ongoing/updated/expired statuses; staleness penalty | "No longer supported by recent data (fewer than 3 reference videos in 30 days)." |
| Ties in score | tie-break chain §5.7 | deterministic order | — |
| Fewer cards than target | after gates | never pad; ThinData if < 3 | — |
| Every candidate suppressed by one gate (bug or bad config) | suppressed_counts[gate] == candidate_count and > 10 | run marked `degraded`; alert to 12 observability | "We couldn't produce reliable ideas today; we're looking into it." |
| Spotlit AI tracking not connected | no BuyerQuestionSource adapter | AI_CITES signals absent; nothing inferred | — |
| OAuth not connected | own_analytics None | OWN_SUBS_PER_1K absent; public-only fit | Settings hint: "Connect YouTube Analytics to see subscribers gained per video." |
| Competitors < 3 confirmed | config | rely on niche scope; notice `few_competitors` | "Only 1 competitor confirmed; most evidence comes from the wider niche." |
| Data older than 48 h (snapshot cron failed) | max snapshot age | run continues; notice `stale_data`; confidence max Medium | "Data last updated 3 days ago." |
| Time budget exceeded | stage timer > 60 s | cap candidates per type (top 50 by evidence) before range/gates; writer concurrency up | — |
| Window boundary timezone | all windows in UTC from `as_of` | dates shown as UTC dates in contract; UI may localise | — |
| User changes competitor set mid-day | config_hash change | full rerun; ids stable where subject unchanged | — |
| Same rec at different windows | stable_key includes window | separate ids; UI shows one per window | — |
| Healthcare client: medical claim in text | PolicyGuardrailsText domain extension | rewrite → fallback | — |
| Price/cost buyer questions | money words with product subject | allowed (lexicon targets channel income) | — |

## 9. Policy & guardrail check

| feature | guardrail | status |
|---|---|---|
| All metrics on cards (ratios, views at age, counts, lift, ranges) | API data + simple maths | Yes: medians, ratios, quantiles, counts; computed in 06/07 code only. Each card carries `data_disclosure`. **Risk:** Developer Policies III.E.4.h bars "new or derived data or metrics" from API data except for audited analytics developers (Section L), and it requires disclosure for non-YouTube metrics. The assignment explicitly allows simple maths. Flagged in §13. |
| Internal score, MMR | not shown as a metric | Score is used only for ranking. The contract exposes `rank`, never the score value. |
| Expected range | no promises; simple maths | Quantiles of ratios with shrinkage, labelled "(indicative)", gated by CertaintyLanguage. |
| Spotlit AI-tracking and own-analytics signals | never merged into one metric with public data | Separate reason codes with a `source` field and labelled text. They never enter E/F numerics except as separate binary fit reasons. Website data is not used. |
| Revenue / monetisation | forbidden | No such fields (structural allow-list) and blocked in text. brand_exp uses only the public disclosure flag, no money estimates. |
| Audience demographics | forbidden | Creator ranking uses content and performance only (§5.5). Text lexicon blocks demographic claims. Declared country is shown as declared by the creator. |
| Video content category | forbidden to infer | Topics are always the client's own TopicRefs (05). `format_key` is duration band + title structure (form, not subject). `categoryId` is never read. The approach is flagged for review per assignment. |
| Thumbnails | simple traits only | Only text_present / word_count_band / contrast_band from 06; no faces. |
| 30-day retention | refresh/delete | §6: payload, draft, suppressions, outcomes and collab rows purged or rebuilt at 30 days. |
| Official APIs only | — | 07 calls no APIs. Collaboration evidence uses API fields and our cached titles and descriptions only. |
| Cross-client sharing | cached public data shared | `creator_collaborations` is client-agnostic public-derived data; client-specific outputs stay tenant-scoped. |
| No copying competitor titles / no repeating client | quality bar | NoCopyingStructural/Text + NoRepeatClient. |
| LLM cannot add videos, numbers or reasons | quality bar | Brief excludes ids. ReasonIntegrity + NumbersFromEvidence. Code fills reference_videos. |

## 10. Performance, cost & scaling

- **Quota:** 07 uses 0 units. Indirect: creator uploads for about 40 candidates ≈ 40 playlistItems.list + 1 channels.list + ~40 videos.list ≈ 81 units/day/client, shared across clients in the same niche through 02's cache. Adding `paidProductPlacementDetails` to videos.list costs 0 extra units.
- **Data size per client:** ≤ 10 competitors + ~100 niche channels, about 5k videos in 90 days, ≤ 60 TopicRefs.
- **Deterministic compute:**
  - EvidenceIndex build is O(videos + matches).
  - Candidates: ≤ 60 topics × 2 formats + ~40 traits + ~40 creators ≈ 200 drafts.
  - NoCopying is about 200 strings × 5k titles. With rapidfuzz `process.extract` and `score_cutoff`, that is under 0.3 s.
  - MMR is O(n·k).
  - Target < 2 s for one window, < 4 s for all three (projection runs).
- **Writer:**
  - About 8 cards × (brief ~1.8k in + ~350 out tokens). Concurrency 5, so about 2 waves of 3–5 s, roughly 10 s.
  - Rewrites in about 10% of cards.
  - Cost per client per daily refresh: Sonnet 5.5 ≈ 8×(1.8k×$2 + 0.35k×$10)/1M ≈ $0.057. Haiku 4.5 (assumed $1/$5) ≈ $0.028. Haiku 5.5 ≈ $0.003.
  - Prompt caching of the shared system prompt and schema cuts input further (10 owns that).
- **Latency budget:** recommend stage ≤ 25 s of the 2-minute per-client target. Under pressure, the fallback renderer keeps the stage under 5 s.
- **Scaling:** pure functions per client, so they parallelise across arq workers. No shared mutable state besides the DB. Memory is about 50 MB per client run.

## 11. Testing strategy

- **Unit (pure functions, synthetic fixtures):**
  - Each recommender with hand-built EvidenceIndex fixtures, covering positive cases, below-threshold cases and broad-topic cases.
  - Gap classifier on all four types.
  - CreatorRanker signals and eligibility.
  - CollaborationDetector over a table of title/description strings (handle, URL, domain, ambiguous brand, sponsor phrases in English and Hindi, flag on/off).
  - Merger rules 1–5.
  - Scorer weights per lens mode.
  - MMR caps and reuse swap.
  - ConfidencePolicy (High/Medium/Low boundaries: 5/3 and 4/3 and 5/2; every cap).
  - ExpectedRange: shrinkage limits (n → ∞ approaches raw IQR; n = 0 → 1.0), caps, min width, size weighting, no-baseline string, blend weights.
- **Gate tests:** for every gate, a passing and a failing draft.
  - NumbersFromEvidence corpus: "18.4k", "18,400", "1.2 lakh", "3.6x", "4 of 6", "67%", "four", "2026", "3–8 min", plus rounding traps ("4x" for 3.6 must fail).
  - NoCopying: 200 labelled pairs (owned with 12), asserting precision ≥ 0.95 at the chosen thresholds, including the "Dental implant cost" subset trap.
- **Golden tests:** a frozen fixture niche (Apollo-like healthcare: 5 competitors, 40 niche channels, 30-day window, stubbed template writer) → exact `RecommendationSet` JSON snapshot. Separate goldens for the 7-day thin case (notice with projection), the no-baseline client and the creator collab labels.
- **Determinism and as_of:** shuffling input order gives an identical output. Patching the system clock gives no change (ensures `as_of` only). Running the same fixture twice gives identical ids.
- **Contract tests:** JSON Schema transcribed from the assignment example. Every emitted object minus `extras` and the added keys validates. `views_day7` and `expected_range_vs_baseline` are nullable, as noted in §13. 08 and 09 drafts run through the same contract test.
- **Writer integration (10 stubbed):** malicious stub outputs (extra number, new reason, competitor title, revenue claim, "guaranteed") → rewrite path → fallback path; assert nothing slips through.
- **Performance test:** synthetic 5k videos, 60 topics, 40 creators → deterministic stage < 2 s on CI.
- **Live smoke (manual / nightly, budgeted):** Apollo with cached data, 30-day window. Assert ≥ 3 cards or a valid notice, every card passes the contract, all refs resolve to real cached videos, and suppression counts are logged. Zero direct quota from 07.
- **Evaluation hooks for 12:** `generate(as_of=T)` on frozen snapshots for backtests; suppressed drafts exported for review; outcome table for calibration.

## 12. Implementation task breakdown

| # | task | size | depends on |
|---|---|---|---|
| 1 | Domain objects (`domain/recommendation.py`), contract model, JSON Schema of assignment contract, contract tests | M | 01 |
| 2 | Settings (`RecommendSettings`, `GatePolicy`, per-client overrides) | S | 01 |
| 3 | EvidenceIndex + window slicing by `as_of` + exclusions (§5.1) | M | 06 VideoMetrics/Traits, 05 TopicMatch, 02 records |
| 4 | Coverage matrix + GapReport + WhiteSpaceDetector | M | 3 |
| 5 | Gate framework (Protocol, registry, chain, suppression logging) + structural gates | M | 1, 2 |
| 6 | ConfidencePolicy (default_v1) + ExpectedRangeEstimator (peer, own, no-baseline) | M | 3, 06 OwnPerformanceReport |
| 7 | VideoIdeaRecommender + LensAssigner + ReferenceSelector | L | 3, 4, 6 |
| 8 | TitlePattern / ThumbnailPattern / Format recommenders | M | 3, 6, 06 TraitLift |
| 9 | CandidateMerger | S | 7, 8 |
| 10 | Scorer + penalties + MmrSelector + tie-break | M | 7, 8 |
| 11 | CollaborationDetector + CreatorRanker + CreatorRecommender (creator_v1) + `creator_collaborations` table | L | 02 creator uploads with paidProductPlacementDetails, 04 entity_kind/aliases |
| 12 | WriterBriefBuilder + DeterministicCardRenderer + reason catalogue | M | 1, 10 writer interface |
| 13 | Text gates (ReasonIntegrity, NumbersFromEvidence, NoCopyingText, PolicyGuardrailsText, CertaintyLanguage) | M | 5, 12 |
| 14 | Set gates (ReferenceReuse, ThinData) + ThinDataAdvisor projections | M | 5, 10 |
| 15 | ContractAssembler + stable ids + staleness statuses + repository + migrations | M | 1, 01 |
| 16 | RecommendationService facade + RecommendStage + run metrics | S | all above, 11 |
| 17 | FeedbackService + OutcomeTracker + feedback effects | M | 15, 11 API |
| 18 | Integration with 08/09 (their drafts through merger, scorer, gates, contract) | S | 08, 09 |
| 19 | Golden fixtures, perf test, live smoke | M | 16 |
| 20 | Threshold calibration hand-off (NoCopying pairs, range k constants) | S | 12 |

Rough total: about 2 weeks for one engineer, after 05 and 06 have stable outputs. Tasks 1–6 can start on fixtures before 06 is done.

## 13. Open questions / proposed changes to shared context

1. **02 must request `paidProductPlacementDetails` in every `videos.list` call.** It costs nothing extra and was verified live. Without it, the "disclosed paid promotion" label the assignment asks for is impossible.
2. **06 must expose, for every video (not only outliers):** `outlier_ratio`, `comparison_age_days`, `views_at_age.basis`, `channel_baseline_views_at_age` and `baseline_n`. It must also provide `format_key` (duration band × title structure), per-format channel baselines (Shorts vs long), TraitLift with `ci_low`/`ci_high` and `supporting_video_ids`, and `OwnPerformanceReport.by_format/by_trait` with n and usage share. The expected range depends on non-outlier ratios to avoid selection bias.
3. **04 should label niche channels with `entity_kind` (business | creator | media | unknown)** and give competitors `brand_aliases` and `domains`. The creator ranker uses these to exclude businesses, and collaboration detection uses them to match brands.
4. **05 should expose TopicRef-to-TopicRef similarity** (for merging) and keep `accepted`/`review_flag`/`method` on TopicMatch. **03 must provide `BuyerQuestion.intent_stage`.**
5. **Contract changes for the shared context:** `reference_videos[].channel` = channel title (with `channel_id` added). `views_day7` is nullable: a 7-day window has no day-7 views for young videos, so `views_at_age` carries the actual comparison age. `expected_range_vs_baseline` is nullable or an "n/a" string for creators and white space. Add an `extras` object and `contract_version`. Section 2 of the shared context should list these as compatible extensions.
6. **Shared gate framework lives in 07** (`services/recommend/gates`). 08 and 09 must emit `RecommendationDraft` with a registered subject type and reason codes, and register extra gates instead of building their own chain. 08 must define `min_reference_videos` for news (proposed 2 YouTube videos + 2 independent news sources), and 09 its refs (the client's own video + ≥ 2 evidence videos using the keyword).
7. **Policy risk worth a design-review line:** YouTube Developer Policies III.E.4.h forbids creating "new or derived data or metrics" from API data (Section L allows it for audited analytics developers) and requires a disclosure for non-YouTube metrics. III.E.2 restricts combining data from different content owners. The assignment explicitly allows "simple maths" and competitor comparison. We should (a) label every computed number "computed by GrowthHacker from public YouTube data", (b) never display composite scores, and (c) have 12/legal confirm whether an audit (Section L) is needed before production.
8. **Ingest depth:** 02 should always ingest 90 days of competitor uploads, whatever window the client selected. Then thin-data notices can project wider windows with real numbers at no extra quota cost.
9. **`as_of` everywhere:** no component in the analysis path should read the wall clock. Everything takes `as_of` from RunContext, so 12 can backtest at frozen T with the same code.
10. **LLM tier:** the shared context names `claude-haiku-4-5` for low-reasoning tasks. The bundled Anthropic docs list `claude-haiku-5-5` as the current Haiku, at $0.10/$0.50 per MTok, with breaking API differences (no `budget_tokens`, no sampling params, no prefill). 10 should evaluate it for card writing.
11. **Retention of outcome booleans:** can `within_range` / `became_outlier` outlive 30 days as our own evaluation data, or are they derived API data that must be purged? Needs a policy decision (affects calibration history).
12. **Spotlit AI-answer tracking** is internal to Spotlit and not available to us. The `ai_cited_video_ids` signal is optional and absent in the demo. The shared context should say so, so that no plan depends on it.
13. **White space is not a card.** Proposed product rule: unanswered buyer questions appear in their own "Unanswered on YouTube" panel (13). They never appear as recommendations, because they cannot meet the reference-video rule.
