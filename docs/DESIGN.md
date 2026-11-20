# GrowthHacker design review

The week-1 deliverable from the assignment: architecture, data model, quota plan, every computed feature with a policy check, and answers to the four design questions. It describes what is built and running, with numbers measured on 2026-11-20. The binding integration plan is `docs/MASTER_PLAN.md`; the per-component plans are in `docs/plans/`.

## 1. What it does

For each client the engine answers the assignment's five questions (own performance, competitors, niche trends, outliers, what to make next) through two lenses, views and conversion. Every recommendation cites at least three reference videos from the client's window (7, 30 or 90 days), each with its views at a comparable age and its ratio to its own channel's normal.

Inputs: a website and/or a YouTube channel (or just a business name). Outputs:

- recommendation cards in the assignment's output contract (`video_idea`, `title_pattern`, `thumbnail_pattern`, `format`, `creator`), plus `news_opportunity` and `description_refresh` in full mode;
- separate surfaces that never mix into the card list: early signals (first-mover news with fewer than 3 references), channel hygiene (rule-based description fixes with paste-ready lines), competitor evidence sheets, creators, niche radar, and AI-answer citations.

## 2. Architecture

```
                 Next.js UI  ──REST /api/v1 + SSE──►  FastAPI (stateless, sessions + API keys)
                                                          │ enqueue (JobQueue port → arq on Redis)
                                                          ▼
          workers: interactive (onboarding, analysis, cards) · batch (daily refresh, backtests)
                   · maintenance (crons: snapshots, news, retention, rollups, reconciler)
                                                          │
                       DagRunner → registered Stages → hash-keyed artifacts (resumable)
   ┌──────────┬──────────┬──────────┬──────────┬──────────┬──────────┬──────────┐
   │ 03 web   │ 04 rival │ 02 YouTube│ 05 topics│ 06 metrics│ 08 news │ 09 refresh│
   │ intel    │ discovery│ data layer│ (hybrid) │ outliers │ radar    │ + hygiene │
   └────┬─────┴────┬─────┴────┬─────┴────┬─────┴────┬─────┴────┬─────┴────┬─────┘
        └──────────┴──────────┴────► 07 recommender: drafts → merge → score → gates
                                                          ▼
                                     10 writer (LLM wording only) → validators → critic
                                                          ▼
                                     finalize: publish 3 windows atomically → UI
   cross-cutting: 01 platform (Postgres 18, Redis, config, tenancy, retention) · 12 evaluation & ops
   external: YouTube Data API v3 (+ Analytics API with owner OAuth) · Tavily · Anthropic (OpenAI-compatible fallback)
```

**Numbers from code, words from the LLM.** Everything up to the recommender is deterministic. The LLM is used at four bounded edges, each schema-constrained and checked against evidence:

