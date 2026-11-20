# GrowthHacker — Master Plan

How the 13 components work together to turn a business's website and YouTube presence into content that converts.

Component plans live in `docs/plans/` (01–13). The shared brief is `docs/plans/00-shared-context.md`, and cross-plan conflicts and their resolutions are in `docs/plans/99-consistency-review.md`. This document is the binding integration layer. Where a component plan disagrees with it, this document wins.

---

## 1. What we are building and why it converts

Most YouTube tools tell a business how many views it got. GrowthHacker tells it **what to publish next, what to fix in what it already published, and what competitors are winning with**. Every answer is tied to the business's buyer questions and offerings, so the output is judged by conversions, not views.

Five promises to an onboarded business:

| Promise | What the user sees | Why it converts |
|---|---|---|
| **We understand your business** | A business profile built from the website: offerings, value propositions, locations, booking/demo actions, niche keywords, buyer-intent questions. Every fact is quoted from a page. | All downstream recommendations are anchored to what the business sells and how customers buy. |
| **We know your arena** | 3–10 verified competitor channels (folder cards), a wider niche set, and creators. | Gaps are measured against real rivals, not generic "top channels". |
| **We show what actually works** | Outliers (videos far above their own channel's normal at the same age), the traits over-represented among them, and topic gaps against your buyer questions. | Evidence beats intuition. Each claim is backed by ≥3 recent reference videos. |
| **We tell you exactly what to make next** | 5–10 cards per window: video ideas, title/thumbnail patterns, formats, creators. Each card has evidence, fit to you, an expected range against your own baseline, and honest confidence. | Cards prioritise buyer-intent topics with proven demand, so they are made to drive bookings, signups and calls, not just views. |
| **We keep your library working** | News-driven video opportunities (e.g. a new frontier model → Runable explainer) and small keyword insertions into old descriptions, applied only when the business and the video warrant it. | Captures fresh search demand and re-activates evergreen videos that already rank. |

### 1.1 The value loop

```
 UNDERSTAND ──► WATCH ──► DIAGNOSE ──► PRESCRIBE ──► ACT ──► MEASURE ──► LEARN ─┐
 03 website     02 data    05 topics    07 recommend   user    02 snapshots  12 eval │
 04 rivals      08 news    06 analytics 08 news cards  marks   06 baselines  07 feed-│
                                        09 refresh     done/   09 impact     back    │
                                        10 writer      applied               calib.  │
     ▲───────────────────────────────────────────────────────────────────────────────┘
```

Each lap through the loop makes the next one sharper. User edits refine the profile. Feedback and measured impact calibrate thresholds. Snapshots turn age-proxy comparisons into true day-7 values.

---

## 2. System map

```
                         ┌──────────────── Next.js UI (13) ────────────────┐
                         │ Today brief · Next videos · Competitors (folders)│
                         │ Your channel · Niche radar · Creators · Settings │
                         └───────────────▲──────────────────▲───────────────┘
                                REST /api/v1          SSE run events
                         ┌───────────────┴──────────────────┴───────────────┐
                         │      FastAPI (11) — stateless, auth, OpenAPI      │
                         └───────────────┬──────────────────────────────────┘
                                         │ enqueue (JobQueue port → arq)
            ┌────────────────────────────▼─────────────────────────────────┐
            │  Workers (11): interactive · batch · maintenance queues        │
            │  DagRunner → Stages (registry) → artifacts (hash-keyed, resumable) │
            └──┬─────────┬─────────┬─────────┬─────────┬────────┬──────────┘
       ┌───────▼──┐ ┌────▼────┐ ┌──▼─────┐ ┌─▼──────┐ ┌▼──────┐ ┌▼────────┐
       │03 Website│ │04 Compe-│ │02 You- │ │05 Topic│ │06 Ana-│ │08 News  │
       │intel     │ │titor    │ │Tube    │ │engine  │ │lytics │ │radar    │
       │(crawler, │ │discovery│ │data    │ │(hybrid)│ │engine │ │         │
       │ profile) │ │         │ │layer   │ │        │ │       │ │         │
       └────┬─────┘ └────┬────┘ └───┬────┘ └───┬────┘ └───┬───┘ └────┬────┘
            │            │          │          │          │          │
            └────────────┴──────────┴────┬─────┴──────────┴──────────┘
                                         ▼
                 ┌──────────────────────────────────────────────┐
                 │ 07 Recommendation engine  ◄── 09 Refresh engine │
                 │ recommenders · creator lists · gates · contract  │
                 └───────────────────────┬──────────────────────┘
                                         ▼
                 ┌──────────────────────────────────────────────┐
                 │ 10 LLM layer: evidence-grounded writer + critic │
                 │ (wording only; validators; template fallback)   │
                 └───────────────────────┬──────────────────────┘
                                         ▼
                      recommendations (output contract) → UI

 Cross-cutting: 01 platform (Postgres, Redis, config, DI, tenancy, retention, security)
                10 LLM layer (every LLM call: Haiku 4.5 low tier, Sonnet 5.5 medium/high, OpenAI-compatible fallback)
                12 evaluation & ops (backtest, outlier P/R, human review, quota/cost/latency, policy matrix)
 External:      YouTube Data API v3 (+ Analytics API via owner OAuth) · Tavily · PubMed/other freshness sources · Anthropic / OpenAI-compatible
```

**Design principle:** numbers come from code and words come from the LLM. Everything up to the recommender is deterministic. LLMs appear only at four fuzzy edges, and each is bounded, schema-constrained and evidence-checked:
1. understanding the website (03);
2. adjudicating ambiguous matches (04, 05);
3. judging news relevance (08);
4. phrasing cards and insertions (07/09 → 10).

---

## 3. The symphony — how components play together

### 3.1 Movement I — Onboarding (cold start, ~4–10 min, streamed live)

```
User enters website and/or channel link
 │
 ├─► 02 ChannelResolver ── parse any URL form → channel id (1 unit) ─┐
 │                                                                  ├─► 03 identity linking: verify site ↔ channel
 ├─► 03 PoliteCrawler ── robots (protego) → sitemaps → nav → template │   ownership via real links, never name similarity
 │      clustering → priority frontier (≤60 pages, ≤12 JS renders)   │   (@Apollo247 case); sister brands recorded
 │      → fact extraction (Haiku, verbatim quotes) → profile          │
 │      synthesis (Sonnet, cites fact ids only) → BusinessProfile v1  ◄┘
 │        + BuyerQuestions (FAQ/derived/user) + FreshnessProfile
 │
 ├─► 05 builds the TopicRef universe from confirmed keywords/offerings/buyer questions
 │
 └─► 04 Competitor discovery ── candidates (LLM + Tavily + site hints + cached niche search + user)
        → resolve to channels cheapest-first (cache → website links → Tavily site:youtube.com →
          forUsername/forHandle → search.list ≤3) → ownership verification → tiered ranking
        → proposed CompetitorSet {competitors 3–10, niche ≤20, benchmarks ≤3, creators → 07}
 │
 ▼  onboarding run completes; client state → awaiting_confirmation  (UI: profile facts + competitor folders appear as they're found)
User confirms or edits profile, keywords, buyer questions, competitors   (demo: auto-confirm mode)
 │
 ▼  analysis run (DAG, see 3.2) → first brief
```

What the user feels: a live analysis panel showing pages being read, facts being discovered, competitor folders landing and outliers being found. Every animated item is a real `RunEvent`; nothing is faked.

### 3.2 Movement II — Analysis run (the DAG)

```
                 ┌─► ingest (02) ─► enrich (02 comments sample, 06 thumbnails) ─┐
 confirm-setup ──┤                                                             ├─► topics (05) ─► analytics (06) ─► recommend (07) ─┐
                 └─► news_radar (08, parallel, cadence-gated) ─────────────────┘                                    │
                                                                                     refresh (09) ◄───────────────────┤
                                                                                     cards (10 writer + critic) ◄──────┘
                                                                                     finalize: gates, publish atomically
```

- **Ingest (02):** recent uploads for the client, competitors, niche and benchmarks over a 90-day window plus 90 days of baseline history. At least 30 videos per format and at most 300 per channel; for Apollo that is 2–3 pages of uploads, not 4.3k videos. Videos are fetched in 50-id batches with `paidProductPlacementDetails`. `topicDetails` is never requested and `categoryId` is dropped at ingest. Cached public data is shared across clients through single-flight locks.
- **Enrich:** comment samples for candidate videos only (author identity never stored, text transient), and thumbnail pixel/OCR traits.
- **Topics (05):** lexical + semantic (multilingual MiniLM) + Haiku adjudication of the ambiguous band, always constrained to the client's own TopicRef ids. Per-channel boilerplate stripping comes first. Answer directness is scored per buyer question.
- **Analytics (06):**
  - views-at-age (observed, interpolated or labelled proxy)
  - median baselines per format
  - outliers
  - traits
  - comment intent
  - pattern lift with minimum support and FDR control
  - own-performance report, competitor evidence sheets with the gap matrix, trending and the outlier list
  - the conversion lens, kept as a separate block
- **News (08)** runs in parallel. It fetches from the shared query cache, clusters stories, judges relevance and grounding, scores credibility and safety, and checks YouTube demand through a reserved search budget, producing NewsSignals.
- **Recommend (07):** the type recommenders, gap analysis, creator lists, the merger, scoring (lens-weighted), diversity selection and the gate chain. **09** and **08** plug their drafts into the same gate framework.
- **Cards (10):** the evidence pack goes to the writer (Sonnet 5.5, low effort), then validators (every number, id and name must exist in the evidence), then the critic (Haiku). If the LLM fails, the template renderer is used; the pipeline never blocks.
- **Finalize:** atomic publish, then a `results.published` event, then the UI refreshes the brief.

### 3.3 Movement III — Daily rhythm (warm, recommendation generation < 2 min/client)

| Time | Job | Components | Notes |
|---|---|---|---|
| Continuous | Snapshot cron | 02 | Adaptive cadence by video age, using `videos:batchGetStats` (own bucket, 50 ids/call). Captures day-1/3/7/30 milestones for new videos. |
| By FreshnessProfile | News poll | 08 | AI niches every 6 h, hospitals daily, dental weekly. The query cache is shared across clients. |
| Nightly per client | Daily refresh run | 02→05→06→07→09→10 | Reuses unchanged artifacts. Analytics and drafts are computed for all three windows (7/30/90). LLM cards are written only for the active window, and unchanged cards are copied forward. Typical ~35 s, worst ~110 s. |
| Weekly | Site change probe | 03 | An incremental re-crawl only when the site changed. The daily refresh never crawls. |
| Monthly | Competitor re-discovery | 04 | Drift checks (inactive, renamed, overlap falling, new entrants) also run daily from cache at 0 extra quota. |
| Daily (day 28) | Retention sweep | 01 | Public API data older than 28 days is refreshed (if still needed) or deleted. Derived artifacts expire with their oldest input. Fails closed. |

### 3.4 Movement IV — Event-driven synergy: one news event, two outputs

The owner's flagship scenario, end to end. The client is an AI company (Runable) and a frontier lab ships "Claude Fable 5.2":

1. **03** set the client's FreshnessProfile to `high`, watching entities such as frontier-lab model launches, its offerings and its competitor names.
2. **08** polls every 6 h from the shared cache. Tavily news returns the launch from multiple outlets, which are clustered into one story; a version guard keeps "Fable 5.1" and "5.2" separate. Sonnet judges relevance and links it to the client's offerings. The entity "Claude Fable 5.2" must appear verbatim in article text, so it is grounded. Credibility comes from tier-1 sources. The demand check spends one shared `search.list` call and finds 4 early videos, 2 of them already above 3x their channel median.
3. **Output A, news opportunity.** If ≥3 qualifying reference videos exist, it is a full `news_opportunity` card that passes every gate. Otherwise it appears on the separate **Early signals** surface, labelled First-mover, and never mixes into the recommendation list. The content: "Explainer: what Claude Fable 5.2 changes for <offering>, Short within 48 h + long-form follow-up". It cites the early videos (or is labelled First-mover if fewer than 3), the news sources, the tie to the client's value proposition and CTA (e.g. Runable signup), urgency and expiry, and the title/thumbnail patterns that already win in the niche (06 TraitLift).
4. **Output B, description_refresh (09):** the client's evergreen video "How to make money using AI in 2026" matches the entity's topic and is still earning views. Its description lacks the keyword, and there is demand evidence. 09 proposes **one additive line**, framed honestly as context ("Update (Nov 2026): Claude Fable 5.2 is now available — the workflow shown here applies to it", only when the owner confirms applicability). It is placed in the above-the-fold region, within the 5000-byte budget, and validated against YouTube's spam and misleading-metadata policy.
5. **Measure:** the user marks the change "applied", or 02 detects it from the re-fetched description. 09 then compares views/day for 14 days before and after, against a control set of the client's unrefreshed videos. The results feed 12's calibration.

For Apollo (hospital, FreshnessProfile `medium`, regulated), the same path yields fewer, safer outputs. Health news requires authoritative sources, health cards go through human review, and refresh insertions never add medical claims. Most of Apollo's refresh value is hygiene: only 2 of its 50 recent descriptions link to booking, none have chapters, and one has malformed links. **This is the "decide whether a refresh is needed" requirement: the same engine produces different outputs depending on the business.**

### 3.5 Movement V — Feedback and learning

- Card actions (save / done / dismiss with reason) are tied to stable recommendation keys, so feedback survives across runs (07).
- When a card is marked done, the matching new upload is detected and its views-at-age are tracked against the expected range. This is outcome calibration.
- Applied refreshes get impact measurement (09).
- Monthly evaluation (12) re-tunes thresholds and gate configs. A human approves every change; nothing auto-tunes silently.

---

## 4. Cross-component contracts (the seams that must not drift)

| Producer → Consumer | Contract | Key rule |
|---|---|---|
| 03 → 04, 05, 07, 08, 09 | `BusinessProfile` (versioned), `BuyerQuestion` (own table, stable ids, funnel stage, facets), `FreshnessProfile` (sensitivity, entities to watch, regulated flag, news-velocity prior, evergreen-expected flag), `ConversionAction`, `brand_terms`, `related_brands`, `primary_languages`, `timezone` | User-confirmed fields override LLM output. Rejected items stay rejected. |
| 02 → everyone | `ChannelRecord`, `VideoRecord` (with `paid_product_placement`, format label, availability), `VideoSnapshot`, `ViewsAtAge` | No `topicDetails`. `categoryId` dropped. Comment authors never stored. |
| 04 → 02, 06, 07, 08 | `CompetitorSet` (versioned) {competitors, niche, benchmarks, creators}, entity aliases and domains, `entity_kind` (business / creator / media) | 3–10 applies to competitors only. Website and YouTube relevance are kept as separate tiers. |
| 05 → 06, 07, 09 | `TopicMatch` (topic id, score, method, confidence, matched terms, quote, review flag), `AnswerDirectness` (direct / partial / mention / none + explanation) | Only client TopicRef ids or "none". No free clustering. |
| 06 → 07, 08, 09 | `VideoMetrics` (views_at_age{age_days, value, method}, outlier_ratio, baseline n, format), `VideoTraits`, `CommentSignals`, `TraitLift` (with intervals, support), evidence sheets + gap matrix, `OwnPerformanceReport`, `ConversionLens` | Simple maths only. Proxy and true values are never mixed in one ratio. Every computed number is labelled "calculated by GrowthHacker". |
| 08 → 07, 09, 05 | `NewsSignal` (status active / disputed / retracted / superseded, grounded entity + aliases, linked offerings, credibility, safety decision, demand check, expiry) | Entities must be verbatim in source text. Retractions propagate to cards and refreshes. |
| 07/08/09 → 10 | `RecommendationDraft` with typed `facts[]`, `pattern_spec`, forbidden titles, allowed dates/entities | The writer cannot add videos, numbers or reasons. Template fallback always exists. |
| 07 → API/UI | `Recommendation` = assignment contract + `extras` + `contract_version`. Reference videos carry `views_at_age`, `comparison_age_days`, `views_basis`, `thumbnail_url`; `views_day7` is nullable. | Strict-contract mode returns exactly the assignment's shape. |
| all stages → 11 → UI | `RunEvent` (persist then publish; SSE with Last-Event-ID replay) | Every animated item in the UI is a real event. |
| all → 12 | usage ledgers (quota per endpoint, Tavily credits, LLM tokens/cost), stage timings, gate rejections, `FeatureSpec` registry | Policy matrix completeness is enforced in CI. |

Canonical field lists and table ownership are in `docs/plans/99-consistency-review.md` §4–5.

---

## 5. The conversion lens, end to end

The API has no conversion data for other channels, so "conversion" is defined honestly and computed in several places:

1. **Buyer-question coverage (03 → 05):** questions extracted from the business's own site, with funnel stage, plus user-edited ones.
2. **Answer directness (05):** whether a video directly answers a tracked buyer question (title is the question, facet coverage, concrete cues like a ₹ figure), or merely mentions it.
3. **Comment intent (06):** a deterministic lexicon (English, romanised Hindi, Devanagari) flags cost/location/booking/doctor/"does it work" questions. This shows where demand is unmet.
4. **Description conversion hygiene (06 → 09):** CTA links to booking/signup, phone/WhatsApp, chapters.
5. **Own-channel truth (02, OAuth only):** subscribers gained, traffic sources and search terms, shown in a separate panel.
6. **Recommendation lens (07):** a card earns the conversion lens only if its reference videos directly answer a tracked buyer question. The user's lens toggle re-weights ranking but never changes numbers.

None of these are blended with view metrics into one number. They are shown side by side and labelled.

---

## 6. Policy & compliance architecture

The assignment's guardrails plus what our research found in YouTube's policies (verified directly by the orchestrator):

| Finding | Source | Consequence (binding) |
|---|---|---|
| III.E.4.h: clients must not "use API Data to create new or derived data or metrics", unless they are an audited developer under the **Section L derived-metrics allowance** (since 2026-06-01). | Developer Policies | **We follow the assignment's simple-maths guardrails** (which match Google's own "acceptable metrics" examples: averages, ratios, sums, sorted top lists). Every computed number carries a "Calculated by GrowthHacker, not a YouTube metric" disclosure. **Apply for the compliance audit and the derived-metrics allowance early.** A `policy.retention_mode` setting (`strict_30d` default, `derived_metrics_36m` after approval) drives retention. |
| The policy guide forbids ranking or gamifying channels against each other, and custom "scores" assigned to channels. | Developer Policies Guide | **No channel leaderboards and no composite channel scores.** Competitor pages are evidence sheets of raw metrics side by side. Creator recommendations are grouped by relevance to the client's keywords, show raw metrics, and are sorted by one transparent, user-selectable criterion. Video-level outlier ratio (video ÷ own-channel median) stays: it is simple maths on one channel's data. |
| Data aggregation is limited across content owners. | Developer Policies (Data Aggregation) | Computations are per client and never cross tenants. No insights into YouTube's business. Flagged for legal review as a residual risk. |
| Non-authorized stored data must be refreshed or deleted within 30 days. | III.E.4 | Sweep at day 28. Snapshots in daily partitions are dropped at day 28. Derived artifacts expire with their oldest input. Expired recommendations are redacted, not deleted (summary and feedback kept, evidence removed). CI fails if a table has no retention rule. |
| One API project per app; no key pools. | III.D.1.c | Scale comes from the shared cache plus an audited quota extension, not from more keys. |
| `search.list` costs 1 unit from its own 100-calls/day bucket. `videos:batchGetStats` has its own bucket. | Quota docs (2026-11) | Search calls, not units, are the binding constraint. They are cached and shared by normalised query across clients in the same niche. |
| No category inference; thumbnails are simple traits only; no demographics; no revenue. | Assignment | `topicDetails` and `categoryId` are banned by lint and tests. Topic matching is anchored to client terms. No face detection. No audience inference. |
| Website and YouTube data are never merged into one metric. | Assignment | Separate tiers. Internal orderings that use both sources are tier-then-score and never displayed as a number. |
| Healthcare content safety. | YouTube medical misinformation policy, India advertising rules | Authoritative sources only. Health news cards and refresh insertions go to human review. Never generate medical claims. |

