# 02 — YouTube data layer

Gateway, quota manager, shared cache and dedupe, channel resolution and ownership, ingest, snapshots (same-age problem), comment sampling, and own-channel YouTube Analytics via OAuth.

Status: plan. Consistent with `00-shared-context.md`; deviations are raised in section 13.

Live verification done on 2026-11-20 with the project API key: about 16 units in total (13 in the main bucket, 3 in the batchGetStats bucket), no search.list calls. Findings marked **[live]**.

---

## 1. Purpose & business value

Every number GrowthHacker shows comes through this layer: the client's baseline, competitor cadence, outliers, the reference videos on each card, and the "views at day 7" figure the assignment uses for fair comparison. The layer has to do four things:

1. **Get the right channel.** If we analyse the wrong channel, everything downstream is confidently wrong. For example, `@Apollo247` resolves to "APOLLO COACHING CLASSES SAMBHAJINAGAR" (31 subscribers) **[live]**. Resolution therefore always checks ownership.
2. **Make fair comparisons possible.** Adaptive snapshots and labelled same-age methods let analytics (06) compare a 3-day-old competitor Short with the client's own 3-day-old Shorts, not with a 2-year-old video.
3. **Stay inside quota and policy at SaaS scale.** Public data is fetched once and shared across clients. A per-client ledger feeds the ops report (assignment evaluation item 4). Stored data respects the 30-day rule.
4. **Add the conversion lens where it really exists.** For a client that connects OAuth, we provide subscribers gained, traffic sources and YouTube search terms from the YouTube Analytics API, kept separate from public data. Search terms tell the client which buyer-intent queries already bring viewers. That is the closest official signal to conversion.

## 2. Scope / non-scope

**In scope**
- `YouTubeGateway` port and its Data API v3 adapter: channels, playlistItems (uploads), videos, videos:batchGetStats, search, commentThreads, mostPopular (disabled by default), captions (own channel only, optional).
- `QuotaManager`: three buckets (main, search, batch-stats), Redis atomic counters, Pacific-time reset, priority classes, run budgets, per-client attribution ledger, degradation.
- Shared cache: freshness policy per data kind, ETags, single-flight, cross-run batch coalescing, search cache keyed on normalised parameters.
- `ChannelResolver` and `OwnershipVerifier`.
- `IngestService`: lookback planning, incremental uploads, hydration, format classification (Shorts, live, upcoming), availability tracking.
- Snapshots: adaptive scheduler, collector, compaction, `SnapshotReader.views_at_age()`.
- `CommentSampler`.
- `OwnAnalyticsService`: OAuth connect, refresh and revoke, Analytics API reports, data deletion.
- Retention rules for every YouTube-derived table. Component 01's retention cron executes them.

**Not in scope**
- Baselines, outlier ratios, traits, lift and comment intent: 06 (we provide the inputs and `ViewsAtAge`).
- Competitor discovery logic: 04 (it calls our resolver and cached search).
- Topic matching: 05. We never request or persist YouTube's topic or category signals (section 9).
- Thumbnail image analysis: 06 (we provide URLs).
- Website crawling: 03 (it provides site-to-channel links for ownership checks).
- Cron wiring, SSE and the HTTP API: 11. Table creation mechanics: 01.
- Publishing or any write to YouTube.

## 3. Inputs & outputs

### 3.1 Inputs
- `RequestContext { client_id: UUID | None, run_id: UUID | None, priority: Priority, budget: RunBudget | None }`. Every gateway call carries one.
- Raw channel input strings (URL, handle, id, video link) plus an optional `BusinessHint { name, website_domain, country, site_channel_links: list[str] }` from 03.
- Channel ids to track, with a role per client (`own | competitor | niche_creator`) from 04 or user confirmation.
- `SearchQuery` from 04, 08 and the creator ranker.
- OAuth authorisation code from the callback (own channel).

### 3.2 Outputs (pydantic domain objects owned here)

```text
ChannelRef
  kind: Literal["channel_id","handle","username","custom_url","video","playlist","unknown"]
  value: str                      # normalised (NFC, percent-decoded, no leading '@' stored separately)
  raw: str

ChannelRecord
  channel_id: str                 # "UC" + 22 chars
  title: str
  handle: str | None              # snippet.customUrl, e.g. "@apollohospitalsindia"
  description: str
  country: str | None
  default_language: str | None
  published_at: datetime
  uploads_playlist_id: str | None # contentDetails.relatedPlaylists.uploads ("UU" + suffix)
  subscriber_count: int | None    # None when hidden; API rounds down to 3 significant figures
  subscriber_count_hidden: bool
  view_count: int
  video_count: int                # public videos only (API definition)
  keywords: list[str]             # brandingSettings.channel.keywords, quoted phrases kept whole
  thumbnail_url: str | None
  status: Literal["active","not_found"]
  metadata_fetched_at: datetime
  stats_fetched_at: datetime
  cadence_flags: list[str]        # e.g. "very_new_channel", "shorts_only", "high_cadence", "dormant"

VideoRecord
  video_id: str
  channel_id: str
  channel_title: str
  published_at: datetime          # snippet.publishedAt
  effective_published_at: datetime# liveStreamingDetails.actualStartTime or published_at; age clock origin
  title: str
  description: str
  tags: list[str]                 # creator-declared keywords (allowed for lexical matching)
  default_language: str | None
  default_audio_language: str | None
  localized: dict[str, LocalizedText] # only when localizations fetched
  duration_s: int | None          # None for upcoming (API returns P0D)
  embed_width: int | None         # player.embedWidth with maxWidth=400
  embed_height: int | None
  aspect: Literal["vertical","square","horizontal","unknown"]
  format: Literal["short","long_form","live_replay","live_now","upcoming","unknown"]
  format_basis: list[str]         # e.g. ["duration<=180s","aspect=vertical","published>=2024-10-15"]
  format_confidence: Literal["high","medium","low"]
  has_captions: bool
  licensed_content: bool
  age_restricted: bool            # contentDetails.contentRating.ytRating == ytAgeRestricted
  region_restriction: RegionRestriction | None   # {allowed: list[str]} | {blocked: list[str]}
  made_for_kids: bool
  privacy_status: Literal["public","unlisted","private"]
  upload_status: str
  embeddable: bool
  public_stats_viewable: bool
  has_paid_product_placement: bool | None  # creator-declared; false means "not declared", not "no promotion"
  thumbnails: dict[str, ThumbnailRef]       # {url, width, height}
  comments_state: Literal["enabled","disabled","unknown"]
  likes_visible: bool
  availability: Literal["available","missing","removed"]
  first_missing_at: datetime | None
  latest: VideoSnapshot | None
  metadata_fetched_at: datetime
  quality_flags: list[str]        # "count_decreased", "late_tracked", "duplicate_title_in_channel", "counting_regime_change"

VideoSnapshot
  video_id: str
  captured_at: datetime           # UTC, from a single clock source (Redis TIME)
  age_hours: float                # captured_at - effective_published_at, clamped >= 0
  view_count: int
  like_count: int | None          # None when hidden or absent
  comment_count: int | None       # None when comments disabled or absent
  source: Literal["batch_stats","videos_list"]
  counting_regime: str            # e.g. "2026-11-16-coviews"; set from config by captured_at

ViewsAtAge                        # produced by SnapshotReader, consumed by 06/07
  video_id: str
  target_age_hours: int           # 24, 168, 720, ...
  views: int | None
  method: Literal["analytics_exact","observed","interpolated","late_observed",
                  "age_band_proxy","immature","unavailable"]
  nearest_age_hours: float | None
  bracket_gap_hours: float | None # for interpolated
  current_views: int
  current_age_hours: float
  age_band: str                   # e.g. "3-7d"
  label: str                      # human label, e.g. "views at day 7 (interpolated, ±9h)"

CommentRecord
  comment_id: str
  video_id: str
  text: str                       # textOriginal (plainText); no author fields stored
  like_count: int
  reply_count: int
  published_at: datetime
  relevance_rank: int             # index in order=relevance page
  fetched_at: datetime

OwnershipSignal { kind: str, detail: str, weight: float }
OwnershipAssessment
  status: Literal["verified_oauth","verified_link","likely","unverified","mismatch"]
  score: float
  signals: list[OwnershipSignal]

ChannelResolution
  input: ChannelRef
  channel: ChannelRecord | None
  method: Literal["id","handle","username","video","playlist","custom_url_search","none"]
  candidates: list[ChannelRecord] # when ambiguous
  ownership: OwnershipAssessment | None   # only when resolving a client's own channel
  needs_user_confirmation: bool
  message: str | None

SearchQuery
  q: str
  type: Literal["video","channel"]
  published_after: date | None    # day-rounded (UTC) for cache sharing
  published_before: date | None
  order: Literal["relevance","date","viewCount","rating"]
  region_code: str | None
  relevance_language: str | None
  video_duration: Literal["any","short","medium","long"]  # API: short<4m, medium 4-20m, long>20m
  max_results: int = 50
  page: int = 0
SearchPage { query_key: str, items: list[SearchHit{kind,id,channel_id,rank}], fetched_at, expires_at, from_cache: bool }

QuotaUsage                        # ledger row, also returned to callers
  bucket: Literal["main","search","batch_stats","analytics"]
  endpoint: str
  units: int
  client_id: UUID | None
  run_id: UUID | None
  priority: Priority
  cache_outcome: Literal["miss","coalesced"]  # hits cost 0 and are counted separately
  http_status: int
  reason: str | None

OwnAnalytics                      # bundle for one client, labelled "YouTube Analytics (owner-authorised)"
  connection: OAuthConnectionStatus { status: active|revoked|invalid_grant|expired_testing, channel_id, scopes, last_verified_at }
  data_through: date              # last complete Pacific-time day returned by the API
  channel_daily: list[OwnDailyMetrics]
  video_period: list[OwnVideoMetrics]
  video_daily_views: list[OwnVideoDayViews]   # enables analytics_exact ViewsAtAge
  traffic_sources: list[TrafficSourceRow]
  search_terms: list[SearchTermRow]

OwnDailyMetrics { day: date(PT), views, engaged_views, estimated_minutes_watched, average_view_duration_s,
                  subscribers_gained, subscribers_lost, likes, comments, shares }
OwnVideoMetrics { video_id, period_start, period_end, creator_content_type, views, engaged_views,
                  estimated_minutes_watched, average_view_duration_s, average_view_percentage,
                  subscribers_gained, subscribers_lost, likes, comments, shares }
OwnVideoDayViews { video_id, day: date(PT), views }
TrafficSourceRow { video_id: str | None, period_start, period_end, source_type: str, views, estimated_minutes_watched }
SearchTermRow   { video_id: str | None, period_start, period_end, term: str, views, estimated_minutes_watched }
```

