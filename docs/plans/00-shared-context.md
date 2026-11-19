# GrowthHacker — shared context for all component plans

Every component plan MUST be consistent with this file. If your research shows a decision here is wrong, keep the plan consistent anyway and raise it under "Open questions / proposed changes to shared context".

## 1. Product
GrowthHacker is a client-agnostic SaaS that tells a business what YouTube content to make next, how to improve what it already published, and what competitors are doing — optimised for **conversions, not just views**. Built on the assignment `Assignment YouTube Content Recommendation Engine.pdf` (path: `/Users/siddham/.t3/userdata/attachments/d77aa8fb-e84b-44e5-80ae-d01fb83eead0-e13ccb5e-3d05-4b81-b737-96e233b406df-pdf.pdf` — read it fully, it is the source of truth for requirements). We must cover everything in that doc AND go beyond it.

The engine answers, per client, through two lenses (views, conversion):
1. How is my content doing vs my own baseline?
2. How are competitors doing? (cadence, performance, what's working)
3. What's trending in my niche now?
4. What are the outliers? (videos far above their own channel's normal)
5. What should I make next? (topic, format, title + thumbnail pattern, creators to work with)
Plus the owner's extensions:
6. **Niche news radar → content opportunities**: detect fresh happenings in the client's niche (e.g. AI company like Runable: a frontier lab launches "Claude Fable 5.2") and recommend a video that ties the news to the client's value propositions and offerings.
7. **Content refresh (keyword-level description improvement)**: for already-published videos, decide *whether* a refresh is warranted (business-specific: high-news niches like AI need it often; dental clinics rarely) and suggest **minimal, additive keyword/line insertions** into the existing description (e.g. add "Claude Fable 5.2" to "How to make money using AI in 2026"). Never a full rewrite. Descriptions matter for keyword-based search ranking.
8. **Deep business context from the website** (crawler) and **automatic competitor discovery**.

Onboarding input: business website URL and/or YouTube channel link. Demo client: **Apollo Hospitals** (healthcare, India) — website `https://www.apollohospitals.com`, channel `UC0InVdvqNyNzKBl1-TL348A` (handle `@ApolloHospitalsIndia`, ~1.12M subs, ~4.3k videos; verified via API). Note: `@Apollo247` resolves to an unrelated coaching channel — channel resolution MUST verify ownership. We do NOT have OAuth access to Apollo's channel, so the demo must produce full value from public data only.

## 2. Hard constraints (from assignment — non-negotiable)
- YouTube data via official APIs only (Data API v3 with API key; Analytics API via owner OAuth). No YouTube scraping/unofficial endpoints. Crawling the client's/competitors' *websites* is a separate data source and is allowed (respect robots.txt), but its data is never merged with YouTube data into one metric.
- Quota: 10,000 units/day/project (main bucket). search.list draws 1 unit from its own bucket of 100 calls/day (verified Nov 2026; it does not consume the main bucket) → search calls are the binding constraint; discovery must be cached and shared across clients. videos:batchGetStats has its own bucket (≤50 ids/call) for stats-only refreshes. Batch videos.list (50 ids/call). Cache public channel/video data once, reuse across clients. See docs/MASTER_PLAN.md for binding resolutions.
- Metrics = API data + simple maths (sums, averages, medians, ratios). No estimated revenue/monetisation. No inferred audience demographics. No inferring a video's content category — topic grouping must match against the client's own keywords and buyer questions (flag the approach for review). Refresh or delete stored API data within 30 days. Thumbnails: simple visual traits only (text present, word count, contrast) — no face recognition.
- Conversion lens = how directly a video answers a tracked buyer-intent question. For the client's own channel (if OAuth connected): subscribers gained, traffic sources, search terms, shown separately. Website data, if any, shown side by side, never merged.
- Pipeline: everything up to the recommender is deterministic code; the LLM only turns structured evidence into readable cards and cannot add videos, numbers or reasons.
- Output contract (one object per recommendation): id, type (video_idea | title_pattern | thumbnail_pattern | format | creator — we add news_opportunity, description_refresh), summary, lens[], buyer_question, why_it_works[], reference_videos[{video_id, channel, published_at, views_day7, outlier_ratio}], fit_to_client, expected_range_vs_baseline, confidence{label, outliers, channels}, window_days, generated_at.
- Quality bar: ≥3 reference videos (configurable) inside the window, each with link, channel, publish date, views at same age, outlier ratio; fair comparisons (same age, own-channel baselines); fit to client; honest confidence (High = 5+ outliers across 3+ channels); expected results as a range vs client's baseline; thin data → say so ("Widen to 30?"); no copying competitor titles or repeating the client's own recent videos.
- Evaluation: backtest at frozen date T vs two naive baselines; outlier precision/recall on 100 hand-labelled videos; human review rubric (target ≥4/5); ops report (quota units per client per daily refresh; < 2 min per client to generate recommendations).
- Design-doc questions that plans must answer where relevant: same-age comparison when snapshots only start at tracking; sharing cached public data across clients within policy; topics without inferring categories; ranking creators without inferring audiences.

## 3. Owner decisions (already made)
- Backend: Python 3.12, FastAPI, SQLAlchemy 2 async + asyncpg, Alembic, PostgreSQL (local Homebrew PG 18 now; managed PG in prod), Redis + arq workers, httpx. Docker compose for deployability.
- LLM: Anthropic primary. `claude-haiku-4-5` for low-reasoning tasks, `claude-sonnet-5-5` for medium/high reasoning. OpenAI-compatible endpoint as fallback. Base URL, model, API key and other params (max tokens, timeout, retries, effort) configurable per provider and per tier. Skill docs for the Anthropic Python SDK are at `/private/tmp/claude-501/bundled-skills/2.1.327/5420f2cd9d92b253e0bc3e7fe2617f18/claude-api/` (read `python/claude-api/README.md`, `tool-use.md`; SDK 1.x uses httpx2).
- Web search / news / extraction: Tavily (`tavily-python` SDK; endpoints search (topic general|news, time_range), extract, crawl, map). Key available.
- Topic grouping: **hybrid** — lexical + embeddings + LLM adjudication, always constrained to the client's own keywords/buyer questions.
- Multi-agent stance: deterministic pipeline (code-controlled DAG). Bounded LLM "agents" only at fuzzy edges: business research, competitor discovery, news radar/research, card review. No free-roaming agents in the analytics core.
- "Ask your analyst" chat: PAUSED (out of scope now).
- Build order: engine (backend) first, then presentation (frontend). Frontend: Next.js; design language like https://www.usespotlit.ai/ ; components from https://myzeusui.com/ (primary, shadcn registry), https://www.beautifului.dev/ (AI-native interactions; copy code via Playwright), https://libraries.dev/ (fallback), BoardUI MCP (agent-native interactions), Rare UI folder component (competitor cards → click opens competitor content analysis). UX must not overwhelm: progressive disclosure.
- Code style: SOLID, open for extension/closed for modification (ports & adapters + registries), minimal comments (lowercase one-liners only when necessary), readable names, structured conventional commits.
- Verified infra: YouTube key, Anthropic key (haiku-4-5 + sonnet-5-5 respond), Tavily key all work. Local Postgres 18 + Redis running.

## 4. Architecture skeleton (shared vocabulary)
```
backend/app/
  core/        config (pydantic-settings), db, redis, logging, security
  models/      SQLAlchemy ORM tables
  domain/      pydantic domain objects + output contract
  ports/       Protocols: YouTubeGateway, LLMClient, WebSearch, SiteCrawler, Embedder, FreshnessSource, BuyerQuestionSource, ProgressReporter
  adapters/    youtube/, llm/, search/, crawler/, embeddings/, freshness/
  services/    website_intel/, competitors/, ingest/, topics/, analytics/, recommend/, news/, refresh/
  pipeline/    Stage protocol, RunContext, runner, stages/
  api/         FastAPI routers
  workers/     arq settings, cron jobs
  evaluation/  backtest, outlier labelling, ops report
frontend/      Next.js app
```
Run model: **onboarding run** (crawl → business profile → competitor discovery → status `awaiting_confirmation`) → user confirms/edit competitors, keywords, buyer questions → **analysis run** (ingest → enrich → topics → analytics → news radar → recommend → refresh → finalize). **Daily refresh run** re-runs analysis from cache; **snapshot cron** captures stats on an adaptive cadence; **retention cron** purges/refreshes API data older than 30 days. Progress events stream to UI over SSE.

## 5. Component map (who owns what) and handoffs
| # | Component | Consumes | Produces |
|---|---|---|---|
| 01 | Platform foundation (data model, tenancy, config, DI, retention, deployment) | — | tables, repositories, settings, container |
| 02 | YouTube data layer (gateway, quota manager, shared cache, ingest, snapshots, OAuth analytics) | channel ids, queries | ChannelRecord, VideoRecord, VideoSnapshot, CommentRecord, OwnAnalytics |
| 03 | Website intelligence (crawler + business context) | website URL / channel | BusinessProfile (offerings, value props, locations, conversion actions, niche keywords, buyer questions, freshness profile), crawled pages |
| 04 | Competitor discovery | BusinessProfile, YouTube gateway, WebSearch | CompetitorCandidate list with evidence + verified channel ids |
| 05 | Topic engine (hybrid matching) | TopicRefs (client keywords + buyer questions), VideoRecords | TopicMatch per video (topic id, score, method, review flag) |
| 06 | Analytics engine (metrics, same-age views, baselines, outliers, traits incl. thumbnail + comment intent, pattern lift, scorecards) | VideoRecords, snapshots, TopicMatches | VideoMetrics, VideoTraits, CommentSignals, TraitLift, ChannelScorecard, OwnPerformanceReport, TrendingReport |
| 07 | Recommendation engine (recommenders, gap analysis, creator ranker, scoring, quality gates, contract) | analytics outputs, profile, topics | RecommendationDraft → Recommendation (contract) |
| 08 | News radar & news-to-content opportunities | BusinessProfile.freshness profile, FreshnessSources, YouTube demand check | NewsSignal, news_opportunity drafts |
| 09 | Content refresh engine (description keyword insertions) | client's own VideoRecords, NewsSignals, analytics gaps, buyer questions | DescriptionRefresh suggestions (additive diff) |
| 10 | LLM layer & evidence-grounded writer | prompts + pydantic schemas | structured outputs; final card wording validated against evidence |
| 11 | Pipeline orchestration, workers, API, progress streaming | all services | runs, events, REST + SSE endpoints |
| 12 | Evaluation & operations | stored data, recommendations | backtest, outlier precision, human review workflow, ops report, observability |
| 13 | Frontend UX & information architecture | API | screens, flows, component mapping |

Domain object names to use consistently: BusinessProfile, Offering, ValueProposition, ConversionAction, BuyerQuestion, FreshnessProfile, TopicRef, TopicMatch, CompetitorCandidate, ChannelRecord, VideoRecord, VideoSnapshot, CommentRecord, CommentSignals, VideoMetrics, ViewsAtAge, ChannelBaseline, VideoTraits, ThumbnailTraits, TraitLift, ChannelScorecard, OwnPerformanceReport, TrendingReport, NewsItem, NewsSignal, RecommendationDraft, Recommendation, ReferenceVideo, Confidence, DescriptionRefresh, RunEvent.

## 6. Plan template (every plan file uses these sections)
1. Purpose & business value (how this component drives conversions for onboarded businesses)
2. Scope / non-scope
3. Inputs & outputs (exact pydantic-style field lists for the domain objects you own)
4. Internal design (modules, classes, Protocols, registries; SOLID extension points)
5. Algorithms & logic (step by step, formulas, thresholds with defaults, all configurable)
6. Data model (tables/columns/indexes you need; TTL/retention)
7. External APIs & libraries (endpoints, params, quotas/costs, rate limits, verified against official docs — cite URLs; mark anything you could not verify)
8. Edge cases & failure modes — a table: case | detection | handling | user-facing message (be exhaustive; this is the most important section)
9. Policy & guardrail check (each feature vs the assignment's guardrails)
10. Performance, cost & scaling (quota units, LLM tokens/cost per client, latency budget toward < 2 min per client refresh)
11. Testing strategy (unit fixtures, golden tests, contract tests, live smoke tests)
12. Implementation task breakdown (ordered, sized, with dependencies on other components)
13. Open questions / proposed changes to shared context

Rules for plan authors: research with official docs (WebFetch/WebSearch) and cite; verify API facts live where possible with the keys in `/Users/siddham/.t3/projects/growthhacker/.env` (never print keys; keep quota use minimal — at most a handful of cheap calls, no search.list calls unless essential); do not write implementation code (short signatures/pseudocode are fine); write only your own plan file.