The full feature-by-guardrail matrix (49 features, 8 flagged for reviewer sign-off) is in `docs/plans/12-evaluation-and-ops.md` §9, backed by a `FeatureSpec` registry plus a CI completeness test.

---

## 7. Answers to the assignment's design-doc questions (summary)

1. **Same-age comparison when snapshots only start at tracking.** Ladder (06 §5, 02 §5):
   - With OAuth, use exact daily views from the Analytics API.
   - Use shared snapshots that another client already tracks.
   - For new videos, use views observed or interpolated at day 1/3/7/30.
   - For older videos, use an **age-band proxy**: compare against the same channel's videos observed at a similar age. It is always labelled, never extrapolated, never mixed with true values in one ratio, and switched to true values at 80% coverage. Proxy accuracy is measured against videos we tracked from publish.
   - Under the 30-day rule, observed values survive only ~37 days after publish until the derived-metrics allowance is granted.
2. **Sharing cached public data across clients.** The 30-day rule applies per stored record. There is one API project, and only API-key (public) data goes in the shared cache, which has no tenant columns. OAuth data stays per client and is visible only to the authorizing user and their approved members. Refresh-or-delete runs at day 28 (01 §5).
3. **Topics without inferring categories.** Topics come only from the client's own confirmed keywords, offerings and buyer questions. Matches are hybrid (lexical + embeddings + LLM restricted to those ids or "none", with a verified quote). YouTube categories and topicDetails are never used. Every match is auditable and low-confidence ones are queued for review (05 §9).
4. **Ranking creators without inferring audiences.** We judge creators only by what they publish and how it performs on their own channel: relevance to the client's topics, outlier rate relative to their own median, upload consistency, and labelled past collaborations (mention / sponsor wording / disclosed paid promotion via `paidProductPlacementDetails`). No audience data and no composite channel score. Lists are grouped by relevance and sortable by one transparent criterion (07 §5, adjusted by §6 above).