`ViewsAtAge` is listed under 06 in the shared context. It is defined here because its methods depend on snapshot mechanics and retention. 06 owns the baselines built on top of it.

## 4. Internal design

```text
app/ports/youtube.py            YouTubeGateway, YouTubeAnalyticsGateway, OAuthTokenStore (Protocols)
app/adapters/youtube/
  http.py                       DataApiHttp: httpx.AsyncClient, error mapping -> typed exceptions
  retrying.py                   RetryingGateway (decorator)
  metered.py                    QuotaMeteredGateway (decorator) -> QuotaManager
  cached.py                     CachedGateway (decorator): freshness, ETag, single-flight, coalescer
  data_api_v3.py                DataApiV3Gateway: endpoint methods, response -> domain mapping
  analytics_v2.py               AnalyticsApiGateway: reports.query
  oauth_google.py               GoogleOAuthFlow: auth URL (PKCE+state), code exchange, refresh, revoke
  costs.py                      ENDPOINT_COSTS registry {endpoint: (bucket, units)}
  errors.py                     QuotaExhausted, RateLimited, NotFound, CommentsDisabled, Forbidden,
                                KeyRejected, Transient, InvalidRequest
app/services/youtube/
  quota.py                      QuotaManager (Redis Lua), PacificDay, PriorityPolicy, RunBudget
  ledger.py                     QuotaLedger (batched async insert to Postgres)
  cache_policy.py               FreshnessPolicy registry by DataKind
  single_flight.py              RedisSingleFlight, BatchCoalescer
  resolve.py                    InputParser (chain of UrlPattern handlers), ChannelResolver
  ownership.py                  OwnershipVerifier + OwnershipSignalProvider registry
  ingest.py                     IngestService, LookbackPlanner
  format_classifier.py          FormatClassifier registry (AspectDurationShortsRule, LiveRule, optional UushConfirmer)
  snapshots.py                  SnapshotScheduler, SnapshotCollector, SnapshotCompactor, SnapshotReader
  comments.py                   CommentSampler
  search.py                     CachedSearchService (query normalisation, keying, budgets)
  own_analytics.py              OwnAnalyticsService, OAuthConnectionService, AuthorizedDataPurger
  retention_rules.py            RetentionRule list exported to 01's retention cron
```

**Port (abridged signatures)**

```python
class YouTubeGateway(Protocol):
    async def channels_by_id(self, ids: Sequence[str], ctx: RequestContext) -> list[ChannelRecord]: ...
    async def channel_by_handle(self, handle: str, ctx: RequestContext) -> ChannelRecord | None: ...
    async def channel_by_username(self, username: str, ctx: RequestContext) -> ChannelRecord | None: ...
    async def uploads_page(self, playlist_id: str, page_token: str | None, ctx: RequestContext,
                           etag: str | None = None) -> UploadsPage | NotModified: ...
    async def videos_by_id(self, ids: Sequence[str], ctx: RequestContext,
                           with_localizations: bool = False) -> VideoBatch: ...   # VideoBatch(found, missing_ids)
    async def video_stats(self, ids: Sequence[str], ctx: RequestContext) -> StatsBatch: ...  # batchGetStats, fallback videos.list
    async def search(self, query: SearchQuery, page_token: str | None, ctx: RequestContext) -> SearchPage: ...
    async def most_popular(self, region: str, ctx: RequestContext) -> list[VideoRecord]: ...
    async def comment_threads(self, video_id: str, max_results: int, ctx: RequestContext) -> CommentPage: ...
    async def playlist_owner(self, playlist_id: str, ctx: RequestContext) -> str | None: ...

class YouTubeAnalyticsGateway(Protocol):
    async def report(self, token: AccessToken, q: AnalyticsQuery) -> AnalyticsTable: ...
```

