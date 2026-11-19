# 04 — Competitor discovery

Turns a BusinessProfile into a verified, scored set of 3–10 competitor YouTube channels, plus a wider niche set, benchmark channels, and creator seeds. Each channel comes with evidence the user can inspect, confirm, reject, or extend.

Consistent with `00-shared-context.md`. Where research contradicts it, the plan stays consistent and the conflict is raised in §13.

---

## 1. Purpose & business value

The assignment's client config has "3–10 competitor channels" (assignment p.2). Every competitor-lens answer depends on getting that set right: "How are my competitors doing?", the competitor-set pattern lift, and gap reasons like "2 competitors cover this; you don't" (assignment p.4). If the set is wrong, every downstream card is confidently wrong. If it is empty, the client has to work it out by hand, which is the friction GrowthHacker exists to remove.

How this drives conversions for an onboarded business:
- **Right rivals → right gaps.** Direct business competitors chase the same buyers. Seeing which buyer questions they answer on YouTube, and which the client doesn't, is the most conversion-relevant gap signal available from public data.
- **Content competitors → where buyers actually learn.** For buyer questions such as "knee replacement cost in Delhi", the channels that win are often doctor-creators or aggregators (Practo), not hospital rivals. Tracking them as a separate niche set feeds trending, outliers, and the creator ranker without polluting competitor comparisons.
- **Verified ownership → trust.** `@Apollo247` resolves to an unrelated coaching channel (shared context §1). Naive handle guessing fails for 3 of 5 major Indian hospital chains (live check, §7.4). One wrong channel in the set poisons baselines and makes the demo look careless. Every channel therefore carries an ownership verdict with evidence.
- **Zero-config demo.** Auto-confirm mode yields a defensible 5-competitor set (the assignment's demo needs "5 competitors each" on 3 niches) with no manual work.

## 2. Scope / non-scope

**In scope**
- Defining competitor types and the downstream set each one feeds.
- Candidate generation from 5 signals: LLM (Sonnet), Tavily web search, website competitor hints, YouTube niche-search frequency (shared cache), and user input.
- Entity merge/dedup, self/subsidiary exclusion, hallucination guard.
- Brand → official channel resolution at minimum quota. Ownership verification with evidence. Multi-channel brands.
- Scoring, ranking, confidence, proposal of sets. Confirm/reject/add/promote flow with 3..10 enforcement. Auto-confirm mode.
- Parsing user-supplied URLs (any YouTube URL form or website URL).
- Daily drift detection (no extra quota) and periodic re-discovery (new entrants).
- Persistence of candidates, evidence, decisions. A cross-client brand→channel resolution cache.

**Not in scope**
- Website crawling internals and BusinessProfile extraction (03). This plan consumes `SiteCrawler` and BusinessProfile.
- Quota manager, shared search cache, channel/video ingest and snapshots (02). This plan calls `YouTubeGateway`.
- Topic matching internals (05). This plan calls the lexical matcher for content overlap.
- Competitor analytics: scorecards, cadence, outliers (06). Creator ranking (07).
- REST router and SSE plumbing (11). This plan specifies the endpoints and events it needs.
- UI (13). This plan specifies what evidence must be visible.
- Other platforms (Instagram/TikTok URLs are rejected). Audience inference of any kind.

## 3. Inputs & outputs

### 3.1 Competitor taxonomy (decision)

| Relation | Definition | Example (Apollo, India) | Downstream role (default) |
|---|---|---|---|
| `direct` | Sells the same offerings to the same kind of buyer in an overlapping geography | Fortis, Max Healthcare, Medanta, Manipal, Narayana Health | **competitor_set**: full ingest of uploads in window; ChannelScorecard (06); competitor-set pattern lift (06); gap analysis (07) |
| `direct_public` | Same offerings and geography, non-commercial or public operator | AIIMS | competitor_set eligible, flagged `public_institution`, ranked below commercial directs; user decides |
| `aggregator` | Marketplace or directory that intermediates the client's buyers | Practo | **niche_set** by default (competes for buyer questions, not the service itself); user may promote |
| `content` | Channels that repeatedly win the client's buyer questions or keywords on YouTube but aren't business rivals: media, educators, individual creators | Doctor-creators, health explainers | **niche_set** (wider-niche lift, TrendingReport); individuals also go to **creator_seed** for 07's creator ranker |
| `benchmark` | Best-in-class in the same category, other geographies; aspirational | Mayo Clinic, Cleveland Clinic | **benchmark_set** (≤3): used only for format, title, and thumbnail pattern ideas, labelled "benchmark". Never in competitor cadence comparisons, gap claims, or `expected_range_vs_baseline` |
| `self` / `subsidiary` / `parent` | The client, its group brands, its owner | Apollo Hospitals, Apollo 24\|7 | **excluded**. Subsidiaries are offered as "your other channels?" (handed to client config), never as competitors |
| `partner` / `supplier` / `insurer` / `unrelated` | Appears in signals but doesn't compete | TPAs, device makers, pharma peers in finance listings | **excluded**, kept with reason so re-discovery doesn't resurface it |

Rules:
- The **3–10 limit applies to `competitor_set` channels only** (assignment p.2). Niche set ≤ 20 channels, benchmark ≤ 3, creator seeds ≤ 30. All are configurable.
- If fewer than 3 eligible `direct` channels exist (common for local SMBs such as a single dental clinic), the proposal fills the competitor set with the highest-ranked `content`/`aggregator` channels and flags `set_contains_content_competitors`. 06/07 then phrase gaps as "channels winning your buyer questions" rather than "competitors".
- The user can move any channel between sets (promote/demote). The UI shows relation and role separately.

### 3.2 Inputs (consumed)

```text
DiscoveryContext
  client_id: UUID
  run_id: UUID
  trigger: Literal["onboarding","scheduled","profile_change","manual"]
  profile: BusinessProfile            # from 03 (fields used below)
  own_channel_ids: list[str]          # client's own (may be empty)
  topic_refs: list[TopicRef]          # client keywords + buyer questions (03/05)
  user_inputs: list[UserCompetitorInput]
  known_entities: list[CompetitorEntity]   # previous runs incl. rejections (suppression)
  settings: CompetitorDiscoverySettings
  budget: DiscoveryBudget             # yt_units, search_calls, tavily_credits, llm_usd, wall_seconds

BusinessProfile fields used (owned by 03; see §13 for additions requested)
  brand_name, brand_aliases[], website_domain, offerings[Offering], value_propositions[],
  locations[{city, region, country_iso2}], primary_markets[country_iso2],
  business_model: Literal["b2c","b2b","b2b2c","marketplace","public"] | None,
  related_brands[{name, domain, relation: subsidiary|parent|sister}],
  competitor_hints[CompetitorHint], niche_keywords[], buyer_questions[BuyerQuestion], language

UserCompetitorInput
  raw: str                            # name, website URL, any YouTube URL, @handle, channel id
  intended_role: Literal["competitor_set","niche_set","benchmark_set"] = "competitor_set"
```

### 3.3 Outputs (owned domain objects)

```text
CandidateSource
  kind: Literal["llm","web_search","website_hint","niche_search","user","rediscovery_news"]
  ref: str                            # query string, URL, or "user"
  snippet: str | None                 # ≤ 500 chars of supporting text (web data, not YouTube)
  retrieved_at: datetime

OwnershipSignal
  name: Literal["site_links_channel","jsonld_sameas","embed_majority","channel_links_site",
                "uploads_link_site","name_similarity","websearch_youtube_hit","llm_adjudication",
                "sanity_penalty","user_asserted"]
  weight: float
  detail: str                         # human-readable, e.g. "footer of medanta.org links /user/MedantaTheMedicity"
  source_url: str | None

OwnershipVerdict
  label: Literal["verified","likely","uncertain","rejected"]
  score: float                        # 0..1, deterministic sum of signals (clamped)
  signals: list[OwnershipSignal]
  llm_reason: str | None              # only when adjudicated; never upgrades to "verified"

ChannelMatch
  channel_id: str                     # canonical (UC…); handles are never stored as identity
  handle: str | None                  # snippet.customUrl
  title: str
  resolution_method: Literal["site_link","jsonld_sameas","embed_vote","websearch_youtube",
                             "for_username","for_handle","search_list","user_url","video_owner",
                             "playlist_owner","resolution_cache"]
  variant: Literal["main","regional","language","specialty","person","unknown"]
  is_primary: bool
  ownership: OwnershipVerdict
  youtube: ChannelEvidence            # API-derived, refreshed or deleted ≤ 30 days
  youtube_scores: YouTubeRelevance
  flags: list[str]                    # inactive, size_much_larger, shorts_only, hidden_subs, …

ChannelEvidence                       # copied from 02's ChannelRecord at fetch time
  subscriber_count: int | None        # None when hiddenSubscriberCount
  video_count: int
  country: str | None                 # snippet.country (self-declared; may be empty)
  published_at: datetime
  uploads_90d: int
  last_upload_at: datetime | None
  recent_upload_ids: list[str]        # ≤ 50
  fetched_at: datetime

BusinessRelevance                     # web-derived only (never mixed with YouTube data)
  offering_overlap: float             # 0..1
  matched_offerings: list[{offering_id, verdict: yes|partial, quote, source_url}]
  geo_overlap: float                  # 0..1
  geo_basis: str                      # "same city: Delhi", "same country: IN"
  source_agreement: float             # 0..1
  tier: Literal["high","medium","low"]

YouTubeRelevance                      # API-derived only
  activity: float                     # 0..1
  content_overlap: float              # 0..1
  matched_topic_refs: list[{topic_ref_id, upload_matches: int}]
  size_ratio: float | None            # candidate subs / client subs
  size_band: Literal["peer","larger","much_larger","smaller","much_smaller","unknown"]
  score: float                        # 0..1

CompetitorCandidate                   # == one brand/entity (shared vocabulary name)
  id: UUID
  client_id: UUID
  brand_name: str
  normalized_name: str
  aliases: list[str]
  official_domain: str | None         # registrable domain
  relation: Literal["direct","direct_public","aggregator","content","benchmark",
                    "self","subsidiary","parent","partner","supplier","insurer","unrelated"]
  owner_kind: Literal["organisation","individual","unknown"]
  role: Literal["competitor_set","niche_set","benchmark_set","creator_seed","excluded"]
  geography: list[{city|None, region|None, country_iso2}]
  sources: list[CandidateSource]
  business: BusinessRelevance
  channels: list[ChannelMatch]        # 0..n; one is_primary
  confidence: Literal["high","medium","low"]
  status: Literal["suggested","confirmed","auto_confirmed","rejected","unresolved",
                  "no_channel","excluded","user_added"]
  flags: list[str]                    # llm_only, public_institution, aggregator, same_name_other_country, …
  evidence_summary: str               # Haiku-phrased from the structured evidence only
  decided_by: Literal["user","auto","system"] | None
  decided_at: datetime | None
  rejection_reason: str | None
  first_seen_run_id: UUID
  last_seen_run_id: UUID

CompetitorSet                         # what the analysis run consumes
  client_id: UUID
  version: int
  competitor_channel_ids: list[str]   # 3..10, enforced
  niche_channel_ids: list[str]        # ≤ 20
  benchmark_channel_ids: list[str]    # ≤ 3
  creator_seed_channel_ids: list[str] # ≤ 30 → 07 creator ranker
  contains_content_competitors: bool
  confirmed_by: Literal["user","auto"]
  confirmed_at: datetime

DiscoveryResult
  candidates: list[CompetitorCandidate]
  proposal: CompetitorSet             # proposed, not yet confirmed (unless auto)
  warnings: list[DiscoveryWarning]    # code + user message
  usage: {yt_units, search_calls, tavily_credits, llm_tokens_in, llm_tokens_out, llm_usd, seconds}

DriftEvent
  client_id, candidate_id, channel_id
  kind: Literal["channel_missing","inactive","renamed","ownership_weakened","overlap_drop",
                "niche_riser","new_entrant","profile_changed"]
  detail: dict
  detected_at: datetime
  acknowledged_at: datetime | None
```

Handoffs:
- 02 receives `CompetitorSet` channel ids: competitor set gets full ingest, niche/benchmark get lighter ingest.
- 06 receives set membership for scorecards and lift.
- 07 receives creator seeds and `contains_content_competitors`.
- 11 receives events and the endpoints in §4.7.
- 13 receives candidates with evidence.

## 4. Internal design

### 4.1 Module layout

```text
services/competitors/
  service.py              CompetitorDiscoveryService (orchestrates one discovery run)
  context.py              DiscoveryContext, DiscoveryBudget, settings model
  sources/                CandidateSource implementations + registry
    llm_source.py         LlmCandidateSource (Sonnet)
    web_search_source.py  WebSearchCandidateSource (Tavily + Haiku entity extraction)
    website_hint_source.py WebsiteHintCandidateSource (BusinessProfile.competitor_hints)
    niche_search_source.py NicheSearchCandidateSource (02 shared search cache)
    user_source.py        UserCandidateSource (URL/name parsing)
    news_source.py        NewEntrantNewsSource (re-discovery only; Tavily topic=news)
  identity/
    normalize.py          name/domain normalisation, registrable domain
    merger.py             EntityMerger (dedup across sources and runs)
    self_filter.py        SelfIdentityFilter (client, subsidiaries, parent)
    hallucination_guard.py
  classify/
    relation_classifier.py RelationClassifier (Sonnet, quotes required)
    owner_kind.py          OwnerKindClassifier (deterministic first, Haiku fallback)
  resolve/
    youtube_url.py         parse_youtube_url()
    chain.py               ResolutionChain (ordered, cost-aware, early stop)
    site_link.py           OfficialSiteLinkResolver (anchors, JSON-LD sameAs, embeds)
    websearch_youtube.py   WebSearchYouTubeResolver (Tavily include_domains=youtube.com)
    legacy_and_handle.py   UsernameHandleResolver (channels.list forUsername / forHandle)
    youtube_search.py      YouTubeSearchResolver (search.list type=channel, last resort)
    resolution_cache.py    BrandChannelResolutionCache (cross-client)
  verify/
    signals.py             OwnershipSignal implementations + registry
    verifier.py            OwnershipVerifier (deterministic score + label)
    adjudicator.py         LlmOwnershipAdjudicator (Haiku)
    grouping.py            MultiChannelGrouper (variant + primary selection)
  score/
    business.py            BusinessRelevanceScorer
    youtube.py             YouTubeRelevanceScorer
    profiles.py            ScoringProfile registry (per relation)
    confidence.py
  select/
    proposer.py            SetProposer (defaults, fairness swap, min/max)
    auto_confirm.py        AutoConfirmPolicy
    validation.py          CompetitorSetValidator (3..10 etc.)
  drift/
    detector.py            DriftDetector + DriftRule registry
    rediscovery.py         RediscoveryJob
  repository.py            CompetitorRepository (SQLAlchemy)
pipeline/stages/competitor_discovery.py   CompetitorDiscoveryStage (onboarding)
pipeline/stages/competitor_drift.py       CompetitorDriftStage (daily refresh, 0 extra units)
workers/ cron: rediscover_competitors (monthly), verify_tracked_channels (≤ 30d refresh)
```

### 4.2 Protocols (extension points)

```python
class CandidateSource(Protocol):
    kind: str
    def enabled(self, ctx: DiscoveryContext) -> bool: ...
    async def generate(self, ctx: DiscoveryContext) -> list[RawCandidate]: ...

class ChannelResolver(Protocol):
    name: str
    cost: ResolverCost                      # yt_units, search_calls, tavily_credits (upper bound)
    def applicable(self, entity: CandidateEntity) -> bool: ...
    async def resolve(self, entity: CandidateEntity, ctx: DiscoveryContext) -> list[ChannelLead]: ...

class OwnershipSignalRule(Protocol):
    name: str
    def evaluate(self, lead: ChannelLead, entity: CandidateEntity, ev: ChannelEvidence) -> OwnershipSignal | None: ...

class DriftRule(Protocol):
    kind: str
    def check(self, candidate: CompetitorCandidate, latest: ChannelEvidence, ctx: DriftContext) -> DriftEvent | None: ...
```

Registries (`CANDIDATE_SOURCES`, `RESOLVERS`, `OWNERSHIP_SIGNALS`, `SCORING_PROFILES`, `DRIFT_RULES`) are populated in the DI container (01). Adding a signal (e.g. a future "Spotlit AI-answer citations" source) means registering a class. The orchestrator stays closed for modification.

Ports used:
- `YouTubeGateway` (02): `channels_by_ids`, `channel_by_handle`, `channel_by_username`, `videos_by_ids`, `recent_uploads(uploads_playlist_id, max=50)`, `search_channels`, `niche_search` (cached). All calls go through the quota manager.
- `WebSearch` (Tavily adapter): `search`, `extract`.
- `SiteCrawler` (03): `fetch_page(url) -> FetchedPage{status, final_url, html, blocked: bool, reason}`. Respects robots.txt and never bypasses bot challenges.
- `LLMClient` (10): `structured(prompt_id, schema, tier, inputs)`.
- `TopicMatcher` (05): lexical mode only.
- `ProgressReporter`.

### 4.3 Run flow (pseudocode)

```text
async def discover(ctx) -> DiscoveryResult:
    raw = await gather_bounded(src.generate(ctx) for src in CANDIDATE_SOURCES if src.enabled(ctx))
    entities = EntityMerger.merge(raw, known=ctx.known_entities)
    entities = SelfIdentityFilter.apply(entities, ctx)           # → self/subsidiary/parent excluded
    entities = suppress_previous_rejections(entities, ctx)
    entities = await HallucinationGuard.check(entities, ctx)     # drops/flag llm_only fabrications
    entities = await RelationClassifier.classify(entities, ctx)  # relation, offerings(+quotes), geography
    to_resolve = prioritise(entities)[: settings.max_entities_to_resolve]   # default 25
    leads = await ResolutionChain.resolve_all(to_resolve, ctx)   # cheapest first, early stop
    evidence = await fetch_channel_evidence(leads)               # channels.list batch + uploads
    verdicts = OwnershipVerifier.verify(leads, evidence)         # + Haiku for the uncertain band
    grouped = MultiChannelGrouper.group(entities, verdicts)
    scored = score(grouped)                                      # BusinessRelevance, YouTubeRelevance
    proposal = SetProposer.propose(scored, ctx)
    if settings.auto_confirm: proposal = AutoConfirmPolicy.apply(proposal, scored)
    summaries = await write_evidence_summaries(scored)           # Haiku, evidence-only
    persist(...); emit(RunEvent(...)); return DiscoveryResult(...)
```

Each step emits progress events, for example "Found 18 possible competitors from 4 sources", "Matched 9 to YouTube channels (7 verified)", "Proposed 5 competitors". Each step checks `DiscoveryBudget`. If the budget or time runs out, the run returns partial results with a warning. It never fails the onboarding run.

Concurrency: a Redis lock per `client_id` (`competitors:discover:{client_id}`). Resolution runs under `asyncio.Semaphore(8)`. LLM calls run under `Semaphore(4)`.

### 4.4 Ownership verification design

Two layers:
1. **Deterministic score** from the signal registry (§5.5). This is the only path to `verified`. `verified` requires hard link evidence: website ↔ channel, or embed majority on the official domain.
2. **Haiku adjudication** only for leads in the uncertain band without link evidence. The output schema is `{verdict: official|regional_official|unofficial|fan|reupload|news_or_media|unrelated, reason, cited_signal_names[]}`. It can move `uncertain → likely` or `→ rejected`. It can never produce `verified`, and it must cite signal names that exist in the evidence bundle. Otherwise the output is discarded.

### 4.5 Separate relevance scores (policy-driven design)

The assignment forbids merging YouTube data with other sources into one metric (p.2). Developer Policies III.E.4.h requires a clear disclosure when non-YouTube metrics are shown alongside API data. So the ranking does **not** compute a single blended number. Instead:
- `BusinessRelevance` (web-derived) is bucketed into a **tier** (high/medium/low).
- `YouTubeRelevance` (API-derived) is a 0..1 score.
- Ranking is **lexicographic**: tier first, then YouTube score within the tier. The UI shows both side by side with labels: "Business match (from websites)" and "YouTube activity & topic match (from YouTube)".

### 4.6 User URL parsing

`parse_youtube_url(text) -> YouTubeRef | WebsiteRef | NameRef | Rejected` handles:
- `/channel/UC…`
- `/@handle` (Unicode allowed)
- `/user/x`
- `/c/x`
- bare vanity `/x`
- `watch?v=`
- `youtu.be/`
- `/shorts/`
- `/live/`
- `/embed/`
- `youtube-nocookie.com/embed/`
- `playlist?list=`
- `m.`, `music.` (rejected: music), and `attribution_link`
- tracking params
- a bare `UC…` id
- a bare `@handle`

Non-YouTube social URLs (instagram, tiktok, x, linkedin) return `Rejected("other platforms are out of scope")`. Any other http(s) URL is treated as the competitor's website and runs through the full resolution chain.

### 4.7 API surface (implemented by 11)

| Method | Path | Purpose |
|---|---|---|
| GET | `/clients/{id}/competitors` | Candidates grouped by role, with evidence, confidence, flags |
| POST | `/clients/{id}/competitors` | Add by `raw` (name/URL/handle). Runs the single-entity resolution path synchronously (≤ 15 s) |
| PATCH | `/clients/{id}/competitors/{candidate_id}` | confirm, reject(reason), promote/demote role, choose primary channel, include secondary channel |
| POST | `/clients/{id}/competitors/confirm` | Validate 3..10 and freeze `CompetitorSet` vN. Starts the analysis run |
| POST | `/clients/{id}/competitors/rediscover` | Manual re-discovery (rate-limited 1/day/client) |
| GET | `/clients/{id}/competitors/drift` | Unacknowledged DriftEvents |

## 5. Algorithms & logic

All thresholds live in `CompetitorDiscoverySettings` (pydantic-settings, overridable per tenant/client). The values below are defaults.

### 5.1 Candidate generation

**A. LLM (Sonnet, `claude-sonnet-5-5`, medium effort)**
- Input: compact BusinessProfile (brand, offerings, locations, primary markets, business model, value props, top 10 buyer questions). This is ≤ 3k tokens and contains no raw crawled HTML.
- Structured output: `list[{name, official_domain_guess, relation_guess, geography[], why: str, aliases[], native_script_name?}]`. Max 15 direct, 5 aggregator, 5 benchmark, 5 content/media.
- The prompt asks for currently operating businesses only and allows "unknown" for the domain.
- Every LLM candidate starts with flag `llm_only` until another source or the hallucination guard corroborates it.

**B. Tavily web search (`search_depth="basic"`, 1 credit each)**
Query templates live in a per-run registry; up to 6 run in parallel:
1. `top competitors of {brand}` (country=primary market name)
2. `{brand} alternatives`
3. `best {top_offering} {category} in {top_city}` (local intent)
4. `list of {category} in {country}` / `largest {category} chains {country}`
5. `{brand} vs` (comparison pages)
6. For B2B/SaaS: `{brand} competitors G2` (include_domains not forced)

Parameters: `max_results=8`, `country` when topic=general, `exclude_domains=[client domain, youtube.com, wikipedia mirrors]`, `include_raw_content=false`.

Each result's `title + content` (no raw HTML) goes to **Haiku entity extraction**. Output: `list[{name, domain?, quote}]`. The quote must be a substring of the provided content; otherwise the entity is dropped. Financial "peer" pages (Groww, Tracxn) often list non-competing listed peers (pharma, diagnostics). They are kept as candidates; the relation classifier excludes them later.

**C. Website hints**
- Use `BusinessProfile.competitor_hints` from 03: brands named on comparison/"why choose us"/"vs" pages, with page URL and quote.
- Outbound links to insurers, TPAs, payment partners, and accreditation bodies are **not** competitors. 03 tags them, or the relation classifier excludes them as `partner/insurer`.
- Expected yield: low for large brands, higher for SaaS and D2C.

**D. YouTube niche-search frequency (quota-costly; shared cache)**
- Queries: the top `niche_queries_for_discovery` (default **6**) TopicRefs by priority. Buyer questions come first, then niche keywords.
- Call: `type=video`, `maxResults=50`, `regionCode=primary market`, `relevanceLanguage=profile.language`, `publishedAfter=now-90d`.
- These are **the same queries 02 runs for trending and the creator ranker**, keyed in 02's shared cache (query+params normalised, TTL from 02). Discovery therefore usually pre-warms the cache the analysis run reuses, and a second client in the same niche pays 0 calls.
- For each channel compute `query_hits = #distinct queries where it appears` and `total_hits`.
- A channel is a candidate if `query_hits ≥ 2` (default) or `total_hits ≥ 4`.
- Video-to-channel mapping comes from the search result's `snippet.channelId`.
- These candidates start as relation `content` with `owner_kind` TBD. A channel that later matches a known brand entity (same channel id) is merged into that entity, adding a `niche_search` source and raising source agreement.
- If the search bucket is unavailable, skip with warning `NICHE_SEARCH_SKIPPED`.

**E. User**: parsed per §4.6. User inputs bypass the hallucination guard and get `user_asserted` ownership weight (§5.5). They are still verified so an obviously wrong channel can be flagged.

**F. Re-discovery only — new entrants**: Tavily `topic="news"`, `time_range="month"`, queries `new {category} launch {country}` and `{category} {city} opens`. Haiku extracts entities with quotes.

Per-source caps: LLM 30, web search 40 extracted entities, niche 30 channels, website hints 20. Global cap before resolution: `max_entities_to_resolve=25`. Priority order: multi-source > user > website hint > web search > LLM-only. Within a group, the relation guess decides (direct first).

### 5.2 Normalisation, merge, self-exclusion

- `normalized_name`:
  - NFKC
  - casefold
  - strip legal suffixes (ltd, limited, pvt, inc, llc, gmbh, enterprise(s), group, healthcare institute…)
  - strip generic category tokens only when other tokens remain ("Max Healthcare" → "max healthcare"; the category token is kept if it's the only distinctive one)
  - collapse whitespace and punctuation
  - keep native script and add a romanised alias when the LLM provided one
- `official_domain`: registrable domain (eTLD+1) of a validated URL. Redirects are followed once via `SiteCrawler.fetch_page` (HEAD/GET) to canonicalise.
- **Merge keys**, in order:
  1. same `official_domain`
  2. same resolved `channel_id`, applied after resolution as a second merge pass
  3. `rapidfuzz.token_set_ratio(normalized_name) ≥ 92`, plus compatible geography
- Merges union sources, aliases, and geography.
- **Self filter**, matching `self|subsidiary|parent`:
  - domain ∈ {client domain} ∪ related_brands domains
  - channel id ∈ own_channel_ids
  - normalized_name equals the brand or an alias
  - the name contains the client's distinctive brand token and the LLM `relation_guess ∈ {subsidiary, sister}`. For example "Apollo 24|7", "Apollo Pharmacy", "Apollo Cancer Centres" → `subsidiary`.

  Matches are excluded from competitor roles. Subsidiaries with channels are surfaced as "Is this also yours?", which 01 can add to the client's own channels.
- **Suppression**: entities the user rejected earlier stay `rejected`. They are re-shown only in an "Previously rejected" drawer, never re-proposed.

### 5.3 Hallucination guard (for `llm_only` entities)

An entity is kept if any of these hold:
- (a) another source kind corroborates it after merge
- (b) Tavily `"{name}"` (basic, 1 credit, `max_results=5`) returns a result whose registrable domain == `official_domain_guess`, or whose content contains the normalized name plus one client niche keyword or location
- (c) `official_domain_guess` responds (any status < 500 including 403 bot walls) **and** a corroborating Tavily hit exists

Otherwise it is dropped and recorded as `excluded` with reason `no_independent_evidence`, so the drop can be audited. Cap: 10 guard searches per run. Entities beyond the cap stay `llm_only` and can't be auto-confirmed.

### 5.4 Relation classification (Sonnet, one batched call)

Input per entity:
- name and domain
- source snippets (≤ 3 × 500 chars)
- channel title and description if already known from niche search
- the client's offerings list with ids
- the client's locations and business model

Output per entity:
```text
{relation, owner_kind, geography[{city?, region?, country_iso2, quote}],
 offerings[{offering_id, verdict: yes|partial|no, quote, source_url}], same_name_other_country: bool}
```
Validation is deterministic:
- every `yes/partial` quote must be a substring of the provided snippets, else it is downgraded to `no`
- country codes are checked against the ISO 3166-1 list
- unknown offering ids are dropped

`owner_kind` is decided deterministically first:
- `individual` if the channel title matches person-name patterns ("Dr.", honorifics, first+last-name tokens with no org tokens)
- `organisation` if it contains org tokens (hospital, clinic, ltd, official, media, news, academy)
- otherwise it falls back to the LLM

This classifies the channel **owner**, not video content (§9).

### 5.5 Channel resolution chain (cheapest first, stops when a lead reaches `verified`)

| # | Resolver | Cost | How |
|---|---|---|---|
| 0 | `resolution_cache` | 0 | Global cache keyed by `official_domain` (and `normalized_name+country`), entry ≤ 30 days old with label `verified/likely`. A cached verdict is reused but re-scored with fresh evidence |
| 1 | `site_link` | 0 YT units | `SiteCrawler.fetch_page(homepage)` plus up to 2 likely pages (`/contact`, `/about`, `/media`). Extract (i) `<a href>` to youtube.com/youtu.be; (ii) JSON-LD `Organization.sameAs` entries ([schema.org/sameAs](https://schema.org/sameAs)); (iii) embedded video ids (`/embed/{id}`). Embedded ids are batched into `videos.list` (1 unit per 50 ids, shared across all entities), giving `embed_majority` = channel owning ≥ 50% of embeds with ≥ 2 votes. Pages behind a bot challenge (403, "Just a moment…") are **not** bypassed. Mark `site_blocked` and continue |
| 2 | `websearch_youtube` | 1 Tavily credit, 0 YT units | Tavily `"{brand} official YouTube channel"`, `include_domains=["youtube.com"]`, `max_results=5`. Parse each URL with §4.6. Channel-shaped URLs become leads. Watch URLs go into the shared `videos.list` batch for owner votes |
| 3 | `for_username` / `for_handle` | 1 unit each | First, any `/user/x`, `/c/x`, or bare `/x` path found in steps 1–2 → `channels.list forUsername=x`, then `forHandle=x` if empty. Next, guesses (max **3** per entity): domain label (`fortishealthcare`), name-concat, name+"official". Guessed hits get **no** ownership credit for being found; they must earn it from signals |
| 4 | `search_list` | 1 Search-bucket call | `search.list type=channel q="{brand}" regionCode maxResults=5`. Runs only if no lead has score ≥ 0.5, the entity is `direct` or user-added with tier ≥ medium, and the search budget allows. Per-run cap `max_resolution_searches=3` |

All resulting channel ids are then fetched in one batch: `channels.list part=snippet,statistics,contentDetails,brandingSettings` (1 unit per ≤ 50 ids). Then `playlistItems.list` is called on each lead's uploads playlist (1 unit each, `maxResults=50`) for `uploads_90d`, `last_upload_at`, recent titles, and descriptions. 02 caches these: if the channel already sits in the shared cache with uploads fresher than 24 h, the cost is 0.

Live grounding of this order is in §7.4. Guessed handles failed for Fortis/Max/Medanta, the website step worked for Medanta, and the Tavily youtube.com search found Fortis and Max.

### 5.6 Ownership score (deterministic; default weights)

| Signal | Weight | Condition |
|---|---|---|
| `site_links_channel` | +0.50 | Official domain page (anchor) links to this channel |
| `jsonld_sameas` | +0.50 | (not additive with the anchor; take max) |
| `embed_majority` | +0.30 | ≥ 2 embeds on the official domain and ≥ 50% owned by this channel |
| `channel_links_site` | +0.40 | Official registrable domain appears in `snippet.description` or `brandingSettings.channel.description` |
| `uploads_link_site` | +0.20 | Domain appears in ≥ 30% of the ≤ 50 recent upload descriptions |
| `name_similarity` | +0.15 / +0.05 | `token_set_ratio(normalized brand, normalized title or customUrl)` ≥ 90 / 75–89 |
| `websearch_youtube_hit` | +0.10 | Tavily youtube.com result for "{brand} official" pointed to this channel (top 2) |
| `user_asserted` | +0.30 | User supplied this exact channel |
| `sanity_penalty` | −0.15 … −0.40 | video_count < 5 (−0.2); `snippet.country` set and ∉ brand countries (−0.15); title/description contains fan/unofficial/"not affiliated"/reupload/news-about tokens (−0.4); channel created after the brand's own verified channel with a near-identical title (−0.2) |

Labels:
- **verified**: score ≥ 0.80 **and** at least one of {site_links_channel, jsonld_sameas, embed_majority, channel_links_site}
- **likely**: 0.50–0.79, or Haiku says `official`/`regional_official` for 0.30–0.79 without link evidence (capped at likely)
- **uncertain**: everything else ≥ 0.20
- **rejected**: < 0.20, or Haiku says fan/unofficial/reupload/unrelated with no link evidence

Only `verified|likely` channels can be auto-confirmed. `uncertain` channels are shown with an "Is this the right channel?" prompt.

### 5.7 Multi-channel brands

When ≥ 2 channels for one entity reach `verified|likely`:
- **Variant**:
  - title or description contains a city/region from a geo gazetteer (client locations plus India/US state lists) → `regional`
  - contains a language name (Hindi, Tamil, Telugu, Bangla, Marathi, Español…) → `language`
  - contains an offering or specialty token matching a client Offering → `specialty`
  - person-name pattern → `person` (moved to creator seeds, not the brand)
  - otherwise `main` if the channel has the highest subs; else `unknown`
- **Primary** = argmax of `content_overlap × activity`. Ties go to `main` and then to higher subscriber_count. If the client operates in only one region (BusinessProfile.locations ⊂ one region), a `regional` channel for that region is preferred.
- Non-primary channels appear under the entity as "Also from {brand}". Each one the user includes counts toward the 10-channel cap.
- Duplicate regional channels that are near-copies (≥ 60% of recent titles identical after normalisation) are collapsed and marked `duplicate_of`.

### 5.8 Scoring

**BusinessRelevance (web data only)**
- `offering_overlap = Σ_o w_o·v_o / Σ_o w_o`, where `v=1` for yes, 0.5 for partial, 0 for no, and `w_o` = Offering priority from 03 (default 1)
- `geo_overlap`:
  - 1.0 shared city/metro
  - 0.8 shared region/state
  - 0.6 same country
  - 0.3 serves the client's country cross-border (e.g. medical tourism, SaaS)
  - 0 none
  - `unknown` → 0.3 with flag `geo_unknown`
  - For multi-market clients, compute against each primary market and take the max; also record which market matched
- `source_agreement = min(1, distinct_source_kinds / 3)`
- `business_points = 0.5·offering_overlap + 0.3·geo_overlap + 0.2·source_agreement`
- tier: high ≥ 0.65, medium ≥ 0.40, else low
- Profiles per relation (registry):
  - `content` and `benchmark` use `0.6·offering_overlap + 0.4·source_agreement` with geography ignored. Benchmarks are by definition elsewhere; content channels are judged by language and region on the YouTube side.

**YouTubeRelevance (API data only; primary channel)**
- `activity = min(1, uploads_90d / activity_target)`, with `activity_target=12` (≈ weekly). `uploads_90d == 0` → flag `inactive`.
- `content_overlap = 0.5·share + 0.5·coverage`
  - `share` = fraction of ≤ 50 recent uploads whose title+description match ≥ 1 TopicRef via 05's lexical matcher
  - `coverage` = distinct matched TopicRefs / min(20, |TopicRefs|)
  - Lexical only, so no embeddings cost at onboarding and topic matching stays constrained to client terms (assignment p.2)
- `size_ratio = subs / client_subs`. Bands: much_smaller < 0.02 ≤ smaller < 0.2 ≤ peer ≤ 5 < larger ≤ 50 < much_larger. `unknown` if either side is hidden or the client has no channel. Size is **not** scored; outliers are already normalised to each channel's own baseline (06).
- `youtube_score = 0.45·content_overlap + 0.35·activity + 0.20·min(1, query_hits/3)`. The last term is 0 if the channel wasn't seen in niche search.

**Confidence (per candidate)**
- high: ownership verified ∧ business tier high ∧ ≥ 2 source kinds ∧ not inactive
- medium: ownership ∈ {verified, likely} ∧ tier ≥ medium
- low: everything else

**Ranking**: `(role eligibility, business tier, youtube_score, ownership score)`, all descending. No cross-source blended metric (§4.5).

### 5.9 Proposal and confirmation

Defaults: `proposal_size=5`, clamped to 3..10.

**competitor_set**
1. Take eligible `direct` (then `direct_public`) entities with a primary channel labelled `verified|likely`, not inactive, ordered by ranking.
2. If fewer than 3, fill from `aggregator` then `content` and set `contains_content_competitors`.
3. **Fairness swap**: if every selected channel is `much_larger` and a `peer|larger` eligible channel exists with tier ≥ medium, swap the lowest-ranked for it. Small clients then see at least one comparable rival; the UI explains "largest players are much bigger than you; outlier ratios are judged against each channel's own normal".

**Other sets**
- niche_set: remaining verified/likely `content` + `aggregator` + extra `direct` beyond the cap, by youtube_score, ≤ 20.
- benchmark_set: top 3 `benchmark` with verified/likely ownership.
- creator_seed: `owner_kind=individual` channels from niche search with `query_hits ≥ 2`, ≤ 30. These are passed to 07 unranked.

**Confirm validation** (`POST …/confirm`):
- `3 ≤ |competitor_set| ≤ 10`
- no channel in two sets
- no own/subsidiary channels
- every channel resolved to a UC id
- `uncertain` ownership requires explicit user acknowledgement (`acknowledge_uncertain=true`)
- inactive channels are allowed but don't count toward the minimum of 3 (`min_active_competitors=3`). They are tracked for cadence only.

**Auto-confirm** (`settings.auto_confirm`, default off; on for demo tenants):
- Applies the proposal as-is if ≥ 3 competitor-set channels have ownership verified, or likely with score ≥ 0.70.
- Sets `decided_by=auto` and `status=auto_confirmed`, freezes CompetitorSet, and the onboarding run continues straight into the analysis run.
- If that condition fails, it falls back to `awaiting_confirmation` with warning `AUTO_CONFIRM_INSUFFICIENT`. A demo is never run on a guessed set.

### 5.10 Evidence summary (Haiku)

Per candidate, a ≤ 2-sentence summary is phrased **only** from structured fields. Examples: sources ("named on Groww peer list, Tracxn, and by the AI"), ownership signals ("medanta.org links this channel; 'medanta' in 41/50 upload descriptions"), offering quotes, geography basis, and YouTube numbers (uploads_90d, matched buyer questions). Post-validation: every number in the text must appear in the input fields, else the deterministic template text is used. This follows the 10 evidence-grounded writer rules.

### 5.11 Drift detection (daily, piggybacks on 02's daily `channels.list` refresh, 0 extra units)

| Rule | Trigger | Action |
|---|---|---|
| `channel_missing` | Tracked id returns no item (deleted/terminated/private) | Event; mark channel `missing`; propose the next-ranked replacement |
| `inactive` | `last_upload_at` older than 90 d | Event; keep tracked for cadence; badge "inactive"; doesn't count toward active minimum |
| `renamed` | title or customUrl changed | Re-run the deterministic ownership signals on fresh evidence. If link signals vanish → `ownership_weakened` |
| `overlap_drop` | `content_overlap` < 50% of value at confirmation for 2 consecutive weekly checks | Suggest demotion to niche_set |
| `niche_riser` | Channel outside all sets appears in ≥ 3 distinct cached niche queries within 14 d (from 02 cache, 0 calls) | Suggest as content competitor |
| `profile_changed` | BusinessProfile version changes offerings or locations | Trigger re-discovery |

**Re-discovery** (cron, `rediscovery_interval_days=30`, also on `profile_changed` or manual):
- Re-runs sources A, B, D (cache-first), and F with the same pipeline.
- Diffs against known entities. Only **new** entities with confidence ≥ medium surface as "New competitor suggestions". Nothing changes the confirmed set without the user, except in auto-confirm tenants where additions still need a free slot (≤ 10) and never replace user-confirmed entries.
- Also refreshes `ChannelEvidence` for all tracked and suggested channels, which satisfies the ≤ 30-day refresh rule (§9).

## 6. Data model

Owned tables (Alembic; tenancy columns per 01):

```text
competitor_discovery_runs
  id uuid pk, client_id fk, pipeline_run_id fk null, trigger enum, status enum(running|completed|partial|failed),
  started_at, finished_at, yt_units int, search_calls int, tavily_credits int,
  llm_tokens_in int, llm_tokens_out int, llm_usd numeric(8,4), warnings jsonb, error text
  idx (client_id, started_at desc)

competitor_candidates                      -- one row per entity per client
  id uuid pk, client_id fk, brand_name, normalized_name, aliases jsonb, official_domain,
  relation enum, owner_kind enum, role enum, geography jsonb, business jsonb (BusinessRelevance),
  confidence enum, status enum, flags text[], evidence_summary text,
  decided_by enum null, decided_at, rejection_reason, first_seen_run_id fk, last_seen_run_id fk,
  created_at, updated_at
  unique (client_id, normalized_name); unique (client_id, official_domain) where official_domain is not null
  idx (client_id, role, status)

competitor_candidate_sources              -- web/LLM provenance (not YouTube data)
  id, candidate_id fk cascade, discovery_run_id fk, kind enum, ref text, snippet text, retrieved_at
  idx (candidate_id)

competitor_channel_matches
  id, candidate_id fk cascade, channel_id text (fk → 02 channels.id, nullable until fetched),
  resolution_method enum, variant enum, is_primary bool, is_tracked bool,
  ownership_label enum, ownership_score real, ownership_signals jsonb, llm_reason text,
  youtube_evidence jsonb, youtube_scores jsonb, youtube_fetched_at timestamptz, flags text[]
  unique (candidate_id, channel_id); idx (channel_id); idx (youtube_fetched_at)

competitor_sets                           -- frozen versions consumed by analysis runs
  id, client_id fk, version int, competitor_channel_ids text[], niche_channel_ids text[],
  benchmark_channel_ids text[], creator_seed_channel_ids text[], contains_content_competitors bool,
  confirmed_by enum, confirmed_at
  unique (client_id, version); the latest version is the active one

brand_channel_resolutions                 -- global, cross-client (no client data inside)
  id, key_type enum(domain|name_country), key text, channel_id text, ownership_label enum,
  ownership_score real, signals jsonb, verified_at, expires_at (verified_at + 30d)
  unique (key_type, key, channel_id); idx (expires_at)

competitor_drift_events
  id, client_id fk, candidate_id fk, channel_id, kind enum, detail jsonb, detected_at, acknowledged_at
  idx (client_id, acknowledged_at nulls first, detected_at desc)
```

Retention:
- `youtube_evidence`, `youtube_scores`, and YouTube-derived parts of `ownership_signals` (descriptions, counts) are refreshed by re-discovery/drift or nulled by 01's retention cron when `youtube_fetched_at` is older than 30 days (Developer Policies III.E.4.c/d).
- `brand_channel_resolutions` rows expire at 30 days and are deleted or refreshed.
- Channel ids in `competitor_sets` are re-confirmed via `channels.list id=` (1 unit per 50) at least every 30 days by the `verify_tracked_channels` cron. See §13 on whether identifiers are exempt.
- Web provenance (`candidate_sources`) is not YouTube API data. Default retention 180 days (configurable).
- Tavily responses are cached in Redis by the search adapter: key = sha256(normalised params), TTL 7 days, news 24 h.

## 7. External APIs & libraries

### 7.1 YouTube Data API v3 (via 02's gateway and quota manager)

| Call | Cost | Use here | Source |
|---|---|---|---|
| `channels.list` `forHandle` (with or without `@`) | 1 unit | Handle resolution and guesses. Exactly one filter per call, so handles can't be batched | [channels.list](https://developers.google.com/youtube/v3/docs/channels/list); forHandle added 2024-01-31 ([revision history](https://developers.google.com/youtube/v3/revision_history)) |
| `channels.list` `forUsername` | 1 unit | Legacy `/user/x`, and also matched a bare vanity path in the live check | same |
| `channels.list` `id=` (comma list), `part=snippet,statistics,contentDetails,brandingSettings` | 1 unit/call | Batch evidence for all leads (≤ 50 ids/call per 02's batching convention; the docs page doesn't state a max) | same; [channel resource](https://developers.google.com/youtube/v3/docs/channels) |
| `playlistItems.list` on `contentDetails.relatedPlaylists.uploads`, `maxResults=50` | 1 unit | uploads_90d, recent titles/descriptions | [quota table](https://developers.google.com/youtube/v3/determine_quota_cost) |
| `videos.list id=` (≤ 50) `part=snippet` | 1 unit | Owner of embedded/linked/user-supplied videos | same |
| `playlists.list id=` `part=snippet` | 1 unit | Owner of a user-supplied playlist URL | same |
| `search.list` `type=video` (niche) / `type=channel` (resolution) | **1 call from the separate Search Queries bucket (100 calls/day)** | Niche frequency (shared cache) and last-resort resolution | [search.list](https://developers.google.com/youtube/v3/docs/search/list): "Quota impact: 100 calls per day. A call to this method has a quota cost of 1 unit in the Search Queries quota bucket"; quota page updated 2026-11-12 |

Facts that matter here:
- The channel resource has **no field exposing the About-page external links**. Channel→site evidence must come from `snippet.description`, `brandingSettings.channel.description`, and upload descriptions (verified on the channel resource page).
- `statistics.subscriberCount` is rounded down to 3 significant figures (revision history, 2019-09-10). `hiddenSubscriberCount` exists, so size ratios are approximate and shown as bands.
- `topicDetails.topicCategories` (Wikipedia URLs) is **not used**, to stay clear of content-category inference.
- `search.list` `q` supports `-` (NOT) and `|` (OR, URL-escaped `%7C`). The niche queries in source D may OR-combine two short keywords into one call to save bucket calls (setting `or_combine_keywords`, default true for keywords ≤ 3 words, never for buyer questions).

### 7.2 Tavily ([search](https://docs.tavily.com/documentation/api-reference/endpoint/search), [extract](https://docs.tavily.com/documentation/api-reference/endpoint/extract))

- **Search** params used: `query`, `search_depth="basic"` (1 credit; `advanced` = 2 credits; `auto_parameters` can silently cost 2, so it's never enabled), `max_results` (0–20, default 10), `topic` (`general|news|finance`), `time_range` (`day|week|month|year`), `include_domains` (≤ 300; `include_domains_mode="restrict"` default), `exclude_domains` (≤ 150), `country` (fixed enum of country names, only with `topic=general`; `"india"` verified live), `language`, `include_usage=true` (credit accounting).
- Response: `results[{title,url,content,score,…}]`, `usage`, `request_id`.
- **Extract**: ≤ 20 URLs/call, `extract_depth` basic (1 credit per 5 successful URLs) / advanced (2 per 5), `format` markdown|text. Always check `failed_results` even on HTTP 200.
- **Live finding**: extract output (advanced, markdown) for fortishealthcare.com and maxhealthcare.in contained **no YouTube links**; footers and social links are stripped. Extract is therefore *not* a resolution source. It is only used as a text fallback for the relation classifier when our crawler is bot-blocked.
- `tavily-python` SDK (`TavilyClient`/`AsyncTavilyClient`) mirrors these params. Adapter details are in `adapters/search/`.

### 7.3 LLM (via 10's `LLMClient`)

| Task | Tier / model | Calls per run | Approx tokens (in/out) |
|---|---|---|---|
| Candidate generation | `claude-sonnet-5-5` | 1 | 3k / 2k |
| Relation classification (batched) | `claude-sonnet-5-5` | 1–2 | 10k / 4k |
| Entity extraction from search results | `claude-haiku-4-5` | 1 (batched) | 8k / 1k |
| Ownership adjudication (uncertain band only) | `claude-haiku-4-5` | 0–8 | 1.5k / 0.2k each |
| Evidence summaries (batched) | `claude-haiku-4-5` | 1 | 6k / 2k |

All calls use structured outputs (`output_config.format` JSON schema per the Anthropic SDK docs in the bundled `claude-api` skill, `python/claude-api/tool-use.md`) with pydantic validation. Crawled and searched text is passed as quoted data inside a delimited block. The model has no tools.

Pricing (bundled skill `shared/model-migration.md`, `shared/models.md`):
- Sonnet 5.5: $2 / $10 per MTok
- Haiku 4.5: $1 / $5
- Haiku 5.5: $0.10 / $0.50 (≤ 100k-token prompts); see §13

### 7.4 Live verification (2026-11-20; 10 YouTube units, 0 search.list, ~6 Tavily credits)

| Check | Result | Implication |
|---|---|---|
| `forHandle` guesses `@FortisHealthcare`, `@MaxHealthcare`, `@medanta` | **No match** for all three | Naive handle guessing is unreliable; cap guesses at 3 and never trust a hit by name |
| `forHandle` `@ManipalHospitals`, `@NarayanaHealth`, `@Practo` | `UCSljO84JCPeaefyHe-8voIg` Manipal Hospitals (361k subs, 12.7k videos, IN; description links manipalhospitals.com); `UCr5mB2bbUrjEKeWNSYqDpdw` Narayana Health (877k, 9.0k, IN); `UCGlsl2YPuCvklhbUWu1vuqQ` Practo (135k, 455, IN) | Guesses sometimes work; Manipal shows the `channel_links_site` signal in practice |
| Plain fetch of homepages | medanta.org 200 → `youtube.com/user/MedantaTheMedicity` plus 8 embeds; fortishealthcare.com, maxhealthcare.in, apollohospitals.com → **403 (Cloudflare "Just a moment…")**, also with browser-like headers | Site-link step is valuable but often blocked; we never bypass challenges, so fallbacks are mandatory |
| Tavily search `"… official YouTube channel"`, `include_domains=["youtube.com"]` | Fortis → `youtube.com/fortishealthcare` + Fortis watch URLs; Max → `youtube.com/channel/UCBKpVvogr2lH2y5IDEJWuFw` + `/user/maxhcvideos` | 0-unit resolution path that worked where crawling failed |
| `channels.list forUsername=MedantaTheMedicity` | `UCSxY3aIGBg47WJASNYH6mUA` Medanta, handle `@medantahealthcare`, 374k, 5.0k videos | Legacy usernames still resolve |
| `channels.list forUsername=fortishealthcare` (bare vanity path) | `UCn0ShMNqXI3kQP-Eoaf7olg` Fortis Healthcare, `@fortishealthcareofficial`, 837k, 6.5k | Bare vanity path → try forUsername first |
| `videos.list id=Mcek5LoOMZA` (from Tavily) | owner `UCn0ShMNqXI3kQP-Eoaf7olg` | Watch-URL owner votes agree |
| `channels.list id=UCBKpVvogr2lH2y5IDEJWuFw` | Max Healthcare, `@maxhealthcarevideos`, 127k, 6.95k, IN | Real handles are unguessable (`…videos`, `…official`) |
| Tavily `"top competitors of Apollo Hospitals …"` `country="india"` | Tracxn, CB Insights, GlobalData (Manipal, Fortis), Groww peers (Max, Aster DM), "Top 10 hospitals" list (Medanta), Wikipedia | Web search yields the expected directs and also financial peers, which the relation classifier must filter |

Expected Apollo proposal (golden test target): Fortis, Narayana, Medanta, Manipal, Max as `competitor_set`. Practo as `aggregator → niche_set`. Apollo 24|7 as `subsidiary → excluded/"is this yours?"`. AIIMS as `direct_public` (channel unverified; not checked live). Mayo/Cleveland Clinic as `benchmark`.

### 7.5 Libraries

`httpx` (via SiteCrawler), `selectolax` (anchor + JSON-LD extraction), `rapidfuzz` (name similarity), `tavily-python`, `anthropic`, `pydantic`. Registrable-domain parsing needs a Public Suffix List: propose `tldextract` with its bundled snapshot and no runtime fetch (§13).

## 8. Edge cases & failure modes

| # | Case | Detection | Handling | User-facing message |
|---|---|---|---|---|
| 1 | Brand has no YouTube channel | Chain exhausted; no lead ≥ 0.2 | `status=no_channel`; kept for business context; not counted | "{Brand} doesn't seem to have a YouTube channel. Add a link if we missed it." |
| 2 | Inactive channel (0 uploads in 90 d) | `uploads_90d == 0` | Flag `inactive`; not proposed by default; if confirmed, tracked for cadence only, not counted toward the active minimum of 3 | "No uploads in the last 90 days. Useful for cadence comparison only." |
| 3 | Channel far larger than client | `size_band=much_larger` | Not penalised; fairness swap (§5.9); outliers use own-channel baselines (06) | "Much larger than you. We compare each video to that channel's own normal, so this is still a fair comparison." |
| 4 | Channel far smaller / tiny | `much_smaller` or video_count < 5 | Allowed; small sample flagged for 06 baseline confidence | "Small channel. Patterns from it carry less weight." |
| 5 | Client has no YouTube channel | `own_channel_ids` empty | size_ratio `unknown`; everything else runs | "Connect or add your channel to compare sizes." |
| 6 | Marketplace/aggregator (Practo) | relation `aggregator` | niche_set by default; promotable | "Practo competes for the same patient questions but isn't a hospital. Added to your wider niche." |
| 7 | Same-name brand in another country ("Max" in US, "Apollo" elsewhere) | `same_name_other_country`, domain TLD/country mismatch, `snippet.country` mismatch penalty | Separate entities by `name+country`; the foreign one is relation `unrelated`/`benchmark` | "We found a {Brand} in {Country}; it isn't your competitor." |
| 8 | Client itself appears as a candidate | Self filter (domain, channel id, name/alias) | Excluded silently, logged | — |
| 9 | Subsidiary or sister brand (Apollo 24\|7, Apollo Pharmacy) | Self filter + relation `subsidiary` | Excluded from competitor roles; offered as own channel | "Is Apollo 24\|7 part of your business? Add it as your channel." |
| 10 | Parent group/acquirer (Fortis → IHH) | relation `parent` relative to the candidate, not the client | Keep the operating brand; the parent becomes an alias | — |
| 11 | Unverified handle hit points to an unrelated channel (`@Apollo247` coaching channel) | Ownership score < 0.2, Haiku `unrelated` | Rejected; never shown as a suggestion | — (visible under "rejected matches" in evidence) |
| 12 | Fan, unofficial, reupload, or news-about-brand channel | Sanity penalty tokens, no link signals, Haiku verdict | Rejected | "We skipped {n} unofficial channels using the brand name." |
| 13 | Brand with several official channels (regional/language/specialty) | ≥ 2 verified/likely leads | Group; choose primary (§5.7); others optional | "{Brand} has {n} channels. We picked {title} as the main one. Add others if relevant." |
| 14 | Near-duplicate regional channels | ≥ 60% identical recent titles | Collapse; keep the larger | — |
| 15 | Founder's or doctor's personal channel tied to the brand | `owner_kind=individual` + brand mention | Moved to creator_seed, linked as "affiliated" | "Dr. {X} is affiliated with {Brand}; listed as a creator, not the brand channel." |
| 16 | LLM hallucinates a company | `llm_only` + guard fails (§5.3) | Dropped, audit row kept | — |
| 17 | LLM invents a domain or a channel URL | Domain doesn't resolve; channel id unknown to API | Domain ignored; LLM channel ids are never used (no LLM output field for ids) | — |
| 18 | LLM returns the client or obviously wrong-industry firms | Self filter; relation classifier with quotes | Excluded | — |
| 19 | Search bucket exhausted | Quota manager denies the `search` bucket | Skip source D and resolver 4; continue with 0-search paths | "YouTube search limit reached for today. Results may miss some channels; we'll retry tomorrow." |
| 20 | Unit bucket low (< reserve) | Quota manager reserve check (02) | Resolve only the top 10 entities; defer the rest to the next day | "Partial competitor scan. More suggestions will appear tomorrow." |
| 21 | Tavily down / 429 / 5xx / auth | Adapter error; circuit breaker (3 failures/60 s) | Sources B/F and resolver 2 skipped. LLM-only candidates can't be auto-confirmed. Relies on website, niche search, handle/username, ≤ 3 search.list | "Web search is unavailable, so suggestions come from AI and YouTube only. Please review them." |
| 22 | Anthropic down | LLMClient error after retries and fallback provider (10) | Skip sources A and F and the relation LLM. Relation defaults: niche-search → content, web/user → `direct?` with tier from geo/source only; adjudication skipped (uncertain stays uncertain); template summaries. Auto-confirm disabled | "AI classification unavailable. Please check suggested competitors." |
| 23 | Competitor site blocked by bot protection / robots.txt | `FetchedPage.blocked` / robots disallow | No bypass; continue with resolver 2+; relation text from Tavily snippets/extract | — (evidence shows "site not readable") |
| 24 | Competitor site is a JS-only shell | Anchors empty, small HTML | 03's crawler may render with Playwright if allowed by robots; else fall through | — |
| 25 | Non-English or native-script brand name | Non-Latin script detected | Keep native + LLM romanised aliases; fuzzy-match both; Unicode handles via forHandle; Tavily `language` set | — |
| 26 | Transliteration variants ("Narayana Hrudayalaya" vs "Narayana Health") | alias overlap / same domain | Merge on domain; aliases kept | — |
| 27 | B2B vs B2C confusion (device makers, pharma, insurers listed as "peers") | relation classifier: buyer type vs client `business_model`; offering overlap quotes | `supplier`/`partner`/`unrelated` → excluded | "Hidden {n} companies that sell to hospitals rather than to patients." (expandable) |
| 28 | Client operates in many geographies | `primary_markets` > 1 | Run sources B and D per market (top 2 by default); geo_overlap per market; UI filter by market; cap still 10 overall | "Showing competitors across India and UAE. Filter by market." |
| 29 | Client is local (one city) and directs have no channels | < 3 eligible directs | Fill with content/aggregator; `contains_content_competitors` | "Few local competitors post on YouTube. We added channels that answer your buyers' questions instead." |
| 30 | Fewer than 3 total verified channels found | Proposal < 3 | Stay `awaiting_confirmation`; prompt add; auto-confirm blocked | "We found {n} so far. Add at least {3-n} more to continue." |
| 31 | User URL is a video/Shorts/live URL | Parser kind=video | `videos.list` → owner; if the owner isn't the named brand (news/collab upload), warn | "That video was published by {Owner}. Track {Owner}?" |
| 32 | User URL is a playlist | kind=playlist | `playlists.list` → owner channel; warn if it's a curated third-party playlist | "This playlist belongs to {Owner}." |
| 33 | User URL is `/c/custom` or a bare vanity path | kind=custom | forUsername → forHandle → Tavily youtube.com → (search.list if budget allows) | "Couldn't find that channel. Paste a link from the channel's page (youtube.com/@…)." |
| 34 | User gives a non-existent handle / typo | forHandle empty | No search.list for typos by default; suggest nearest from known candidates | "No channel with handle @{x}." |
| 35 | User gives another platform's URL | Parser `Rejected` | Reject | "Only YouTube channels can be tracked." |
| 36 | User adds the client's own or a subsidiary channel | Self filter | Block in competitor_set; offer as own | "That looks like your own channel." |
| 37 | User adds a channel already in another set | Set validator | Move, don't duplicate | "Moved {X} to competitors." |
| 38 | User tries > 10 or < 3 | Validator | 422 with counts | "Pick between 3 and 10 competitors ({n} selected)." |
| 39 | User confirms an `uncertain` ownership channel | Validator requires acknowledgement | Allowed with flag; reverified in drift | "We couldn't confirm this is {Brand}'s official channel." |
| 40 | Candidate resolves to a channel already tracked for another client | `brand_channel_resolutions` hit | Reuse the verdict and shared cache data (02); 0 extra units | — |
| 41 | Two entities resolve to the same channel | Second merge pass on channel_id | Merge entities; union sources | — |
| 42 | Channel deleted, terminated, or made private after confirmation | Drift `channel_missing` | Event; replacement suggestion; the set version stays valid with n−1 (if < 3 active → prompt) | "{Channel} is no longer available on YouTube." |
| 43 | Handle or title changed (rebrand) | Drift `renamed` | Channel id is the identity; re-verify ownership | "{Old} is now called {New}." |
| 44 | Hidden subscriber count | `hiddenSubscriberCount=true` | size_band `unknown` | "Subscriber count hidden." |
| 45 | Shorts-heavy channel | 02 duration data (≤ 60 s share) if available | Flag `shorts_heavy` for 06 format comparisons; no exclusion | "Mostly Shorts." |
| 46 | Channel country empty or wrong | `snippet.country` null | No penalty when null; geography from web evidence | — |
| 47 | Previously rejected competitor resurfaces | Suppression | Not re-proposed | — |
| 48 | Paid listicle / SEO spam drives candidates | Single-source web_search only + low offering overlap | Low tier; never auto-confirmed without a second source kind | — |
| 49 | Prompt injection in crawled or search text | Untrusted text delimited; structured output; quote-substring validation; no tools | Injection can't add entities without quotes from real text, or change verdicts beyond schema | — |
| 50 | Candidate explosion | Per-source caps, `max_entities_to_resolve` | Lowest-priority overflow is listed "unresolved" with a "check" button | "{n} more possible competitors not checked yet." |
| 51 | Discovery exceeds time budget | Wall clock > `wall_seconds` (default 120) | Return partial; stage status `partial`; background continuation job | "Still checking {n} competitors; we'll update the list." |
| 52 | Concurrent discovery for the same client | Redis lock | Second request returns the current run id | — |
| 53 | Public/government institution (AIIMS) | relation `direct_public` | Eligible, ranked after commercial directs, flagged | "Public institution: competes for patients but not commercially." |
| 54 | Competitor channel is made for kids or unrelated adult content | `status.madeForKids` / sanity tokens | Reject unless user-asserted | — |
| 55 | BusinessProfile thin (channel-only onboarding, no website) | Profile completeness < threshold (03) | Lean on niche search and LLM with the channel description; confidence capped at medium | "We had little business info, so please review these suggestions." |
| 56 | Embedded videos on a competitor site belong to news or partners | embed votes split | Require ≥ 2 votes and ≥ 50% majority | — |
| 57 | Tavily youtube.com search returns only videos, no channel URLs | No channel-shaped URLs | Owner votes via one batched `videos.list` | — |
| 58 | Competitor acquired or merged with another candidate | Domains redirect to the same registrable domain | Merge; keep the surviving brand | "{A} is now part of {B}." |
| 59 | YouTube API key invalid / project quota misconfigured | 400/403 non-quota errors | Fail the stage with an ops alert; onboarding shows an error state | "We can't reach YouTube right now. Our team has been alerted." |
| 60 | Brand has only a Topic/auto-generated channel | Title ends "- Topic", no uploads playlist activity | Reject | — |
| 61 | Client in a news-heavy niche with fast-changing entrants (AI SaaS) | FreshnessProfile.high (03) | `rediscovery_interval_days` = 14 for that client | — |

## 9. Policy & guardrail check

| Feature | Guardrail | Status |
|---|---|---|
| All YouTube data via `channels.list`, `playlistItems.list`, `videos.list`, `playlists.list`, `search.list` with an API key | Official APIs only; no scraping | ✅ YouTube pages are never fetched. Tavily results *pointing to* youtube.com are only parsed as URLs (ids/handles), then resolved via the API |
| Website crawl of competitors | Separate data source; robots.txt; no merging | ✅ Respect robots; no bot-challenge bypass; web evidence stored and shown separately |
| BusinessRelevance (web) vs YouTubeRelevance (API) | "Never merge YouTube data with other sources into one metric" (assignment p.2; Developer Policies III.E.4.h disclosure) | ✅ Two scores, lexicographic ranking, labelled side by side (§4.5) |
| Size ratio, uploads_90d, content share | Metrics = API data + simple maths | ✅ Ratios and counts only; no estimates |
| No audience inference | Must not infer audience demographics | ✅ Geography comes from the **business's** published locations and the channel's self-declared `snippet.country`, never viewer location. Size bands use subscriber counts as reported. No "audience overlap" claims. B2B/B2C refers to the **client's and competitor's business model** from their websites, not viewers. ⚠️ Flag for review: `owner_kind` (organisation vs individual) describes the channel owner, not audience or content category |
| Content overlap | No inferring content category; topic grouping constrained to client keywords and buyer questions | ✅ Lexical matching against client TopicRefs only (05); `topicDetails` is not used |
| No revenue/monetisation estimates | | ✅ None computed |
| 30-day storage | Refresh/delete ≤ 30 days (III.E.4.c/d) | ✅ `youtube_evidence` refresh/null; resolution cache expiry 30 d; tracked ids re-verified ≤ 30 d. ⚠️ Policy text has no explicit identifier exemption (§13) |
| Cross-client sharing | Cache public data once, reuse across clients | ✅ `brand_channel_resolutions` contains only public channel ids and verification signals, no client data; client-specific decisions stay in per-client tables. Aggregation limits (III.E.2) aren't triggered: we show per-channel data, not cross-owner aggregates |
| LLM role | Bounded; can't invent videos, numbers or reasons | ✅ LLM proposes *names* only; every channel is API-resolved and verified; quotes validated; summaries number-checked |
| Public data only | Demo has no OAuth | ✅ Discovery uses public data exclusively |

## 10. Performance, cost & scaling

**Per onboarding discovery (typical: 18 entities, 12 resolved)**

| Resource | Estimate | Notes |
|---|---|---|
| YouTube units (10k pool) | **25–60** | `videos.list` owner votes 1–2; forUsername/forHandle ≤ 3 × ~8 = ≤ 24; `channels.list` batch 1; `playlistItems` 1 × ≤ 30 leads (0 if cached ≤ 24 h by 02) |
| search.list calls (100/day bucket) | **0–9** | Niche 6 (often 0 via shared cache; OR-combining reduces this) + resolution ≤ 3. This is the binding constraint: a cold niche costs ~6–9 calls, so ~11+ cold onboardings/day per project. Daily refresh uses 0 discovery calls |
| Tavily credits | **12–20** | 6 discovery searches, ~6–8 resolution searches, ≤ 5 guard searches, extract fallback ≤ 2 |
| LLM tokens | ~35k in / ~10k out | Sonnet ≈ 13k/6k → ≈ $0.09; Haiku 4.5 ≈ 22k/4k → ≈ $0.04. **≈ $0.13 per onboarding** (≈ $0.10 with Haiku 5.5) |
| Wall time | p50 ≈ 45 s, p95 ≤ 120 s | Sources in parallel (~15 s, dominated by Sonnet); resolution parallel (≤ 20 s); evidence batch (≤ 5 s); classification + summaries (~20 s) |

**Daily refresh**: 0 extra units. Drift rules read 02's daily `channels.list` (1 unit per 50 tracked channels, already budgeted by 02) and the cached niche searches. Discovery doesn't count against the < 2 min per-client refresh target.

**Monthly re-discovery**: ≈ onboarding cost minus the user-input path, with high cache hit rates. ≈ 10–30 units, 0–6 search calls, ~10 Tavily credits, ≈ $0.08 LLM.

**Scaling**:
- `brand_channel_resolutions` makes the second client in a niche nearly free (Fortis resolved once for every Indian hospital client).
- Search bucket calls are scheduled by 02's quota planner. Onboarding gets priority over re-discovery, and re-discovery is spread across the month by `hash(client_id) % 30`.

## 11. Testing strategy

**Unit**
- `parse_youtube_url`: table of ~45 forms (§4.6) including Unicode handles, `youtu.be` with `?si=`, `/shorts/`, `/live/`, `youtube-nocookie`, `attribution_link`, `music.` (reject), Instagram (reject).
- Normalisation and merge: legal suffixes, native script aliases, domain redirects, channel-id second pass.
- Self filter: Apollo, Apollo 24|7, Apollo Pharmacy, an unrelated "Apollo Tyres" (must *not* be self; it's `unrelated` by offerings).
- Ownership scoring: fixtures from live checks:
  - Medanta `/user/` + embeds → verified
  - Fortis vanity → forUsername + Tavily → verified via description/domain
  - Max channel id → verified
  - `@Apollo247` coaching channel → rejected
  - synthetic fan channel → rejected
- Scoring math and tiers. Fairness swap. Validator 3..10. Auto-confirm fallback. Drift rules with time-frozen fixtures.

**Contract tests**
- Fake `YouTubeGateway`, `WebSearch`, `SiteCrawler`, `LLMClient` implementing the ports.
- Assert the orchestrator never calls `search_channels` when a cheaper resolver verified, and never exceeds `DiscoveryBudget`. A quota ledger assertion is part of every test.
- LLM schema tests: invalid quotes are downgraded; LLM channel ids are ignored.

**Recorded HTTP (respx)**: YouTube and Tavily responses captured in §7.4 (keys scrubbed) replayed for the resolution chain.

**Golden test (Apollo)**: full discovery with recorded sources must yield:
- competitor_set ⊇ {Fortis `UCn0ShMNqXI3kQP-Eoaf7olg`, Max `UCBKpVvogr2lH2y5IDEJWuFw`, Medanta `UCSxY3aIGBg47WJASNYH6mUA`, Manipal `UCSljO84JCPeaefyHe-8voIg`, Narayana `UCr5mB2bbUrjEKeWNSYqDpdw`}
- Practo in niche_set
- Apollo 24|7 excluded
- 0 rejected channels proposed
- ≤ 60 units, ≤ 9 search calls

**Live smoke** (opt-in `pytest -m live`, ≤ 10 units, 0 search.list, ≤ 5 Tavily credits): re-resolve 3 known brands and assert ids are unchanged.

**Evaluation (feeds 12)**
- Hand-label competitor sets for the 3 demo ICP niches (dental clinics, auto dealers, home services) plus Apollo.
- Report precision@5 of proposed competitor_set (target ≥ 0.8) and ownership precision on 50 labelled brand→channel pairs (target ≥ 0.98 for `verified`, ≥ 0.9 for `likely`).
- Report quota per onboarding in the ops report.

## 12. Implementation task breakdown

| # | Task | Size | Depends on |
|---|---|---|---|
| 1 | Settings model, domain objects (§3.3), enums | S | 01 config/domain |
| 2 | Alembic tables (§6) + repository | M | 01 db, 02 channels table |
| 3 | `parse_youtube_url` + tests | S | — |
| 4 | Normalisation, EntityMerger, SelfIdentityFilter, suppression | M | 3, 03 BusinessProfile fields (§13) |
| 5 | Candidate sources: user, website hint | S | 3, 03 competitor_hints |
| 6 | Candidate sources: LLM (Sonnet) + Tavily search + Haiku extraction | M | 10 LLMClient, Tavily adapter |
| 7 | Niche-search source (frequency) | S | 02 niche_search + shared cache |
| 8 | Hallucination guard | S | 6 |
| 9 | Relation classifier (Sonnet, quote validation) + owner_kind | M | 10 |
| 10 | Resolution chain: site_link (anchors, JSON-LD, embeds) | M | 03 SiteCrawler raw HTML, 02 videos_by_ids |
| 11 | Resolvers: websearch_youtube, forUsername/forHandle, search.list; resolution cache | M | 02 gateway methods + quota buckets |
| 12 | Evidence fetch (batched channels + uploads) | S | 02 |
| 13 | OwnershipVerifier signals + Haiku adjudicator + MultiChannelGrouper | M | 10, 12 |
| 14 | Scorers (business, YouTube via 05 lexical matcher), confidence | M | 05 lexical matcher |
| 15 | SetProposer, AutoConfirmPolicy, validator | S | 14 |
| 16 | Evidence summaries (Haiku, number check) | S | 10 |
| 17 | `CompetitorDiscoveryStage` + progress events + budget/time limits + lock | M | 11 pipeline |
| 18 | API endpoints (§4.7) incl. single-entity add path | M | 11 API |
| 19 | Drift rules + daily stage; re-discovery cron + news source; ≤ 30 d verify cron | M | 02 daily refresh, 01 retention |
| 20 | Golden Apollo test, recorded fixtures, live smoke, evaluation labels | M | all above, 12 |

Critical path: 1 → 3 → 4 → 6/7 → 9 → 10/11 → 12 → 13 → 14 → 15 → 17. Roughly 8–10 dev-days.

## 13. Open questions / proposed changes to shared context

1. **search.list quota wording (factual correction).** §2 says "search.list is 100 units/call". The official docs (search.list page and quota table, last updated 2026-11-12) say search.list draws **1 unit from a separate "Search Queries" bucket capped at 100 calls/day**, and does not draw from the 10,000-unit pool. Proposal: reword §2 to "two buckets: 10,000 units/day for everything else; 100 search.list calls/day". 02's quota manager should model both buckets explicitly. The binding constraint is unchanged.
2. **Low-tier model.** `claude-haiku-5-5` is the current Haiku ($0.10/$0.50 per MTok at ≤ 100k prompt vs $1/$5 for `claude-haiku-4-5`, per the bundled skill docs). Consider making it the default low tier; it's config-only.
3. **SiteCrawler port (03) must expose raw HTML / outbound links and JSON-LD**, plus a `blocked` status, not just cleaned text. Tavily extract strips footer and social links (verified), and three of four hospital homepages are behind Cloudflare challenges that we must not bypass.
4. **BusinessProfile additions (03)**: `brand_aliases`, `related_brands[{name, domain, relation}]` (subsidiaries such as Apollo 24|7), `business_model`, `primary_markets`, `competitor_hints[{name, url, quote}]`, Offering `priority`, and a profile completeness score.
5. **Client config (01)**: persist `CompetitorSet` versions with four lists (competitor/niche/benchmark/creator_seed). State explicitly that "3–10" applies to the competitor list only. Add a per-client `auto_confirm` flag and an "add subsidiary as own channel" path.
6. **YouTubeGateway (02) methods needed**: `channel_by_handle`, `channel_by_username`, `channels_by_ids` (parts incl. brandingSettings, contentDetails), `recent_uploads`, `videos_by_ids`, `playlists_by_ids`, `search_channels`, `niche_search` with the shared cache keyed on normalised params. 02 should also own or approve the global `brand_channel_resolutions` cache.
7. **Never-merge rule applied to rankings**: adopt "web-derived and YouTube-derived scores stay separate; rank lexicographically" as a shared principle. It applies to 07's creator ranker and fit scoring too.
8. **Identifier retention**: Developer Policies III.E.4 contains no explicit exemption for channel/video ids. Proposal: treat ids as API data and re-verify every ≤ 30 days (cheap: 1 unit per 50 ids). 01's retention design should confirm this.
9. **Vocabulary additions**: CompetitorSet, ChannelMatch, OwnershipVerdict, OwnershipSignal, CandidateSource, BusinessRelevance, YouTubeRelevance, DriftEvent.
10. **Dependency**: add `tldextract` (bundled PSL snapshot, no network fetch) for registrable-domain parsing.
11. **Review flag**: `owner_kind` (organisation vs individual) is a channel-owner classification, not audience or content category. Confirm it's acceptable under the guardrails; it is needed to route doctor-creators to 07.
12. **Open**: should benchmark channels be ingested by default (proposed: yes, ≤ 3, labelled), and should AIIMS-type public institutions be eligible for auto-confirm (proposed: no)?