---

## 8. Economics — quota, cost, latency (per client)

| Item | Onboarding (cold) | Daily refresh (warm) |
|---|---|---|
| YouTube main bucket | ~110–120 units | ~25–30 units |
| YouTube search bucket | ~10–15 calls (≤3 for competitor resolution) | ~3–5 calls (0 if the niche cache is hit) |
| batchGetStats | — | ~10–15 calls |
| Tavily credits | ~12–20 (competitors) + ≤30 (crawl fallback) | news: niche-dependent (AI ≈ daily polling; dental ≈ weekly) |
| LLM (Haiku 4.5 + Sonnet 5.5) | ~$1.10 profile + ~$0.13 competitors + ~$0.55 analysis | ~$0.20–0.30 |
| Wall time | crawl 3–8 min + analysis ~1–2 min | recommendations ≈ 35 s typical, ≤ 110 s worst (target < 2 min) |

**Capacity on one project:** the main bucket supports ~285 clients/day. **The search bucket (100 calls/day per project) is the binding constraint.** It is split into reserved budgets (02 owns them): onboarding 30, news demand checks 15, shared daily niche queries 55. That allows **~3–4 cold onboardings/day in a brand-new niche**, and more when the shared niche cache hits. Daily refreshes run mostly from cache.

Scaling levers, in order:
1. niche-level search sharing;
2. search-free discovery paths (website links, Tavily `site:youtube.com`, handle lookups);
3. an audited quota extension, which a real SaaS launch needs anyway.