**Composition (decorators implement the same Protocol, open for extension):**
`CachedGateway(QuotaMeteredGateway(RetryingGateway(DataApiV3Gateway(DataApiHttp))))`.
- Cache sits outermost, so cache hits never touch quota.
- Metering sits outside retry, so **each attempt** reserves units. Google charges every request, including invalid ones ([getting-started](https://developers.google.com/youtube/v3/getting-started)).
- New behaviour (e.g. a recorded-fixture gateway for backtests, a frozen-at-T gateway for 12) is added as another implementation, not by editing services.

**Registries (extend by adding entries):**
- `ENDPOINT_COSTS`: bucket and units per endpoint (section 7).
- `FreshnessPolicy`: TTL per `DataKind`.
- `InputParser` handlers: an ordered list of `UrlPatternHandler`.
- `OwnershipSignalProvider`s.
- `FormatClassifier` rules.
- `RetentionRule`s.
- `SnapshotCadenceTier`s.

**Dependency injection:** wired in 01's container. Services depend only on ports. Settings come from `YouTubeSettings` (pydantic-settings, prefix `YT_`).

## 5. Algorithms & logic

All thresholds are settings. Defaults are shown.

### 5.1 Input parsing (`InputParser`)
Normalise: trim, NFC-normalise, percent-decode, strip tracking params. Lower-case the host only, never the path, because channel and video ids are case-sensitive. Accepted hosts: `youtube.com`, `www.`, `m.`, `music.`, `youtube-nocookie.com`, `youtu.be`. Handlers in order:

| Pattern | Kind | API call |
|---|---|---|
| `^UC[0-9A-Za-z_-]{22}$` or `/channel/UC…` | channel_id | `channels.list id=` (batchable, 50 ids per call) |
| `/@{handle}` or bare `@handle` | handle | `channels.list forHandle=@handle` (the `@` prefix is accepted, [docs](https://developers.google.com/youtube/v3/docs/channels/list)) **[live: with and without @]** |
| `/user/{name}` | username | `channels.list forUsername=` **[live: GoogleDevelopers]** |
| `/c/{name}` or `/{name}` (legacy custom URL) | custom_url | try `forHandle={name}`, then `forUsername={name}`. If both fail, ask for a video link. Optional fallback: `search.list type=channel q={name}`, accepted only if a candidate's `customUrl` equals `@{name}` case-insensitively and the user confirms |
| `youtu.be/{id}`, `watch?v={id}`, `/shorts/{id}`, `/live/{id}`, `/embed/{id}`, `/v/{id}` | video | `videos.list id=` → `snippet.channelId` → `channels.list` |
| `list=PL…/UU…` | playlist | `UU…` → `UC…` by prefix swap (this is how uploads ids are built **[live]**). Otherwise `playlists.list id=` → `snippet.channelId` (1 unit) |
| anything else | unknown | not resolved here; 04 may treat it as a business name |

The API has no lookup for `/c/` custom URLs. Fetching the HTML page to read the canonical id would be scraping (III.E.6), so it is forbidden. The UI copy says: "Paste any video link from the channel — that always works."

Unicode handles: handles may use letters from 75 languages, are 3–30 characters (1–10 for Han/Hangul), and are case-insensitive ([help](https://support.google.com/youtube/answer/11585688)). We pass the NFC-normalised handle with an `@` prefix and let httpx percent-encode it. **Not verified** that `forHandle` accepts non-ASCII handles. A live smoke test is task T14. If it fails, we fall back to the video-link path.

### 5.2 Ownership verification (client's own channel only)
Competitors only need to be "the channel the user meant", so they skip this; users confirm them in the onboarding step. For the client's own channel, `OwnershipVerifier` sums signal weights. All inputs are data already fetched, so this costs no extra quota.

| Signal | Weight | Source |
|---|---|---|
| OAuth `channels.list mine=true` returns this id | decisive → `verified_oauth` | OAuth |
| Client website links to this channel id or handle | +0.6 | 03 crawler `site_channel_links` |
| Website registrable domain appears in channel description, or in ≥ 2 of the last 20 video descriptions | +0.4 | ingest data |
| Channel title token-similarity to business name ≥ 0.8 | +0.15 | |
| `snippet.country` equals business country | +0.05 | |
| Scale mismatch: subscribers < 1,000 and business has multiple locations or offerings | −0.3 | |
| Website links to a **different** channel | −0.6 | |

Status mapping:
- `verified_oauth` when OAuth confirms.
- `verified_link` when score ≥ 0.6 and includes the website-link signal.
- `likely` when score ≥ 0.4.
- `mismatch` when score < 0.
- `unverified` otherwise.

Anything not verified needs user confirmation before an analysis run. Example: `@Apollo247` scores 0.15 − 0.3 < 0 → `mismatch`. The message reads "This handle belongs to 'APOLLO COACHING CLASSES SAMBHAJINAGAR' (31 subscribers), which doesn't link to apollohospitals.com. Did you mean a different channel?"

### 5.3 Quota manager

**Buckets** (verified against [quota costs](https://developers.google.com/youtube/v3/determine_quota_cost), [getting-started](https://developers.google.com/youtube/v3/getting-started), [revision history](https://developers.google.com/youtube/v3/revision_history)):
- `main`: 10,000 units/day. Covers all methods except the two below.
- `search`: 100 calls/day. search.list costs 1 unit in its own "Search Queries" bucket since June 1, 2026.
- `batch_stats`: `videos.batchGetStats`, 1 unit/call in its own bucket, default 10,000 units/day (revision history entry of June 3, 2026; the method page itself just says "1 unit").
- `analytics`: request-count accounting only. Analytics API quota is not published in units; we log requests and watch the Cloud Console. **Unverified limit.**

All limits are settings, so a quota extension needs no code change.

**Day boundary:** "Daily quotas reset at midnight Pacific Time (PT)" ([quota page](https://developers.google.com/youtube/v3/determine_quota_cost)).
- `pt_day = now_utc.astimezone(ZoneInfo("America/Los_Angeles")).date()`. This handles DST: midnight PT is 13:30 IST in winter and 12:30 IST in summer.
- `now_utc` comes from Redis `TIME`, so all workers share one clock.

**Atomic reservation** (Lua, one round trip):
```text
KEYS: used = quota:{project}:{bucket}:{pt_day}, cls = quota:{project}:{bucket}:{pt_day}:{priority},
      client = quota:{project}:{bucket}:{pt_day}:client:{client_id}, exhausted = quota:{project}:{bucket}:{pt_day}:exhausted
ARGV: cost, limit, class_ceiling_ratio, client_cap, ttl_s
if exists(exhausted) -> return EXHAUSTED
if used + cost > floor(limit * class_ceiling_ratio) -> return CLASS_CEILING
if client_cap > 0 and client + cost > client_cap -> return CLIENT_CAP
INCRBY used cost; INCRBY cls cost; INCRBY client cost; EXPIRE all ttl_s (48h)
return used + cost
```
The run budget (`RunBudget.remaining`) is checked in-process before the Lua call.

**Refund policy:**
- Refund only if the request provably never reached Google: `httpx.ConnectError` or `ConnectTimeout` before sending.
- Read timeouts, 4xx and 5xx are not refunded. Google charges invalid requests, and we cannot know whether a timed-out request was billed.

**Priority classes and ceilings** (share of the bucket a class may push total usage up to):

| Priority | Used by | main | search | batch_stats |
|---|---|---|---|---|
| P0 `interactive` | onboarding resolution, analysis run of a new client, user-triggered refresh | 100% | 100% | 100% |
| P1 `daily_refresh` | scheduled daily analysis runs, reference-video freshness check | 85% | 70% | 95% |
| P2 `snapshot` | milestone snapshots (24h/7d/30d) | 90% (fallback only) | — | 90% |
| P3 `background` | niche-creator refresh, metadata re-refresh not yet due, comment sampling for non-references, mostPopular | 60% | 40% | 70% |

**Run budgets** (defaults):
- Onboarding analysis run: 400 main units + 15 search calls.
- Daily refresh run: 80 main units + 5 search calls.
- Per-client daily caps: 600 main units, 20 search calls. This stops a noisy tenant from starving others.

**Exhaustion handling:**
- Google returns 403 `quotaExceeded` while our counter is below the limit: set `exhausted` until the next PT midnight, log `quota_drift` with our count, alert ops. Drift can come from manual console use or another process.
- Our reservation fails: the gateway raises `QuotaExhausted(bucket, resets_at)`.

**Graceful degradation, in shedding order:**
1. P3 jobs are skipped and rescheduled after the reset.
2. Daily refresh switches to **stale-while-revalidate**: analysis runs on cached data, and every card shows "data as of {fetched_at}". New-upload detection is deferred.
3. Snapshot milestones fall back from `batch_stats` to `videos.list part=statistics` (main bucket, P2) only for videos within ±6h of a 24h or 7d milestone.
4. Onboarding queues with status `waiting_for_quota` and a UI message giving the reset time in the client's timezone.

**Attribution (ops report):**
- Every billable call writes a `QuotaUsage` ledger row with `client_id`, `run_id`, `priority` and `cache_outcome`.
- Cache hits are counted per client in a Redis hash and flushed daily, at 0 units.
- The ops report (12) shows per client per daily refresh:
  - (a) **direct units**: misses triggered by that client's runs;
  - (b) **amortised units**: for shared-entity fetches, units divided evenly among clients whose runs read that entity that PT day;
  - (c) cache hit ratio.
- Coalesced fetches serving several clients are attributed to the initiator. The amortised view corrects for this.

**Multi-project key pool: rejected.** Developer Policies III.D.1.c: "you must create exactly one (1) API Project for that API Client". III.D.1.b forbids masking an API client's access or usage ([policies](https://developers.google.com/youtube/terms/developer-policies)). Rotating keys across several projects to multiply quota is exactly that. Decision:
- Production uses one project, with a `KeyProvider` that supports **rotating keys within the same project** (same quota, credential hygiene only).
- More capacity comes from the documented route: quota extension through the API Compliance Audit (III.D.3).
- Local development may use a developer's own sandbox project. Production traffic never does.

### 5.4 Shared cache, freshness, ETags, single-flight

**Freshness policy** (TTL means "re-fetch when older than this"; nothing may exceed the 30-day hard cap in section 6):

| DataKind | TTL | Notes |
|---|---|---|
| handle/username → channel_id alias | 7 d | handles can change; re-verify on use if older |
| channel metadata (snippet, branding, contentDetails) | 7 d | |
| channel statistics | 24 h | subscriber count is rounded to 3 significant figures, so finer cadence is pointless |
| uploads page 1 (new-upload detection), own/competitor | 6 h | uses ETag |
| uploads page 1, niche creators | 72 h | |
| video metadata (snippet, contentDetails, status, player) | 7 d; own channel 24 h | own channel needs title/description change detection for 09 |
| video statistics | governed by snapshot cadence (5.6) | |
| comment sample | 7 d | raw text deleted at 30 d |
| search results | by `published_after` span: ≤ 7 d → 12 h; ≤ 30 d → 24 h; else 72 h. News-radar demand checks (08): 6 h | |
| mostPopular (if enabled) | 6 h | |
| OAuth analytics | daily pull plus re-pull of last 3 PT days (latency) | |

**ETags:**
- The API supports conditional requests: unchanged resources return 304 ([getting-started](https://developers.google.com/youtube/v3/getting-started)).
- **[live]** `If-None-Match` with the **unquoted** body `etag` returned 304. The RFC-style quoted value returned 200. No `ETag` HTTP response header was sent; the etag is only in the JSON body.
- The docs do not say whether a 304 costs quota. We assume it does (every request costs ≥ 1).
- Benefit: bandwidth, parsing and DB-write avoidance on uploads page 1 and channel polls. We store the etag per (endpoint, normalised params).

**Single-flight:**
- Entity-level work (ingesting a channel, resolving a handle, executing a search key) takes a Redis lock `sf:{kind}:{key}` (`SET NX PX 60000`, fencing token).
- Other runs wanting the same key wait on `done:{kind}:{key}` pub/sub with a 60 s timeout, then read the result from Postgres (a cache hit).
- If the lock holder dies, the lock expires and one waiter takes over.

**Batch coalescing** (`BatchCoalescer`):
- Video-id requests for `videos.list` and `batchGetStats` from concurrent runs in the same worker go into a queue. The queue flushes at 50 ids or after 150 ms.
- Across workers, each id is claimed with `SET NX inflight:video:{id} PX 30000`. Ids claimed elsewhere are awaited, not re-fetched.
- This is what makes the assignment's "batch videos.list (50 IDs per call)" hold across clients.

**Search cache key** (`CachedSearchService`):
- `q` normalisation: NFKC, casefold, collapse whitespace, strip punctuation except `"`, `-` and `|`, then sort `|`-alternatives.
- Key = sha256 over that plus `type, order, published_after` (day-rounded UTC), `published_before`, `region_code, relevance_language, video_duration, page`.
- Day-rounding `published_after` lets every client with the same niche and window share a key on the same day.
- `maxResults` is fixed at 50, so one call returns the most results.
- Results store ids and ranks only. Hits are hydrated through the cached `videos_by_id` and `channels_by_id`, so hydration is also shared.
- Search results are a **list position in the API response**. Any "visibility" use (04/08) must be labelled "position in YouTube API search results (may differ from what users see)".

### 5.5 Ingest flows

**Lookback planner**: enough data for baselines and the largest window, never the full archive.

```text
window_max_days      = 90      # clients can switch 7/30/90 without re-ingest
baseline_extra_days  = 90      # history before the window for age-band baselines
min_per_format       = 30      # videos per format (short / long_form) for a usable baseline
max_videos_channel   = 300     # 6 pages (own/competitor)
max_videos_niche     = 50      # 1 page (niche creators from search)

page through uploads (maxResults=50, part=contentDetails,snippet,status):
  keep items with privacyStatus == public
  stop when ALL hold:
    oldest videoPublishedAt < now - (window_max_days + baseline_extra_days)
    collected counts per format >= min_per_format (format known after hydration; estimate from duration once hydrated per page)
  or when 10 consecutive items are older than the cutoff and format minimums are unreachable within the cap
  or when the cap is reached
```

- Ordering of the uploads playlist is **not documented**. **[live]** It was newest-first for Apollo (position 0 = latest).
- We don't rely on strict order. We stop only after 10 consecutive items past the cutoff, and incremental runs stop after 5 consecutive known ids.
- **Apollo example [live]**: 50 uploads covered Jul 30 – Nov 19, 2026 (~13/month, about half vertical Shorts). The planner should take about 2–3 pages (100–150 videos) for 180 days and ≥ 30 per format. Cost: about 3 + 3 units.
- `playlistItems pageInfo.totalResults` (4,314) ≠ `channels.videoCount` (4,328) **[live]**. Neither is used for completeness checks.

**Incremental (daily and 6-hourly):**
- Fetch page 1 with ETag. On 304, nothing changed.
- Otherwise collect unseen ids until 5 consecutive known ids, up to 2 pages. Overflow sets the `high_cadence` flag.

**Hydration:**
- `videos.list part=snippet,contentDetails,status,statistics,player,liveStreamingDetails,paidProductPlacementDetails&maxWidth=400`, 50 ids per call, 1 unit regardless of parts **[live]**.
- `localizations` is added only when `defaultLanguage` is not in the client's languages. It helps 05 match Hindi or regional titles. Same cost.
- `topicDetails` is **never requested**. `snippet.categoryId` is **dropped at the adapter** (section 9).
- The hydration call's statistics are also stored as a `VideoSnapshot` (`source=videos_list`).

**Format classification** (`FormatClassifier`, first matching rule wins; outputs `format`, `format_basis`, `format_confidence`):

1. `snippet.liveBroadcastContent == "upcoming"` → `upcoming`. `duration_s=None` (API returns `P0D`), no embed size **[live]**. Excluded from metrics and rechecked after `scheduledStartTime`.
2. `liveBroadcastContent == "live"` → `live_now`. Excluded until it ends.
3. `liveStreamingDetails.actualEndTime` present → `live_replay`. This covers both lives and premieres; the API does not reliably tell them apart. `effective_published_at = actualStartTime`.
4. Shorts rule. YouTube's definition: videos uploaded on or after 2024-10-15 that are square or vertical and up to 3 minutes are Shorts. Before that the limit was 60 s ([help](https://support.google.com/youtube/answer/15424877)). The API has **no official is-Short field**.
   - Aspect comes from `player.embedWidth/embedHeight`, which are returned when `maxWidth` is set and the aspect ratio is known ([videos](https://developers.google.com/youtube/v3/docs/videos)). **[live]**: 400×711 (9:16), 400×500 (4:5), 400×533, 400×225 (16:9).
   - Rule: `ratio = h/w ≥ 1.0` and `duration_s ≤ (180 if effective_published_at ≥ 2024-10-15 else 60)` → `short`, confidence `high`. Official Artist Channels had a later cut-off, 2025-12-08; this is configurable, and we use 60 s for them before that date when detectable, otherwise confidence `medium`.
   - Aspect unknown and duration ≤ 60 s → `short` with confidence `low`.
   - Vertical and longer than the limit → `long_form`. **[live]**: `q9hPAs_pOjo` is 3:47 and vertical, so long_form.
   - Horizontal and ≤ 60 s → `long_form`. **[live]**: `8Ldl2evezp4` is 31 s at 16:9 and is absent from the Shorts list.
5. Otherwise → `long_form`.

**Optional cross-check (`UushConfirmer`, default OFF):**
- The undocumented playlist `UUSH{channel suffix}` returned the channel's Shorts through the official `playlistItems.list` endpoint **[live]**: 726 items. Its first 10 matched the aspect+duration rule exactly, and horizontal ≤ 60 s videos were absent.
- It uses an official endpoint, but the playlist id is undocumented and could disappear. When enabled, it only raises or lowers `format_confidence` and never overrides the rule silently.
- Owner decision in section 13.

**Availability tracking:**
- Ids requested but absent from `videos.list` or `batchGetStats` results are omitted silently, with no error **[live: fake id omitted in both]**.
- First miss: `availability=missing`, `first_missing_at=now`.
- Second miss at least 6 h later: `removed`. All stored data and snapshots for that video are purged within 24 h. III.E.4.b requires checking every 30 days that a video has not been deleted; we check far more often.
- Reappears before then: back to `available`.

**Comment sampling (`CommentSampler`):**
- Eligible videos: the client's own videos in the window; competitor videos in the window that are outlier candidates or reference candidates (06 provides the list); top 5 per competitor by views in the window.
- Skip when `comments_state=disabled`, `commentCount` is absent or 0 **[live: commentCount 0 videos return empty pages and waste a unit]**, or `made_for_kids` (comments are off on made-for-kids content).
- Call: `commentThreads.list videoId=… part=snippet order=relevance maxResults=50 textFormat=plainText` ([docs](https://developers.google.com/youtube/v3/docs/commentThreads/list): maxResults 1–100, default order is `time`). One page only, 1 unit.
- Store `textOriginal`, `likeCount`, `totalReplyCount`, `publishedAt` and rank. **Author name, author channel id and avatar URL are never stored** (data minimisation; nothing downstream needs them).
- 403 `commentsDisabled` → set `comments_state=disabled`, no retry.
- Per-run cap: 30 calls at onboarding, 10 on daily refresh.

**Captions** (own channel only, optional, default OFF):
- `captions.list` costs 50 units; `captions.download` costs 200 units and requires permission to edit the video (`youtube.force-ssl` scope) ([download](https://developers.google.com/youtube/v3/docs/captions/download)).
- Competitors' captions cannot be retrieved. It could later ground 09's description suggestions in what the video actually says, but it needs the broader `force-ssl` scope, which is harder to verify with Google. Deferred (section 13).

**mostPopular:**
- Since 2025-07-21 the chart features videos from the Trending Music, Movies and Gaming charts ([revision history 2025-07-10](https://developers.google.com/youtube/v3/revision_history)).
- **[live]** `regionCode=IN` returned Bhojpuri songs, a trailer and a GTA stream. That is useless for a hospital's niche.
- Default OFF. "Trending in my niche" is built from cached niche search (`order=viewCount`, `publishedAfter=window`) plus competitor and niche-creator velocity. Filtering the chart by `videoCategoryId` is not used, because that relies on YouTube's category labels (section 9).

### 5.6 Snapshots and the same-age problem

**Design-doc answer: how we compare videos at the same age when snapshots only start the day we begin tracking.**
1. **Own channel with OAuth:** exact. Analytics API `dimensions=day&filters=video==ID` gives daily views from publish, so cumulative views at day 1/7/30 are known for every video, including ones published before tracking. Method `analytics_exact` (day granularity, Pacific days).
2. **Public videos published after tracking starts:** observed. Adaptive snapshots aimed at the 24 h, 7 d and 30 d milestones. Methods `observed` and `interpolated`.
3. **Public videos already old when tracking starts:** cross-sectional age-band comparison. On the first ingest, one stats call gives each lookback video's *current* views at its *current* age. A video aged 40–60 days is compared with the same channel's other videos currently aged 40–60 days (method `age_band_proxy`, label "views at ~X days, compared with this channel's videos of similar age"). It is fair from day one, and the label makes clear it is not a day-7 figure.
4. **Over time**, observed milestones replace proxies for the 7- and 30-day windows (after ~7 and ~30 days of tracking).
5. **Policy limit:** public statistics may not be stored longer than 30 days (III.E.4.b). So an observed day-7 value can only be kept until its snapshot is 30 days old, i.e. for videos up to ~37 days old. For the 90-day window, reference videos older than ~37 days always use `age_band_proxy`. This is deliberate, not a gap (proposed contract change in section 13).

**Cadence tiers** (`SnapshotScheduler` keeps `next_due_at` per tracked video; age is measured from `effective_published_at`):

| Video age | Cadence | Purpose |
|---|---|---|
| < 48 h | hourly | day-1 precision; velocity |
| 2 – 8 d | every 6 h, plus a milestone shot at 168 h ± 1 h | day-7 |
| 8 – 35 d | daily, plus a milestone at 720 h ± 3 h | day-30 |
| 35 d – (window_max + baseline_extra) | weekly, and always ≤ 25 days apart | age-band baselines; keeps stored stats fresh within 30 days |
| older / not tracked by any active client | none; data ages out | |

- `next_due_at = min(next cadence tick, next milestone − tolerance)`.
- Collection runs every 10 minutes: select due videos ordered by (milestone first, then priority, then due time), batch by 50 into `videos:batchGetStats` ([method](https://developers.google.com/youtube/v3/docs/videos/batchGetStats)).
- **[live]**: 50 ids → 200 OK, returning `statistics` (view/like/comment counts), `snippet.publishTime` and `contentDetails.duration`. 51 ids, including one fake id, → 400 `badRequest`; the fake id alone was silently omitted. So the effective maximum is 50. The `summary` block in the docs was **not** returned.
- Fallback when that bucket is exhausted or the endpoint errors: `videos.list part=statistics` (main bucket, P2, milestone-near videos only).
- A video is tracked while at least one active client has its channel in a role, or it is a stored reference or search result. Snapshots are shared across clients; a video is snapshotted once whatever the client count.
- Detection latency: own and competitor channels are polled every 6 h, so a new video's first snapshot is at ≤ ~6 h of age, which brackets day 1 well. Niche creators are polled every 72 h, so their day-1 values are usually `late_observed` or proxy.

**`SnapshotReader.views_at_age(video, target_h)`:**

```text
if own channel and OAuth daily views available -> analytics_exact (sum of PT-day views through day ceil(target_h/24))
S = snapshots sorted by age_hours; enforce running max for interpolation (raw kept; flag count_decreased)
if current_age < target_h                            -> immature
if exists s with |s.age - target_h| <= tol(target_h) -> observed       tol: 24h:2h, 168h:12h, 720h:48h
if exists lo < target_h < hi and hi.age-lo.age <= max_gap(target_h)   max_gap: 24h:12h, 168h:48h, 720h:7d
                                                     -> interpolated: v_lo + (v_hi-v_lo)*(target-lo.age)/(hi.age-lo.age)
if earliest snapshot age in (target_h, target_h*(1+0.25)]
                                                     -> late_observed (value = earliest; an upper bound; labelled)
else                                                 -> age_band_proxy (views=None; current_views/current_age/age_band populated)
```

Linear interpolation is used only between close snapshots; growth curves are concave, so wide gaps are not interpolated. We never extrapolate backwards.

Age bands (for proxies and 06's baselines): 0–1 d, 1–3 d, 3–7 d, 7–14 d, 14–30 d, 30–60 d, 60–90 d, 90–180 d, 180 d+.

**Compaction (`SnapshotCompactor`, daily):**
- For videos older than 8 days, thin snapshots taken before age 8 d down to: the bracketing pair around 24 h, the bracketing pair around 168 h, and one per day.
- For videos older than 35 days, keep one per week.
- Everything older than 30 days is dropped by partition (section 6).
- Storage is therefore bounded by policy, not by growth.

**Counting-regime markers:** YouTube changed view counting on 2025-03-31 (Shorts count every start or replay), 2026-08-27 (all formats count from playback start) and **2026-11-16** (co-views included) ([revision history](https://developers.google.com/youtube/v3/revision_history)).
- Each snapshot records `counting_regime`.
- Deltas that span a regime change get `counting_regime_change` and are excluded from velocity.
- The 2026-11-16 change is four days before this plan, so it affects early demo data.

**Display freshness (III.E.4.f, "display the most updated API Data"):** before 07 finalises cards, the stats of all reference videos older than 24 h are refreshed with one batchGetStats call per 50 (P1).

### 5.7 Own-channel YouTube Analytics (OAuth)

**Connect:**
- Google OAuth 2.0 web flow with `access_type=offline`, `prompt=consent`, PKCE and a `state` bound to the client session.
- Scopes: `https://www.googleapis.com/auth/yt-analytics.readonly` and `https://www.googleapis.com/auth/youtube.readonly`. The Analytics reference banner says requests now require `youtube.readonly` ([reports.query](https://developers.google.com/youtube/analytics/reference/reports/query)). `yt-analytics-monetary.readonly` is **not requested**: no revenue, by policy.
- After the code exchange: `channels.list mine=true` → channel id. If it does not match the confirmed own channel, show a mismatch message and offer to switch. A match upgrades ownership to `verified_oauth`.
- The refresh token is encrypted with the app key from 01's security module (Fernet or KMS) and never logged.

**Daily pull** (`ids=channel==MINE`, endpoint `GET https://youtubeanalytics.googleapis.com/v2/reports`). All dates are Pacific days ([dimensions](https://developers.google.com/youtube/analytics/dimensions)). We re-pull the last 3 days because data arrives with up to 48–72 h delay and the API returns data only "up until the last day for which all metrics in the query are available".

| Report | Params | Use |
|---|---|---|
| Channel daily | `dimensions=day`, metrics `views,engagedViews,estimatedMinutesWatched,averageViewDuration,subscribersGained,subscribersLost,likes,comments,shares` | own baseline, conversion lens |
| Top videos in window | `dimensions=video` (+`creatorContentType`), `sort=-views`, `maxResults≤200` ([channel reports](https://developers.google.com/youtube/analytics/channel_reports)) | per-video subs gained, watch time, `averageViewPercentage` |
| Video daily views | `dimensions=day`, `filters=video=={id}` per video, only for videos without a complete series | `analytics_exact` ViewsAtAge |
| Traffic sources | `dimensions=insightTrafficSourceType`, channel-level and per video for window videos; videos × days ≤ 50,000 | conversion lens: share from `YT_SEARCH`, `EXT_URL`, `RELATED_VIDEO`, `SHORTS`… |
| Search terms | `dimensions=insightTrafficSourceDetail`, `filters=insightTrafficSourceType==YT_SEARCH[;video==id]`, `sort=-views`, `maxResults≤25` | buyer-intent queries already driving views; input to 05/07/09 |

- Request volume: about 25 requests a day per connected client. The initial backfill is one video-daily request per lookback video, ≤ 300 once.
- `creatorContentType` (`SHORTS`, `VIDEO_ON_DEMAND`, `LIVE_STREAM`, `STORY`) gives an **authoritative** format for own videos and overrides the classifier for them.
- Combining `dimensions=video,day` in a single request is **unverified**. The plan uses one request per video.

**Token lifecycle:**
- Refresh on expiry.
- `invalid_grant` → status `invalid_grant`, notify the user, stop pulls. Causes include revocation, 6 months unused, the ~100-token-per-account limit, and 7-day expiry while the OAuth app is in "Testing" ([oauth2](https://developers.google.com/identity/protocols/oauth2)).
- Production needs Google OAuth app verification for sensitive scopes, typically 3–5 business days ([sensitive scope verification](https://developers.google.com/identity/protocols/oauth2/production-readiness/sensitive-scope-verification)).

**Revocation and deletion (III.D.2.3, III.E.4.g):**
- In-app disconnect: call `https://oauth2.googleapis.com/revoke`, then `AuthorizedDataPurger` deletes all `own_*` rows and the token **immediately** (policy maximum 7 days).
- Revocation detected through `invalid_grant`: purge within 24 h (policy maximum 30 days).
- A monthly job verifies that each connection still works (successful refresh plus one tiny report). That satisfies III.E.4.b's "verify every 30 days" for keeping authorised statistics longer than 30 days. If verification fails, purge.

**Can we get competitor analytics, or Apollo's private analytics, without owner OAuth? No.**
- The Analytics and Reporting APIs only return data for channels the authorised user owns or manages (`channel==MINE`), or for a content owner's own CMS (`contentOwner==`, YouTube Partner Program only) ([reports.query](https://developers.google.com/youtube/analytics/reference/reports/query)).
- Watch time, retention, traffic sources, search terms, impressions, demographics and revenue for a channel we do not control are not available through any official API.
- Third-party "estimators" (e.g. sites that publish estimated earnings or subscriber projections) are off-limits. They are not official APIs (assignment), III.E.6 forbids obtaining scraped YouTube data, and III.E.2.b and III.E.4.h forbid using API data for revenue insights or substitute metrics.
- What we use instead, all labelled as public data: views and engagement at the same age from our own snapshots, velocity, likes/comments per view (simple ratios), upload cadence and format mix, comment-question signals, and position in API search results for tracked buyer questions.
- For Apollo the demo runs entirely on these. The own-channel conversion panel shows an empty state: "Connect your channel to see subscribers gained, traffic sources and the search terms that bring viewers."

### 5.8 Error handling, retries and backoff (`RetryingGateway`)

| Response | Retry? | Action |
|---|---|---|
| 403 `quotaExceeded` | no | mark bucket exhausted until PT midnight; raise `QuotaExhausted` |
| 403/429 `rateLimitExceeded`, `userRateLimitExceeded` (generic Google errors; not on the YouTube errors page) | yes | backoff |
| 500 `backendError`, 502, 503, 504 | yes | backoff |
| network: connect/read timeout, reset | yes | backoff (connect errors refunded) |
| 400 `keyInvalid` / 403 `accessNotConfigured`, `ipRefererBlocked`, API-key restriction errors | no | open circuit for the key for 10 min, page ops; all calls fail fast with `KeyRejected` |
| 403 `forbidden` / `channelForbidden` / `playlistItemsNotAccessible` | no | record on the entity; skip |
| 403 `commentsDisabled` | no | `comments_state=disabled` |
| 404 `channelNotFound`, `playlistNotFound`, `videoNotFound` | no | mark entity `not_found` / `removed` |
| 400 `invalidPageToken` | once | restart pagination from page 1 |
| other 400 | no | log with params (no key), raise `InvalidRequest` (a bug) |

- Backoff: exponential with full jitter (`base=0.5 s`, `cap=20 s`, `max_attempts=4`), honouring `Retry-After`.
- Circuit breaker per endpoint: 5 consecutive 5xx within 60 s → open for 60 s.
- Client-side throttling: `asyncio.Semaphore(8)` concurrent requests and a 20 req/s token bucket. YouTube's per-minute limits are visible only in the Cloud Console (**unverified figure**).
- The key travels only as a query parameter. Logging redacts any `key=` value.

## 6. Data model

Shared (no tenant column) unless marked. Component 01 creates the tables; this is the specification. Every row from the Data API has `fetched_at`. The **hard cap is 30 days** for all non-authorised API data (III.E.4.b–d); the retention cron deletes or refreshes it.

| Table | Key columns | Indexes | Retention |
|---|---|---|---|
| `yt_channel` | `channel_id` PK, title, handle, description, country, default_language, published_at, uploads_playlist_id, subscriber_count NULL, subscriber_hidden, view_count, video_count, keywords text[], thumbnail_url, status, cadence_flags text[], etag, metadata_fetched_at, stats_fetched_at | `lower(handle)` unique where not null | refresh ≤ 30 d while tracked; delete 30 d after last fetch |
| `yt_channel_alias` | (`alias_kind`, `alias_value_norm`) PK, channel_id, resolved_at | | 7 d TTL, hard 30 d |
| `yt_channel_snapshot` | (`channel_id`, `captured_at`) PK, subscriber_count, view_count, video_count | | 30 d (partition drop) |
| `yt_video` | `video_id` PK, channel_id FK, all `VideoRecord` scalar fields, `localized` jsonb, `thumbnails` jsonb, `region_restriction` jsonb, `quality_flags` text[], `etag`, `title_hash` | (channel_id, effective_published_at desc); (availability); partial index on format | metadata refreshed ≤ 25 d while tracked; delete 30 d after last refresh or 24 h after `removed` |
| `yt_video_snapshot` | (`video_id`, `captured_at`) PK, age_hours, view_count, like_count, comment_count, source, counting_regime | **range-partitioned daily on captured_at**; BRIN(captured_at) | drop partitions older than 30 d |
| `yt_snapshot_schedule` | `video_id` PK, next_due_at, tier, milestone_target_h, priority, tracking_started_at, first_seen_age_h, last_captured_at | (next_due_at) | row deleted when untracked |
| `yt_comment` | `comment_id` PK, video_id, text, like_count, reply_count, published_at, relevance_rank, fetched_at | (video_id) | 30 d hard; 06's derived `CommentSignals` follow 06's retention |
| `yt_comment_fetch` | `video_id` PK, fetched_at, outcome (ok/disabled/empty/error), n | | 30 d |
| `yt_search_cache` | `query_key` PK, params jsonb, fetched_at, expires_at, hit_count | (expires_at) | 30 d hard |
| `yt_search_result` | (`query_key`, `rank`) PK, kind, resource_id, channel_id | (resource_id) | with parent |
| `yt_http_etag` | `request_key` PK, etag, stored_at | | 30 d |
| `quota_ledger` | id, occurred_at, pt_day, project, bucket, endpoint, units, client_id NULL, run_id NULL, priority, cache_outcome, http_status, reason | (pt_day, client_id), (pt_day, bucket) | 400 d (our own operational data, not API data) |
| `quota_cache_hits_daily` | (pt_day, client_id, data_kind) PK, hits | | 400 d |
| `client_channel` *(tenant)* | (client_id, channel_id) PK, role, ownership_status, ownership_score, ownership_signals jsonb, confirmed_by_user_at | (channel_id) | client lifetime (stores ids only) |
| `oauth_connection` *(tenant)* | id, client_id, channel_id, google_sub, scopes text[], refresh_token_enc bytea, access_expires_at, status, connected_at, last_verified_at, revoked_at | unique(client_id, channel_id) | deleted at revoke |
| `own_analytics_daily` *(tenant, authorised)* | (client_id, video_id ['' = channel], day) PK, the metrics in `OwnDailyMetrics`, creator_content_type | (client_id, day) | kept while authorisation is verified every 30 d; purged on revoke (≤ 7 d in-app, done immediately) |
| `own_traffic_source` *(tenant, authorised)* | (client_id, video_id, period_start, period_end, source_type) PK, views, minutes | | same |
| `own_search_term` *(tenant, authorised)* | (client_id, video_id, period_start, period_end, term) PK, views, minutes | | same |

Partitioning `yt_video_snapshot` by day makes retention an O(1) `DROP` of the oldest partition (pg_partman or a small job in 01).

## 7. External APIs & libraries

**YouTube Data API v3** (key auth; every call 1 unit unless stated; the `part` list does not change cost).

| Method | Params we use | Cost / bucket | Source | Verified |
|---|---|---|---|---|
| `channels.list` | `id` (≤ 50, comma list), `forHandle` (`@` optional), `forUsername`, `mine` (OAuth); parts `snippet,contentDetails,statistics,status,brandingSettings` | 1 / main | [channels.list](https://developers.google.com/youtube/v3/docs/channels/list), [channels](https://developers.google.com/youtube/v3/docs/channels) | **[live]** forHandle with and without `@`, forUsername |
| `playlistItems.list` | `playlistId=UU…`, `part=contentDetails,snippet,status`, `maxResults=50`, `pageToken` | 1 / main | [playlistItems.list](https://developers.google.com/youtube/v3/docs/playlistItems/list) | **[live]** newest-first (undocumented); `contentDetails.videoPublishedAt` |
| `videos.list` | `id` (≤ 50), parts `snippet,contentDetails,status,statistics,player,liveStreamingDetails,paidProductPlacementDetails[,localizations]`, `maxWidth=400` | 1 / main | [videos.list](https://developers.google.com/youtube/v3/docs/videos/list), [videos](https://developers.google.com/youtube/v3/docs/videos) | **[live]** all parts in one call; missing ids omitted |
| `videos:batchGetStats` | `id` (≤ 50 effective), `part=id,snippet,statistics,contentDetails` | 1 / own bucket (default 10,000/day per revision history 2026-06-03) | [batchGetStats](https://developers.google.com/youtube/v3/docs/videos/batchGetStats) | **[live]** 50 ok, 51 → 400; no `summary` block |
| `search.list` | `q, type=video|channel, publishedAfter/Before (RFC 3339), order, regionCode, relevanceLanguage, videoDuration, maxResults=50` | 1 / Search Queries bucket, 100 calls/day | [search.list](https://developers.google.com/youtube/v3/docs/search/list), [quota](https://developers.google.com/youtube/v3/determine_quota_cost) | docs only (no live calls) |
| `videos.list chart=mostPopular` | `regionCode`, `maxResults` | 1 / main | [videos.list](https://developers.google.com/youtube/v3/docs/videos/list) | **[live]** IN = music/trailers/gaming |
| `commentThreads.list` | `videoId, part=snippet, order=relevance, maxResults=50, textFormat=plainText` | 1 / main | [commentThreads.list](https://developers.google.com/youtube/v3/docs/commentThreads/list) | **[live]** relevance order; empty for commentCount 0 |
| `playlists.list` | `id` (playlist URL resolution) | 1 / main | quota table | docs |
| `captions.list` / `captions.download` | own channel, OAuth `youtube.force-ssl` | 50 / 200, main | [captions.download](https://developers.google.com/youtube/v3/docs/captions/download) | docs; deferred |

**Field notes:**
- `paidProductPlacementDetails.hasPaidProductPlacement` exists. It is true when the creator declared paid product placement **[live: present on all 50 Apollo videos, all false]**. Since 2026-11-16 the property "no longer defaults to false" on insert/update. We read `false` as "not declared", not "no promotion". The creator ranker (07) uses it to tell disclosed paid promotion apart from plain mentions.
- `statistics.dislikeCount` is owner-only. `favoriteCount` is always 0. Neither is used.
- `statistics.likeCount` and `commentCount` may be absent: hidden likes, disabled comments. The docs do not describe absence, so we treat both as optional and store null, never 0.
- `channels.statistics.subscriberCount` is rounded down to 3 significant figures. `hiddenSubscriberCount` flags hidden counts. `videoCount` counts public videos only.
- `brandingSettings.channel.keywords` is creator-declared and used as a lexical input to 04/05. **[live]** Apollo lists "best cardiology hospital" and similar keywords.
- `search.list` `videoDuration` bands are short < 4 min, medium 4–20 min, long > 20 min. They do not line up with Shorts (≤ 3 min plus aspect), so the API filter is never used as a Shorts filter.
- Search is capped at 500 results when `channelId` is set; `order` other than relevance may return incomplete sets. Per-channel listing always uses the uploads playlist, never search.

**ETag:** body `etag`; send it **unquoted** in `If-None-Match` → 304 **[live]**. Quota cost of a 304 is undocumented (assume 1).

**Quota reset:** midnight Pacific Time ([quota](https://developers.google.com/youtube/v3/determine_quota_cost)).

**YouTube Analytics API v2:** `reports.query` ([reference](https://developers.google.com/youtube/analytics/reference/reports/query)), [metrics](https://developers.google.com/youtube/analytics/metrics), [dimensions](https://developers.google.com/youtube/analytics/dimensions), [channel reports](https://developers.google.com/youtube/analytics/channel_reports).
- Dates are Pacific; latency is 48–72 h.
- Thumbnail impression and CTR metrics are **not** listed on the metrics page, so they are not planned.
- Quota is not published in units (**unverified**).

**Google OAuth:** [oauth2 overview](https://developers.google.com/identity/protocols/oauth2) (refresh token expiry reasons, `invalid_grant`), [sensitive scope verification](https://developers.google.com/identity/protocols/oauth2/production-readiness/sensitive-scope-verification). Revoke endpoint: `https://oauth2.googleapis.com/revoke`.

**Policies:** [YouTube API Services Developer Policies](https://developers.google.com/youtube/terms/developer-policies): III.D.1 (one project per API client), III.D.2.3 (deletion on revocation), III.D.3 (quota extension through audit), III.E.2 (aggregation), III.E.4.a–h (storage, 30 days, display, derived metrics), III.E.6 (scraping).

**Libraries:**
- `httpx` (HTTP/2 off; one pooled AsyncClient per worker).
- `redis.asyncio` (Lua scripts, locks, pub/sub).
- `isodate`, or a ~10-line ISO-8601 duration parser.
- `authlib` or `google-auth-oauthlib` for the OAuth web flow. The official `google-api-python-client` is **not** used: it is synchronous and duplicates our typed adapter.
- `cryptography` (Fernet).
- `zoneinfo` (stdlib).
- `tldextract` with a pinned public-suffix snapshot, offline mode, for registrable-domain matching.

**Unverified items:** `forHandle` with non-ASCII handles; batchGetStats default bucket size (from revision history only); whether 304s cost quota; YouTube per-minute rate limits; Analytics API quota; `video,day` combined dimensions; a ~20k-item cap on deep uploads pagination (folklore; irrelevant at our 300-item cap).

## 8. Edge cases & failure modes

| # | Case | Detection | Handling | User-facing message |
|---|---|---|---|---|
| 1 | Main quota exhausted mid-run | Lua reservation returns EXHAUSTED/CLASS_CEILING, or Google 403 `quotaExceeded` | stop new fetches for the run; finish on cached data; mark stale sections; re-queue remaining ingest after PT midnight | "We've hit today's YouTube data limit. Showing data as of {time}; fresh data after {reset in client tz}." |
| 2 | Search bucket exhausted | same, bucket=search | competitor discovery and news demand checks use cached keys only; new keys queued | "Niche search is paused until {reset}. Results use searches from {date}." |
| 3 | batchGetStats bucket exhausted or endpoint errors | reservation / 4xx-5xx | fall back to videos.list statistics for milestone-near videos only; widen other cadences | none (internal); ops alert |
| 4 | 403 `rateLimitExceeded`/`userRateLimitExceeded`/429 | error reason | backoff with jitter, up to 4 attempts, each attempt metered | none unless the run fails: "YouTube is rate-limiting us; retrying." |
| 5 | 403 `forbidden`/`channelForbidden` | reason | record on the entity, skip, don't retry | "This channel's data isn't available through YouTube's API." |
| 6 | Quota drift (Google says exceeded, our counter doesn't) | 403 quotaExceeded with used < limit | set exhausted flag, log drift, alert ops | as #1 |
| 7 | Clock skew between workers / DST change at PT midnight | — | single clock (Redis TIME); `zoneinfo` PT date; keys expire after 48 h | none |
| 8 | API key invalid, restricted (IP/referrer/API restriction) or API disabled | 400 keyInvalid / 403 accessNotConfigured, ipRefererBlocked | open key circuit, fail fast, page ops; never retry-loop | "YouTube data is temporarily unavailable. Our team has been alerted." |
| 9 | Network failure / timeout | httpx exceptions | retry with backoff; refund only connect-before-send failures | none unless the run fails |
| 10 | 5xx `backendError` bursts | consecutive 5xx | circuit breaker 60 s; partial results saved | "Some YouTube data couldn't load; we'll retry automatically." |
| 11 | Handle resolves to an unrelated channel (`@Apollo247`) | ownership score < 0 | `mismatch`, needs confirmation | "@Apollo247 is 'APOLLO COACHING CLASSES…' (31 subscribers) and doesn't link to your website. Is this your channel?" |
| 12 | Handle not found | empty `items` | try username, then ask for a video link | "We couldn't find @{handle}. Paste a link to any video from your channel." |
| 13 | Legacy `/c/custom` URL | parser kind custom_url | forHandle → forUsername → optional cached search + confirmation | "Old-style /c/ links can't always be looked up. Paste a video link instead." |
| 14 | Unicode handle | non-ASCII in handle | NFC + percent-encode; if not found, the video-link fallback | as #12 |
| 15 | Video/Shorts/live/embed link given as channel input | parser kind video | videos.list → channelId | "Found channel '{title}' from that video." |
| 16 | Channel id not found / terminated | empty items or 404 `channelNotFound` | status `not_found`; remove from tracking; keep client_channel row marked | "{channel} is no longer available on YouTube." |
| 17 | Channel has no uploads / uploads playlist 404 | `playlistNotFound` or 0 items | treat as 0 videos; flag `very_new_channel` | "This channel has no public videos yet." |
| 18 | Very new channel (< 5 videos) | count after ingest | flag `very_new_channel`; 06 uses niche-relative comparisons and says so | "Not enough of your own videos for a baseline yet; we compare against similar channels instead." |
| 19 | Channel with only Shorts (or only long-form) | per-format counts | per-format baselines only; flag `shorts_only` | "All your recent videos are Shorts; long-form ideas are compared with competitors only." |
| 20 | Huge channel (Apollo ~4.3k videos) | lookback planner | cap 300 videos / 6 pages; stop at cutoff | none |
| 21 | High-cadence channel (> 50 uploads per 6 h) | incremental overflow | flag `high_cadence`; increase poll frequency for that channel (P1) | none |
| 22 | Dormant channel (no uploads in 180 d) | lookback | flag `dormant`; baseline from last 30 videos regardless of age, labelled | "This channel hasn't posted in 6 months." |
| 23 | Uploads ordering not newest-first | non-monotone `videoPublishedAt` | 10-consecutive-older stop rule; sort locally | none |
| 24 | Invalid page token mid-pagination | 400 invalidPageToken | restart from page 1 once; dedupe by id | none |
| 25 | Private item in uploads playlist | `status.privacyStatus != public` | skip | none |
| 26 | Video deleted / made private after ingest | id omitted from videos.list/batchGetStats | missing → removed after a 2nd miss ≥ 6 h later; purge data within 24 h; drop from references | reference silently replaced; card regenerates if it falls below 3 references |
| 27 | Unlisted video encountered (via search or user link) | privacyStatus | not used as evidence | "That video is unlisted, so we don't use it as evidence." |
| 28 | Upcoming / scheduled premiere | `liveBroadcastContent=upcoming`, `P0D` | format upcoming, excluded; recheck after scheduledStartTime | none |
| 29 | Currently live | `liveBroadcastContent=live` | excluded until it ends | none |
| 30 | Live replay / premiere | `liveStreamingDetails.actualEndTime` | format live_replay; age from actualStartTime; separate format band | "Live" badge on cards |
| 31 | Age-restricted video | `contentRating.ytRating=ytAgeRestricted` | flag; usable as evidence but labelled; never recommended as a format to copy | "Age-restricted" label |
| 32 | Region-blocked in client's country | `regionRestriction` | flag; excluded from niche-trending for that region | "Not viewable in {country}" |
| 33 | Made for kids | `status.madeForKids` | no comment fetch | none |
| 34 | Likes hidden | `likeCount` absent | null, `likes_visible=false`; engagement ratios computed without likes | "Likes hidden by creator" |
| 35 | Comments disabled | `commentCount` absent or 403 `commentsDisabled` | `comments_state=disabled`; no further calls | "Comments are off for this video" |
| 36 | commentCount = 0 | stats | skip commentThreads (saves a unit) | none |
| 37 | Comments in many languages / emoji only | — | store as is; 06 decides use | none |
| 38 | Hidden subscriber count | `hiddenSubscriberCount=true` | subscriber_count null; creator ranker uses views-based scale | "Subscriber count hidden" |
| 39 | Subscriber count rounding | 3 significant figures | never compute daily sub deltas from public counts | none |
| 40 | View count decreases (spam filtering) | snapshot < previous | keep raw; running max for interpolation; flag `count_decreased` | none |
| 41 | View counting definition change (2025-03-31, 2026-08-27, 2026-11-16) | snapshot `counting_regime` | exclude cross-regime deltas from velocity | footnote on velocity charts |
| 42 | Video older than tracking start | first snapshot age > target | `age_band_proxy` / `late_observed` labels | "Views at ~40 days, compared with this channel's videos of similar age" |
| 43 | Day-7 snapshot aged out by 30-day rule (video 37–90 d old) | no snapshot ≤ tol | `age_band_proxy` | as #42 |
| 44 | Snapshot job late (worker down) | gap > max_gap | no interpolation; next tier proceeds; ops metric `milestone_miss_rate` | none |
| 45 | Video detected late (niche creator, 72 h poll) | first_seen_age_h > 24 | day-1 = late_observed/proxy | label |
| 46 | Same video requested by many clients concurrently | in-flight keys | single-flight + coalescer; one fetch | none |
| 47 | Same channel is a competitor for client A and own channel for client B | client_channel roles | one shared ChannelRecord; OAuth data only for B; never shown to A (III.E.3.b) | none |
| 48 | Duplicate ids across search queries / pages | id set | dedupe before hydration | none |
| 49 | Re-upload of the same content by a channel | same normalised title hash within channel, published within 30 d | flag `duplicate_title_in_channel`; 06 counts once for lift | none |
| 50 | Video appears in a channel's playlists other than uploads | n/a (we use only uploads) | nothing to dedupe | none |
| 51 | Multi-language / localized titles | `defaultLanguage` ≠ client languages | fetch `localizations` (same cost); store `localized` | none |
| 52 | Title/description edited after ingest (own channel) | metadata re-fetch diff (24 h TTL) | update record; emit `video_metadata_changed` event for 09 | "Description updated on YouTube {date}" |
| 53 | Shorts detection ambiguity (no embed dims) | embed dims missing | duration-only rule, confidence low | "Format: probably a Short" |
| 54 | Vertical video > 3 min | aspect vertical, duration > 180 | long_form | none |
| 55 | Horizontal ≤ 60 s | aspect horizontal | long_form (not a Short) | none |
| 56 | Pre-2024-10-15 video 61–180 s vertical | date rule | long_form | none |
| 57 | Paid placement flag false on new uploads | Nov 2026 default change | treat as "not declared" | creator card: "No paid promotion declared" |
| 58 | Views inflated by ads (e.g. Apollo `CrMLtMdxhag` 898,779 views, 484 likes, 0 comments **[live]**) | likes/view far below channel's norm | stored as-is; data-quality hint passed to 06 | 06 decides: "Unusually few likes per view; treat this outlier with caution." |
| 59 | OAuth user connects a different channel than confirmed | `mine=true` id ≠ own channel | refuse silent switch; offer to replace | "You connected '{X}', but your workspace tracks '{Y}'. Switch?" |
| 60 | OAuth user has no YouTube channel | 401 `youtubeSignupRequired` | abort connect | "This Google account has no YouTube channel." |
| 61 | Refresh token invalid (revoked, Testing-mode 7-day expiry, 6-month idle) | `invalid_grant` | status invalid_grant; purge authorised data ≤ 24 h; email/UI prompt | "Your YouTube connection expired. Reconnect to keep channel analytics." |
| 62 | User disconnects in app | API call | revoke + immediate purge | "Disconnected. Your channel analytics have been deleted." |
| 63 | Analytics data not yet available for recent days | rows omitted / `data_through` < yesterday | re-pull last 3 PT days | "Channel analytics are up to {data_through} (YouTube reports with a 2–3 day delay)." |
| 64 | Analytics query too large (videos × days > 50,000) | precomputed size | split into date chunks | none |
| 65 | Analytics returns no rows | `rows` omitted | empty result, not error | "No data for this period." |
| 66 | Insufficient OAuth scope | 403 `insufficientPermissions` | prompt re-consent with correct scopes | "Please reconnect and allow analytics access." |
| 67 | Search returns fewer than maxResults / incomplete for non-relevance order | count | accept; mark `partial` | none |
| 68 | Search returns channels/playlists when type mismatched | kind | filter by requested type | none |
| 69 | mostPopular chart unavailable for region | 400 `videoChartNotFound` | disable for region | none (feature off by default) |
| 70 | Record older than 30 days still referenced by an old recommendation | retention cron | recommendation archived with ids only; numbers not re-shown unless refreshed | "Archived recommendation — data refreshed on view" |
| 71 | Redis unavailable | connection error | quota manager fails **closed** for P1–P3; P0 allowed with in-process conservative counter (≤ 200 units) and ledger write-ahead in Postgres | ops alert |
| 72 | Postgres write failure after a successful fetch | exception | retry write; fetch not repeated (result held in run memory) | none |
| 73 | Response schema surprise (missing part, new enum) | pydantic validation | lenient parsing for optional fields; unknown enums → `unknown`; log sample | none |
| 74 | batchGetStats > 50 ids | 400 | chunk at 50 (enforced in adapter) | none |
| 75 | Members-only / premium video | absent from public listings, or videos.list omits it | treated as unavailable | none |

## 9. Policy & guardrail check

| Feature | Guardrail | Status / mitigation |
|---|---|---|
| All data via Data API v3 key + Analytics API OAuth | official APIs only; III.E.6 no scraping | No HTML fetch of youtube.com, including `/c/` resolution and `/shorts/` redirect checks. The undocumented `UUSH` playlist uses an official endpoint but is flagged and OFF by default. Thumbnail images are fetched from API-provided URLs only (06). |
| Shared cache across clients | assignment: "cache public data once, reuse across clients"; III.D.1.c one project for our API client | One API client (GrowthHacker) and one project; caching Non-Authorised data for ≤ 30 d is permitted (III.E.4.d). Authorised (OAuth) data is never shown to other tenants (III.E.3.b). |
| 30-day refresh/delete | III.E.4.b–d | Hard 30-day cap on every Non-Authorised table; partitioned snapshots dropped daily; metadata refreshed ≤ 25 d; removed videos purged within 24 h. Consequence: the same-age methods in 5.6. |
| Display freshest data | III.E.4.f | Reference stats refreshed ≤ 24 h before cards are finalised; "as of" timestamps when stale. |
| Metrics = API data + simple maths | assignment; III.E.4.h derived metrics | This layer stores raw API values only. Interpolated day-N views are derived (simple linear maths). They are labelled with method and never shown in place of YouTube's current count. **Tension:** III.E.4.h literally forbids "new or derived data or metrics", while the assignment explicitly allows simple maths. Raised in section 13. |
| No merging YouTube data with other sources | assignment | Own-channel Analytics data and public data are kept in separate tables and labelled. Website data never enters these tables. |
| No category inference | assignment | `topicDetails` not requested; `snippet.categoryId` dropped at the adapter; `videoCategoryId` chart filter unused. Tags and channel keywords (creator-declared) are only lexical inputs. |
| No audience demographics | assignment | No demographic Analytics reports requested (no `ageGroup`, `gender`, `country` audience reports). Comment author identity not stored. |
| No revenue / monetisation estimates | assignment; III.E.2.b | Monetary scope not requested; no revenue metrics; paid placement flag shown as the creator's own disclosure only. |
| Aggregation across channels | III.E.2.a "may only aggregate API Data relating to YouTube channels that are under the same content owner" | **Risk.** Niche baselines and lift across competitors aggregate data from different owners. This layer stores per-channel data and does not aggregate. 06/07 should present per-channel comparisons and treat any cross-channel aggregate as an internal ranking signal, not a displayed YouTube metric. Needs compliance review (section 13). |
| OAuth data handling | III.D.2.3, III.E.4.a/b/g | Tokens encrypted; in-app revoke → immediate deletion; external revoke → ≤ 24 h; 30-day authorisation re-verification; deletion on request ≤ 7 d. |
| Multiple projects for quota | III.D.1.b/c | Rejected; quota extension through the audit. |
| Thumbnails | simple traits only | URLs only here; no face analysis anywhere (06). |

**Design-doc answer: how we share cached public data across clients within policy.**
1. Data is fetched once per entity under one API project, the one registered for GrowthHacker.
2. It is stored as Non-Authorised data with a 30-day hard cap and refreshed while any client tracks it.
3. It is displayed with "as of" timestamps and refreshed before display.
4. It is never mixed with any tenant's OAuth data, which stays tenant-scoped.
5. Per-client attribution is kept in the quota ledger, so the cost of sharing is visible.

## 10. Performance, cost & scaling

**Per-client quota, typical client:** own channel + 5 competitors + 15 niche creators, 10 keyword queries, 90-day max window.

| Activity | Onboarding (cold cache) | Daily refresh (warm) |
|---|---|---|
| Resolve channels (handles + batched ids) | 4–8 | 0–1 (channel stats batch) |
| Uploads paging (own + 5 competitors at ~3 pages; 15 creators at 1 page) | ~33 | ~6 (own/comp page 1 ×4/day amortised by ETag ≈ 6) + ~5 (creators every 72 h) |
| videos.list hydration (≈ 900 own/comp + 750 creator + ~400 search ids) | ~41 | ~3 (new uploads + search hits) |
| channels.list for search-result channels (~150) | ~3 | ~1 |
| commentThreads sampling | ~30 | ~5–10 |
| Metadata re-refresh for the 30-day rule (≈ 2,050 videos / 50 / 25 d) | 0 | ~2 |
| Reference freshness before cards | 0 (fresh) | in batch_stats |
| **Main bucket total** | **≈ 110–120 units** | **≈ 25–30 units** |
| **Search bucket** | ≈ 10–15 calls (incl. 04 discovery) | ≈ 3–5 calls (0 when another client in the same niche refreshed the key today) |
| **batchGetStats bucket** | ≈ 0 (hydration has stats) | ≈ 10–15 calls (≈ 450 tracked videos daily/weekly + hourly young videos, shared) |
| Analytics API (OAuth clients only) | ≤ 300 requests backfill | ≈ 25 requests |

**Capacity per project at default quota:**
- Main bucket: (10,000 − 2,000 reserved for onboarding) / ~28 ≈ **285 clients per daily refresh**, plus ~16 onboardings/day.
- Search: 100 calls/day ÷ ~4 per client ≈ **25 clients/day without niche sharing**. **Search is the binding constraint.** Mitigations: day-rounded shared keys, TTLs by window, per-client caps, then a quota extension through the audit.
- batch_stats: ~10,000 calls/day = 500k video-stat reads; ample.

**Latency budget** (toward < 2 min per client; this layer's share ≤ 20 s warm, ≤ 60 s cold):
- Calls take ~150–400 ms. Cold onboarding is ~120 calls at concurrency 8 → ~6–10 s plus DB writes.
- A warm daily refresh is mostly cache reads, plus ~25 calls → < 5 s.
- Snapshot collection runs out of band, so it is never on the 2-minute path.
- Analytics backfill runs as a background job after OAuth connect and is not blocking.

**Storage (500 clients, ~2,000 distinct channels, 50% competitor overlap):**
- ~600k video rows (with 30-day refresh only for tracked ones).
- Snapshots: ~190k/day × ≤ 30 days ≈ 5.7M rows ≈ 0.5 GB with indexes, bounded by policy.
- Comments: ≤ 50 per sampled video, ~30 days ≈ 1–2M rows.

**LLM cost:** none in this component.

## 11. Testing strategy

- **Recorded fixtures** (`tests/fixtures/youtube/`, key-free, captured from the live calls in this plan):
  - Apollo channel (forHandle)
  - `@Apollo247` (mismatch case)
  - uploads page 1
  - 50-video `videos.list` with upcoming `P0D`, vertical 400×711, 4:5 400×500, 16:9 400×225, live replays
  - batchGetStats page
  - fake-id omission
  - 304 ETag
  - commentThreads empty and non-empty
  - mostPopular IN
- **Unit tests:**
  - `InputParser`: table-driven, every URL form in 5.1, plus Hypothesis fuzzing of junk and unicode inputs.
  - `OwnershipVerifier`: golden Apollo cases (`@ApolloHospitalsIndia` → verified_link with a website link fixture; `@Apollo247` → mismatch).
  - `FormatClassifier`: all rules incl. the 2024-10-15 boundary and the Official Artist Channel date.
  - `LookbackPlanner`: synthetic channels (shorts-only, dormant, high-cadence, 4.3k videos).
  - `SnapshotReader.views_at_age`: every method branch. Hypothesis properties: interpolated value within [v_lo, v_hi]; monotone in target age; never extrapolates.
  - Search key normalisation: equivalent queries → same key.
- **Quota manager** (fakeredis with Lua, or a real Redis in CI):
  - concurrent reservations never exceed the limit (asyncio stress with 200 tasks);
  - priority ceilings and per-client caps;
  - PT day roll at 00:00 PT including DST transitions (2026-03-08, 2026-11-01) with frozen time;
  - refund only on connect errors;
  - exhausted flag on a simulated 403.
- **Retry/backoff:** `respx` mocks for each row of the 5.8 table; assert no retry on quotaExceeded, keyInvalid or commentsDisabled, and that every attempt is metered.
- **Single-flight / coalescer:** two simulated runs for the same channel → exactly one upstream call; 120 ids from 3 runs → 3 calls.
- **Contract tests:** one `YouTubeGateway` contract suite run against `FakeGateway`, `RecordedGateway` (fixtures) and the real adapter (live, opt-in).
- **Retention tests:** rows older than 30 d are deleted by the rules; a `removed` video is purged; OAuth revoke deletes every `own_*` row.
- **Live smoke** (opt-in `YT_LIVE_SMOKE=1`, ≤ 25 units, no search unless `YT_LIVE_SEARCH=1` at 1 call):
  - Apollo resolution, uploads page, hydration of 50, batchGetStats of 50, one commentThreads call, and a unicode `forHandle` probe (T14).
  - Asserts schema assumptions still hold (embed dims, paid placement part, 304 behaviour).
- **Analytics:** mocked `reports.query` responses (including omitted `rows`); OAuth flow tested with a fake token endpoint; a manual test with the owner's own channel before the demo.

## 12. Implementation task breakdown

| # | Task | Size | Depends on |
|---|---|---|---|
| T1 | Domain models (section 3) + `ENDPOINT_COSTS` + typed errors | S | 01 domain package |
| T2 | `DataApiHttp` + `DataApiV3Gateway` (all read methods, response mapping, categoryId/topicDetails stripping, key redaction) | M | T1, 01 settings |
| T3 | `RetryingGateway` + circuit breakers + throttling | S | T2 |
| T4 | `QuotaManager` (Lua, PT day, priorities, run budgets, client caps) + `QuotaLedger` | M | 01 Redis/Postgres |
| T5 | `QuotaMeteredGateway` | S | T3, T4 |
| T6 | Freshness registry, `CachedGateway`, ETag store, `RedisSingleFlight`, `BatchCoalescer` | M | T5, 01 tables |
| T7 | `InputParser` + `ChannelResolver` | M | T6 |
| T8 | `OwnershipVerifier` + signal providers | S | T7, 03 (site links; stub until ready) |
| T9 | `FormatClassifier` (+ optional `UushConfirmer` behind a flag) | S | T2 |
| T10 | `LookbackPlanner` + `IngestService` (initial + incremental + availability tracking) | M | T6, T9 |
| T11 | Snapshot tables (partitioned), `SnapshotScheduler`, `SnapshotCollector` (batchGetStats + fallback), `SnapshotCompactor` | M | T6, 01 migrations, 11 cron |
| T12 | `SnapshotReader.views_at_age` + age bands + counting regimes | S | T11 |
| T13 | `CachedSearchService` (normalisation, keys, TTLs, budgets) | S | T6; consumers 04/08 |
| T14 | Live smoke suite + fixture recorder (incl. unicode forHandle probe) | S | T2 |
| T15 | `CommentSampler` | S | T6, 06 candidate list interface |
| T16 | Retention rules export + removed-video purge | S | 01 retention cron |
| T17 | OAuth flow (connect, callback, encrypt, refresh, revoke, monthly verify) | M | 01 security, 11 API routes |
| T18 | `AnalyticsApiGateway` + `OwnAnalyticsService` (daily pull, backfill, traffic sources, search terms) + `AuthorizedDataPurger` | M | T17 |
| T19 | Reference freshness hook for 07 finalise | XS | T11, 07 |
| T20 | Ops metrics: ledger rollups, cache hit ratios, milestone miss rate → 12 | S | T4, 12 |

Critical path for the Week-2 milestone (ingestion + daily snapshots): T1 → T2 → T3 → T4 → T5 → T6 → T7 → T9 → T10 → T11 → T12. OAuth (T17–T18) can run in parallel; the demo does not need it for Apollo.

## 13. Open questions / proposed changes to shared context

1. **search.list cost wording.** The shared context says "search.list is 100 units/call". Current docs: search.list costs **1 unit in its own Search Queries bucket**, default 100 calls/day, since 2026-06-01 ([quota](https://developers.google.com/youtube/v3/determine_quota_cost), [search.list](https://developers.google.com/youtube/v3/docs/search/list)). Capacity is the same (≈ 100 searches/day), but search **does not consume the 10,000-unit main bucket**. Proposal: update section 2 to "search.list: separate bucket, 100 calls/day; main bucket 10,000 units for everything else".
2. **Add `videos:batchGetStats` to the shared vocabulary.** 1 unit per ≤ 50 ids in its own bucket (default 10,000/day per revision history, 2026-06-03), verified live. All snapshots run on it, which takes stats polling off the main bucket entirely.
3. **The 30-day statistics rule vs `views_day7` in the output contract.** III.E.4.b forbids storing Non-Authorised statistics for more than 30 days. Observed day-7 values for public videos older than ~37 days therefore cannot exist; 90-day-window references need `age_band_proxy`. Proposal: change `ReferenceVideo.views_day7: int` to `views_at_age: {views: int | None, age_days: int, method: str, label: str}`, keeping `views_day7` populated only when method ∈ {analytics_exact, observed, interpolated}. The quality bar's "views at the same age" is met by the labelled proxy.
4. **III.E.2.a (aggregation only within the same content owner) and III.E.4.h (no derived metrics)** conflict with niche baselines, lift and outlier ratios across competitors. The assignment explicitly allows simple maths, so we follow it. Proposal: record this as a known policy risk in the shared context. 06/07 should display per-channel comparisons rather than cross-owner aggregates where possible. File the API Compliance Audit early (it is also the only path to more search quota).
5. **Multi-project key pool: decided against** (III.D.1.c). Add to Owner decisions: "one Google Cloud project for production; more quota only through the compliance audit".
6. **Shorts detection.** Primary rule: `player` embed aspect (needs `maxWidth`) plus the duration and date rule. Optional: the undocumented `UUSH` uploads-Shorts playlist as a confidence cross-check, verified live but undocumented. Owner to decide whether it may ever be enabled.
7. **"Trending charts".** `chart=mostPopular` now only reflects Music/Movies/Gaming (since 2025-07-21; verified live for IN), so it is useless for business niches. Proposal: define "trending in my niche" as cached niche search (`order=viewCount`, window) plus competitor/creator velocity from snapshots. mostPopular is OFF by default.
8. **Category signals.** `topicDetails` is not requested and `snippet.categoryId` is dropped at ingest. Proposal: state this in section 2 as the concrete implementation of "no category inference".
9. **OAuth production readiness.** `yt-analytics.readonly` and `youtube.readonly` are sensitive scopes needing Google verification. While the app is in Testing, refresh tokens expire after 7 days. Captions (`youtube.force-ssl`, 200 units per download) are deferred. Add to the build-order notes.
10. **Engagement anomaly.** Apollo has videos with ~900k views but ~500 likes and 0 comments, which looks like promoted views. 06 needs a labelled low-engagement flag so such videos don't dominate outliers. The data layer passes raw counts only.
11. **View counting regime changes** (2025-03-31, 2026-08-27, 2026-11-16 co-views) should be recorded in shared config and respected by 06's velocity and backtest code (12). The Nov-16 change sits inside any backtest window chosen now.
12. **Comment privacy.** Proposal: shared rule "comment author identity is never stored".
13. **Ops report definition.** Propose reporting both direct and amortised units per client per daily refresh, across all three buckets plus the Analytics request count, because sharing makes "units per client" ambiguous otherwise.