1. reading the website into a business profile (every fact carries a verbatim quote from a stored page);
2. adjudicating ambiguous topic and competitor matches (answers restricted to the client's own topic ids or "none");
3. judging news relevance (the entity must appear verbatim in the article);
4. phrasing cards and description insertions (every number, video id and name must exist in the evidence pack; up to 3 retries, then a template).

**Pipelines** (stage names as registered):

| pipeline | stages |
|---|---|
| onboarding | resolve_inputs → crawl_site → build_profile → {discover_competitors, seed_topic_refs} + prefetch_own_channel → finish_onboarding |
| analysis, daily_refresh | snapshot_config → {ingest, niche_discovery, news, own_analytics, competitor_drift} → enrich → topics → analytics → refresh → recommend → write_cards → finalize |
| cards | snapshot_config → write_cards → finalize (on-demand wording for another window) |
| backtest | snapshot_config → frozen_corpus → enrich → topics → analytics → recommend → record_backtest |

Stage outputs are artifacts keyed by an input fingerprint, so a daily refresh reuses unchanged work and a failed run resumes from the failed stage. One active run per client is enforced by a partial unique index plus leases and a reconciler cron.

**Degrade, never block.** LLM down: templates and deterministic fallbacks. Tavily down: discovery from the profile and site links; news skipped. Search quota gone: cached results, labelled "demand not checked". Crawl blocked: the acquisition cascade (§2.1). Every gap appears as a run notice in the UI.

### 2.1 Website acquisition cascade

Business context comes from tiered sources, run in order until the profile's required facets (offerings, locations, conversion actions, questions) are covered. Each fact records the tier that produced it (`source_kind`).

| tier | source | notes |
|---|---|---|
| 1 | own polite crawler | robots.txt via protego; honest user agent, a compatible agent after a WAF 401/403 |
| 2 | headless browser render | JavaScript shells and browser-gated pages; robots obeyed; SSRF guard on every request |
| 3 | reader proxies | Tavily extract, then markdown readers; never touches the origin |
| 4 | search index | Tavily's indexed copies of the client's own domain |
| 5 | archives | latest Wayback / Common Crawl captures, labelled with the snapshot date |
| 6 | knowledge base | Wikipedia summary and Wikidata facts |
| 7 | web mentions | what third parties say; never auto-confirmed |
| 8 | YouTube channel | channel snippet and recent uploads through the Data API |
| 9 | user material | text, brochures or URLs the user provides |

Archive, knowledge-base and web-mention facts need the user's confirmation. There is no CAPTCHA solving and no proxy rotation. A business with no reachable site can still be onboarded from its name.

## 3. Data model

PostgreSQL 18, 118 tables, one Alembic chain (head `0012_citations`). Every table declares a zone, checked by tests:

- **shared:** public YouTube data cached once for all clients, with no tenant columns (`yt_channels`, `yt_videos`, `yt_video_snapshots`, `yt_search_cache`, `yt_video_metrics`, `yt_video_outliers`, `yt_channel_baselines`, `yt_thumbnail_traits`). Only API-key data goes here.
- **tenant / workspace:** `workspace_id` (+ `client_id`) on every row; everything about a client, including OAuth analytics, which only the authorising workspace sees.
- **global:** operational tables without client data (ledgers, shared news items and stories, LLM result cache, users and sessions).
- **evaluation:** freezes, label and review packs, results.

| area | main tables |
|---|---|
| tenancy and auth | workspaces, users, workspace_members, api_keys (HMAC-hashed with a pepper), user_sessions, clients, audit_log, deletion_requests |
| website intelligence (03) | websites, crawl_runs, crawled_pages, page_blobs, page_facts, business_profiles (versioned), profile_edits, buyer_questions, keywords |
| YouTube cache (02) | yt_channels, yt_videos, yt_video_snapshots (daily partitions), yt_snapshot_schedule, yt_search_cache, yt_comments (text transient, no authors), yt_quota_ledger |
| owner analytics (02) | oauth_tokens (Fernet-encrypted), own_analytics_daily, own_traffic_source, own_search_term, own_video_metrics |
| competitors (04) | competitor_discovery_runs, competitor_candidates, competitor_channel_matches, competitor_sets (versioned), competitor_drift_events |
| topics (05) | topic_universes, topic_refs, topic_aliases, topic_matches, answer_directness, topic_review_items |
| metrics (06) | yt_video_age_values, yt_video_metrics, yt_video_outliers, yt_channel_baselines, yt_video_traits, yt_thumbnail_traits, yt_comment_signals |
| recommendations (07/10) | recommendations, recommendation_references, recommendation_copy, recommendation_suppressions, recommendation_feedback, recommendation_outcomes, white_space_notes |
| news (08) | news_queries, news_items, news_stories, news_judgements, news_signals, news_review_queue |
| refresh (09) | refresh_eligibility, description_refreshes, refresh_impact, link_check_cache |
| citations | answer_citation_checks, answer_citations |
| runs (11) | pipeline_runs, stage_runs, run_events (SSE replay), run_artifacts, active_result_sets, client_schedules, idempotency_keys |
| evaluation and ops (12) | eval_freeze, eval_config, eval_pattern_spec, eval_outcome_video, eval_result, label_task, label, review_item, review_rating, provider_usage_ledger, ops_daily_rollup, alert_event |

**Retention.** Every table that holds API data has a retention rule, and CI fails if one is missing. The hourly sweep refreshes or deletes public API data at day 28 and fails closed. Derived artifacts expire with their oldest input. Expired recommendations are redacted, not deleted (summary and feedback kept, evidence removed). `policy.retention_mode` stays `strict_30d` until a Section L derived-metrics audit is accepted.

**Ids.** Recommendation ids are `rec_` plus 12 base32 characters derived from a stable key, so feedback survives across runs.

## 4. Quota plan

Buckets per Google Cloud project: 10,000 main units/day, 100 `search.list` calls/day (its own bucket), and `videos:batchGetStats` in its own bucket. The quota day follows Pacific time. Every call is recorded in `yt_quota_ledger` with bucket, endpoint, client and run.

**Rules.**

- Videos are fetched 50 ids per call, uploads through playlists (1 unit per page), stats-only polling through `batchGetStats`.
- `topicDetails` is never requested; `categoryId` is dropped at ingest.
- Public data is cached once and shared by every client that tracks the same channel, with single-flight locks so two runs never fetch the same channel twice.
- Search is the binding constraint. It is split into reserved budgets: onboarding 30, news demand checks 15, shared niche queries 55. Niche queries are normalised and cached across clients in the same niche; competitor resolution uses at most 3 search calls per run, preferring website links, Tavily `site:youtube.com` and handle lookups.
- Per-run caps (onboarding 400 units / 15 searches, daily refresh 80 units / 5 searches) and per-client daily caps (600 units, 20 searches). Priority classes keep background work from starving interactive runs.

**Measured** (2026-11-20, see `docs/evaluation/ops-report.md`):

| item | onboarding | analysis | warm daily refresh |
|---|---|---|---|
| main units | 54–62 | 66–76 | 10–11 (0 when repeated within the same 6 h data bucket) |
| search.list calls | 2–6 | 0–1 | 0 |
| batchGetStats calls | — | — | 12–31 per refresh (snapshot cron) |
| Tavily credits | 14–20 | 0 | 0 (news credits vary by niche; answer citations 10/client/week) |
| LLM cost | $0.35–0.55 | $0.23–0.32 | $0.00–0.014 |
| wall time | 3.3–7.7 min | 1.6–3.7 min | 77–109 s (10–23 s when repeated) |

At ~11 main units per warm refresh, the main bucket is not the limit. Search is: with 30 onboarding searches a day and 2–6 per onboarding, a project can onboard roughly 5–15 new clients a day in a new niche. Scale comes from the shared niche cache and an audited quota extension, not from more keys.

## 5. Computed features and policy check

Every computed feature is registered in a `FeatureSpec` registry (`backend/app/evaluation/policy/features.py`) with its source, the maths it uses and a verdict against each guardrail. The full matrix with conditions is `docs/evaluation/feature-policy-matrix.md`; CI fails if a field of the analytics, news or scoring objects has no row, or if a FLAG has no explanation.

Guardrails: **G1** API data plus simple maths · **G2** no merging YouTube data with other sources into one metric · **G3** no revenue estimates · **G4** no demographics or category inference · **G5** refresh or delete within 30 days · **G6** simple thumbnail traits only.

| # | feature (owner) | source · maths | verdict |
|---|---|---|---|
| 1 | Channel stats: subscribers, views, video count; subscriber band (02) | YT public · raw, threshold flag | COND G5 |
| 2 | Video metadata: title, description, tags, duration, publishedAt, thumbnail URL (02) | YT public · raw | COND G4, G5 |
| 3 | Video stats: views, likes, comments (02) | YT public · raw | COND G5 |
| 4 | VideoSnapshot time series on batchGetStats (02) | YT public · raw | FLAG G5 |
| 5 | Comment text (02) | YT public · raw | COND G4, G5 |
| 6 | Own Analytics: watch time, retention, traffic sources, search terms, subscribers gained (02) | YT Analytics (OAuth) · raw | COND G4, G5 |
| 7 | Quota ledger (02) | internal · count, sum | OK |
| 8 | Video age and metric provenance (06) | YT public · difference | COND G5 |
| 9 | ViewsAtAge observed or interpolated (06) | YT public · interpolation | COND G1, G5 |
| 10 | ViewsAtAge age-band proxy for pre-tracking videos (06) | YT public · median, ratio | COND G1, G5 |
| 11 | View velocity (06) | YT public · difference, ratio | COND G5 |
| 12 | Engagement rate (likes + comments) / views (06) | YT public · sum, ratio | COND G5 |
| 13 | ChannelBaseline: median and mean views at age by format (06) | YT public · median, mean | COND G5 |
| 14 | Outlier ratio and flag (06) | YT public · ratio, threshold flag | COND G5 |
| 15 | Length band, Shorts flag and format segment (06) | YT public · threshold flag | COND G5 |
| 16 | Title and description traits (06) | YT public · lexical match, count | COND G5 |
| 17 | Thumbnail traits: text present, word count, contrast, brightness, colourfulness (06) | YT public · simple image stats | COND G5 |
| 18 | Publish day and hour (06) | YT public · raw | COND G5 |
| 19 | CommentSignals: buyer-question mentions, purchase-intent keywords (06) | YT public · lexical match, count, ratio | FLAG G4 |
| 20 | TraitLift over outliers vs non-outliers (06) | YT public · count, ratio, statistical test | COND G5 |
| 21 | ChannelScorecard evidence sheet (06) | YT public · count, median, ratio | COND G5 |
| 22 | OwnPerformanceReport; Analytics side panel (06) | YT public + Analytics · median, ratio | COND G2, G5 |
| 23 | TrendingReport (06/02) | YT public · ratio, count | COND G5 |
| 24 | TopicMatch lexical score (05) | YT public + client input · lexical match | COND G2, G4, G5 |
| 25 | TopicMatch embedding similarity (05) | YT public + client input · embedding similarity | FLAG G1 |
| 26 | TopicMatch LLM adjudication (05) | YT public + client input + LLM · judgement | FLAG G1, G4 |
| 27 | Topic coverage and gap counts (05/07) | YT public · count | COND G5 |
| 28 | BusinessProfile (03) | website + LLM · judgement | OK |
| 29 | Buyer questions (03) | website + client input · raw | OK |
| 30 | FreshnessProfile (03/08) | web search · count | OK |
| 31 | Competitor candidate evidence (04) | web search + YT public + LLM · count, lexical match | FLAG G2 |
| 32 | Recommendation card ordering (07) | YT public + client input · rank | FLAG G2 |
| 33 | Gap analysis: competitors cover it, you don't (07) | YT public · count | COND G5 |
| 34 | expected_range_vs_baseline (07) | YT public · quantile, ratio | COND G5 |
| 35 | Confidence label (07) | YT public · count, threshold flag | COND G5 |
| 36 | Creator list: relevance, performance vs own median, consistency (07) | YT public · lexical match, median, dispersion | COND G5 |
| 37 | Collaboration labels: mention vs disclosed paid promotion (07) | YT public · lexical match, raw | COND G5 |
| 38 | No-copy title similarity and novelty vs own uploads (07) | YT public · lexical match | COND G5 |
| 39 | NewsSignal: recency, sources, credibility, relevance, safety (08) | web search + LLM · count, judgement | OK |
| 40 | YouTube demand check for a news keyword (08) | YT public · count | COND G2, G5 |
| 41 | news_opportunity ordering (08) | web search + YT public + LLM · rank | FLAG G2 |
| 42 | Refresh-warranted decision (09) | web search + YT public · threshold flag | COND G1, G2, G5 |
| 43 | Description keyword insertions (09) | LLM + web search + Analytics · text generation | COND G5 |
| 44 | Card wording with grounding validation (10) | LLM + YT public · text generation | COND G5 |
| 45 | LLM critic score (10) | LLM · judgement | OK |
| 46 | Backtest metrics (12) | YT public + internal · ratio, statistical test | COND G5 |
| 47 | Outlier precision/recall (12) | YT public + internal · ratio, statistical test | COND G5 |
| 48 | Human review ratings (12) | internal · mean, statistical test | COND G5 |
| 49 | Ops metrics: quota, latency, tokens, cost (12) | internal · count, sum, quantile | OK |
| 50 | Cited in AI answers per buyer question (03/07) | web search + YT public · count, lexical match | FLAG G2 |

Nine FLAG cells on eight features need reviewer sign-off; each has its reasoning and mitigation in the matrix. The ones that matter most:

- **Topic matching (#25, #26)** is the riskiest part, as the assignment says. It never clusters freely: it scores videos only against the client's confirmed keywords, offerings and buyer questions, and the LLM may only answer with one of those ids or "none", with a quote that is verified against the video text.
- **Card ordering (#32)** multiplies YouTube evidence with client fit internally. The order is never displayed as a number, and each input is shown separately on the card.
- **No channel scores.** The YouTube policy guide forbids ranking channels or giving them custom scores, so competitor pages are evidence sheets of raw figures side by side, and creators are grouped by relevance and sorted by one user-chosen raw criterion.

Every computed number in the UI carries "Calculated by GrowthHacker, not a YouTube metric".

## 6. Design questions

### 6.1 How do we compare videos at the same age when snapshots only start when tracking begins?

The Data API returns only current lifetime views, so a ladder picks the best available value and labels its method (`views_at_age{age_days, views, method, label}`):

1. **Owner analytics** (`owner_analytics`): with the client's OAuth, exact daily views from the Analytics API, for the client's own videos only.
2. **Observed** (`observed`): a snapshot within a small per-age tolerance of the target age. The snapshot cron uses `batchGetStats` on an adaptive cadence (dense in the first days, milestones at days 1, 3, 7, 30), and a snapshot taken for one client serves every client tracking the channel.
3. **Interpolated** (`interpolated`): linear between two snapshots that bracket the age. Never extrapolated.
4. **Age-band proxy** (`age_band_proxy`): for videos published before tracking, take the current count at the video's real age and compare it only with the same channel's videos at a similar age, shown as "views at 23 days (proxy)".

Proxy and true values are never mixed in one ratio, and the evidence sheet shows which one is used. The comparison age is day 3 for the 7-day window and day 7 for 30/90. The contract keeps `views_day7` (null when not observed) and adds `views_at_age` and `views_basis`. Under the 30-day rule an observed day-7 value survives only until day 28 after capture; the `derived_metrics_36m` retention mode is ready for when the audit is accepted. The backtest is forward-looking for the same reason: outcome videos are tracked from publish, so their day-7 views are observed.

### 6.2 How do we share cached public data across clients within YouTube's policies?

- One API project, no key pools (III.D.1.c).
- Only API-key (public) data goes into the global cache tables, which have no tenant columns. OAuth data stays in tenant tables and is shown only to the authorising workspace.
- The 30-day rule applies per stored record: the hourly retention sweep refreshes a record if some client still needs it, or deletes it, at day 28, and fails closed. Derived rows expire with their oldest input.
- Computations are per client and never produce cross-tenant aggregates or insights about YouTube itself. Residual risk on cross-owner aggregation is flagged for legal review.

### 6.3 How do we group videos into topics without inferring categories?

Topics are the client's own words. The topic universe is built only from confirmed keywords, offerings and buyer questions, after per-channel boilerplate is stripped from descriptions. Matching is hybrid:

- lexical rules with aliases and Hindi/Hinglish transliteration;
- multilingual MiniLM embeddings against each topic;
- a Haiku adjudication pass for the ambiguous band only, restricted to the client's topic ids or "none", with a verbatim quote check.

`categoryId` and `topicDetails` are banned by lint and tests, there is no free clustering, and keyword suggestions from unmatched videos are off. Each match stores its method, score, quote and confidence; low-confidence ones go to a review queue, and reviewer decisions survive rebuilds.

### 6.4 How do we rank creators without inferring audiences?

We do not rank them. Creators come from cached niche search and competitor mentions, and are judged only by what they publish and how it performs on their own channel:

- relevance: share of recent videos matching the client's keywords, and topics covered;
- recent performance: median outlier ratio against their own normal, and median views at day 7;
- consistency: upload interval and share of videos above their own normal;
- past collaborations: labelled as mention, sponsor wording, or disclosed paid promotion (`paidProductPlacementDetails`).

No audience data, no demographics, no composite score. The list is grouped by relevance ("mostly on your topics", "sometimes on your topics") and sorted by one raw criterion the user picks. A creator card in the recommendation list still needs at least three reference videos.

## 7. Differences from the plan

- Cards whose wording falls back to the template keep the same numbers and evidence; the UI says so.
- Video ideas whose topics share a label (an offering and a keyword both called "Cardiac Sciences") are merged into one card.
- The backtest replays without the card writer, since wording does not affect it.
- Only five card types appear in strict contract mode (`?format=contract`); `news_opportunity` and `description_refresh` are full-mode extensions.
- "Ask your analyst" chat was paused by the owner.