Switching the low tier to Haiku 5.5 cuts low-tier LLM cost about 10× (owner decision). "< 2 min per client" is measured as total warm daily-refresh wall time (12's definition). Budgets live with the resource owners: 10 for LLM, 02 for YouTube, 08 for Tavily news.

---

## 9. Quality system (layers, in order)

1. **Input truth:** quotes verified against stored pages (03), entities verbatim in articles (08), topic quotes verified (05).
2. **Deterministic evidence:** median baselines, outlier rule (≥3× own median at same age + spread check + 100-view floor + like-rate sanity to exclude promoted-view spikes), lift with Wilson/Katz intervals + BH FDR + minimum support (≥3 outliers across ≥2 channels), and per-channel balanced lift so no single channel dominates.
3. **Gates (07 framework, shared by 08/09):** minimum reference videos, evidence completeness, numbers-from-evidence, no copying (titles must not match competitor titles or the client's own recent titles too closely), fit to client, honest confidence (High = 5+ outliers across 3+ channels), thin data → "Widen to 30?" with projected counts, policy text.
4. **Writer validators (10):** every number, id and name is checked against the evidence pack, with format-tolerant matching (18.4K, 3.6x, lakh/crore). Up to 3 regenerations, then the template.
5. **Critic (10):** a Haiku rubric matching the human review criteria (specific, evidenced, relevant, actionable, not obvious) feeds ranking. At most one rewrite.
6. **Evaluation (12):** forward-looking backtest from T ≥ tracking start with frozen bundles and a leakage canary, compared against the copy-top and random arms. Outlier precision/recall is measured on 100 stratified hand-labelled videos (κ/AC1). Human review covers 30 cards plus 6 blinded decoys (target ≥ 4.0). The ops report covers quota, latency, cost and compliance.

---

## 10. Failure philosophy

- **Degrade, never block.** LLM down → deterministic fallbacks and templates. Tavily down → profile/LLM-only discovery and news skipped. Search quota gone → cached results, with "demand not checked" labels. Crawl blocked → acquisition cascade (render, reader proxies, search index, archives, knowledge base, web mentions, channel), then user prompts.
- **Partial results are labelled, not hidden.** Run status `partial` / `completed_degraded`, with notices in the UI.
- **Thin data says so.** Insufficiency notices show found, required and projected counts per window.
- **Resume, don't restart.** Hash-keyed stage artifacts; a failed run resumes from the failed stage.
- **One active run per client.** A database partial unique index, single-flight locks on shared channels, and leases with a reconciler cron.

---

## 11. Build plan (engine first, then presentation)

Contracts are frozen first so parallel subagents can't drift. Each phase ends with an integration check and a review agent pass against the assignment and this plan.

| Phase | Scope | Parallel tracks (subagents) | Exit criteria |
|---|---|---|---|
| **P0 Foundation & contracts** | 01 F1–F15 core (config, clock, logging, db, Alembic, all migrations, registries, security, Redis primitives, container); `domain/` canonical objects + output contract; `ports/`; 11 pipeline contracts (Stage, RunContext, RunEvent) | 1 orchestrator + 2 agents (migrations/repos; domain/ports) | `alembic upgrade head` on PG18; contract tests green; container boots |
| **P1 Acquisition** | 02 (gateway, quota, cache, resolver, ingest, snapshots, batchGetStats); 10 (providers, fallback, budgets, fakes); 11 (runner, state machines, artifact store, progress, arq queues, API foundation, SSE); 03 crawler half (T3–T11) | 4 agents | Apollo channel ingested; snapshots cron running; LLM structured calls on both tiers; SSE replay test |
| **P2 Understanding** | 03 profile half (T12–T20); 04 complete; 05 lexical → semantic → adjudication; 06 metrics → baselines → outliers → traits → lift → reports | 4 agents | Apollo onboarding reaches awaiting_confirmation with a sensible profile and the golden competitor set (Fortis, Narayana, Medanta, Manipal, Max; Practo as niche; Apollo 24\|7 excluded); analytics golden fixture passes |
| **P3 Prescription** | 07 (recommenders, gates, creators, contract); 08 (sources, clustering, judge, grounding, safety, demand); 09 (hygiene first, then keyword/news triggers); 10 writer + critic | 4 agents | `/recommendations` returns valid contract cards for Apollo (30-day window); a Runable-style news fixture yields a news card + a refresh suggestion |
| **P4 Integration, eval & ops** | 11 stage wiring + all routers + crons; 12 freeze job, stats, backtest, label/review packs, ops report, policy matrix; Docker compose; README/runbook | 2–3 agents + reviewer agents | E2E Apollo run ≤ 2 min warm refresh; ops report shows units/client; policy matrix CI green |
| **P5 Presentation** | 13 F1–F27: tokens from Spotlit, vendored Zeus/BoardUI/Beautiful UI/Rare UI (or Zeus fallback), onboarding live panel, Today brief, cards, competitors folders, radar, your channel, settings | 3–4 agents | Onboard Apollo in the UI → live analysis → confirm → first brief; a11y and perf passes |
| **P6 Hardening & demo** | Review agents (correctness, policy, security), evaluation report, design doc assembly, demo runbook (Apollo + Runable + 3 ICP niches with 5 competitors each) | 3 agents | All assignment deliverables present |

**Subagent rules:**
- Each agent owns specific directories.
- Agents must not edit shared contracts. Change requests go to the orchestrator.
- No commits by agents; the orchestrator commits per component with conventional subjects (`feat(youtube): ...`).
- Every agent runs `ruff`, `pytest` and the contract tests before reporting.
- Reviewer agents get the code, not the author's conclusions.

**Critical path:** P0 → 02 ingest + 11 runner → 05 lexical + 06 baselines/outliers → 07 video_idea + gates → 10 writer → 11 wiring → 13 onboarding/brief.

---

## 12. Decision log (settled)

| # | Decision | Reason |
|---|---|---|
| D1 | Python 3.12, FastAPI, SQLAlchemy 2 async, Alembic, PostgreSQL ≥ 18 (`uuidv7()`), Redis (AOF in prod), arq behind a `JobQueue` port | arq is maintenance-only; the port keeps it swappable |
| D2 | Anthropic primary: `claude-haiku-4-5` (low), `claude-sonnet-5-5` low effort (medium), Sonnet 5.5 medium effort (high); OpenAI-compatible fallback with per-provider base URL/model/key/params | Owner choice; no sampling params or forced tool choice sent to Anthropic routes (400s verified) |
| D3 | Structured outputs everywhere (`output_config.format` / json_schema), pydantic validation + one repair, template fallbacks | Pipeline never blocks on the LLM |
| D4 | Hybrid topic engine anchored to client terms; multilingual MiniLM via fastembed; transliteration layer required | Hindi/Hinglish content; policy |
| D5 | Median baselines per format; outlier ≥3× + spread + floor + like-rate sanity | Apollo data: median 1,320 vs mean 51,415; promoted-view spikes |
| D6 | No channel scores or leaderboards; evidence sheets; creators grouped by relevance + single transparent sort | YouTube policy guide |
| D7 | Retention at day 28, fail-closed; `policy.retention_mode` | III.E.4 |
| D8 | Search calls are the binding quota; shared niche search cache; reserved budgets (competitor resolution ≤3/run, news ≈15/day) | Quota docs |
| D9 | `mostPopular` chart not used for niche trending | It only covers Music/Movies/Gaming now |
| D10 | Every recommendation card keeps the ≥3-reference rule, including `news_opportunity` and keyword-type `description_refresh`. First-mover news goes to a separate **Early signals** surface; rule-based description fixes go to a separate **Channel hygiene** list. Confidence stays High/Medium/Low plus a `basis` field. | The assignment says any card failing a rule is not shown; separate surfaces stay honest (owner sign-off O3) |
| D15 | Contract: keep `views_day7` (nullable), add `views_at_age{age_days, views, method, label}`; comparison age is day 3 for the 7-day window and day 7 for 30/90; extensions live in `extras` + `contract_version`; stable `rec_` ids (07) | The 30-day rule makes true day-7 values impossible for older references; this keeps backward compatibility |
| D16 | Outlier default ratio ≥ 3.0 + 100-view floor + spread gate (06 sole owner); final value from 12's threshold sweep (best F0.5 with precision ≥ 0.80) | One definition system-wide |
| D17 | Single owners: `ViewsAtAge` resolver = 06 (02 owns snapshot cadence incl. a 72 h milestone); channel ownership verifier = 02; answer directness = 05; format/Shorts classification = 02; `WebSearch` port = 03; run kinds, statuses and queues = 11 | Removes triple implementations found in review |
| D18 | No sampling params anywhere; determinism comes from the LLM result cache + pinned model ids | Anthropic 400s on temperature (verified) |
| D19 | All stats-only refreshes use `videos:batchGetStats`; the quota ledger tracks three buckets (main, search, batchGetStats) | Cheapest compliant polling |
| D20 | Business context comes from a tiered `ContextSource` cascade (03 §14); every fact carries `source_kind`, and third-party or archived facts need confirmation | Owner: blocked or changed sites must still yield a profile |
| D11 | Refresh = additive insertions only (≤3 lines + link fixes), truth levels, healthcare policy pack | YouTube spam/misleading metadata policy; owner requirement |
| D12 | Crawler respects robots.txt (protego); Tavily extract is a capped fallback only | Python's robotparser mis-parsed Apollo's rules |
| D13 | Beautiful UI installed from its shadcn registry (no Playwright copying needed); Zeus primary; BoardUI free tier; libraries.dev effects only | Research findings in plan 13 |
| D14 | "Ask your analyst" chat paused | Owner |

## 13. Owner decisions (recommended default in bold)

Owner approved all bold defaults on 2026-11-20; O4 was revised as below.

| # | Decision | Options |
|---|---|---|
| O1 | Derived-metrics policy posture | **Proceed under assignment guardrails + disclosure labels + apply for audit/derived-metrics allowance now** · or limit to raw metrics only |
| O2 | Low-tier model | **Keep Haiku 4.5 (your choice), with Haiku 5.5 available as a config switch after a benchmark** · switch now (~10× cheaper) |
| O3 | Cards with fewer than 3 references (first-mover news, rule-based hygiene) | **Separate "Early signals" and "Channel hygiene" surfaces, outside the recommendation list** · allow inside the list with a label |
| O4 | Crawler identity on WAF block | **Resolved 2026-11-20 (owner): acquisition cascade (03 §14). Honest UA → compat UA → Playwright render → Tavily extract / Jina Reader → Tavily index → Wayback / Common Crawl → Wikipedia/Wikidata → web mentions → YouTube channel → user input. No CAPTCHA solving or proxy rotation. Name-only onboarding supported.** |
| O13 | Search budget split (100 calls/day) | **Onboarding 30 · news 15 · shared niche 55** · custom |
| O5 | Rare UI folder licence (MIT + Commons Clause + mandatory visible credit link) | **Use Zeus `artemis-folder-card` behind the same interface; Rare UI only if you accept the credit link** · use Rare UI |
| O6 | Health news/refresh human approval | **Required for regulated niches (Apollo)** · optional |
| O7 | Fallback LLM provider data access | **No client-private analytics or comments to the fallback; those tasks degrade to deterministic logic** · allow |
| O8 | Auth for v1 | **Workspace API keys + session login with argon2 passwords (simple), Google sign-in later** · Google sign-in only |
| O9 | Keyword suggestions from unmatched videos | **Off by default (brushes against free clustering)** · on |
| O10 | Keeping bare video/channel ids in feedback and eval labels beyond 30 days | **Keep ids only, re-fetch text at eval time; confirm with legal** · purge at 30 days |
| O11 | Demo set | **Apollo (healthcare) + Runable-style AI fixture for news + 3 ICP niches (dental, auto dealers, home services) with 5 competitors each.** Note: `@AspenDental` didn't resolve and `@runable` resolves to an empty channel, so channels need confirming |
| O12 | Legal review items | Indian hospital-advertising rules; reuse of cached Tavily results across clients; cross-owner aggregation residual risk |

## 14. Your earlier question: other ways to get channel analytics?

There is no official way to get a competitor's private analytics, or Apollo's, without the channel owner's OAuth consent. Watch time, retention, traffic sources and search terms exist only in the YouTube Analytics API for the owner. Third-party "estimator" sites scrape or model data, which the assignment and YouTube's policies forbid.

What we use instead, all from public, official data:
- **Velocity and views-at-age** from our own snapshots (cheap through `batchGetStats`).
- **Outlier ratios** against each channel's own median.
- **Engagement shape**, i.e. like-rate sanity, which exposes promoted-view spikes. On Apollo, six videos with 188k–899k views have about 1/50th of the channel's normal like rate.
- **Comment intent** as a conversion proxy.
- **Description hygiene.**
- **Answer directness** to buyer questions.
- **Paid-placement disclosures.**

When a client connects OAuth, their own panel adds exact day-N views, subscribers gained, traffic sources and search terms, shown separately.

## 15. Plan index

| # | Plan | Lines |
|---|---|---|
| 00 | [Shared context](plans/00-shared-context.md) | 94 |
| 01 | [Platform foundation](plans/01-platform-foundation.md) | 1250 |
| 02 | [YouTube data layer](plans/02-youtube-data-layer.md) | 917 |
| 03 | [Website intelligence](plans/03-website-intelligence.md) | 1426 |
| 04 | [Competitor discovery](plans/04-competitor-discovery.md) | 898 |
| 05 | [Topic engine](plans/05-topic-engine.md) | 767 |
| 06 | [Analytics engine](plans/06-analytics-engine.md) | 1416 |
| 07 | [Recommendation engine](plans/07-recommendation-engine.md) | 966 |
| 08 | [News radar](plans/08-news-radar.md) | 903 |
| 09 | [Content refresh](plans/09-content-refresh.md) | 678 |
| 10 | [LLM layer & writer](plans/10-llm-layer.md) | 898 |
| 11 | [Pipeline & API](plans/11-pipeline-and-api.md) | 989 |
| 12 | [Evaluation & ops](plans/12-evaluation-and-ops.md) | 1075 |
| 13 | [Frontend UX](plans/13-frontend-ux.md) | 1061 |
| 99 | [Consistency review](plans/99-consistency-review.md) | 766 |
