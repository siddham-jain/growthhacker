# 09 — Content refresh engine (keyword-level description improvements)

Status: plan · Owner component: `services/refresh/` · Pipeline stage: `refresh` (after `news radar` and `recommend`, before `finalize`)

Live checks done for this plan (2 quota units total): `playlistItems.list` + `videos.list` (part=snippet,contentDetails,statistics,status,localizations) on the latest 50 uploads of `UC0InVdvqNyNzKBl1-TL348A` (Apollo Hospitals), fetched 2026-11-20. Findings are quoted where they shape a decision ("Apollo sample").

---

## 1. Purpose & business value

A business's back catalogue keeps earning search traffic long after publish. When the world changes (a new model launches, a new year starts, the business opens a booking page or a new centre), those still-earning videos stop matching what people search for, or stop sending viewers anywhere useful. This component finds the few videos where a **small, truthful, additive** edit to the description is worth making:

- **Views lens:** add the missing keyword people now search for (news entity, buyer-question phrasing that niche outliers use) to a video that is still earning views. YouTube says search relevance is estimated by "how well the title, tags, description, and video content match your search query" ([How YouTube search works](https://support.google.com/youtube/answer/16090438)) and that "writing descriptions with keywords can help viewers find your videos more easily through search" ([Tips for video descriptions](https://support.google.com/youtube/answer/12948449)).
- **Conversion lens:** make a still-earning video *do something*: add the booking/sign-up link the business already has on its website, fix broken links, add chapters so buyer-intent viewers reach the answer. Apollo sample: only **2 of 50** recent descriptions link to a booking page, and one (`HI8fFZEfrRA`) has malformed social links (`https://www.facebook.com/https://www.facebook.com/...`). These are concrete conversion leaks we can find from public data alone.
- **Restraint is the feature.** Refresh is not mandatory. A dental clinic with five evergreen videos and no niche news gets "nothing to refresh" or a couple of hygiene fixes; an AI tools company after a frontier model launch gets a handful of targeted keyword insertions on its top-velocity videos. The engine must say "no refresh needed, here's why" as confidently as it suggests one.

Non-goals restated: we never rewrite a description, never publish, never change a title automatically.

## 2. Scope / non-scope

**In scope**
- Business-level refresh eligibility decision (`RefreshEligibility`) with cadence.
- Video-level eligibility, trigger detection, deterministic scoring and ranking.
- Trigger types: `news_entity`, `stale_year`, `buyer_question_gap`, `missing_cta`, `missing_contact`, `missing_chapters`, `missing_hashtags`, `broken_link`, `above_fold_keyword`.
- Establishing what the video actually covers (`VideoContentEvidence`) and restricting insertions to claims consistent with it.
- Additive diff operations (`InsertionOp`) with exact text, anchors and byte budget; one merged `DescriptionRefresh` per video.
- LLM phrasing of insertion lines only (via component 10), with a deterministic validator and template fallback.
- Mapping into the recommendation contract (`type = description_refresh`) and type-specific quality gates.
- Link health checking (HEAD/GET, rate-limited, SSRF-safe, cached).
- "Applied" tracking (user mark + automatic detection from re-fetched public description) and before/after velocity comparison.
- Retraction / supersession handling for news-driven insertions.

**Out of scope**
- Publishing or writing to YouTube (`videos.update`) — assignment: "Out of scope: publishing". User copies the line.
- Title rewrites (we only flag risky title issues, e.g. stale year in title).
- Thumbnail changes, pinned comments, end screens, cards (possible future trigger types via the registry).
- Translating insertions into the video's `localizations` (flagged to the user; see §8).
- Discovering news (component 08), computing velocity/outliers (06), topic matching (05), crawling conversion actions (03).
- Any YouTube scraping (e.g. reading auto-chapters, Studio search insights or the watch page). Only Data API v3 (+ Analytics API when OAuth exists).

## 3. Inputs & outputs

### 3.1 Inputs (consumed, owned elsewhere)
| From | Object | Fields we rely on |
|---|---|---|
| 01 | `ClientConfig` | client_id, own_channel_id, window_days (7/30/90), refresh_settings (see §5.10), oauth_connected |
| 02 | `VideoRecord` (client's own channel) | video_id, channel_id, title, description, tags[], published_at, duration_s, privacy_status, live_broadcast_content, default_language, default_audio_language, localizations{lang: {title, description}}, made_for_kids, fetched_at, description_hash |
| 02 | `VideoSnapshot` | video_id, captured_at, view_count |
| 02 | `OwnAnalytics` (OAuth only) | per-video search terms (`insightTrafficSourceDetail` with `insightTrafficSourceType==YT_SEARCH`), views by traffic source per day |
| 02 | `CaptionTrack` (OAuth only, optional) | video_id, language, cues[(start_s, end_s, text)] |
| 03 | `BusinessProfile` | offerings[], locations[], conversion_actions[ConversionAction], niche_keywords[], buyer_questions[BuyerQuestion], freshness_profile[FreshnessProfile], website_domain |
| 03 | `ConversionAction` | kind (book/signup/call/whatsapp/download/contact), label, url or phone, source_url (page it was crawled from), location_id?, offering_ids[] |
| 03 | `FreshnessProfile` | news_velocity_prior (low/medium/high), regulated_domain (bool) + domain_kind (healthcare/finance/legal/none), evergreen_expected (bool) — see §13 if 03 names differ |
| 05 | `TopicMatch` | video_id, topic_ref_id, score, method, review_flag |
| 05 | `TopicRef` | id, kind (keyword/buyer_question), text, aliases[] |
| 06 | `VideoMetrics` | video_id, age_days, velocity_recent (views/day over last ≥7-day snapshot span), velocity_basis (snapshots/lifetime_avg), views_day7, outlier_ratio |
| 06 | `ChannelBaseline` | channel_id, median velocity per age band |
| 06 | `TrendingReport` | niche + competitor videos in window with views_day7, outlier_ratio, title, description |
| 08 | `NewsSignal` | id, entity, aliases[], entity_kind (product/model/regulation/event/org), is_competitor_entity, topic_ref_ids[], first_seen_at, event_date, sources[{url, publisher, published_at}], status (active/disputed/retracted/superseded), superseded_by, demand_videos[video_id] (from 08's cached YouTube demand check), relevance, expires_at |

### 3.2 Outputs (owned by 09)

```text
RefreshEligibility
  client_id: str
  run_id: str
  decision: Literal["none", "hygiene_only", "periodic", "active"]
  cadence_days: int                       # how often keyword triggers are re-evaluated
  keyword_refresh_need: float             # 0..1, internal, never shown as a metric
  components: {news_component, evergreen_view_share, buyer_gap_rate, hygiene_defect_rate}
  evergreen_video_count: int
  eligible_video_count: int
  velocity_basis: Literal["snapshots", "lifetime_avg_provisional"]
  reasons: list[str]                      # deterministic reason strings, e.g. "No niche news in 30 days"
  computed_at: datetime

VideoContentEvidence
  video_id: str
  sources_used: list[Literal["title","description","chapters","tags","hashtags","owner_transcript","owner_confirmation"]]
  covered_terms: set[str]                 # normalised 1–4 grams from the sources above, boilerplate removed
  covered_topic_refs: list[str]           # TopicRef ids matched by 05 with score >= fit threshold
  boilerplate_spans: list[(start, end)]   # channel-wide repeated blocks (footer, socials)
  structure: DescriptionStructure

DescriptionStructure
  paragraphs: list[(start, end)]
  first_paragraph_end: int
  links_block_start: int | None
  hashtag_line_start: int | None
  separator_lines: list[int]
  chapters: list[(seconds, label)]        # parsed timestamps; valid flag per YouTube rules
  chapters_valid: bool
  hashtags: list[str]
  urls: list[ExtractedUrl]
  byte_length: int
  char_length: int
  detected_language: str
  newline_style: Literal["\n", "\r\n"]

RefreshTrigger
  id: str
  kind: TriggerKind                       # registry key
  video_id: str
  keyword: str | None                     # canonical keyword/entity
  strength: float                         # 0..1 relevance R
  demand: float                           # 0..1 demand evidence D (YouTube data only)
  urgency: float                          # 0..1 U
  evidence: TriggerEvidence
  dedupe_key: str                         # e.g. "news_entity:claude fable 5.2"

TriggerEvidence
  reference_video_ids: list[str]          # niche/competitor videos using the keyword, inside window
  news_sources: list[{url, publisher, published_at}]   # shown side by side, never merged into numbers
  owner_search_terms: list[str]           # OAuth only, shown only to the owner
  defect: str | None                      # e.g. "404 Not Found on https://..." or "no booking link"
  notes: list[str]                        # e.g. "keyword already in tags, not in description"

InsertionOp
  op_id: str
  kind: Literal["insert", "link_fix"]     # link_fix is the only non-additive op: exact span replace of a broken URL
  position: Literal["top_line", "after_first_paragraph", "before_hashtag_line", "before_links_block", "append"]
  anchor: {strategy, anchor_excerpt (<=80 chars), anchor_hash, char_offset_hint}
  old_text: str | None                    # link_fix only
  text: str                               # exact text the user pastes, <= max_op_chars
  text_bytes: int
  keywords_added: list[str]
  hashtags_added: list[str]
  claim_level: Literal["L0_navigational","L1_context","L2_coverage","L3_applicability"]
  trigger_ids: list[str]
  phrased_by: Literal["template", "llm"]

DescriptionRefresh
  id: str
  client_id: str
  video_id: str
  run_id: str
  triggers: list[RefreshTrigger]
  operations: list[InsertionOp]           # <= max_ops_per_video, merged and ordered
  advisories: list[Advisory]              # non-text suggestions: risky title year, chapters without transcript, localizations parity
  suggested_tags: list[str]               # optional, misspellings/aliases only
  keywords_added: list[str]
  preview_after: str                      # rendered at read time from current VideoRecord, not persisted (retention)
  inserted_bytes: int
  resulting_bytes: int
  expected_effect: str                    # honest text, no numeric promise unless own-history evidence
  confidence: Confidence                  # see 5.8
  priority: float                         # internal ordering only
  expires_at: datetime
  status: Literal["proposed","dismissed","applied_claimed","applied_detected","expired","withdrawn","superseded"]
  description_hash_at_generation: str
  review_required: bool                   # true for regulated domains and L1 news context
  generated_at: datetime

Advisory
  kind: Literal["title_stale_year","chapters_needed","localizations_not_updated","too_many_hashtags","invalid_chapters","shorts_links_not_clickable","manual_review"]
  message: str
  risk: Literal["low","medium","high"]

RefreshImpact
  refresh_id: str
  applied_at: date
  applied_source: Literal["user", "detected"]
  pre_window_days: int; post_window_days: int
  pre_velocity: float; post_velocity: float; ratio: float
  control_ratio: float | None             # same-period ratio of the client's comparable non-refreshed videos
  adjusted_ratio: float | None            # ratio / control_ratio
  owner_search_views_pre/post: int | None # OAuth only
  verdict: Literal["insufficient_data","no_clear_change","up","down","confounded"]
  caveats: list[str]
  computed_at: datetime                   # TTL 30 days (derived from API data)
```

## 4. Internal design

```
services/refresh/
  eligibility.py        BusinessEligibilityEvaluator, VideoGate
  evidence.py           VideoContentEvidenceBuilder, DescriptionParser, BoilerplateMiner
  keywords.py           KeywordNormaliser, KeywordPresence (variant/alias/hashtag-aware)
  triggers/
    base.py             TriggerDetector Protocol, TriggerRegistry
    news_entity.py      stale_year.py  buyer_question_gap.py  missing_cta.py  missing_contact.py
    missing_chapters.py missing_hashtags.py  broken_link.py  above_fold.py
  demand.py             DemandEvidenceCollector (YouTube-only evidence from cache)
  scoring.py            RefreshScorer (pure functions)
  ops/
    planner.py          OperationPlanner (trigger -> InsertionOp skeleton + position)
    phrasing.py         InsertionPhraser (templates + LLM via component 10)
    validator.py        InsertionValidator (truth, policy, medical, bytes, stuffing, no-copy)
    merge.py            RefreshMerger (one DescriptionRefresh per video)
    apply.py            apply_ops(description, ops) -> str (pure, invariant-tested)
  links/
    checker.py          LinkChecker (async httpx, SSRF guard, per-domain limiter)
    classify.py         malformed / dead / soft_404 / redirect / unverifiable / ok
  policy/
    claim_rules.py      ClaimLevelPolicy, RegulatedDomainPolicy (healthcare pack: India)
    lexicons/           blocked claim terms per domain_kind and language
  impact.py             AppliedDetector, ImpactCalculator
  lifecycle.py          RefreshLifecycle (expiry, retraction, supersession, revalidation)
  mapping.py            to_recommendation_draft(DescriptionRefresh) -> RecommendationDraft
  stage.py              RefreshStage (pipeline Stage implementation)
```

Key Protocols (ports, signatures only):

```python
class TriggerDetector(Protocol):
    kind: TriggerKind
    applies_to: set[Literal["long", "short"]]
    keyword_trigger: bool                      # gated by business eligibility if True; hygiene otherwise
    def detect(self, video: VideoRecord, evidence: VideoContentEvidence, ctx: RefreshContext) -> list[RefreshTrigger]: ...

class InsertionTemplate(Protocol):
    trigger_kind: TriggerKind
    claim_level: ClaimLevel
    def render(self, trigger: RefreshTrigger, evidence: VideoContentEvidence, ctx: RefreshContext) -> InsertionOp | None: ...

class DomainPolicy(Protocol):
    domain_kind: str                           # "healthcare", "finance", "none"
    allowed_trigger_kinds: set[TriggerKind]
    def violations(self, text: str, evidence: VideoContentEvidence, lang: str) -> list[str]: ...

class LinkProbe(Protocol):
    async def probe(self, url: str) -> LinkStatus: ...
```

SOLID notes:
- **Open/closed:** new trigger types (e.g. `seasonal_awareness_day`, `price_change`) are new `TriggerDetector` + `InsertionTemplate` classes registered in `TriggerRegistry`; no change to scorer/merger. New regulated domains are new `DomainPolicy` packs.
- **Single responsibility:** detection, scoring, phrasing, validation, merging are separate; scorer and `apply_ops` are pure and fixture-testable.
- **Dependency inversion:** stage depends on ports (`LLMClient` via component 10's writer, `LinkProbe`, repositories), injected from the container (01).
- `RefreshContext` is a frozen snapshot of everything the stage needs (profile, eligibility, metrics, news signals, settings, now) — no repository calls inside detectors.

Stage flow (inside `RefreshStage.run`):
1. Load `RefreshContext` (client's own VideoRecords, metrics, TopicMatches, NewsSignals, profile, previous refreshes).
2. `BusinessEligibilityEvaluator` → `RefreshEligibility` (persist; emit RunEvent "refresh: decision=hygiene_only, 3 evergreen videos").
3. `VideoGate` → candidate set.
4. For each candidate: `VideoContentEvidenceBuilder` → detectors (keyword detectors only if decision ∈ {periodic, active} and cadence due).
5. `DemandEvidenceCollector` fills `demand` + reference videos per keyword (cached, no new search.list by default).
6. `RefreshScorer` → priority; caps; per-keyword top-N.
7. `OperationPlanner` → `InsertionPhraser` → `InsertionValidator` (retry/fallback) → `RefreshMerger`.
8. `RefreshLifecycle` reconciles with existing refreshes (dedupe, supersede, withdraw retracted, detect applied).
9. `mapping.to_recommendation_draft` → handed to 07's quality gates / 10's writer for card wording.

## 5. Algorithms & logic

All thresholds live in `RefreshSettings` (pydantic-settings, per-client overridable in `refresh_settings`). Defaults below.

### 5.1 What the research says, and how it constrains us

| Fact (source) | Consequence for design |
|---|---|
| Search relevance uses "title, tags, description, and video content" ([answer/16090438](https://support.google.com/youtube/answer/16090438)) | Description keyword insertion is a legitimate lever; "video content" is also matched, so a keyword the video does not cover will not earn durable ranking and risks misleading viewers. |
| "Identify 1-2 main words that describe your video and feature them prominently in both your description and title"; "first few lines … what viewers will see first"; "each video has a unique description"; max 5000 characters ([answer/12948449](https://support.google.com/youtube/answer/12948449)) | Max **3 new keywords per video**; above-the-fold trigger; never add the same sentence across many videos. |
| "Tags play a minimal role in your video's discovery"; useful "if the content of your video is commonly misspelled"; "Adding excessive tags to your video description is against our policies on spam, deceptive practices, and scams" ([answer/146402](https://support.google.com/youtube/answer/146402)) | Tag suggestions are optional, limited to aliases/misspellings; never a keyword list line in the description. |
| Hashtags: "up to three hashtags that are considered most engaging will appear by your video title"; >60 hashtags → all ignored; "Don't add hashtags that are not directly related"; "adding ordinary descriptive tags or repetitive sentences in the description is (still) prohibited" ([answer/6390658](https://support.google.com/youtube/answer/6390658)) | Owner brief said "first 3 above title": **docs say "most engaging", not "first"** — we do not promise placement. Add ≤3 hashtags, only from covered terms; never if existing ≥ 15; warn if > 60. |
| Chapters: first timestamp 00:00, ≥3 timestamps ascending, min 10 s each ([answer/9884579](https://support.google.com/youtube/answer/9884579)) | Chapter text generated only with owner transcript; validator enforces the three rules. |
| Spam policy covers "content, metadata, or behavior designed to take advantage of the YouTube community"; "Malicious clickbait: using maliciously misleading titles, thumbnails, descriptions, or imagery"; "Automated or synthetic mass-production … high volumes of similar content with minimal changes" ([answer/2801973](https://support.google.com/youtube/answer/2801973), current version lists 9 categories) | Insertions must be truthful to the video; no templated identical lines across dozens of videos; per-keyword cap; no news hook claiming the video covers something it doesn't. |
| YouTube's "egregious clickbait" enforcement started in **India**, focused on breaking news/current events titles and thumbnails that make claims the video doesn't deliver ([Google India blog, Dec 2024](https://blog.google/intl/en-in/products/platforms/strengthening-enforcement-against-egregious-clickbait-on-youtube/)) | Highly relevant to Apollo (India) and to every news-driven insertion: news goes only in the description, framed as dated context; titles are never auto-changed. |
| Medical misinformation policy: no content contradicting local health authority guidance on prevention/treatment ([answer/13813322](https://support.google.com/youtube/answer/13813322)) | Healthcare insertions never contain health claims (§5.7). |
| External links policy forbids links to malware, phishing, porn, egregious harm ([answer/9054257](https://support.google.com/youtube/answer/9054257)) | We only insert links from the client's own verified domain (crawled by 03). |
| Shorts: up to 3 min, square/vertical ([answer/15424877](https://support.google.com/youtube/answer/15424877)); links in Shorts descriptions are not clickable ([answer/13748639](https://support.google.com/youtube/answer/13748639)) | No CTA link ops on Shorts; advisory to use Studio's related-video link instead. |
| `snippet.description` max **5000 bytes**, no `<` or `>` ([videos resource](https://developers.google.com/youtube/v3/docs/videos)) | Byte budget, not character budget (Help says 5000 characters — we use the stricter bytes; Devanagari/Tamil are 3 bytes/char in UTF-8). Validator strips/rejects `<` `>`. |
| `captions.download`: 200 units, "requires the user to have permission to edit the video"; `captions.list`: 50 units, OAuth scopes `youtube.force-ssl`/`youtubepartner` ([download](https://developers.google.com/youtube/v3/docs/captions/download), [list](https://developers.google.com/youtube/v3/docs/captions/list)) | **Verified: non-owners cannot get transcripts.** Public mode (Apollo demo) builds evidence from title, description, chapters, tags only. |
| Above-the-fold truncation length: **no official number**. Help only says check how it appears on search/watch/mobile. Third-party figures: ~100 chars mobile, ~150–157 desktop (unverified, inconsistent) | `above_fold_chars` default 150 with a secondary 100-char mobile check; label as heuristic in the UI. |

### 5.2 Establishing what the video covers (`VideoContentEvidence`)

1. **Boilerplate mining (per channel):** split every cached description of the channel into lines; normalise (casefold, collapse whitespace, strip URLs' UTM); a line that appears in ≥ `boilerplate_min_share` (0.3) of the channel's descriptions is boilerplate. Contiguous boilerplate lines form the footer/links block. Apollo sample: 49/50 descriptions share a `____` separator + "Apollo Hospitals is the leading multi-specialty…" + social links → detected as boilerplate, excluded from `covered_terms` (it's about the hospital group, not the video).
2. **Parse structure:** paragraphs (blank-line separated), separator lines (`^[_\-=—*]{5,}$`), hashtag line (line where ≥ 60% tokens start with `#`), URL lines, timestamps (`^\s*(\d{1,2}:)?\d{1,2}:\d{2}\b`) → chapters, validity per §5.1.
3. **Covered terms:** normalised 1–4 grams from title, non-boilerplate description, chapter labels, tags, hashtags (camel-case split: `#DiabeticFootCare` → "diabetic foot care"), plus owner transcript n-grams when OAuth + captions exist (`sources_used` records which). Stopwords removed for 1-grams only.
4. **Covered topics:** TopicMatches from 05 with score ≥ `fit_threshold` (0.6). This is the only "what is it about" signal; we do not use `topicDetails.topicCategories` (YouTube's Wikipedia categories) to stay clear of the "no inferring content category" guardrail and because they are too coarse.
5. **Owner confirmation:** the UI may let an OAuth-connected owner tick "I confirm this video's content applies to X" — recorded as `owner_confirmation` evidence, enabling L3 for that op only.

### 5.3 Claim levels (truthfulness contract)

| Level | What the line asserts | Allowed when | Example |
|---|---|---|---|
| L0 navigational | Where to go / how to act | Link/phone exists in `BusinessProfile.conversion_actions` with `source_url` on the client's own domain | "Book a consultation with an Apollo endocrinologist in Hyderabad: https://www.apollohospitals.com/…" |
| L1 context | An external, dated fact, explicitly not claimed as video content | NewsSignal status=active, ≥1 source, trigger relevance R ≥ 0.6, entity not a competitor brand | "Update (Nov 2026): Claude Fable 5.2 is now available. This video was recorded before its release." |
| L2 coverage | What the video covers | Every content term ∈ `covered_terms` | "Diabetic foot care: daily checks and warning signs to watch for." |
| L3 applicability | The video's content applies to the new thing | Owner transcript contains supporting terms **or** owner confirmation | "Update (Nov 2026): Claude Fable 5.2 is now available — the workflow shown here applies to it." |
| L4 factual/medical/financial claim | Efficacy, outcomes, prices, statistics | **Never generated** | — |

In public mode (no OAuth, the Apollo demo), L3 is never produced; the owner's AI-company example would be phrased at L1 ("…recorded before its release; the steps may differ.") with an `owner_confirmation` upsell if they connect OAuth.

### 5.4 Business-level eligibility

Inputs: `FreshnessProfile`, active `NewsSignal`s for the client in last 30 days with relevance ≥ `news_relevance_min` (0.5), the client's own `VideoMetrics`.

```
news_prior = {low: 0.1, medium: 0.5, high: 0.9}[freshness.news_velocity_prior]
news_count_score = min(1, active_relevant_signals_30d / news_target)          # news_target = 6
news_component = 0.5 * news_prior + 0.5 * news_count_score

evergreen_video = public, not live, age_days >= evergreen_min_age (90),
                  velocity_recent >= max(velocity_floor_abs (5/day), velocity_floor_rel (0.5) * channel median velocity for its age band)
evergreen_view_share = Σ velocity(evergreen videos) / Σ velocity(all public videos with velocity data)

buyer_gap_rate = share of evergreen videos with >=1 buyer_question_gap trigger candidate (cheap pre-check: keyword absence only)
hygiene_defect_rate = share of earning videos (velocity >= floor) with a broken_link or missing_cta defect

keyword_refresh_need = 0.45 * news_component + 0.35 * evergreen_view_share + 0.20 * buyer_gap_rate
```

Decision:
| Condition | decision | cadence_days | Effect |
|---|---|---|---|
| No public video passes velocity floor, or own channel unresolved | `none` | 30 | Nothing shown except "Your videos aren't earning steady views right now, so edits won't pay off. We'll check again in 30 days." |
| need < 0.35 | `hygiene_only` | 30 | Only hygiene triggers (links, CTA, contact, chapters advisory, hashtags) |
| 0.35 ≤ need < 0.65 | `periodic` | 14 | All triggers; news triggers require NewsSignal relevance ≥ 0.7 |
| need ≥ 0.65 | `active` | 3 | All triggers |

Overrides: `refresh_settings.mode = off | hygiene_only | auto` (default auto). `regulated_domain` caps decision at `periodic` and forces `review_required`. Cadence means keyword detectors run only if `now - last_keyword_eval >= cadence_days` or a new NewsSignal with relevance ≥ 0.8 arrived; daily runs still revalidate existing suggestions.

Velocity basis: on onboarding day we usually have one snapshot. `velocity_recent` then falls back (component 06) to `lifetime_avg = views / age_days`, which overstates evergreen-ness for old viral videos. We mark `velocity_basis = lifetime_avg_provisional`, apply a 0.5 multiplier to `evergreen_view_share`, cap decision at `periodic`, and say so: "Based on lifetime averages; we'll confirm once we have 7 days of snapshots." From day 7, `velocity_recent` uses snapshots (Δviews/Δdays over the latest span ≥ 7 days).

Expected outcomes (to sanity-check thresholds in tests): dental clinic (prior low, no news, 3 evergreen videos) → `hygiene_only`; AI tools company after a model launch (prior high, 8 signals, evergreen share 0.4) → 0.45·0.95 + 0.35·0.4 + 0.2·0.3 = 0.63 → `periodic`, `active` with more evergreen share; Apollo (healthcare, prior medium-low, regulated) → likely `hygiene_only` or `periodic` — demo shows the reasoning either way.

### 5.5 Video-level gate (hard filters, all must pass)

1. `privacy_status == public` (unlisted/private: skip; we can't see private videos anyway).
2. `live_broadcast_content == none` (Apollo sample: 2 `upcoming` with duration `P0D` → skipped until they end; for ended live streams `liveStreamingDetails` exists → treated as event videos, no stale-year trigger).
3. `age_days >= min_age_days` (21; `news_entity` uses 14; hygiene triggers 2 — a broken link on a 3-day-old video is worth fixing).
4. velocity ≥ floor (§5.4) — except `broken_link` which applies to any video with `views >= 100`.
5. Not applied within `cooldown_days` (45), no same `dedupe_key` dismissed within `dismiss_snooze_days` (30), not in `refresh_settings.excluded_video_ids`.
6. Description fetched ≤ `max_description_age_days` (2) — otherwise refetch via batched `videos.list` (1 unit / 50 ids).
7. Business fit: max TopicMatch score ≥ `fit_threshold` (0.6) for keyword triggers. Videos with no fit (e.g. Apollo's "Apollo Aarambh – Model Anganwadi Centre" CSR launch) get **hygiene triggers only**, never keyword or CTA-to-booking ops (a booking link under a CSR video is off-topic; a broken link fix is still fine).
8. Format: Shorts = `duration_s <= 180` (and, if 02 fetches `player` embed dimensions, height > width; otherwise ≤ 60 s is certain, 61–180 s "likely Short"). Shorts allow only `missing_hashtags`, `broken_link` (advisory, since links aren't clickable), `news_entity` at half weight.

### 5.6 Trigger detectors

Keyword presence check (`KeywordPresence.in_description(keyword, aliases)`): NFKC, casefold, strip diacritics, collapse punctuation/hyphen/space differences ("Fable-5.2" = "Fable 5.2"), hashtag split, alias list from NewsSignal/TopicRef, plural/singular for English. Presence in boilerplate counts as present. Presence in tags only = absent (noted in evidence).

| Kind | Keyword-gated? | Detection | R (strength) | D (demand) | U (urgency) |
|---|---|---|---|---|---|
| `news_entity` | yes | NewsSignal active; signal.topic_ref_ids ∩ video.covered_topic_refs ≠ ∅; entity absent from description; entity not `is_competitor_entity`; video published before `event_date` | max TopicMatch score on shared topic refs | §5.6.1 using 08's `demand_videos` + niche cache | `exp(-days_since_event / 7)` |
| `stale_year` | yes | year Y < current year in title or first paragraph; not in event/date context (regex excludes "on 24 Nov 2025", "since 2019", "est.", "©", ranges); video evergreen (age ≥ 120 d, velocity ≥ floor); today ≥ Jan 15 | 1.0 if in title, 0.7 if description only | 0.5 baseline + 0.5 if niche refs use the current year with the same TopicRef | 1.0 |
| `buyer_question_gap` | yes | video matches BuyerQuestion BQ; ≥ `min_refs` niche/competitor videos in window that match BQ contain phrase K in title/description and have outlier_ratio ≥ 1.5; K absent from video's description; K's content terms ⊂ covered_terms (otherwise video doesn't cover it → no trigger) | TopicMatch score for BQ | §5.6.1 | 0.6 |
| `missing_cta` | no | long-form, fits business, no URL in description whose normalised host+path matches any `ConversionAction(kind in book/signup/contact)`; best action chosen by offering/location match (§5.6.2) | 1.0 if location/offering match, 0.7 generic | 0 (hygiene) | 0.8 |
| `missing_contact` | no | regulated or local business; no phone/WhatsApp present; a `call`/`whatsapp` ConversionAction exists on the client's site | 0.6 | 0 | 0.5 |
| `missing_chapters` | no | long-form ≥ `chapters_min_duration_s` (480); no valid chapters | 0.6 | 0 | 0.4 |
| `missing_hashtags` | no | 0 hashtags in title+description; ≥1 covered term maps to a client keyword | 0.5 | 0 | 0.3 |
| `broken_link` | no | §5.9 status ∈ {malformed, dead, soft_404} | 1.0 | 0 | 1.0 |
| `above_fold_keyword` | yes | primary keyword (top TopicRef keyword matched, or title head phrase) not in first `above_fold_chars` (150) of description; or first 150 chars are an expired event notice / boilerplate | 0.7 | 0.3 + refs-based | 0.4 |

#### 5.6.1 Demand evidence D (YouTube data only)
```
refs(K) = videos in TrendingReport/niche cache (competitors + niche search, already cached by 02/06),
          published within client window, whose title or description contains K (presence check above),
          excluding the client's own channel
outlier_refs = [v in refs if v.outlier_ratio >= ref_outlier_min (1.5)]
D = 0.6 * min(1, len(outlier_refs) / min_refs) + 0.4 * min(1, distinct_channels(refs) / 3)
if OAuth and K (or alias) ∈ owner search terms for this video (or channel): D = min(1, D + 0.3)
```
No new `search.list` calls by default: news demand videos come from 08's cached demand check; niche refs from 02's shared cache. `refresh_settings.allow_search_calls` (default 0/day) can permit up to 2 targeted `search.list` calls per client per day for the top news keyword when refs are thin — counted against the shared search bucket.

Window rule: reference videos must be published inside the client's `window_days`. If fewer than `min_refs` (3), the trigger is retained internally but fails the evidence gate (§5.8) and produces the thin-data message.

#### 5.6.2 Choosing the CTA
Score each `ConversionAction`: +0.5 if its `offering_ids` intersect offerings mapped to the video's TopicRefs; +0.3 if its `location_id` matches a location string found in the video's title/description (Apollo descriptions name the hospital: "Apollo Hospitals, Financial District, Hyderabad"); +0.2 if kind=book. Highest wins; ties → the generic booking page. The URL is used exactly as crawled (optionally UTM-tagged, §5.10). The action's `source_url` is retained as provenance.

### 5.7 Healthcare / regulated-domain policy (applies when `FreshnessProfile.regulated_domain`)

Allowed trigger kinds: `broken_link`, `missing_cta`, `missing_contact`, `missing_chapters` (owner transcript only), `missing_hashtags`, `above_fold_keyword` (L2 only), `buyer_question_gap` (L2 only), `stale_year` (advisory + link to newer own video only), `news_entity` only when the entity is a **non-clinical** event (e.g. "World Diabetes Day 2026", a new Apollo centre opening, a government scheme name) and only at L1 with a source from an official body or the client's site; never a drug/device/treatment entity.

Validator blocks (in any language pack we support):
- Efficacy/outcome words: cure, cures, guaranteed, 100%, permanent, miracle, no side effects, painless, risk-free, success rate, best, No. 1, top, leading, cheapest, lowest cost, before/after (superlatives allowed only if the exact phrase already exists in the description, e.g. Apollo's own footer).
- Disease + cure/treat/prevent combinations for the conditions scheduled under India's [Drugs and Magic Remedies (Objectionable Advertisements) Act, 1954](https://www.indiacode.nic.in/handle/123456789/1412?locale=en) (schedule list stored as data; marked for legal review).
- Numbers not present in evidence (prices, percentages, counts), doctor names/credentials not already in the description, testimonials.
- Any sentence not classifiable as L0/L1/L2.
Every regulated suggestion carries `review_required = true` and the card says "Have your medical/compliance team review before publishing." Existing disclaimers (Apollo: "This video is for general awareness and does not replace medical advice…") must remain; insertions go before the disclaimer, never between the disclaimer and the content it qualifies. Other Indian frameworks referenced for review (not machine-checked): Consumer Protection Act 2019 and CCPA misleading-ad guidelines 2022 (via [CMS India guide](https://cms.law/en/int/expert-guides/cms-expert-guide-to-advertising-of-medicines-and-medical-devices/india)); NMC professional-conduct rules on advertising — status not verified, flagged in §13.

### 5.8 Scoring, caps, confidence, quality gates

```
V = percentile rank of velocity_recent among gated videos of the channel (0..1)
priority = 100 * (0.35*V + 0.30*R + 0.25*D + 0.10*U)        # hygiene triggers: D term replaced by V (weight 0.60 total on V)
show if priority >= 50 (keyword triggers) or >= 30 (hygiene)
```
Caps (defaults): max 10 `DescriptionRefresh` per client per run; per `dedupe_key` (same keyword) max 5 videos, chosen by `V*R` (note on card: "12 more videos match; showing your 5 highest-traffic ones"); per video max 3 ops, max `max_inserted_chars` 300 total, max `max_op_chars` 200 per op, max 3 new keywords, max 3 new hashtags.

Confidence (type-specific, honest):
- Keyword triggers: `High` = ≥5 outlier refs across ≥3 channels and R ≥ 0.75 and velocity_basis=snapshots; `Medium` = ≥3 refs across ≥2 channels; `Low` = otherwise → fails gate unless OAuth search-term evidence exists. `outliers`/`channels` counts carried into the contract.
- Hygiene triggers: label `Rule-based` with `outliers = 0, channels = 0` — the evidence is the defect itself (proposed contract addition, §13).

Quality gates for `description_refresh` (applied by 07's gate runner with our type config):
| Gate | Keyword triggers | Hygiene triggers |
|---|---|---|
| Evidence first | ≥ `min_refs` (3) reference videos inside window using the keyword, each with link, channel, published_at, views_day7, outlier_ratio | Defect evidence present (HTTP status / missing element), refs not required |
| Numbers computed | All numbers in why_it_works come from scorer/metrics; validator diff | same |
| Fair comparisons | V is within own channel; refs judged on own-channel outlier ratio | n/a |
| Fit to client | R ≥ 0.6 (TopicMatch) | CTA chosen by offering/location match |
| Honest confidence | as above | `Rule-based` |
| Thin data | fail → "Only 1 video in the last 7 days uses 'Claude Fable 5.2'. Widen to 30?" | n/a |
| No copying | insertion text has no 6-gram overlap with any reference video title/description; never repeats an existing line of the same description | same |
| Truthfulness (new) | claim level permitted by evidence (§5.3) | L0 provenance check |
| Policy (new) | no stuffing (§5.8.1), domain policy, byte budget, no `<`/`>` | same |

"Reference videos" for this type therefore means: **recent niche/competitor videos (inside the window) that use the keyword and performed above their own channel's normal** — evidence that the keyword has search demand right now. The client's video being refreshed is the *subject*, shown separately with its own velocity, never as a reference.

#### 5.8.1 Anti-stuffing rules
- After insertion, any keyword occurs ≤ 2 times in the description (excluding hashtags).
- No inserted line with ≥ 4 comma/pipe-separated noun phrases and no verb (keyword-list detector).
- Same normalised inserted sentence suggested on ≤ 3 videos per channel per 30 days (cross-suggestion similarity, token Jaccard ≥ 0.8 counts as same) — guards "repetitive sentences" and "mass-production" policy language.
- Hashtags total after insertion ≤ 15.

### 5.9 Link checking
1. Extract URLs (regex tolerant of trailing `)`, `.`, `,`, em-dash prefixes like `Facebook—https://…`).
2. **Syntactic first (no network):** nested scheme (`/https://` inside path), whitespace, invalid host, duplicated domain → `malformed`. Apollo `HI8fFZEfrRA` is caught here. Fix op = `link_fix` with `old_text` = malformed URL, `text` = repaired URL only when the repair is unambiguous (text after the last embedded scheme) and the repaired URL probes `ok`.
3. Network probe (`LinkChecker`): http/https only; resolve DNS and refuse private, loopback, link-local, CGNAT and metadata IP ranges (SSRF guard, re-checked on every redirect hop); `HEAD`, fallback `GET` (stream, close after headers) on 405/501; timeout 5 s; ≤ 5 redirects; no cookies; UA `GrowthHackerLinkCheck/1.0`; per-domain limiter 1 request / 2 s, global concurrency 5; max 200 probes per client per run; robots.txt disallow → `unverifiable`.
4. Classify: 404/410, NXDOMAIN, TLS failure, 5xx on two probes ≥ 24 h apart → `dead`; deep path redirecting to site root or a page titled like "not found" → `soft_404` (GET only for the client's own domain); 301/308 to a different URL → `redirect` (info, e.g. twitter.com → x.com; no suggestion unless the client wants it); 401/403/429/999 (LinkedIn), timeouts → `unverifiable` (never flagged as broken).
5. Cache by normalised URL (UTM stripped) for 7 days in `link_check_cache`, shared across clients (this is web data, not YouTube data).
6. Probe budget prioritises non-boilerplate links and links on high-V videos; boilerplate links are probed once per channel (Apollo: 4 social URLs cover 49 videos).

### 5.10 Operation planning, phrasing, validation, merge

Position rules (deterministic, by trigger kind):
| Trigger | Position | Rationale |
|---|---|---|
| `above_fold_keyword` | `top_line` | Must be in the first lines |
| `news_entity` | `after_first_paragraph` (default) or `top_line` if `refresh_settings.news_on_top` | Visible but doesn't displace the video's own summary |
| `buyer_question_gap` | `after_first_paragraph` | Reads as part of the summary |
| `missing_cta`, `missing_contact` | `before_links_block` (before boilerplate/separator); if none, `before_hashtag_line`; else `append` | Next to other links, above the fold of the footer |
| `stale_year` (newer-video link) | `after_first_paragraph` | |
| `missing_hashtags` | `append` on a new last line (or merged into existing hashtag line if present) | |
| `missing_chapters` (OAuth) | `before_links_block` as a block | |

Anchors: insert only at **line boundaries**. `anchor_excerpt` = the 80 chars before the insertion point; `anchor_hash` = sha256 of the full line; `char_offset_hint` = offset in the description at generation. If the description changed since generation (`description_hash` differs), re-anchor by excerpt; if not found, regenerate on next run and mark stale.

Phrasing:
1. Template first (per trigger kind, per supported language), e.g.:
   - `news_entity` L1: `Update ({mon_yyyy}): {entity} is now available. This video was recorded before its release.`
   - `news_entity` L3: `Update ({mon_yyyy}): {entity} is now available — the {covered_noun} shown here applies to it.`
   - `missing_cta` L0: `{action_label}: {url}` with `action_label` from the crawled ConversionAction or `Book an appointment`.
   - `buyer_question_gap` L2: `{covered_summary_phrase} — {K}.`
   - `stale_year` (newer own video exists): `Looking for the latest? Watch our {current_year} update: https://youtu.be/{newer_video_id}`
2. LLM (component 10, tier `claude-haiku-4-5`) may rephrase only `news_entity`, `buyer_question_gap`, `above_fold_keyword` lines into natural language matching the description's tone, via structured output `{text: str}` with inputs: template line, allowed terms (covered_terms ∪ {K} ∪ {entity, aliases, month-year}), language, max chars, claim level, forbidden terms. The LLM never sees or emits URLs (URLs are appended by code).
3. `InsertionValidator`: (a) contains K exactly (or allowed alias); (b) every content token ∈ allowed terms (stopwords excluded); (c) numbers ⊂ allowed numbers (month-year, version strings from entity); (d) claim level classifier (rule-based patterns: "covers", "shows", "explains", "applies", "works with" → L3; dated "Update (…)" with "recorded before" → L1) ≤ permitted level; (e) domain policy; (f) anti-stuffing; (g) no `<`/`>`; (h) bytes within budget; (i) language detected == description language; (j) no 6-gram overlap with refs. Fail → one retry with violation list → template fallback → drop op (logged).
4. Merge (`RefreshMerger`): one `DescriptionRefresh` per video; ops ordered by position then priority; same-position ops concatenated as separate lines; overlapping entities (e.g. "Claude Fable 5.1" and "Claude Fable 5.2") keep the newest (`superseded_by`); a `top_line` op and an `above_fold_keyword` op collapse into one line containing both keywords if ≤ 200 chars, else keep the higher priority; total caps from §5.8 enforced by dropping lowest-priority ops; `link_fix` and CTA always kept (independent).
5. Byte budget: `resulting_bytes = len(utf8(description)) + Σ len(utf8(op.text + newline_style))` must be ≤ 5000 − `byte_margin` (100). If over: drop lowest-priority ops; if the description alone is ≥ 4900 bytes, emit advisory only ("Your description is at the 5000-byte limit; to add this line, remove something first") and no ops.
6. UTM (`refresh_settings.utm_tagging`, default off): append `utm_source=youtube&utm_medium=video_description&utm_campaign={video_id}` only to links we insert, only if they have no `utm_*` already; preserve existing parameter order and fragments; equality checks strip `utm_*`.
7. `apply_ops(description, ops) -> str` is pure: inserts text at line boundaries, preserves original newline style, emojis (no grapheme splitting since we cut only at `\n`), whitespace and existing links byte-for-byte. Invariant: for `insert` ops, the original description is a subsequence of the output with only whole lines added.

### 5.11 Expected effect (honest)
- Default text: "Adds '{K}' where search can match it. We can't predict a number: description edits change relevance, not demand. We'll compare this video's daily views for 14 days before and after you apply it."
- Only if the client has ≥ `min_own_history` (3) measured past refreshes of the same trigger kind with verdicts within retention: "Your last {n} keyword refreshes changed daily views by {min}x–{max}x vs comparable videos (indicative)." → used as `expected_range_vs_baseline`; otherwise that field = "Not estimated (no comparable refresh history)".
- Hygiene: "Fixes a link viewers can't use" / "Gives viewers who want to book a direct link" — no view claims.

### 5.12 Applied detection & impact measurement
- **User mark:** "Applied on {date}" (default today) → `applied_claimed`.
- **Auto-detect:** daily refetch of the client's own descriptions (02) → if all `keywords_added` (or ≥ 80% token overlap of an op's text) now present → `applied_detected` with `applied_at` = first snapshot date where seen. Mismatch case: user says applied but not visible after 2 days → "We don't see the new line in the public description yet."
- **Impact** (computed at `post_window_days` = 14 after apply; recomputed at 21 if snapshots allow):
  ```
  pre_velocity  = (views[t0] - views[t0 - 14d]) / 14
  post_velocity = (views[t0 + 14d] - views[t0]) / 14
  ratio = post_velocity / pre_velocity
  control set = client's public videos, same age band (±50%), same format, not refreshed in ±30d, velocity >= floor (min 5 videos)
  control_ratio = median over control of (post/pre) for the same dates
  adjusted_ratio = ratio / control_ratio
  verdict: insufficient_data if pre_velocity < floor or missing snapshots or control < 5;
           confounded if another refresh applied on this video or a title/thumbnail change detected within window;
           up if adjusted_ratio >= 1.2; down if <= 0.83; else no_clear_change
  ```
  OAuth adds `owner_search_views_pre/post` from YouTube Analytics (traffic source `YT_SEARCH`), shown separately — the cleanest signal for a description change.
- Caveats always shown: natural decay of older videos; news itself raises searches for the topic regardless of our edit; seasonality (e.g. World Diabetes Day spikes); YouTube system changes; small numbers; correlation not causation.
- Retention: snapshots older than 30 days are purged (01), so the 14+14-day comparison must be computed by day 14–16 after apply; the computed `RefreshImpact` is a derived metric from API data → stored with a 30-day TTL and recomputed or dropped (see §13 on Developer Policies III.E.4.h).

### 5.13 Lifecycle
- `expires_at`: news_entity = min(signal.expires_at, generated_at + 21 d); keyword = +30 d; hygiene = +30 d (all ≤ 30 days because suggestion evidence is API data).
- Daily revalidation: description changed → re-check presence (auto-applied/superseded), re-anchor; video went private/deleted → withdraw; NewsSignal `retracted` → withdraw proposals, and for already-applied ones raise a **correction alert**: "The news you added on {date} ({entity}) was retracted per {source}. Consider removing: '{line}'."; `disputed` → hide new proposals, add caution to applied; `superseded` → regenerate with newer entity.

## 6. Data model

Tables (ORM in 01's style; all with `client_id` tenant column and RLS/tenant filters per 01):

`refresh_eligibility`
- id PK, client_id, run_id, decision (enum), cadence_days, keyword_refresh_need (numeric), components jsonb, evergreen_video_count, eligible_video_count, velocity_basis, reasons jsonb, computed_at, last_keyword_eval_at
- index (client_id, computed_at desc). Retention: 90 days (our own decision data; contains only derived aggregates → TTL 30 days to be safe, see §13).

`description_refresh`
- id PK, client_id, video_id, run_id, recommendation_id FK nullable, status enum, priority numeric, confidence jsonb, triggers jsonb (kind, keyword, R/D/U, evidence ids, defect, news source URLs), operations jsonb (InsertionOp without any copied description text beyond `anchor_excerpt` ≤ 80 chars), advisories jsonb, suggested_tags text[], keywords_added text[], dedupe_keys text[], description_hash_at_generation, review_required bool, expires_at, generated_at, applied_at date null, applied_source enum null, dismissed_reason text null, updated_at
- indexes: (client_id, status, priority desc); (client_id, video_id, status); GIN(dedupe_keys); (expires_at)
- Retention: rows containing anchor excerpts/evidence from API data → hard expiry ≤ 30 days after `generated_at` unless `applied`; applied rows keep only our own fields (video_id, op text we generated, applied_at, status) — API-derived fields nulled at 30 days.

`refresh_impact`
- refresh_id PK/FK, pre/post fields as §3.2, verdict, caveats jsonb, computed_at; TTL 30 days.

`link_check_cache` (shared, not tenant-scoped)
- url_normalised PK, status_class enum, http_status int null, final_url, error text, checked_at, next_check_at
- index (next_check_at). TTL 7 days.

`channel_boilerplate` (derived from API text)
- channel_id PK, line_hashes text[], computed_at; TTL 30 days, recomputed on refresh.

`refresh_feedback`
- id, client_id, refresh_id, action (dismiss/apply/edit/flag_inaccurate), reason, free_text, created_at — our own data; feeds evaluation (12).

No full description copies are stored by 09; previews are rendered from 02's cached `VideoRecord` (which 02 keeps within the 30-day rule).

## 7. External APIs & libraries

| API / lib | Use | Cost / limits | Verified |
|---|---|---|---|
| YouTube Data API `videos.list` (part=snippet,contentDetails,statistics,status,localizations[,player]) via 02 | descriptions, tags, language, localizations, privacy, live state, duration | 1 unit / call, 50 ids ([quota calculator](https://developers.google.com/youtube/v3/determine_quota_cost)) | Live 2026-11-20: tags returned for public videos when present (4/50 Apollo); `defaultLanguage=en-IN`; localizations returned (Apollo: en-IN + up to 20 languages); upcoming streams have `duration=P0D`, `liveBroadcastContent=upcoming`. `player` embed dimensions for Shorts detection: **not verified**. |
| `playlistItems.list` via 02 | uploads enumeration | 1 unit / 50 items | Live verified |
| `search.list` | optional demand top-up only | 100 units; shared ~100 calls/day bucket | Docs; not called |
| `captions.list` / `captions.download` | owner transcript (OAuth only) | 50 / 200 units; download "requires the user to have permission to edit the video" | Docs verified; not callable for Apollo |
| YouTube Analytics API `reports.query` dims `insightTrafficSourceDetail`, filter `insightTrafficSourceType==YT_SEARCH;video==ID` | owner search terms, search views pre/post | maxResults ≤ 25, sort required ([channel reports](https://developers.google.com/youtube/analytics/channel_reports)) | Docs partially verified (YT_SEARCH value per dimensions docs, not live-tested) |
| Anthropic Messages API (via 10's `LLMClient`) | phrase ≤ 3 lines per video | Haiku 4.5 $1/$5 per MTok (skill docs) | Model responds (shared context) |
| httpx (async) | link probes | our limits §5.9 | — |
| `lingua-language-detector` (or fastText lid.176 if 05 already ships it) | description language when `defaultLanguage` missing | local | Library choice to confirm with 05 |
| `regex` (Unicode-aware), `idna`, stdlib `ipaddress`, `urllib.parse` | URL extraction/normalisation, SSRF checks | — | — |

Policy sources cited: [Spam, deceptive practices & scams](https://support.google.com/youtube/answer/2801973), [Tags](https://support.google.com/youtube/answer/146402), [Hashtags](https://support.google.com/youtube/answer/6390658), [Description tips](https://support.google.com/youtube/answer/12948449), [How search works](https://support.google.com/youtube/answer/16090438), [Chapters](https://support.google.com/youtube/answer/9884579), [Shorts length](https://support.google.com/youtube/answer/15424877), [Shorts links](https://support.google.com/youtube/answer/13748639), [External links](https://support.google.com/youtube/answer/9054257), [Medical misinformation](https://support.google.com/youtube/answer/13813322), [Developer Policies](https://developers.google.com/youtube/terms/developer-policies), [India clickbait enforcement](https://blog.google/intl/en-in/products/platforms/strengthening-enforcement-against-egregious-clickbait-on-youtube/).

Unverified / heuristic: above-the-fold char count (no official figure); whether hashtag selection by title is "first three" (docs say "most engaging"); whether description edits on old videos change ranking measurably (no official statement — hence honest effect text); Shorts aspect ratio via `player` part.

## 8. Edge cases & failure modes

| Case | Detection | Handling | User-facing message |
|---|---|---|---|
| Description near 5000-byte limit | `byte_length + inserted + margin > 5000` (bytes, UTF-8) | Drop lowest-priority ops; if base ≥ 4900 B, advisory only | "This description is at YouTube's 5000-byte limit. Remove something before adding this line." |
| Multibyte scripts push over limit | bytes ≠ chars | Budget in bytes always | (same as above when triggered) |
| Description empty | `len == 0` | Only L0 CTA (if fits business) and L2 top line from title terms; no news context | "This video has no description. Start with one line about what it covers:" |
| Description is only boilerplate | all lines boilerplate | Treat as empty for coverage; insert above boilerplate | "Your description only has the standard footer. Add a line about this video above it." |
| Non-English description | `defaultLanguage` / detector ∉ `supported_languages` (default `en`) | Keyword/news ops skipped; hygiene ops only (link_fix, CTA with localized label table if available, else URL-only line) | "We only suggest wording in English right now. Link fixes below still apply." |
| Code-mixed (Hinglish) | detector confidence < 0.6 between en/hi | Treat as en, LLM disabled, templates only | — |
| Multiple languages via `localizations` | `localizations` has > 1 key | Ops apply to default-language description only; advisory `localizations_not_updated` | "This video has translated descriptions in 20 languages. Viewers in those languages won't see this line unless you update them in Studio." |
| `defaultLanguage` missing | null | Detector; if mismatch with `defaultAudioLanguage`, use description text language | — |
| Unlisted / private / deleted | `privacy_status` ≠ public or missing from `videos.list` | Gate out; withdraw existing suggestions | "This video is no longer public, so we removed the suggestion." |
| Live / upcoming / premiere | `live_broadcast_content` ≠ none | Skip until `none`; ended streams = event videos (no stale-year) | — |
| Video unrelated to the business (e.g. Apollo CSR Anganwadi launch) | max TopicMatch < fit_threshold | Hygiene only; no booking CTA, no keyword ops | "Only link fixes are suggested for videos outside your main topics." |
| Same keyword fits 50 videos | `dedupe_key` count > cap | Top 5 by V·R; card lists count | "23 videos match 'Claude Fable 5.2'. We picked your 5 with the most daily views; adding it everywhere would look like spam." |
| Same sentence repeated across videos | Jaccard ≥ 0.8 across suggestions | ≤ 3 per 30 days; LLM asked to vary per video using its own covered terms | — |
| News later retracted | NewsSignal.status = retracted | Withdraw proposals; correction alert on applied ones | "Heads-up: the news you added on 12 Nov was retracted (source). Consider removing: '…'" |
| News disputed | status = disputed | Hide new; caution on applied | "Reports about X are now disputed. We've paused this suggestion." |
| News superseded (5.2 → 5.3) | `superseded_by` | Regenerate with newer entity; old marked superseded | "Updated: a newer release replaces the one we suggested earlier." |
| Competitor brand as news entity (e.g. a rival hospital) | `is_competitor_entity` | Never inserted | — |
| News about the client itself (new centre, new service) | entity matches BusinessProfile offering/location | Allowed as L0/L1 with client's site as source; CTA to new page | — |
| Ambiguous entity token ("Fable", "Apple") | entity is a common word / alias collision | Require full canonical form ("Claude Fable 5.2"); presence check uses full form only | — |
| Keyword in tags but not description | tags contain K; description doesn't | Trigger stays; tag suggestion suppressed; note in evidence | "'X' is already in your tags, which YouTube says play a minimal role. Adding it to the description helps search." |
| Keyword already in title, not description | title contains K | R × 0.5; usually below threshold | — |
| Keyword already as hashtag only | hashtag-split presence | Counted as present (no trigger) | — |
| Competing suggestions for same video | > 1 trigger per video | Merge (§5.10.4), caps, newest entity wins | One card per video, listing each change |
| Shorts | duration ≤ 180 s (+ aspect if available) | Only hashtags / link_fix advisory / news at half weight; no CTA link ops | "Links in Shorts descriptions aren't clickable. Use the 'Related video' option in Studio to point viewers to a long video." |
| Ambiguous 61–180 s video (no aspect data) | duration band | Treat as Short for CTA (conservative), as long-form for keywords | — |
| Medical claim in LLM output | domain policy violation | Retry → template → drop | — (never shown) |
| Legal/regulatory wording (hospitals) | regulated_domain | `review_required`; whitelist triggers only; existing disclaimer preserved; insert before disclaimer | "Have your medical/compliance team review before publishing." |
| Disclaimer missing on health-adjacent video | regulated + no disclaimer line detected but channel boilerplate has one | Advisory suggesting the channel's own disclaimer (exact text reused, not generated) | "Most of your videos include your standard disclaimer; this one doesn't." |
| Emojis / special formatting / RTL text | grapheme-aware checks | Insert only at line boundaries; byte-exact preservation; newline style preserved | — |
| `<` or `>` in generated text | validator | Strip/replace; API would reject on paste | — |
| Existing links with UTM tags | `utm_*` present | Preserve exactly; equality ignores UTM; we never add UTM to existing links | — |
| CTA already present with different UTM / trailing slash / http vs https | normalised comparison | No `missing_cta` | — |
| Malformed URL (Apollo `HI8fFZEfrRA`) | syntactic check | `link_fix` with exact old/new span | "This link is broken: it contains the address twice. Replace it with: https://www.facebook.com/TheApolloHospitals/" |
| Social site blocks probes (403/999) | status class | `unverifiable`, not flagged | — |
| Old domain redirects (twitter.com → x.com) | 301 | Info only, no suggestion by default | — |
| URL shortener (bit.ly) | redirect chain | Follow ≤ 5 hops; judge final | — |
| Link to private IP / internal host | SSRF guard | Never probed; `unverifiable` | — |
| Link host down temporarily | 5xx once | Recheck after 24 h before flagging | — |
| Description changed since suggestion | hash mismatch | Re-anchor; recheck presence → applied_detected/superseded; else regenerate | "Your description changed since we made this suggestion; we've updated it." |
| User pastes partially / edits wording | ≥ 80% token overlap or keywords present | applied_detected | — |
| User marks applied but text not visible | no presence after 2 days | Keep `applied_claimed`, nudge | "We don't see the new line in the public description yet." |
| Applied detected without user marking | presence check | `applied_detected`, impact starts | "Looks like you applied this on 14 Nov. We'll report the effect on 28 Nov." |
| Impact confounded (another refresh, title change, news spike) | overlap within ±14 d | verdict `confounded` | "Several things changed at once, so we can't attribute the change to this edit." |
| Too few snapshots for impact | missing pre/post points | `insufficient_data` | "Not enough daily data before you applied this to measure the effect." |
| Onboarding day (1 snapshot) | velocity_basis = lifetime_avg | Provisional, decision capped at periodic | "Based on lifetime averages; we'll confirm once we have 7 days of data." |
| Viral spike on old video | velocity ≫ channel P99 | Allowed (good moment) but U unchanged; impact flagged `confounded` if spike in pre window | — |
| `viewCount` missing for a video | statistics absent | Gate out | — |
| Channel with thousands of videos (Apollo ~4.3k) | count | 02 snapshots back catalogue weekly (≈ 87 units/week), daily only for gated candidates (top 500 by velocity) | — |
| Stale year inside event context ("Live on 24 Nov 2025") | regex exclusions + liveStreamingDetails | No trigger | — |
| Stale year in title of evergreen video | title regex | Advisory `title_stale_year` (risk high); description link to newer own video if one exists | "The title says 2025. Only change it if the content is still accurate for 2026; changing the year alone can mislead viewers." |
| Historical year that should stay ("2018 Kerala floods") | year in a named-event phrase / not evergreen pattern | No trigger (only 'in YEAR'/'YEAR guide/list/update' patterns qualify) | — |
| Invalid existing chapters (first ≠ 00:00, < 3, < 10 s) | parser | Advisory `invalid_chapters` | "Your timestamps won't show as chapters: the first must be 00:00 and you need at least 3." |
| Chapters requested without transcript | public mode | Advisory only; no timestamps invented | "Adding chapters helps viewers jump to the answer. We can draft them if you connect your channel." |
| > 60 hashtags existing | count | Advisory (removal is not additive) | "YouTube ignores all hashtags on videos with more than 60." |
| ≥ 15 hashtags existing | count | No hashtag op | — |
| made_for_kids video | flag | Allowed (descriptions unaffected) but no CTA to sign-up/booking | — |
| LLM unavailable / timeout | LLMClient error | Template-only phrasing; stage continues | — |
| LLM output fails validation twice | validator | Template fallback; if template not applicable, drop op | — |
| OAuth revoked | 02 event | Drop transcript/search-term evidence; L3 ops downgraded or withdrawn; delete authorized data within 7 days | "We lost access to your channel analytics; some suggestions were simplified." |
| No conversion actions crawled | profile empty | No CTA trigger | "We couldn't find a booking or sign-up page on your website. Add one in Settings to get link suggestions." |
| Conversion action URL itself is dead | link check | Do not suggest it; report to 03 | — |
| Client domain mismatch (link to unrelated domain) | host ∉ client domains | Never inserted | — |
| Channel ownership not verified (wrong channel resolved) | 02/04 flag | Stage skipped | "Confirm your channel to get refresh suggestions." |
| Demo / no owner access | `oauth_connected = false` | Public mode; card footer "Prepared from public data. Only the channel owner can apply these." | — |
| Business decision `none` | §5.4 | Show reasoning card, no suggestions | "No refresh needed right now: your videos aren't earning steady views and there's no fresh news in your niche." |
| Decision `hygiene_only` with zero defects | no triggers | Positive empty state | "Your descriptions look healthy. Nothing to change this month." |
| Retention purge removes evidence of an open suggestion | expires_at ≤ 30 d | Suggestion expires with its evidence | — |
| Run time budget exceeded | stage timer | Skip remaining link probes (next run), skip LLM for low-priority ops | — |

## 9. Policy & guardrail check

| Guardrail | How 09 complies |
|---|---|
| Official APIs only | Data API (API key) + Analytics API/captions (OAuth). No watch-page scraping; auto-chapters and Studio insights not read. Link probes hit the client's/third-party *websites*, not YouTube. |
| Quota / shared cache | Zero `search.list` by default; reuse 02's cache and 08's demand check; batched `videos.list`. |
| Metrics = API data + simple maths | Velocity = Δviews/Δdays; ratios; medians; percentile ranks. `priority` is an internal ordering, never displayed as a metric. |
| Never merge YouTube data with other sources into one metric | D uses YouTube data only; news sources and website-derived CTA are shown as separate evidence lines; owner Analytics shown separately. Note: priority combines news urgency (U) with YouTube metrics for ordering only — flagged for review in §13. |
| No revenue estimates | None. Expected effect text has no money/conversion numbers. |
| No inferring demographics or content category | Coverage uses the client's own TopicRefs (05) + literal terms in the video's own metadata; no `topicCategories`, no classifier. Flag for review: `covered_terms` is lexical extraction, not categorisation. |
| 30-day refresh/delete | All suggestions expire ≤ 30 d; no full description copies stored; impact results TTL 30 d; boilerplate cache TTL 30 d. |
| Thumbnails | Not touched. |
| Conversion lens honesty | CTA suggestions are labelled as conversion hygiene; no conversion numbers claimed; OAuth subscriber/traffic data shown separately. |
| LLM only phrases | Detection, scoring, positions, keywords, URLs, numbers are code; LLM sees allowed-term lists, cannot add URLs/entities/numbers; validator enforces. |
| Evidence ≥ 3 refs in window | Enforced for keyword triggers; hygiene triggers use `Rule-based` evidence (proposed contract extension). |
| No copying | 6-gram overlap check vs reference titles/descriptions; no competitor titles; no repetition of client's existing lines. |
| YouTube spam / misleading metadata | Truth contract (L0–L3), anti-stuffing rules, per-keyword caps, no title auto-changes, India clickbait enforcement noted. |
| Medical misinformation / ad law | Regulated-domain policy pack, blocked lexicon, review_required, no L4 claims. |
| Developer Policies: don't alter links in API data; display latest data | Previews show the existing description with links unchanged; fixes are separate, user-applied suggestions; previews rendered from current cache. |
| Publishing out of scope | Copy-to-clipboard only; no `videos.update`. |

## 10. Performance, cost & scaling

Per client daily refresh (Apollo scale, 4.3k videos):
- Quota: description refetch for gated candidates ≤ 500 ids → 10 units/day (shared with 02's snapshot calls, usually 0 extra). Back-catalogue velocity relies on 02's weekly sweep (~87 units/week ≈ 12/day amortised). `search.list`: 0 by default (max 2 if enabled = 200 units).
- Link probes: distinct URLs ≈ 10–60 for Apollo (boilerplate dominates); cached 7 days → typically < 20 network probes/run, ≤ 20 s wall time with per-domain limits, run concurrently with scoring; uncompleted probes deferred.
- LLM: ≤ 10 refreshes × ≤ 2 LLM-phrased ops ≈ 20 calls × (~1,200 input + ~120 output tokens) ≈ 24k in / 2.4k out ≈ **$0.036/client/run** on Haiku 4.5 ($1/$5 per MTok); retries add ≤ 30%. Calls concurrent (limit 5) → ~3–6 s. Most hygiene ops are template-only (no tokens).
- CPU: parsing/boilerplate mining over 4.3k descriptions ≈ < 1 s; scoring trivial.
- Latency budget for the `refresh` stage: ≤ 30 s p95 of the < 2 min per-client target (link probes time-boxed at 20 s; LLM 10 s; rest < 2 s). Keyword detectors skipped on non-cadence days, so typical daily runs take < 5 s.
- Scaling: link cache shared across clients; boilerplate cached per channel; stage is per-client independent (arq jobs parallelise across clients).

## 11. Testing strategy

- **Unit (pure functions):** `DescriptionParser` (paragraphs, separators, hashtags, chapters validity, URL extraction with em-dash prefixes); `BoilerplateMiner`; `KeywordPresence` (aliases, hyphen/space, hashtag split, Unicode); year-context regex; byte budget with Devanagari/emoji; UTM normalisation; malformed URL repair; scorer formulas; eligibility decision table; impact maths incl. control ratio.
- **Property tests (hypothesis):** `apply_ops` — original is a subsequence of output and only whole lines are added; output ≤ 5000 bytes; idempotent detection after applying (re-running detectors on the output yields no trigger for the same keyword); emoji/RTL/CRLF preserved.
- **Validator adversarial suite:** LLM outputs that add numbers, URLs, unlisted entities, "cures", "best hospital", keyword lists, L3 phrasing without evidence, wrong language, `<script>` — all rejected.
- **Golden tests:** synthetic fixtures modelled on the Apollo sample structure (we do not commit real API data, to respect the 30-day rule): health explainer with footer + no booking link → `missing_cta` with location-matched URL; malformed social links → `link_fix`; CSR video → hygiene only; upcoming live → skipped; AI-company "How to make money using AI in 2026" + NewsSignal "Claude Fable 5.2" with 4 outlier refs → L1 insertion after first paragraph; same without refs → thin-data message; dental clinic → `hygiene_only`/positive empty state.
- **Contract tests:** `to_recommendation_draft` output validates against 07's `RecommendationDraft` schema and passes 07's gate runner with type config; `DescriptionRefresh` JSON schema snapshot.
- **Lifecycle tests:** retraction → withdraw + correction alert; supersession; description changed → re-anchor/applied_detected; expiry at 30 d.
- **Link checker tests:** local test server (pytest-httpserver) for 200/301/404/405-HEAD/5xx-then-200/timeout; SSRF cases (127.0.0.1, 169.254.169.254, IPv6 loopback, DNS rebinding via redirect).
- **Live smoke (manual/CI-optional, ≤ 2 units):** fetch 50 Apollo uploads, run evidence builder + hygiene detectors, assert the malformed-link case is found while it exists, no keyword ops in public healthcare mode without refs.
- **Evaluation hooks (12):** refresh cards included in the 30-card human review (rubric: specific, evidenced, relevant, actionable, not obvious + "truthful to video" check); backtest variant: for refreshes detected as applied in history, compare adjusted ratios vs random non-refreshed videos.

## 12. Implementation task breakdown

| # | Task | Size | Depends on |
|---|---|---|---|
| 1 | Domain models (§3.2) + settings (`RefreshSettings`) + Alembic tables (§6) | M | 01 |
| 2 | `DescriptionParser`, `BoilerplateMiner`, `KeywordPresence`, language detection | M | 02 (VideoRecord) |
| 3 | `VideoContentEvidenceBuilder` + claim-level policy | S | 2, 05 (TopicMatch) |
| 4 | `BusinessEligibilityEvaluator` + `VideoGate` | M | 03 (FreshnessProfile), 06 (VideoMetrics/ChannelBaseline), 08 (NewsSignal) |
| 5 | Link checker (probe, classify, SSRF guard, cache, limiter) + `broken_link` detector + `link_fix` op | M | 1 |
| 6 | Hygiene detectors: `missing_cta`, `missing_contact`, `missing_chapters`, `missing_hashtags` + CTA chooser | M | 3, 03 (ConversionAction) |
| 7 | Keyword detectors: `news_entity`, `buyer_question_gap`, `stale_year`, `above_fold_keyword` + `DemandEvidenceCollector` | L | 3, 4, 06 (TrendingReport), 08 |
| 8 | `RefreshScorer`, caps, confidence | S | 6, 7 |
| 9 | `OperationPlanner`, templates, `apply_ops` (+ property tests) | M | 3 |
| 10 | `InsertionValidator` + regulated healthcare policy pack (lexicons, DMR schedule data) | M | 9 |
| 11 | `InsertionPhraser` LLM integration (structured output, retry, fallback) | S | 10, 10-component LLM layer |
| 12 | `RefreshMerger` + byte budget + UTM | S | 9, 10 |
| 13 | Mapping to `RecommendationDraft` + gate config for 07 | S | 07 contract |
| 14 | `RefreshLifecycle` (expiry, retraction, supersede, revalidate, applied detection) | M | 1, 08 status field |
| 15 | `ImpactCalculator` + control set | M | 02 snapshots, 14 |
| 16 | `RefreshStage` wiring, RunEvents, time budgets | S | 11 (orchestration), all above |
| 17 | API endpoints with 11: list refreshes, mark applied, dismiss (with reason), confirm applicability (OAuth) | S | 11 |
| 18 | Golden/adversarial/contract tests, live smoke, ops metrics for 12 | M | all |

Order: 1 → 2 → 3 → (5, 6, 9) → 10 → 12 → 4 → 7 → 8 → 11 → 13 → 14 → 15 → 16 → 17 → 18. Hygiene path (tasks 1–3, 5, 6, 9, 10, 12, 13, 16) is demo-able for Apollo before news/keyword work lands.

## 13. Open questions / proposed changes to shared context

1. **Contract extension.** Add an optional `details` object to the recommendation contract carrying the type payload (`DescriptionRefresh` ops, advisories, review flag). Without it the copyable text has no home.
2. **Evidence gate for hygiene refreshes.** Allow `type=description_refresh` with `reference_videos = []` and `confidence.label = "Rule-based"` when the trigger is a verifiable defect (broken link, missing CTA, invalid chapters). Alternative if rejected: show hygiene items in a separate "Channel hygiene" list, outside Recommendations.
3. **Hashtag display fact.** The shared brief says "first 3 above title". The current Help page says "up to three hashtags that are considered most engaging will appear by your video title". Update the wording and don't promise placement.
4. **Description limit unit.** API: 5000 **bytes**. Help: 5000 characters. Standardise on bytes everywhere (matters for Hindi/Tamil descriptions).
5. **FreshnessProfile fields (03).** 09 needs `news_velocity_prior`, `regulated_domain`, `domain_kind`, `evergreen_expected`. Please confirm the names.
6. **NewsSignal fields (08).** 09 needs `status` (active/disputed/retracted/superseded), `superseded_by`, `aliases`, `entity_kind`, `is_competitor_entity`, `event_date`, `demand_videos`, `expires_at`.
7. **Component 02 asks:**
   - a weekly back-catalogue snapshot sweep for own channels (velocity for evergreen detection), with daily snapshots for refresh candidates;
   - store `description_hash`, `localizations` and `liveStreamingDetails`;
   - optionally request part=`player` (embed dimensions) to detect Shorts by aspect ratio. Not verified.
8. **Developer Policies III.E.4.h (project-wide risk).** It says clients must not use API Data "to create new or derived data or metrics", with more allowances only for audited developers from June 2026 (§III.L). The assignment explicitly permits simple maths, but outlier ratios, velocity and refresh impact are derived metrics. Proposal: store every derived metric under the same 30-day TTL, and have 01/12 raise this in the design review.
9. **Priority mixes news urgency with YouTube metrics.** It is used for ordering only and never displayed. Confirm this is acceptable under "never merge into one metric", or make U a tie-breaker only.
10. **LLM tier.** The bundled Anthropic skill docs list `claude-haiku-5-5` ($0.10/$0.50 per MTok at ≤100K prompt) as the current Haiku, cheaper and better at instruction following than `claude-haiku-4-5` ($1/$5). Consider switching the low tier.
11. **Owner confirmation UX (13).** OAuth-connected owners get an "I confirm this applies to X" checkbox, which unlocks L3 wording. Public/demo mode never shows L3.
12. **Localization parity.** Apollo descriptions carry up to 20 translated localizations. Should we offer machine-translated insertions? Proposed: not for regulated domains; for others, later.
13. **Regulatory review.** Hospital advertising rules in India need legal confirmation: NMC professional-conduct regulations (current status not verified), CCPA 2022 misleading-ad guidelines, and the DMR Act schedule. The lexicons and schedule should be owned and reviewed by a human.
14. **Link-check cache ownership.** `link_check_cache` is web data shared across clients. Should it live in 03 (website intelligence) instead of 09?
15. **Search bucket.** Approve optional `allow_search_calls ≤ 2/day/client` for news demand top-ups, or keep it at 0 and rely only on 08.
