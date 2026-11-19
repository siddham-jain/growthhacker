# 08 — Niche news radar & news-to-content opportunities

Status: plan (no code). Owner component: 08. Consistent with `00-shared-context.md`. Where research contradicts shared context, the plan stays consistent and the issue is raised in §13.

Research date: 2026-11-20. Live checks made: 2 Tavily `/search` calls (topic=news, basic, 1 credit each), PubMed `esearch`/`esummary`/`einfo`, WHO Disease Outbreak News OData, openFDA `drugsfda`, arXiv API, Hugging Face Hub API, lab RSS feeds (OpenAI, Google DeepMind, Google AI blog, Hugging Face blog, Anthropic), PIB RSS, GDELT DOC API. No YouTube `search.list` calls.

---

## 1. Purpose & business value

Some views come from what is already working in the niche (components 06/07). Others come from **being early on something that just happened**. Being early is where a small channel can beat a big one: YouTube has no established winners yet for a query like "Claude Fable 5.2", and buyers search for the new name the day it ships.

The component does three jobs:

1. **Detect** fresh events in the client's niche from news, regulators, research and vendor feeds. Examples: a frontier lab ships a model, CDSCO approves a therapy, ICMR publishes a dengue guideline, a carmaker launches an EV, an EV subsidy changes.
2. **Connect** each event to the client's business: which `Offering` and `ValueProposition` it touches, which `BuyerQuestion` it answers, and which `ConversionAction` the video should drive. For Runable plus "Claude Fable 5.2", the card would read: *"Show Claude Fable 5.2 building a deck in Runable → CTA: start free on Runable"*.
3. **Decide honestly** whether making a video is worth it now. That means a credibility check, a YouTube early-demand check, an urgency/TTL, the client's own history with news-reactive videos, and safety rules (healthcare first).

Conversion angle: a news video converts only when the news is the hook and the offering is the payoff. So every `news_opportunity` must link to at least one offering or value prop and carry a conversion action. News with no business link becomes "FYI" at most and never a recommendation.

Per-business sensitivity: news sensitivity varies a lot by niche (AI tools: very high; hospitals: medium and seasonal; dental: low; auto dealers: medium and launch-driven). The `FreshnessProfile` from component 03 controls sources, cadence, query volume and caps. A dental clinic can go weeks with zero opportunities, and the system must treat that as a correct result, not a failure.

Outputs feed:
- (a) `news_opportunity` recommendation drafts, handed to 07's quality gates and contract writer and to 10's evidence-grounded writer;
- (b) `NewsSignal`s consumed by 09 (description keyword insertions such as adding "Claude Fable 5.2" to an existing tutorial).

---

## 2. Scope / non-scope

**In scope**
- `FreshnessSource` port, a source registry, and adapters: Tavily news, Tavily domain-scoped (official sites with no feed), PubMed E-utilities, arXiv, Hugging Face Hub, RSS/Atom (lab blogs, regulators, PIB), WHO Disease Outbreak News, openFDA, and GDELT (fallback).
- Building the query plan from `FreshnessProfile` + `BusinessProfile` + confirmed competitors.
- Cadence scheduling by sensitivity, and a shared cross-client query/result cache.
- The pipeline: fetch → normalise → canonicalise → dedupe/cluster into stories → recency filter → cheap prefilter → LLM relevance judgement (Sonnet, structured output) → grounding validation → credibility scoring → safety/brand-safety screen → YouTube early-demand check → priority/urgency → `NewsSignal`.
- `news_opportunity` draft design, including the first-mover variant of the quality gate, TTL/urgency, format suggestion, title/thumbnail pattern hookup, and value-prop and CTA linking.
- Healthcare/brand-safety guardrails and the edge-case handling listed in §8.

**Not in scope** (owned elsewhere)
- Building `FreshnessProfile` itself (03). §3.1 lists the fields 08 needs.
- YouTube gateway, quota manager and shared video cache (02). 08 calls `YouTubeGateway.search_videos` / `videos_list` through 02's quota manager and budget buckets.
- Baselines, views-at-age and `TraitLift` (06). 08 reads them.
- Final card wording (10), the gate framework and contract serialisation (07), description diffs (09), cron/SSE plumbing (11), UI (13).
- Publishing, full-article storage or re-hosting, summarising paywalled content, and any claim-checking beyond source-tier rules (we are not a fact-checker; we refuse to recommend on weak evidence instead).

---

## 3. Inputs & outputs

### 3.1 Inputs (consumed)

`BusinessProfile` (03): offerings[], value_propositions[], conversion_actions[], locations[], niche_keywords[], buyer_questions[], brand_name, brand_aliases[], website_domain, and `freshness: FreshnessProfile`.

**Required `FreshnessProfile` fields** (03 owns them; this list is 08's requirement. If 03 uses different names, an adapter maps them):
```
FreshnessProfile:
  sensitivity: Literal["high","medium","low","none"]
  sensitivity_score: float                # 0..1, 03's own rationale-backed value
  rationale: str
  source_packs: list[str]                 # e.g. ["ai_frontier","dev_tools"] | ["health_in","health_global"] | ["auto_in"]
  watch_entities: list[WatchEntity]       # orgs/products/regulators to follow
  event_types: list[EventType]            # which kinds of events matter (launch, approval, guideline, ...)
  regions: list[str]                      # ISO 3166-1 alpha-2, primary first, e.g. ["IN"]
  languages: list[str]                    # ISO 639-1, e.g. ["en","hi"]
  seasonal_calendar: list[SeasonalWindow] # e.g. dengue IN: Jun–Nov
  excluded_topics: list[str]              # client-declared no-go topics
  is_regulated_health: bool               # turns on healthcare guardrails
WatchEntity: name, aliases[], kind (org|product|model_family|regulator|person|policy), importance (1..3)
SeasonalWindow: topic, region, start_month, end_month, query_terms[]
```
From 04: confirmed `CompetitorCandidate`s (name, aliases, domain, channel_id).
From 02: `YouTubeGateway`, `QuotaManager` (bucket `search_news_demand`), cached `ChannelRecord`/`VideoRecord`.
From 06: `ChannelBaseline`, `ViewsAtAge.baseline_at_age(channel_id, age_hours)`, `TraitLift` (niche + competitor), `OwnPerformanceReport` (length-band performance).
From 05: `TopicRef`s and the `TopicMatcher` (to map signals to tracked buyer questions).
Client config: `window_days` (7/30/90), timezone (IANA, default from primary location).

### 3.2 Domain objects owned by 08

```
EventType = Literal["product_launch","model_release","regulatory_approval","regulatory_action",
  "clinical_guideline","research_publication","public_health_advisory","outbreak","policy_change",
  "pricing_change","funding_or_ipo","partnership","incident","recall","other"]
ConfirmationStatus = Literal["confirmed","officially_announced_upcoming","reported","rumour_or_leak","speculative"]

NewsQuery:                            # global (cross-tenant), shared cache key
  id: UUID
  source_key: str                     # "tavily_news", "pubmed", "rss:openai_news", ...
  normalized_query: str               # lowercase, collapsed whitespace, sorted OR-terms
  params: dict                        # time_range, include_domains, language, ...
  query_hash: str                     # sha256(source_key + normalized_query + canonical_json(params))
  cadence_minutes: int                # min over subscribers' required cadence
  next_run_at: datetime               # UTC

NewsItem:                             # global; one fetched article/record
  id: UUID
  source_key: str
  url: HttpUrl
  canonical_url: str
  url_hash: str                       # sha256(canonical_url)
  domain: str                         # registrable domain (eTLD+1)
  title: str
  snippet: str                        # ≤ 1,500 chars, as returned by the source; never full text
  language: str | None                # detected ISO 639-1
  published_at: datetime | None       # UTC
  published_precision: Literal["datetime","date","none"]
  fetched_at: datetime
  source_rank_score: float | None     # provider relevance score (Tavily score) — never shown as a metric
  external_ids: dict                  # {"pmid":..., "doi":..., "arxiv_id":..., "don_id":..., "hf_repo":...}
  source_tier: SourceTier             # resolved at ingest
  flags: set[str]                     # "paywalled","social","press_release","preprint","satire_blocklist",...
  story_id: UUID | None

NewsStory:                            # global cluster of NewsItems about one event
  id: UUID
  first_published_at: datetime        # min published_at of members with precision != none
  last_published_at: datetime
  item_ids: list[UUID]
  outlet_count: int                   # distinct registrable domains
  best_tier: SourceTier
  representative_item_id: UUID
  version_tokens: set[str]            # e.g. {"5.2"} — guards against merging 5.1 and 5.2 stories
  status: Literal["developing","stable","stale"]

GroundedEntity:
  text: str                           # exactly as in source text
  canonical: str                      # alias-resolved display form
  kind: Literal["model","product","org","drug","device","condition","policy","regulator","person","other"]
  evidence_item_id: UUID
  evidence_quote: str                 # ≤ 200 chars, must contain `text`

NewsJudgement:                        # LLM output, per (client, story, profile_version); validated
  relevance_band: Literal["none","tangential","adjacent","strong","direct"]
  key_entity: GroundedEntity
  other_entities: list[GroundedEntity]
  event_type: EventType
  event_date: date | None             # when the event happened per text (catches stale re-surfacing)
  confirmation: ConfirmationStatus
  offering_links: list[OfferingLink]  # OfferingLink: offering_id, value_prop_ids[], rationale (≤ 240 chars)
  buyer_question_ids: list[UUID]      # must exist in profile
  angle: str                          # ≤ 200 chars, how the client can use the news
  region_relevance: Literal["primary_region","global","other_region_only","unclear"]
  about_client: bool
  about_competitor_ids: list[UUID]
  safety_flags: list[SafetyFlag]      # tragedy, death, medical_claim, unverified_health_claim, political, legal, controversy, minors, self_harm, fear_inducing
  conflicting_reports: bool
  conversion_action_id: UUID | None

CredibilityAssessment:
  best_tier: SourceTier               # tier1_primary | tier1_authority | tier2_established | tier3_low | blocked
  independent_outlets: int
  has_primary_source: bool
  has_authoritative_health_source: bool
  peer_reviewed: bool | None
  score: float                        # 0..1 computed (§5.8)
  reasons: list[str]                  # machine reason codes

DemandCheck:                          # YouTube-derived (30-day retention rule applies)
  status: Literal["checked","unchecked_quota","unchecked_disabled","unchecked_error","local_only"]
  entity_query: str
  published_after: datetime
  checked_at: datetime
  early_videos: list[EarlyVideo]      # grounded: entity present in title or description
  early_video_count: int
  channel_count: int
  outlier_count: int                  # early videos with outlier_ratio ≥ outlier_threshold
  median_views_per_hour: float | None
  saturation: Literal["empty","emerging","active","crowded"]
  quota_units_spent: int
EarlyVideo: video_id, channel_id, channel_title, published_at, age_hours, views, views_per_hour,
            views_at_age: ViewsAtAgePoint(age_hours, views), outlier_ratio: float | None, duration_band

NewsSignal:                           # tenant-scoped, consumed by 07 (news_opportunity) and 09 (refresh)
  id: UUID
  client_id: UUID
  story_id: UUID
  profile_version: int
  judgement: NewsJudgement
  credibility: CredibilityAssessment
  demand: DemandCheck
  relevance_score: float              # 0..1 computed from band + deterministic features
  priority: float                     # 0..1 (§5.11)
  urgency: Literal["now","this_week","evergreen_window"]
  half_life_hours: int
  expires_at: datetime                # UTC
  refresh_keywords: list[str]         # grounded keyword candidates for 09 (subset of entity canonical forms)
  refresh_eligible: bool
  safety: SafetyDecision              # allow | allow_with_guidance | needs_review | block (+ reasons, disclaimers)
  status: Literal["new","drafted","shown","dismissed","used","suppressed","expired"]
  suppression_reason: str | None
  created_at: datetime
```

### 3.3 Output to 07: `NewsOpportunityDraft` (a `RecommendationDraft` subtype)

This maps one-to-one onto the shared output contract. Fields marked ★ are proposed contract extensions (§13).

| Contract field | How 08 fills it |
|---|---|
| id | `rec_` + ULID |
| type | `"news_opportunity"` |
| summary | Written by 10 from the structured brief: entity + angle + format, e.g. "60-sec Short: Claude Fable 5.2 builds a pitch deck in Runable" |
| lens[] | `views` if demand.status=checked and saturation ∈ {emerging, active}, or the client's news-reactive history ≥ 1.0x; `conversion` if ≥1 tracked buyer_question matched or ≥1 offering link with a conversion action. Must be non-empty |
| buyer_question | Best tracked `BuyerQuestion` (via judgement ids, re-verified by 05 matcher); `null` if none (then lens excludes conversion) |
| why_it_works[] | Deterministic reason codes rendered by 10, e.g. `PRIMARY_SOURCE(anthropic.com, 14h ago)`, `OUTLETS(9)`, `EARLY_DEMAND(3 videos, 2 outliers, median 1.8k views/h)`, `OFFERING_LINK(AI slides)`, `OWN_NEWS_HISTORY(n=4, median 1.6x)` |
| reference_videos[] | Early videos (published after the story's first_published_at minus 12h, inside window): video_id, channel, published_at, `views_day7` (null if age < 7d), ★`views_at_age{age_hours, views}`, outlier_ratio (null when channel baseline unknown). May be empty in the first-mover case |
| ★reference_sources[] | ≥1 article per story: url, outlet, tier, published_at, title (no copied body) |
| fit_to_client | Offering/value-prop link + CTA + the client's own news-reactive results, e.g. "Ties to 'AI presentations' offering; CTA: Start free; your 4 launch-reaction videos ran 1.2x–2.1x your baseline" |
| expected_range_vs_baseline | §5.13; `"insufficient history"` allowed |
| confidence | `{label, outliers, channels}` + ★`basis: "first_mover"|"early_demand"|"standard"` and label `"First-mover"` (§5.12) |
| window_days | Client window; references are always inside it (news is ≤ 7 days by construction) |
| generated_at | UTC ISO-8601 |
| ★expires_at / ★urgency | From signal |
| ★format_suggestion | `{format: "short"|"long_explainer"|"tutorial"|"live_or_premiere", rationale_codes[]}` |
| ★title_patterns / thumbnail_patterns | Top `TraitLift` traits (06) that are compatible with the format; patterns, never titles |
| ★conversion_action | `{id, label, url}` from `BusinessProfile.conversion_actions` |
| ★compliance | `{disclaimers[], review_required: bool, safety_flags[]}` |

### 3.4 Output to 09
`NewsSignalRepository.active_for_client(client_id, now) -> list[NewsSignal]` filtered to `refresh_eligible=True`. Eligibility: confirmation=confirmed, credibility ≥ tier2 (health: has_authoritative_health_source), relevance_band ∈ {strong, direct}, safety=allow, not expired. 09 uses `refresh_keywords`, `key_entity.canonical`, `offering_links` and `expires_at`.

---

## 4. Internal design

```
app/ports/freshness.py         FreshnessSource (Protocol), SourceCapabilities
app/adapters/freshness/
  tavily_news.py               TavilyNewsSource            (topic=news)
  tavily_domains.py            TavilyDomainSource          (topic=general, include_domains=official sites, time_range)
  pubmed.py                    PubMedSource                (esearch + esummary v2.0 JSON)
  arxiv.py                     ArxivSource                 (Atom API, 1 req / 3 s)
  huggingface.py               HuggingFaceTrendingSource   (Hub API models?sort=trendingScore)
  rss.py                       RssSource                   (generic RSS/Atom; feeds declared in packs)
  who_don.py                   WhoOutbreakNewsSource       (OData, metadata + link only)
  openfda.py                   OpenFdaApprovalsSource      (drugsfda ORIG/AP submissions)
  gdelt.py                     GdeltDocSource              (fallback, 1 req / 5 s, attribution)
app/services/news/
  registry.py                  FreshnessSourceRegistry, SourcePackRegistry
  packs/                       ai_frontier.yaml, health_in.yaml, health_global.yaml, dental.yaml, auto_in.yaml, auto_us.yaml, home_services_us.yaml
  query_planner.py             QueryPlanner  -> list[PlannedQuery]
  scheduler.py                 CadencePolicy, NewsPollScheduler (arq cron hook)
  fetcher.py                   NewsFetcher (cache-aware, budget-aware, circuit breakers)
  normalize.py                 ItemNormalizer, UrlCanonicalizer, DateParser, LanguageDetector
  tiers.py                     SourceTierResolver (domain → tier; blocklists)
  clustering.py                StoryClusterer (exact → fuzzy title → embedding, version guard)
  prefilter.py                 RelevancePrefilter (lexical + embedding vs business vector)
  judge.py                     NewsRelevanceJudge (LLMClient, Sonnet tier, structured output)
  grounding.py                 GroundingValidator
  credibility.py               CredibilityScorer
  safety.py                    SafetyPolicy (Protocol) + HealthcarePolicy, BrandSafetyPolicy, CompetitorPolicy
  demand.py                    YouTubeDemandChecker (shared cache, quota bucket)
  priority.py                  PriorityScorer, DecayModel
  history.py                   NewsReactiveHistory (client's own past news-reactive videos)
  opportunity.py               NewsOpportunityBuilder -> NewsOpportunityDraft
  service.py                   NewsRadarService (facade used by pipeline stage)
app/pipeline/stages/news_radar.py   NewsRadarStage (Stage protocol)
app/workers/cron/news_poll.py        shared polling job (not per client)
```

### 4.1 Port

```python
class SourceCapabilities(BaseModel):
    supports_time_filter: bool
    supports_language: bool
    supports_region: bool
    returns_dates: Literal["datetime","date","mixed","none"]
    cost_unit: Literal["tavily_credit","free"]
    min_interval_seconds: float          # provider politeness / rate limit
    content_policy: Literal["snippet_ok","metadata_and_link_only"]

class FreshnessSource(Protocol):
    key: str
    capabilities: SourceCapabilities
    async def fetch(self, query: PlannedQuery, since: datetime) -> FetchResult: ...
    async def health(self) -> SourceHealth: ...

class FetchResult(BaseModel):
    items: list[RawNewsItem]; cost: float; status: Literal["ok","partial","rate_limited","exhausted","error"]
    error: str | None; provider_request_id: str | None
```

Open/closed:
- New sources are new adapters plus a registry entry (`@freshness_source("key")`).
- New niches are new YAML packs (feeds, official domains, tier overrides, event types, default half-lives, safety policy set). No code changes.
- Safety policies are a list of `SafetyPolicy` objects chosen by profile flags (`is_regulated_health` → `HealthcarePolicy`).
- Decay models are a registry keyed by `EventType`.

### 4.2 Source packs (initial)

| Pack | Sources (adapter → target) | Notes |
|---|---|---|
| `ai_frontier` | RSS: `openai.com/news/rss.xml`, `deepmind.google/blog/rss.xml`, `blog.google/technology/ai/rss/`, `huggingface.co/blog/feed.xml` (all verified 200 on 2026-11-20); TavilyDomainSource for labs with **no** feed (`anthropic.com` returned 404 for `/rss.xml`; also `mistral.ai`, `x.ai`, `ai.meta.com`); TavilyNews for watch entities; arXiv `cs.CL/cs.AI/cs.LG` by watch entities; HF trending models | arXiv items = `preprint`, tier2 at best |
| `health_in` | TavilyDomainSource on `cdsco.gov.in`, `mohfw.gov.in`, `icmr.gov.in`, `ncdc.mohfw.gov.in`, `pib.gov.in`; RSS: PIB press releases (verified 200); TavilyNews `language=en` (+`hi` if profile lists it); PubMed (India-relevant conditions from offerings) | CDSCO has no feed/API (robots.txt 404 → nothing disallowed, but we don't crawl; Tavily only). PIB copyright terms: verify (§7) |
| `health_global` | WHO DON OData (metadata + link only); openFDA drugsfda approvals; PubMed; TavilyNews | WHO commercial reuse needs permission → titles/dates/links only |
| `dental` | PubMed (dental MeSH from offerings), TavilyDomainSource (`ada.org`, `fda.gov` devices, national dental councils by region), TavilyNews weekly | sensitivity usually low |
| `auto_in` / `auto_us` | TavilyNews (watch makes/models, EV policy terms), TavilyDomainSource (OEM newsrooms, `heavyindustries.gov.in`, `fueleconomy.gov`/`energy.gov` for US) | launch-driven |
| `home_services_us` | TavilyNews (rebates, codes, storm/heat advisories by region), TavilyDomainSource (`energystar.gov`, `weather.gov` advisories) | seasonal |
| fallback (any) | GDELT DOC 2.0 | used when Tavily is exhausted/down; attribution required |

**Excluded sources:**
- Google News RSS: its terms limit use to personal, non-commercial use and forbid reformatting/display of results ([terms](https://www.google.com/intl/en_us/terms_google_news.html)).
- Scraping any news site.
- X/Twitter/Instagram/Facebook as sources (social URLs can appear in Tavily results; they are flagged `social` and can never be the sole support).
- youtube.com URLs from Tavily: YouTube data only via the official API, so these are excluded and their video ids are passed to the official API demand check only as candidates (§5.10).

---

## 5. Algorithms & logic

All thresholds live in `NewsSettings` (pydantic-settings, env-overridable, per-tenant overrides in DB). Defaults are shown in brackets.

### 5.1 Sensitivity → operating envelope

| sensitivity | Tavily polls/day per query | feed/API polls/day | max queries per client | max signals judged/run | max opportunities/day | default demand checks/day (global share) |
|---|---|---|---|---|---|---|
| high (AI tools) | 4 [every 6h] | 12 [every 2h] | 12 | 15 | 5 | up to 3 per client |
| medium (hospital, auto) | 1 [daily] | 4 | 8 | 8 | 3 | 1 |
| low (dental, home services) | 1/7 [weekly] | 1 | 4 | 4 | 1 | 1 on demand |
| none | 0 (monthly sweep 1) | 0 | 2 | 2 | 0, except `about_client` or `about_competitor` with strong relevance (cap 1) | 0 |

Seasonal windows raise a client one level during the window (e.g. Apollo in Jun–Nov dengue season: medium → high only for the seasonal query set).

### 5.2 Query planning (`QueryPlanner.plan(profile, competitors, packs) -> list[PlannedQuery]`)

1. **Entity queries:** for each `WatchEntity` (importance-ordered), build `"<name>" (<event terms for its kind>)`. Event terms come from the pack: model_family → `launch OR release OR "new model"`; regulator → `approval OR guideline OR advisory`; car make → `launch OR unveil OR price`. Aliases join with OR (`"Claude" OR "Anthropic"`).
2. **Offering queries:** for each offering, top 2 niche keywords + `"new"|"update"|year`. Example: `"AI presentation maker" new 2026`.
3. **Competitor queries:** `"<competitor name>" (launch OR raises OR acquires OR outage OR pricing)`, 1 per competitor, capped at 5.
4. **Client query:** `"<brand_name>"` (press coverage), always 1, on the low cadence.
5. **Seasonal queries:** active `SeasonalWindow.query_terms` + region name, e.g. `dengue advisory India`.
6. **Domain queries:** for pack official domains with no feed, one TavilyDomainSource query per domain group, with `include_domains=[...]`, `topic=general`, `time_range` per cadence.
7. **Region:** Tavily `country` works only with `topic=general` (verified, §7), so for `topic=news` the region name goes into the query text for region-specific concepts, and the result-level region check happens in the judgement (`region_relevance`).
8. **Language:** `language=<primary>`; `filter_by_language=true` only when the profile is single-language. For bilingual profiles (Apollo en+hi), plan one query per language for the seasonal/regulatory set only.
9. **Dedupe across clients:** compute `query_hash`. If the hash exists, add a subscription (no new query). Example: "Anthropic Claude launch" is shared by every AI-tools client.
10. **Budget fit:** sort by (importance, origin weight [client 1.0, entity 0.9, seasonal 0.9, offering 0.7, competitor 0.6]) and truncate to the sensitivity's `max queries per client`. Report the dropped queries in the run log.

The planned queries are stored with `profile_version`. A profile edit (keywords, competitors, entities) re-plans and retires queries that have no subscribers.

### 5.3 Scheduling and shared cache

- A cron job `news_poll` (11 hosts it) runs every 15 min and selects `news_queries WHERE next_run_at <= now() AND subscriber_count > 0`, ordered by `next_run_at`. The query's cadence = min cadence over its subscribers.
- Per source, a token bucket enforces `min_interval_seconds`: arXiv 3 s single connection; GDELT 5 s; NCBI ≤ 3 rps without key / ≤ 10 rps with key; Tavily under plan RPM.
- Redis keys:
  - hot cache `news:q:{query_hash}:{time_bucket}` (TTL = cadence) holds item ids;
  - lock `news:lock:{query_hash}` (SET NX, 120 s) so two workers never spend twice on the same query.
- Results are written to global `news_items`. Clients never trigger fetches directly. The per-client `NewsRadarStage` (in the analysis or daily refresh run) only reads the cache, which keeps the run fast (§10).
- **On-demand freshness:** on onboarding, or when the user clicks "Check news now", the stage may force-run that client's queries with a cooldown [30 min per client] and the global budget check.

### 5.4 Fetch adapters: request shapes

- **TavilyNewsSource:** `search(query, topic="news", time_range="day"|"week", search_depth="basic", max_results=10, include_usage=True, filter_by_published_date=True, exclude_domains=[youtube.com, facebook.com, instagram.com, x.com, tiktok.com, + blocklist], language=…)`.
  - `include_raw_content=False`: snippets only; this also keeps us clear of storing full text.
  - `auto_parameters` stays off, because it may silently switch to advanced (2 credits).
  - Use `time_range="day"` for high-cadence queries and `"week"` otherwise. Overlap is intended; dedupe handles it.
- **TavilyDomainSource:** same, but `topic="general"`, `include_domains=[…]` (mode `restrict`), `country` set from the primary region.
- **PubMedSource:**
  - `esearch.fcgi?db=pubmed&term=<terms>&reldate=<days>&datetype=edat&retmax=20&sort=pub_date&retmode=json&tool=growthhacker&email=<ops email>&api_key=<optional>`;
  - then `esummary.fcgi?db=pubmed&id=<csv>&retmode=json&version=2.0`.
  - Recency uses **entry date (edat)/epubdate**, not `pubdate`. Verified live: a record entered 2026-11 with epubdate 2026-10-28 has `pubdate: "2027 Mar"`.
  - `pubtype` containing "Retracted Publication" or "Retraction of Publication" → blocked.
- **ArxivSource:** `export.arxiv.org/api/query?search_query=(cat:cs.CL OR cat:cs.AI) AND all:"<entity>"&sortBy=submittedDate&sortOrder=descending&max_results=25`. Only CC0 metadata is stored; we link to the abstract page.
- **HuggingFaceTrendingSource:** `huggingface.co/api/models?sort=trendingScore&limit=50`. Keep repos whose org matches a `WatchEntity` (e.g. `meta-llama`, `google`, `mistralai`, `Qwen`) or whose trendingScore is ≥ p95 of the response. Only org-verified repos are tier1_primary; community repos are tier3. HF API terms are not fully verified (§7).
- **RssSource:** httpx GET with `If-None-Match`/`If-Modified-Since`, parse RSS/Atom (dependency choice in §7). Store title, link, pubDate and description truncated to 1,500 chars.
- **WhoOutbreakNewsSource:** `www.who.int/api/news/diseaseoutbreaknews?$orderby=PublicationDate desc&$top=20&$select=Title,PublicationDate,ItemDefaultUrl,DonId` (verified live). Store title, date and link (`https://www.who.int/emergencies/disease-outbreak-news/item/<ItemDefaultUrl>`; confirm the URL pattern at build time). No body text.
- **OpenFdaApprovalsSource:** `api.fda.gov/drug/drugsfda.json?search=submissions.submission_type:ORIG+AND+submissions.submission_status:AP+AND+submissions.submission_status_date:[<from>+TO+<to>]&limit=100` (date-range syntax to confirm in implementation; the sort query was verified live). Region = US. Relevant to Indian clients only as `global` news, through the region rule.
- **GdeltDocSource:** `api.gdeltproject.org/api/v2/doc/doc?query=<q> sourcecountry:<c>&mode=artlist&format=json&maxrecords=50&timespan=1d&sort=datedesc`. It is strictly rate limited (we hit a 429 asking for ≤ 1 request / 5 s), so it is used only as a fallback.

### 5.5 Normalisation

1. **URL canonicalisation:**
   - lowercase scheme/host; strip `www.`, `m.`, `amp.` host prefixes and `/amp` path suffix;
   - drop fragment, `utm_*`, `fbclid`, `gclid`, `mc_cid`, `mc_eid`, `ref`, `cmpid`, `ito`, `__twitter_impression`;
   - sort the remaining query params; remove trailing slash;
   - resolve known shorteners only when they come from feeds (HEAD request, max 3 redirects, 3 s timeout);
   - `url_hash = sha256(canonical)`.
2. **Domain:** registrable domain via the Public Suffix List (`tldextract`, or bundled PSL; §7).
3. **Dates:**
   - Tavily returns RFC 1123 strings such as `"Mon, 16 Nov 2026 18:27:56 GMT"` (verified live). Parse with `email.utils.parsedate_to_datetime`.
   - `00:00:00` exactly → `published_precision="date"`.
   - `null` → `none`. Verified live: an Instagram result came back with `published_date: null` even with `time_range=week`, so `filter_by_published_date=True` is required and the date gate is enforced again locally.
   - Store UTC. A date later than now + 6h → set to `fetched_at`, flag `future_date`.
4. **Language:** a cheap detector on title+snippet (dependency in §7). Set `language`.
5. **Flags:**
   - `paywalled`: domain in pack paywall list or snippet length < 200 with a "subscribe" marker;
   - `press_release`: prnewswire, businesswire, globenewswire, einpresswire, or path contains `/press-release`;
   - `social`; `preprint`: arXiv, medRxiv, bioRxiv;
   - `continuously_updated`: path patterns like `/releases`, `/changelog`, `/live`. Verified live: `github.com/anthropics/claude-code/releases` was returned as "news" with a last-updated date;
   - `satire_blocklist`.
6. **Tier** via `SourceTierResolver`: pack overrides → global `source_tiers` table → default tier3. Defaults:
   - `tier1_primary`: the entity's own domain, e.g. `openai.com` for OpenAI news; this is set dynamically: domain ∈ watch entity domains;
   - `tier1_authority`: regulators, WHO, ICMR, NIH, PubMed peer-reviewed records;
   - `tier2_established`: curated outlet list per pack/region (Reuters, AP, BBC, The Hindu, Indian Express, NDTV, Times of India, Mint, The Verge, TechCrunch, Ars Technica, STAT, …);
   - `tier3_low`: everything else, including commercial blogs (verified live: a pharmacy blog post on dengue vaccine approval ranked #3);
   - `blocked`: satire/fake/content-farm list.

### 5.6 Dedupe and story clustering (`StoryClusterer`)

The work is incremental, over items from the last 7 days.

1. **Exact:** same `url_hash` → same item (keep the earliest `published_at`, union `source_key`s). Same DOI/PMID/arXiv id/DON id → same item.
2. **Version guard:** extract `version_tokens` from the title with the regex `\b(v?\d+(\.\d+){0,2}[a-z]?)\b` near an entity token, plus model-name tokens like `5.2`, `Pro`, `Ultra`, `mini`. Two items whose version token sets are non-empty and disjoint are **never** merged. This stops "Fable 5.1" and "Fable 5.2" from collapsing into one story.
3. **Fuzzy title:** `rapidfuzz.fuzz.token_set_ratio(norm_title_a, norm_title_b) ≥ 88` [config] and |Δpublished| ≤ 72h → same story.
4. **Embedding:** fastembed (model chosen by 05, default `BAAI/bge-small-en-v1.5`; multilingual model for non-English packs, §7) on `title + " " + snippet[:300]`. Merge into a story if cosine to the story centroid ≥ 0.86 [config], AND they share ≥1 entity token (capitalised n-gram or watch alias), AND the version guard passes.
5. **Story fields:** `first_published_at` = min over members with precision ≠ none; `outlet_count` = distinct domains; `best_tier` = max tier.
6. **Status:**
   - `developing` if new members arrived in the last 12h;
   - `stable` otherwise;
   - `stale` if first_published_at < now − max_story_age [7d high, 14d medium/low; 30d for `clinical_guideline`/`regulatory_approval`].
7. Story ids are global. Per-client state lives on `NewsSignal`.

### 5.7 Recency filter and prefilter

- **Recency:** keep stories with `first_published_at ≥ now − recency_days`, where recency_days = high 3, medium 7, low 14 (guidelines/approvals 30). Stories whose only members have precision `none` are dropped unless they come from a dated feed.
- **Re-surfacing guard:** if the judgement's `event_date` (text-derived) is older than recency_days, mark `stale_resurfaced` and suppress. This catches "anniversary" and "recap" pieces with new publish dates.
- **Prefilter score** (deterministic, cheap):
  ```
  lexical = max over (watch entity aliases ∪ offering keywords ∪ competitor names ∪ brand aliases) of
            1.0 if exact phrase in title, 0.6 if in snippet, else rapidfuzz partial_ratio/100 * 0.4
  semantic = cosine(story_centroid, business_vector)   # business_vector = mean emb of offerings + value props + buyer questions
  prefilter = 0.6*lexical + 0.4*semantic
  ```
  Keep stories with prefilter ≥ 0.35 [config], plus all stories with `about_client` or competitor lexical hits. Take the top-N by prefilter × tier weight, where N = "max signals judged/run" (§5.1).
- **Already judged:** skip stories that already have a judgement for (client, story, profile_version), unless the story gained a higher tier or ≥ 3 new outlets since the last judgement (re-judge).

### 5.8 LLM relevance judgement (`NewsRelevanceJudge`)

- **Model and call:** Sonnet tier (`claude-sonnet-5-5`) through 10's `LLMClient` with structured output: Python SDK `client.messages.parse(..., output_format=NewsJudgementLLM)` or `output_config.format = json_schema`. This is described in the bundled SDK docs (`python/claude-api/tool-use.md` → Structured Outputs). The OpenAI-compatible fallback uses `response_format=json_schema`.
- **Prompt layout:**
  - **System (cached, per client):** business summary (offerings with ids, value props with ids, buyer questions with ids, conversion actions with ids, regions, excluded topics, competitor names with ids). Instructions:
    - judge, don't invent;
    - entities must be copied verbatim from the article text and accompanied by a quote;
    - classify confirmation status;
    - flag safety;
    - choose ids only from the lists.
  - **User:** story bundle of up to 5 members (best tier first), each with `[item_id]`, outlet, tier, published_at (UTC), title and snippet. No raw HTML or URLs beyond the domain, so the prompt surface stays small and injection is reduced.
- **Prompt-injection hardening:** article text is wrapped in delimiters and labelled untrusted; the schema has no free-form "instructions" field. Any id not in the provided lists fails validation.
- **Output schema:** `NewsJudgementLLM`. Same as `NewsJudgement`, but entities carry `evidence_item_id` + `evidence_quote`; `relevance_band` is categorical; `angle` ≤ 200 chars.
- **Concurrency** 5 per run; timeout 30 s; 1 retry; on failure → `signal.status=suppressed, reason=llm_unavailable` (no fallback guessing).
- **`stop_reason == "refusal"`** (possible on sensitive health/violence content) → suppress with reason `llm_refusal` and route to `needs_review` if the story is about the client.

### 5.9 Grounding validation (`GroundingValidator`)

Deterministic. This is the hallucination firewall.

1. For each entity:
   - normalise (NFKC, casefold, collapse whitespace, unify dashes and quotes);
   - require `normalize(text) ⊂ normalize(evidence_quote)` and `normalize(evidence_quote) ⊂ normalize(title + " " + snippet)` of `evidence_item_id`.
   - If either fails → drop the entity.
2. `key_entity` must survive and must appear in ≥1 member's **title or snippet**. Otherwise drop the signal (`ungrounded_entity`).
3. **Canonicalisation:** map to a `WatchEntity` alias table if it matches. Otherwise canonical = the most frequent surface form across members. Version tokens must be identical between canonical and text: "Claude Fable 5.2" can never be canonicalised to "Claude Fable 5".
4. **Alias pairs:** pack-declared equivalences. Example from the Anthropic model docs: "Claude Mythos 5.2" is the same model as "Claude Fable 5.2" under a restricted-access name. Such pairs are merged for demand checks but displayed with the name used by the tier-1 source.
5. All ids (offering, value prop, buyer question, conversion action, competitor) must exist in the current profile version. Unknown ids are removed, and `offering_links` empty after validation → `relevance_band` capped at `tangential`.
6. `refresh_keywords` = the key entity's canonical form, plus `other_entities` of kind model/product/drug/policy that pass grounding and appear in ≥2 members (or 1 tier-1 member).

### 5.10 YouTube early-demand check (`YouTubeDemandChecker`)

It runs only for signals with relevance_score ≥ 0.5 and safety ≠ block, in priority order, while the budget allows.

1. **Local first (free):** search the 02 shared video cache (competitor + niche videos already ingested) for the canonical entity in title/description, published after `first_published_at − 12h`. If ≥ `min_refs` [3] matches are found → `status=local_only`, and the search is skipped.
2. **Shared cache:** key `yt_demand:{sha1(canonical_entity_norm)}:{utc_date}`. A hit → reuse it (any client, same day). Store in `yt_demand_checks`.
3. **Budget:** `QuotaManager.try_acquire(bucket="search_news_demand", units=1 search call)`.
   - Default reserve: 15 of the 100 daily search calls [config; owned by 02].
   - Per-client daily cap per §5.1.
   - Denied → `status=unchecked_quota`.
4. **search.list:** `q='"<canonical entity>"'` (+ disambiguator from the key org when the entity is ambiguous, e.g. `"Fable 5.2" Claude`), `type=video`, `publishedAfter=max(first_published_at − 12h, now − 7d)` (RFC 3339), `order=relevance`, `maxResults=50`, `relevanceLanguage=<primary language>`, `regionCode=<primary region>`. Use `order=relevance` because the docs warn non-relevance orders with date filters can return incomplete sets, and that `order=date` relies on a possibly delayed index.
5. **videos.list** (1 unit per 50 ids) for statistics, snippet and contentDetails. **Grounding:** keep a video only if the canonical entity or an alias appears in its title or description. This removes homonyms such as "Fable" the video game.
6. **Per early video** (06's `ViewsAtAge`):
   - `age_hours = now − published_at`; `views_per_hour = views / max(age_hours, 1)`;
   - `outlier_ratio = views / baseline_at_age(channel_id, age_hours)`.
   - If 06 has no baseline for the channel, request a lightweight baseline through 02/06 (uploads playlist + videos.list ≈ 2 units/channel) for at most the top 10 channels by views. Otherwise `outlier_ratio=None`, and the video does not count toward `outlier_count`.
7. **Aggregates:**
   - `early_video_count`, `channel_count`, `outlier_count` (ratio ≥ 2.0 [06's threshold]), `median_views_per_hour`.
   - `saturation`:
     - `empty` (0 videos);
     - `emerging` (1–4 videos, or < 3 channels);
     - `active` (5–19 videos across ≥ 3 channels);
     - `crowded` (≥ 20 videos, or ≥ 5 videos from channels with > 10× the client's subscriber count).
8. YouTube-tab URLs found by Tavily are passed into step 5 as extra candidate ids (official API only, still grounded).

### 5.11 Credibility, safety, priority

**Credibility score** (0..1):
```
tier_w = {tier1_primary:1.0, tier1_authority:1.0, tier2_established:0.8, tier3_low:0.4, blocked:0}
score = tier_w[best_tier] + 0.05*min(independent_outlets-1, 4)   (cap 1.0)
if confirmation in {rumour_or_leak, speculative}: score *= 0.5
if conflicting_reports: score *= 0.7
if all members flagged press_release: score = min(score, 0.5)
```
Gates:
- all signals need score ≥ 0.5 [config];
- health (`is_regulated_health`) needs `has_authoritative_health_source` (tier1_authority or peer-reviewed PubMed) **for any medical claim**;
- preprints alone → only a "research angle with caveat" and never for health;
- rumours are allowed only outside health, with `confirmation` shown and capped at confidence Low.

**SafetyDecision** (policies run in order; the most restrictive wins):

| Policy | Rule | Decision |
|---|---|---|
| BrandSafety | tragedy/death/disaster/violence/self_harm/minors flags | `block` (default). Exception: health public advisories (e.g. outbreak deaths reported by a regulator) → `allow_with_guidance` with "no sensational framing" guidance |
| BrandSafety | political/legal/controversy | `needs_review` |
| Healthcare | medical_claim without tier1_authority/peer-reviewed | `block` |
| Healthcare | any health signal | `allow_with_guidance` + disclaimers ("educational, not medical advice; consult a doctor"), symptom-and-when-to-seek-care framing, no cure/guarantee language, cite authority. India: flag `drugs_and_magic_remedies_act` review for drug/treatment claims (legal review recommended; not verified in this plan) |
| Healthcare | prescription drug launch | angle restricted to "what it is / who regulates / talk to your doctor"; no promotion of the drug |
| Healthcare | fear_inducing | rewrite guidance: prevention-and-facts angle only; outbreak titles may not use alarm words (list in pack) |
| Competitor | about_competitor | allow only factual comparison/"how this affects you" angles; no disparagement; no use of competitor trademarks in title patterns beyond nominative use; `needs_review` for negative competitor news (outage, lawsuit) |
| Client | about_client positive (award, launch) | allow ("amplify") with lens conversion; negative (lawsuit, incident, complaint) | `needs_review`, never auto-recommend |
| Excluded topics | matches `excluded_topics` | `block` |
| Embargo | text mentions "embargo"/"under embargo until" | `block` until the stated time passes; we only ever use publicly published sources, never press kits |

**Relevance score** (deterministic from categorical judgement):
```
band_w = {none:0, tangential:0.2, adjacent:0.45, strong:0.75, direct:0.95}
rel = band_w + 0.05*[key entity ∈ watch_entities] + 0.05*min(len(offering_links),2) + 0.05*[buyer_question matched]   (cap 1.0)
if region_relevance == "other_region_only": rel *= 0.3
if region_relevance == "unclear": rel *= 0.8
```

**Decay** (half-life by event type; pack-overridable):

| event_type | half-life | default TTL (expires_at) |
|---|---|---|
| model_release, product_launch | 48h | 7d |
| pricing_change, partnership, funding_or_ipo, incident | 36h | 5d |
| regulatory_approval, recall, regulatory_action | 7d | 21d |
| clinical_guideline, policy_change | 14d | 45d |
| research_publication | 7d | 21d |
| public_health_advisory, outbreak | 5d (while advisory active) | until source advisory superseded or season end |
| other | 48h | 7d |

`freshness = 0.5 ** (age_hours / half_life_hours)`, where age is counted from `first_published_at`.

**Demand multiplier:**
- unchecked → 1.0;
- empty → 1.05 (first-mover upside, unproven);
- emerging → 1.15, plus 0.05 per early outlier (cap 1.3);
- active → 1.2;
- crowded → 0.8, unless the angle is offering-specific (tutorial in client product) → 1.0.

**Priority:**
```
priority = rel * credibility.score * freshness * demand_mult * sensitivity_w
sensitivity_w = {high:1.0, medium:0.9, low:0.8, none:0.7}
```
Emit an opportunity if priority ≥ 0.30 [config] AND rel ≥ 0.45 AND credibility gate AND safety ∈ {allow, allow_with_guidance}. `needs_review` items go to a review queue (not shown to the client until approved).

**Flood control:** per client, sort by priority and keep the top "max opportunities/day" (§5.1). A diversity rule allows ≤ 2 from the same `key_entity.canonical` org and ≤ 1 per story. The rest are kept as signals (still usable by 09) with `suppression_reason=cap`.

**Urgency:**
- `now` if freshness ≥ 0.6 and saturation ∈ {empty, emerging};
- `this_week` if freshness ≥ 0.3;
- else `evergreen_window` (guidelines/approvals).

**Already covered:**
- If the client's own uploads (cached by 02) contain the canonical entity in title/description after `first_published_at` → suppress the opportunity (`client_already_covered`) and hand the signal to 09 instead.
- If the client covered it before the news broke (e.g. an older video on "Claude Fable 5"), that video is a refresh candidate (09).

### 5.12 news_opportunity quality gate (first-mover variant)

The assignment's gate requires ≥3 reference videos inside the window. For news this conflicts with the very value of being first. Two options:

**Lane design (proposed default):**
- `news_opportunity` cards are shown in a separate **News radar** lane, labelled "Early opportunity", so the main "What to make next" list keeps the strict assignment gate.
- In strict mode (`news.strict_reference_gate=true`), first-mover cards are suppressed and only `early_demand` cards (≥3 early videos) are shown.

`NewsEvidenceGate` (all must pass):
1. ≥1 tier1 source, OR ≥2 independent tier2 outlets (health: ≥1 tier1_authority/peer-reviewed).
2. Grounded key entity (§5.9).
3. ≥1 validated offering link and a conversion action (fit).
4. Safety ∈ {allow, allow_with_guidance}; for health, disclaimers attached.
5. Not stale, not already covered by the client in the window, not a copy: the suggested title pattern is not ≥ 0.8 `token_set_ratio` similar to any early-video title.
6. Each reference video present carries link, channel, published_at, views_at_age and outlier_ratio (or explicit "baseline unknown").

Confidence (separate from the main-lane label so it is honest):

| basis | condition | label |
|---|---|---|
| `early_demand` | ≥3 grounded early videos inside window AND outlier_count ≥ 5 across ≥ 3 channels | High |
| `early_demand` | ≥3 grounded early videos AND outlier_count ≥ 2 | Medium |
| `early_demand` | ≥3 early videos, < 2 outliers | Low |
| `first_mover` | < 3 early videos (or unchecked) | "First-mover" (rendered as Low + badge), with text "Few or no videos yet; evidence is the news itself" |

`confidence.outliers` / `channels` are always the actual counts, including 0. If `demand.status=unchecked_quota`, the card says "YouTube demand not checked today (quota)".

### 5.13 Expected range vs baseline (`NewsReactiveHistory`)

1. **Client's news-reactive videos** (own channel, last 365 days in cache, subject to 02's retention; note §9):
   - (a) videos whose title/description contains a grounded entity of any past `NewsSignal` story of this client, published within [first_published_at, +7d]; OR
   - (b) cold start, labelled "heuristic": title contains a `WatchEntity` alias together with a version token or a pack launch term ("launch", "new", "released", "approved", "update"). This uses the client's own keywords only, so no category inference.
2. Need views_at_day7 from 06 (same-age). Ratio_i = views_day7_i / client_baseline_day7 (06).
3. Ranges:
   - n ≥ 3 [config] → `"P25x–P75x (indicative, from your N news-reaction videos)"`;
   - n = 1–2 → `"limited history: your news-reaction videos ran 0.9x and 1.6x your baseline"`;
   - n = 0 → `"insufficient history — no past news-reaction videos; treat as an experiment"`.
4. Early videos' outlier ratios are **never** turned into the client's expected range (other channels ≠ client). They appear only in `why_it_works`.
5. No OAuth / no own channel (website-only onboarding) → "insufficient history (no channel connected)".

### 5.14 Format, patterns, CTA

**Format rules** (deterministic, reason codes):
- `short` if urgency=now AND event_type ∈ {model_release, product_launch, pricing_change, public_health_advisory}, OR the client's Shorts beat their long-form at the same age (06 `OwnPerformanceReport`);
- `tutorial` if an offering link exists AND the entity is something you can demo using the client's product (pack rule: model_release/product_launch in AI packs);
- `long_explainer` for clinical_guideline, regulatory_approval, policy_change, research_publication;
- crowded saturation → prefer `tutorial` (differentiated by the client's own product) over a generic explainer.
- Duration band: the client's best-performing band from 06 within that format.

**Title/thumbnail patterns:** top `TraitLift` traits with lift ≥ 1.3 and sample ≥ 06 minimum, filtered by format. Examples: title contains number, question form, year, ≤ 50 chars; thumbnail: text present, ≤ 4 words, high contrast. Each comes with the entity slot (e.g. pattern `"<Entity>: <number> things <outcome>"`). Patterns only, never a competitor's title (no-copy check, §5.12).

**CTA:** pick the conversion action linked by the judgement. Otherwise use the action attached to the linked offering. Otherwise use the profile's primary action. Health: CTA is "book a consultation / helpline", never "buy treatment".

### 5.15 Worked examples

**Runable (AI tools, sensitivity high).**
- Story: "Anthropic releases Claude Fable 5.2", 1 tier1_primary (anthropic.com via TavilyDomainSource) + 6 tier2 outlets.
- Judgement: model_release, confirmed, key entity "Claude Fable 5.2" (quoted), offering link "AI presentations" + value prop "uses frontier models", buyer question "Which AI model makes the best slides?", angle "show Fable 5.2 inside Runable building a deck".
- Demand: 2 early videos, 1 outlier → first-mover.
- Card: Short now, then tutorial within 72h; CTA "Start free"; expires in 7d; 09 gets keyword "Claude Fable 5.2" for the client's "How to make money using AI in 2026" description.

Note from the live Tavily check: the query "Anthropic Claude new model release" (week, news) returned mostly IPO coverage (CNBC, Yahoo Finance, BBC), plus two youtube.com results and a GitHub releases page. That is why the prefilter + judgement + `event_type` + exclusions above are needed: for Runable the IPO is `funding_or_ipo`, rated tangential, and does not become an opportunity.

**Apollo Hospitals (healthcare IN, medium + dengue seasonal high).**
- Live Tavily "dengue cases health advisory India" (week) returned:
  - TOI Kanpur dengue-prevention inspection (tier2, IN) → candidate;
  - an Instagram post with null date → dropped;
  - a pharmacy blog on "dengue vaccine approval in India" (tier3) → medical claim without authority → blocked unless CDSCO/ICMR confirms;
  - Victoria (AU), Canada travel and Florida advisories → `other_region_only` → down-weighted out.
- Opportunity, if an MoHFW/NCDC advisory exists: long explainer "Dengue warning signs and when to go to hospital", disclaimers, CTA "book a consultation / emergency helpline", no fear framing.

**Dental clinic (low):** weekly PubMed + domain queries usually yield nothing → "No news worth a video this week — normal for your niche" (no card).

**Auto dealer IN (medium):** OEM launch of an EV model + PM E-DRIVE subsidy change → tutorial "what the new subsidy means for <model> on-road price at <dealer city>" with CTA "book a test drive".

---

## 6. Data model

All timestamps are `timestamptz` (UTC). Global = not tenant-scoped (shared cache; contains no client data). Tenant = has `client_id`, with RLS/tenancy per 01.

| Table | Scope | Key columns | Indexes | Retention |
|---|---|---|---|---|
| `news_queries` | global | id, source_key, normalized_query, params jsonb, query_hash UNIQUE, cadence_minutes, next_run_at, last_run_at, last_status, subscriber_count | (next_run_at) WHERE subscriber_count>0 | delete 30d after subscriber_count=0 |
| `news_query_subscriptions` | tenant | client_id, query_id, origin enum, weight, profile_version | PK(client_id, query_id) | with client / profile re-plan |
| `news_query_runs` | global | id, query_id, started_at, finished_at, status, cost_credits numeric, item_count, error, provider_request_id | (query_id, started_at desc) | 90d |
| `news_items` | global | id, source_key, url, canonical_url, url_hash UNIQUE, domain, title, snippet (≤1500), language, published_at, published_precision, fetched_at, source_rank_score, external_ids jsonb, source_tier, flags text[], story_id, embedding float4[] (or pgvector, §13) | (story_id), (published_at desc), (domain), GIN(external_ids) | 90d (not YouTube data; snippet-only copies) |
| `news_stories` | global | id, first_published_at, last_published_at, representative_item_id, outlet_count, best_tier, version_tokens text[], centroid float4[], status | (first_published_at desc), (status) | 90d |
| `source_tiers` | global | domain PK, tier, packs text[], notes, updated_by, updated_at | — | permanent (config) |
| `news_judgements` | tenant | id, client_id, story_id, profile_version, model, judgement jsonb, grounded bool, tokens_in, tokens_out, created_at | UNIQUE(client_id, story_id, profile_version) | 180d |
| `news_signals` | tenant | id, client_id, story_id, judgement_id, relevance_score, credibility jsonb, demand_check_id, priority, urgency, half_life_hours, expires_at, refresh_keywords text[], refresh_eligible, safety jsonb, status, suppression_reason, created_at, updated_at | (client_id, status, expires_at), (client_id, priority desc) | signal row 180d; **demand-derived fields nulled at 30d** |
| `yt_demand_checks` | global (YouTube data) | id, entity_norm, day_bucket date, query, published_after, video_ids text[], early_videos jsonb, aggregates jsonb, quota_units, checked_at, status | UNIQUE(entity_norm, day_bucket) | **hard delete ≤ 30d** (retention cron, 01/11) |
| `news_review_queue` | tenant | id, client_id, signal_id, reason, assigned_to, decision, decided_at | (client_id, decision) | 180d |
| `news_feedback` | tenant | id, client_id, signal_id, recommendation_id, action (dismiss/used/not_relevant/unsafe), note, created_at | (client_id, created_at) | 1y (non-YouTube) |

Redis: `news:q:{hash}:{bucket}` (TTL = cadence), `news:lock:{hash}` (120 s), `news:budget:tavily:{yyyy-mm-dd}` (counter, TTL 48h), `news:breaker:{source_key}` (circuit state).

`NewsSignal.demand` keeps only ids + computed aggregates referencing `yt_demand_checks`. When that row is purged, the signal shows "demand data expired". Signals expire long before 30 days in practice.

---

## 7. External APIs & libraries

| API / lib | Facts used (verified) | Source |
|---|---|---|
| Tavily `/search` | Params: `topic` general/news/finance; `time_range` day/week/month/year (+d/w/m/y); `start_date`/`end_date` YYYY-MM-DD; `search_depth` basic/advanced/fast/ultra-fast; `max_results` 0–20 (default 10); `include_domains` (≤300), `exclude_domains` (≤150), `include_domains_mode` restrict/prefer; `country` **only with topic=general**; `language` + `filter_by_language`; `filter_by_published_date` (drops out-of-window and undated results); `max_age_hours` (cache age of Tavily's copy, not publish age); `exact_match`; `include_usage`; `auto_parameters` may upgrade to advanced. Response: results[title,url,content,score,published_date,raw_content,id], `usage.credits`, `response_time`, `request_id`. `published_date` = "best estimate of when published or last updated" | https://docs.tavily.com/documentation/api-reference/endpoint/search |
| Tavily credits | basic/fast/ultra-fast 1 credit, advanced 2; free 1,000 credits/month; PAYG $0.008/credit | https://docs.tavily.com/documentation/api-credits |
| Tavily rate limits | RPM per plan: free 100, PAYG/Project 1,000, Growth/Scale 1,500; all keys in an org share the limit; 429 + `Retry-After`. Error codes: 400, 401, 422, 429, **432 key/plan limit exceeded**, **433 PayGo limit exceeded**, 434 flex capacity, 500 | https://docs.tavily.com/documentation/rate-limits , search endpoint page |
| Tavily live check (2026-11-20) | Two `topic=news, time_range=week, basic` calls returned `usage.credits=1` each. `published_date` comes as RFC 1123 (`Mon, 16 Nov 2026 18:27:56 GMT`), often midnight (date precision), and can be `null` (Instagram). Results included youtube.com, facebook.com, instagram.com, a GitHub releases page and out-of-region advisories | live |
| Tavily terms | API use "solely for Customer's internal business purposes"; integration into Customer Applications permitted; no resale. Caching is not addressed → cache snippet/URL only, link out; **legal to confirm** cross-client cache reuse within our app | https://www.tavily.com/terms |
| tavily-python | Latest 0.8.5 (2026-10-06); `AsyncTavilyClient.search` exposes `time_range`, `start_date`, `max_age_hours`, `include_domains_mode`, `include_usage`, `exact_match`, `filter_by_language`, `**kwargs`. **Pin `>=0.8.5`** (pyproject has `>=0.5`) | PyPI JSON; github.com/tavily-ai/tavily-python `tavily/async_tavily.py` |
| PubMed E-utilities | Base `https://eutils.ncbi.nlm.nih.gov/entrez/eutils/`. Verified live: `esearch` JSON with `reldate=7&datetype=edat` translates to an entry-date window; `esummary&version=2.0` JSON fields incl. `pubdate`, `epubdate`, `sortpubdate`, `pubtype`, `lang`, `fulljournalname`, `elocationid` (DOI). Limits: 3 rps without key, 10 rps with `api_key` (all E-utilities combined). Include `tool` and `email`; NCBI disclaimer and copyright notice must be evident to users; no endorsement/advertising use; abstracts may be publisher-copyrighted → store metadata only. Large-job off-hours guidance: **not verified** (NCBI docs blocked by bot check) | https://support.nlm.nih.gov/knowledgebase/article/KA-05317/en-us ; https://www.ncbi.nlm.nih.gov/home/about/policies/ ; https://www.ncbi.nlm.nih.gov/books/NBK25499/ (could not load) |
| arXiv API | ≤ 1 request / 3 s, single connection, across all our machines; metadata CC0 (store/transform OK); e-prints personal/research use only → we never store PDFs; no implied endorsement | https://info.arxiv.org/help/api/tou.html |
| Hugging Face Hub API | `GET /api/models?sort=trendingScore&limit=N` returns `id, likes, trendingScore, downloads, tags` (verified live). Rate limits and ToS for commercial polling **not verified** | live; https://huggingface.co/docs/hub/api (to verify) |
| Lab RSS feeds | Verified 200: `openai.com/news/rss.xml`, `deepmind.google/blog/rss.xml`, `blog.google/technology/ai/rss/`, `huggingface.co/blog/feed.xml`. `anthropic.com/rss.xml` → 404 (use TavilyDomainSource). Feed terms are generally syndication; we store title/link/date/short description only | live |
| WHO DON API | `https://www.who.int/api/news/diseaseoutbreaknews` OData; `$orderby`, `$top`, `$select` work (verified live: 2026-DON627 published 2026-11-17). WHO: "Permission is required for commercial uses and licensing of WHO materials" → **metadata + link only**; no implied endorsement | live; https://www.who.int/about/policies/publishing/copyright |
| openFDA | 240 req/min; 1,000/day per IP without key, 120,000/day with key; data CC0 (credit requested); response includes "Do not rely on openFDA to make decisions regarding medical care" → we treat it as a signal to look for the FDA announcement, not a sole medical source | https://open.fda.gov/apis/authentication/ ; https://open.fda.gov/terms/ |
| FDA RSS | Official feed page could not be fetched (404/bot block). Feed URL `…/rss-feeds/press-releases/rss.xml` **unverified** → use TavilyDomainSource `fda.gov` until verified | — |
| GDELT DOC 2.0 | `api.gdeltproject.org/api/v2/doc/doc`, `mode=artlist`, `format=json`, `maxrecords` ≤ 250, `timespan`, `sort=datedesc`, `sourcecountry:`/`sourcelang:` operators; 65 machine-translated languages; data free for commercial use **with citation + link to GDELT**. Live: 429 "limit requests to one every 5 seconds" | https://www.gdeltproject.org/about.html ; https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/ |
| Google News RSS | Terms: personal non-commercial display only; no reformatting/displaying results → **excluded**. Page is dated (2006/2011), but no newer permissive terms were found | https://www.google.com/intl/en_us/terms_google_news.html |
| PIB RSS | `pib.gov.in/RssMain.aspx?ModId=6&Lang=1&Regid=3` returns RSS (verified). Reuse terms **not verified** → metadata + link only until checked | live |
| CDSCO | No feed/API found; `robots.txt` 404. Use TavilyDomainSource only (no crawling) | live |
| YouTube `search.list` | Docs header (fetched 2026-11-20): **"Quota impact: 100 calls per day. A call to this method has a quota cost of 1 unit in the Search Queries quota bucket."** Params used: `q` (supports `|` OR and `-` NOT), `type=video`, `publishedAfter` RFC 3339, `order` (relevance default; date relies on a possibly delayed index; non-relevance orders with date filters may be incomplete), `maxResults` 0–50, `regionCode`, `relevanceLanguage`, `videoDuration` short (<4m)/medium/long | https://developers.google.com/youtube/v3/docs/search/list |
| YouTube `videos.list` | 1 unit per call, ≤ 50 ids (02 owns) | 02 plan / API docs |
| Anthropic SDK | Structured outputs via `messages.parse(output_format=PydanticModel)` or `output_config.format=json_schema`; `stop_reason="refusal"` possible. Sonnet 5.5 (`claude-sonnet-5-5`) $2/$10 per MTok in/out, cache reads $0.10/MTok, 1M context. Haiku 4.5 $1/$5. (Newer `claude-haiku-5-5` at $0.10/$0.50 ≤100K prompt, §13.) Model naming fact used in grounding tests: `claude-fable-5-2` exists; "Claude Mythos 5.2" is the same model under restricted access | bundled skill docs `claude-api/python/claude-api/tool-use.md`, `shared/models.md`, `shared/model-migration.md` |
| fastembed | Embeddings for clustering/prefilter; model shared with 05 (`BAAI/bge-small-en-v1.5` en; multilingual e.g. `intfloat/multilingual-e5-small` for hi/regional — **availability in fastembed to verify**) | https://qdrant.github.io/fastembed/ (to verify) |
| rapidfuzz | `token_set_ratio`, `partial_ratio` | https://rapidfuzz.github.io/RapidFuzz/ |
| New deps (proposed) | `feedparser` (RSS/Atom), `tldextract` (PSL; configure offline snapshot to avoid network fetch), a language detector (`lingua-language-detector` or `fast-langdetect`; pick in implementation), `python-dateutil` (only if a source sends non-RFC dates) | to add to pyproject (01 owns deps) |

---

## 8. Edge cases & failure modes

| # | Case | Detection | Handling | User-facing message |
|---|---|---|---|---|
| 1 | No news for weeks (dental) | 0 signals ≥ threshold in the sensitivity's window | Normal outcome. No card. Keep the low cadence. After 4 empty weeks, suggest reviewing watch entities | "No news worth a video this week — normal for your niche. We'll keep watching <entities>." |
| 2 | Flood of news (AI launch week) | > max_opportunities candidates | Rank by priority, diversity cap (≤2 per org, 1 per story); rest kept as signals for 09 with `suppression_reason=cap` | "Showing the 5 most relevant of 23 news items. See all in News radar." |
| 3 | Same story across many outlets | url_hash / DOI / fuzzy / embedding cluster | One story, outlet_count raises credibility; references list ≤ 5 best-tier members | Card shows "covered by 9 outlets" |
| 4 | Version-adjacent stories ("Fable 5.1" vs "5.2") | version-token guard | Never merged; separate stories | — |
| 5 | Conflicting reports (dates, specs, case counts) | judgement `conflicting_reports=true` or numeric mismatch across tier1/tier2 | Credibility ×0.7; cite only highest-tier sources; status developing; health → `needs_review` | "Reports differ — figures from <authority> shown." |
| 6 | Fake / satire / content farm | domain in `blocked` tier | Item dropped before clustering | — |
| 7 | Single tier3 source only | best_tier = tier3, outlets = 1 | Fails credibility gate; kept as `watch` signal; re-evaluated when better coverage arrives | (not shown) |
| 8 | Press-release-only story | all members flagged press_release | Credibility capped 0.5; needs an independent outlet or primary source | — |
| 9 | Paywalled articles | `paywalled` flag | Use title + provider snippet only; never extract or bypass; credibility not reduced; prefer non-paywalled member as representative link | Link labelled "may require subscription" |
| 10 | Non-English news (Hindi for Apollo) | language detector ≠ en | Allowed if language ∈ profile.languages; judgement prompt handles source language; grounding checked in original script; card shows original title + translated gloss (labelled) | "Source in Hindi (translated)" |
| 11 | Non-English, language not in profile | language ∉ profile.languages | Dropped unless tier1_primary for a watch entity | — |
| 12 | Regional mismatch (US FDA approval for Apollo IN) | `region_relevance=other_region_only` | Relevance ×0.3; for health, require a regional authority or `global` significance (e.g. WHO) | If shown: "US approval — not yet approved in India" guidance |
| 13 | News about the client (press coverage) | `about_client` / brand alias lexical hit | Positive → "amplify" opportunity (lens conversion), always considered even at sensitivity none; negative → `needs_review`, never auto-shown | "You were covered by <outlet> — consider a response video." |
| 14 | News about a competitor | `about_competitor_ids` | Only factual/comparative angles; negative competitor news → `needs_review`; no competitor title copying | "Competitor news: <X> launched <Y>. Angle: how <your offering> compares." |
| 15 | Stale news re-surfacing (recap, anniversary, re-indexed old article) | event_date < recency window, or `continuously_updated` flag, or story existed > max age | Suppress `stale_resurfaced` | — |
| 16 | Continuously updated pages (GitHub releases, live blogs) | path patterns / flag | Not used as stories; primary-source support only if paired with dated item | — |
| 17 | Tavily credits exhausted (432/433) | HTTP status | Circuit breaker opens until next billing reset/admin action; switch news pack queries to GDELT fallback + feeds; mark run `degraded` | "News search limited today; using official feeds only." |
| 18 | Tavily 429 | status + Retry-After | Honour Retry-After, exponential backoff (tenacity), max 3; reschedule query +15 min | — |
| 19 | Tavily down (5xx/timeouts) | errors > 3 in 10 min | Breaker open 30 min; serve cached stories; degraded flag | Same as 17 |
| 20 | Global Tavily daily budget reached | Redis counter ≥ cap | Defer lower-priority queries (offering, competitor) first; client and entity queries last | — |
| 21 | PubMed rate limit / NCBI outage | 429/5xx | Token bucket 3 rps (10 with key); retry; skip run | — |
| 22 | PubMed future `pubdate` ("2027 Mar") | pubdate > now | Use edat/epubdate | — |
| 23 | Retracted publication | pubtype contains retraction | Block | — |
| 24 | Preprint only (arXiv/medRxiv) | `preprint` flag | AI: allowed, labelled "not peer reviewed"; health: blocked as sole source | "Preprint — not peer reviewed" |
| 25 | RSS feed moved/404/changed format | feed health check | Breaker; alert ops; pack falls back to TavilyDomainSource for that domain | — |
| 26 | Feed items without dates | no pubDate | precision none → use first_seen_at only if feed is tier1_primary; otherwise drop | — |
| 27 | Ambiguous entity names ("Gemini", "Fable", "Apollo", "Ranger") | entity in ambiguity list or demand search grounding rate < 50% | Require co-occurrence with org/alias in text; demand query adds disambiguator; early videos must contain both | — |
| 28 | LLM hallucinates entity or ids | GroundingValidator | Drop entity/ids; no grounded key entity → drop signal (`ungrounded_entity`); metric logged | — |
| 29 | LLM refusal / timeout / invalid output | stop_reason / exception / schema failure | 1 retry; then suppress `llm_unavailable`; health/client stories → review queue | — |
| 30 | Prompt injection inside article text | delimiters + schema-only output + id validation | Instructions inside articles cannot add ids or fields; suspicious phrases logged | — |
| 31 | YouTube search quota exhausted / bucket denied | QuotaManager denial / 403 quotaExceeded | `demand.status=unchecked_quota`; multiplier 1.0; card says so; retry next day if still live | "YouTube demand not checked today (quota)." |
| 32 | Demand search returns unrelated videos | grounding (entity in title/description) | Excluded; if 0 remain → `empty` | — |
| 33 | Early videos from channels without baselines | 06 returns None | outlier_ratio null; not counted as outlier; lightweight baseline fetch for top 10 if budget allows | "Outlier ratio unavailable (new channel)" |
| 34 | Early videos deleted/private at render time | videos.list missing id | Drop from references; recompute counts | — |
| 35 | Crowded topic (big channels already covered it) | saturation crowded | Multiplier 0.8; push tutorial/offering-specific angle | "Already crowded — differentiate with a Runable walkthrough." |
| 36 | Client already published on it | own uploads contain entity after first_published_at | Suppress opportunity; signal → 09 refresh | "You already covered this — see description refresh." |
| 37 | Tragedy/death/disaster news | safety flags | Block (brand safety); health advisories with deaths → guidance only | — |
| 38 | Fear-inducing outbreak framing | fear_inducing flag / alarm words | allow_with_guidance: prevention/facts angle, banned title words | Guidance block on card |
| 39 | Unverified health claim ("cure", "breakthrough") | medical_claim without authority | Block | — |
| 40 | Rumour/leak (AI model leak) | confirmation rumour_or_leak | Non-health: allowed only with label, confidence Low, never 09-eligible; health: block | "Unconfirmed report" badge |
| 41 | Embargoed or "coming soon" announcements | embargo text / officially_announced_upcoming | Embargo → block; upcoming official → "prepare" opportunity with expires at event date + 3d | "Announced for <date> — prep now." |
| 42 | Seasonal predictable event (dengue season) without breaking news | seasonal window active, no new story | Seasonal queries run; need a current-season authoritative source; otherwise no card | — |
| 43 | Duplicate opportunity across runs | existing signal for (client, story) | Update in place; re-show only if priority rose ≥ 0.15 or confirmation upgraded | — |
| 44 | Same story for many clients | shared story id | Judged per client (their profile); shared fetch/demand cache; no client data in global tables | — |
| 45 | Profile edited mid-run | profile_version changes | Run uses the snapshot version; next run re-plans queries and re-judges | — |
| 46 | FreshnessProfile missing (03 failed) | profile.freshness is None | Default sensitivity medium, entities = competitors + niche keywords; flag `freshness_profile_default` | "Using default news settings — review in settings." |
| 47 | Time zones / DST | all stored UTC | Day buckets (budgets, demand cache) in UTC; display and "today" in client tz; urgency based on hours, not dates | Times shown in client tz |
| 48 | Date-only precision (midnight GMT) | precision=date | Age computed from midday of that date (±12h error bound); never marked "hours ago" | "Published Nov 17" |
| 49 | Future-dated items | published_at > now+6h | Replace with fetched_at; flag | — |
| 50 | Social posts in results | social flag | Cannot be sole support; excluded from Tavily via exclude_domains | — |
| 51 | YouTube URLs in Tavily results | domain youtube.com | Excluded from news; ids passed to official-API demand check only | — |
| 52 | News whose only link to the client is generic ("AI is booming") | offering_links empty | Relevance capped tangential → no card | — |
| 53 | Political/policy news (EV subsidy) | political flag | Allowed for neutral explainers of facts; partisan framing → needs_review | — |
| 54 | Client opted out of news or topic excluded | settings / excluded_topics | No queries / block | — |
| 55 | Multi-region client (Apollo IN + international patients) | regions list | Primary region weight 1.0, secondary 0.6 | — |
| 56 | Tavily `published_date` = last-updated, not original | item dated recently but story text references older event | Judgement event_date check (row 15) | — |
| 57 | Very new story with only 1 primary source (lab blog minutes old) | tier1_primary, outlets=1 | Passes gate (primary source); confidence First-mover; urgency now | "Just announced by <org>." |
| 58 | Retention: demand data > 30 days | retention cron | Purge `yt_demand_checks`; null derived fields | "Demand data expired" |
| 59 | LLM budget cap per client reached | token counter | Judge top-K only; others → `suppression_reason=llm_budget` | — |
| 60 | Clock skew between workers | NTP assumption + DB `now()` for comparisons | All age computations use DB time | — |

---

## 9. Policy & guardrail check

| Feature | Guardrail | Status |
|---|---|---|
| News fetching (Tavily, feeds, PubMed…) | Not YouTube data → not subject to YouTube API rules; terms per source checked (§7); Google News excluded; WHO/PIB metadata only | OK; legal review for Tavily cross-client caching and PIB |
| Demand check via search.list/videos.list | Official API only; cached and shared across clients; dedicated budget bucket | OK |
| Early-video metrics | API data + simple maths (views/hour, ratio to own-channel baseline at same age) | OK |
| Never merge YouTube with other sources into one metric | News credibility and YouTube demand are **separate fields**. `priority` is an internal ranking score that is never displayed as a metric; the card shows news facts and YouTube numbers side by side, labelled | OK, with this interpretation flagged for review (§13) |
| No revenue/monetisation estimates | None computed | OK |
| No audience demographics | Regional relevance comes from article content and client-declared regions, not audiences | OK |
| No inferring a video's content category | Early videos selected by exact entity match in title/description; news-reactive history uses the client's own watch entities/keywords | OK, flagged as part of topic approach |
| 30-day refresh/delete | `yt_demand_checks` hard purge ≤ 30d; derived fields nulled | OK |
| Thumbnails | Only 06's simple traits used for patterns | OK |
| Deterministic pipeline; LLM only writes cards | Exception permitted by owner decision (bounded LLM at news-radar edge). The LLM output is categorical classification + id linking + grounded quotes; every number (scores, counts, ratios, ranges) is code-computed; 10 writes the card from structured evidence | OK, documented exception |
| ≥3 reference videos | First-mover variant in a separate lane, honestly labelled; strict mode available | Deviation flagged (§13) |
| Honest confidence & thin data | First-mover label; "insufficient history"; "demand not checked" | OK |
| No copying | Similarity check against early-video and competitor titles; patterns not titles | OK |
| Healthcare safety | Authority requirement, disclaimers, no fear framing, review queue | OK |
| NCBI/openFDA/GDELT attribution | NCBI disclaimer + copyright notice visible; "Data provided by the U.S. FDA" credit; GDELT citation + link | UI must render (13) |

---

## 10. Performance, cost & scaling

**Latency (per-client NewsRadarStage in daily refresh; budget ≤ 30 s of the 2-min target):**
- read cached stories + prefilter: < 1 s;
- LLM judgements for new stories only (high: ≤ 15, concurrency 5, ~4–6 s each): ~15–20 s worst case, typically 0–5 s because most stories were already judged;
- demand checks from cache: < 1 s; live search (if any): ~1–2 s each, max 3;
- build drafts: < 1 s.
- Fetching runs off the critical path (cron).

**Tavily credits** (basic = 1 credit):

| sensitivity | queries/client | polls/day | credits/day/client (unshared) | with typical 40–60% sharing in a niche |
|---|---|---|---|---|
| high | 12 | 4 | 48 | ~20–30 |
| medium | 8 | 1 | 8 | ~4–6 |
| low | 4 | 1/7 | ~0.6 | ~0.3 |

Free tier (1,000/month) supports roughly 1 high client, or ~5 medium. PAYG $0.008/credit → high ≈ $0.16–0.38/day/client, medium ≈ $0.03–0.06. Config: global daily cap `news.tavily_daily_credit_cap` [300] and per-client cap.

**LLM (Sonnet 5.5, $2 in / $10 out per MTok; cache read $0.10/MTok):**
- per judgement ≈ 1.2k cached system (profile) + 1.5k story + 0.4k output → ≈ $0.0031 + $0.004 ≈ **$0.007**;
- high client ≈ 15/day → ~$0.11/day; medium 8 → ~$0.06; low → <$0.01.
- Card writing is in 10's budget.

**YouTube quota:**
- global news reserve 15 search calls/day (of 100) [config] + videos.list ≈ 1 unit per check + baselines ≤ 20 units per check;
- one check per entity per UTC day is shared by all clients;
- worst case ≈ 15 × 21 ≈ 315 units/day from the 10,000 pool, plus 15 search calls.
- Reported to 12's ops report per client: `news_quota_units`, `news_search_calls`, `tavily_credits`, `llm_tokens`.

**Scaling:**
- Global tables grow with the number of distinct queries, not clients.
- Clustering is incremental over a 7-day window (in-memory per source pack batch; at > 50k items/week move centroids to pgvector, §13).
- arq workers scale horizontally; per-source token buckets live in Redis so the limits hold across workers (arXiv single-connection rule enforced with a Redis lock).

---

## 11. Testing strategy

- **Unit:**
  - URL canonicaliser (utm/amp/m./trailing slash table);
  - RFC 1123 / date-only / null / future date parsing (fixtures from the live Tavily responses, with keys and request ids removed);
  - version-token guard ("Claude Fable 5.1" vs "5.2", "GPT-5" vs "GPT-5.1", "iPhone 17" vs "17 Pro");
  - tier resolver; credibility formula; decay; priority; saturation thresholds; flood cap and diversity; urgency; format rules; expected-range percentiles; gate logic for first-mover vs early_demand.
- **Grounding tests:**
  - LLM output with an entity not in the text ("Claude Fable 5.3") is dropped;
  - quote mismatch is dropped;
  - unknown offering id is dropped;
  - alias "Claude Mythos 5.2" ↔ "Claude Fable 5.2" merges for demand but keeps the display form;
  - Unicode/dash normalisation.
- **Adapter contract tests (respx):** Tavily (200 / 429 with Retry-After / 432 / 433 / 500), PubMed esearch+esummary JSON (incl. retraction pubtype, 2027 pubdate), arXiv Atom, HF JSON, RSS/Atom variants (no dates, HTML descriptions), WHO OData, openFDA, GDELT 429. Each adapter must satisfy the `FreshnessSource` protocol test suite.
- **Golden tests** (fixed fixtures → expected signals/drafts, LLM stubbed with recorded structured outputs):
  1. Runable + "Claude Fable 5.2" launch cluster → one news_opportunity, first-mover, tutorial/short, CTA signup, 09 keyword.
  2. Runable + IPO stories → no opportunity (tangential).
  3. Apollo + live dengue fixture → TOI kept; Instagram dropped; pharmacy vaccine claim blocked; foreign advisories down-weighted.
  4. Apollo + MoHFW advisory → long explainer with disclaimers.
  5. Dental + empty week → no card and the "normal for your niche" message.
  6. Satire domain → dropped.
  7. Competitor outage → needs_review.
  8. Client negative press → needs_review.
- **Contract test:** `NewsOpportunityDraft` → 07 → `Recommendation` validates against the shared contract schema (+ proposed extensions); every number in the card text equals a structured-evidence number (10's validator).
- **Quota/budget tests:** QuotaManager denial → `unchecked_quota` path; Tavily budget cap ordering; shared demand cache hit across two clients.
- **Retention test:** purge job deletes `yt_demand_checks` older than 30d and nulls derived fields.
- **Live smoke (opt-in `-m live`, not CI default):** 1 Tavily basic news call (1 credit), 1 PubMed esearch, 1 RSS fetch. Asserts the response shapes match the adapters. No search.list.
- **Evaluation hooks (12):**
  - relevance precision@5 on a hand-labelled set (≥ 50 stories per demo niche, with labels relevant/irrelevant/unsafe);
  - grounding failure rate;
  - backtest: at frozen T, signals vs videos published after T containing the entity — did videos published within 72h beat their channel baseline at day 7 more often than videos later than 7d? (reported, not promised);
  - human rubric includes "would publish" and "safe".

---

## 12. Implementation task breakdown

| # | Task | Size | Depends on |
|---|---|---|---|
| 1 | Domain models (§3.2) + `NewsSettings` + reason codes | S | 01 config |
| 2 | `FreshnessSource` port, registry, source-pack YAML loader + 3 packs (ai_frontier, health_in, dental) | M | 1 |
| 3 | Alembic migrations for §6 tables + repositories | M | 01 |
| 4 | Normaliser: canonical URL, dates, language, flags, tier resolver + seed `source_tiers` | M | 2, 3 |
| 5 | Tavily adapters (news + domain) with budget counter, breaker, 429/432/433 handling; bump tavily-python ≥ 0.8.5 | M | 2, 4 |
| 6 | RSS, PubMed, arXiv, HF, WHO, openFDA, GDELT adapters + rate-limit buckets | L | 2, 4 |
| 7 | QueryPlanner + subscriptions + CadencePolicy + `news_poll` cron | M | 3, 5, 03 FreshnessProfile, 04 competitors, 11 cron |
| 8 | StoryClusterer (exact/fuzzy/embedding + version guard) | M | 4, 05 Embedder |
| 9 | Prefilter + business vector | S | 8 |
| 10 | NewsRelevanceJudge (Sonnet structured output) + prompt + caching | M | 10 LLMClient |
| 11 | GroundingValidator + alias tables | M | 10 |
| 12 | CredibilityScorer + SafetyPolicy set (brand, healthcare, competitor, client, embargo) + review queue | L | 11 |
| 13 | YouTubeDemandChecker (local-first, shared cache, quota bucket, grounding, outlier ratios) | M | 02 gateway/quota, 06 ViewsAtAge |
| 14 | PriorityScorer, DecayModel, flood cap, urgency, already-covered check | M | 12, 13 |
| 15 | NewsReactiveHistory + expected range | S | 06 baselines |
| 16 | NewsOpportunityBuilder (format, patterns, CTA, compliance) + NewsEvidenceGate | M | 14, 15, 06 TraitLift, 07 gate framework |
| 17 | NewsRadarStage + NewsSignal API for 09 + run events | S | 11 pipeline, 16 |
| 18 | Retention job hooks + ops metrics | S | 01, 12 eval |
| 19 | Tests (§11) + golden fixtures + live smoke | L | all |
| 20 | Remaining packs (health_global, auto_in/us, home_services_us) + tier lists review | M | 2, 4 |

Critical path for the Apollo demo: 1–5, 7–12, 13 (can stub as `unchecked`), 14–17.

---

## 13. Open questions / proposed changes to shared context

1. **Contract extensions for `news_opportunity`:**
   - `reference_sources[]` (url, outlet, tier, published_at, title);
   - `reference_videos[].views_at_age {age_hours, views}`, because `views_day7` is null for videos under 7 days old;
   - `confidence.basis` and the `"First-mover"` label;
   - `expires_at`, `urgency`, `format_suggestion`, `conversion_action`, `compliance{disclaimers, review_required, safety_flags}`.
   07 and 10 should adopt these as optional fields on all types.
2. **Quality-gate variant:** news cards go in a separate "News radar" lane under `NewsEvidenceGate`, with `news.strict_reference_gate` to enforce ≥3 videos. This needs owner sign-off because the assignment says a card failing any rule "is not shown".
3. **search.list quota wording:** the live docs now say "100 calls per day … 1 unit in the Search Queries quota bucket", while shared context says 100 units/call. 02 should model the separate call bucket as binding, and should **reserve ~15 calls/day for news demand checks** (`search_news_demand`).
4. **LLM low tier:** `claude-haiku-5-5` is available ($0.10/$0.50 per MTok ≤100K prompt vs Haiku 4.5 $1/$5) per the bundled SDK docs. Consider switching the low tier. 08 uses Sonnet for judgements as requested.
5. **`FreshnessProfile` schema:** adopt the field list in §3.1 (03 owns it). In particular `watch_entities` with aliases, `regions`, `languages`, `seasonal_calendar`, `is_regulated_health`.
6. **New shared domain names:** `NewsStory`, `NewsJudgement`, `GroundedEntity`, `CredibilityAssessment`, `DemandCheck`, `EarlyVideo`, `SafetyDecision`, `NewsOpportunityDraft`, `SourceTier`, `EventType`.
7. **Dependencies:** pin `tavily-python>=0.8.5`; add `feedparser`, `tldextract` (offline PSL), and a language detector. 01 decides.
8. **Embedding storage:** use float4[] + in-app cosine now. Decide whether to enable pgvector on PG 18 (shared with 05).
9. **Legal checks before production:**
   - Tavily terms on caching/sharing results across our tenants (internal use + "Customer Applications" integration seems to allow it);
   - PIB and HF API reuse terms;
   - WHO metadata-only use;
   - India's Drugs and Magic Remedies Act / medical advertising rules for hospital content;
   - NCBI disclaimer display in UI.
10. **"Never merge into one metric":** confirm that an internal, never-displayed ranking score mixing news credibility and YouTube demand is acceptable. Alternative: rank lexicographically (credibility gate → relevance → YouTube demand) with no blended score.
11. **News entities as temporary TopicRefs:** let confirmed `key_entity` values become time-boxed, client-approved TopicRefs so 05 can match videos to them (keeps topic grouping tied to client-approved keywords). Flag for review with 05.
12. **Health review:** should every healthcare `news_opportunity` require human approval before display (`review_required=true` by default for `is_regulated_health`)? Recommended yes for the Apollo demo.
13. **Ops email** for NCBI `email` param, and an optional `NCBI_API_KEY` (already in `.env.example`); `OPENFDA_API_KEY` should be added to `.env.example` (01).
