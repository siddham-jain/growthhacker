# Changelog

## [Unreleased]

### Added
- Add full per-provider LLM settings (tiers, limits, retries, effort, extra params, enable, order) and a no-token connection test.
- Add gap matrix and per-client notification settings endpoints; add the client's own column to competitor detail.
- Wire YouTube OAuth connect/callback/disconnect to 02's service with a clear not-configured error.
- Wire the write path: per-run stage deps, 06→07/09/08/10 input assemblers, niche_discovery and seed_topic_refs stages, setup confirmer, crons.
- Add name-only onboarding (client needs no website or channel) with country-aware site lookup.
- Add docs/RUNBOOK.md for the local stack and the Apollo demo.
- Add api read and command services for profile, topics, competitors, analytics, cards, news, refresh, creators and llm settings.
- Add evaluation and ops: freezes with leakage canary, forward backtest vs copy-top and random, label and review packs, ops report and alerts, feature-policy matrix.
- Add website intelligence with crawler, acquisition cascade, business locator and profile synthesis.
- Add hybrid topic engine with Hindi/Hinglish transliteration, semantic matching and adjudication.
- Add competitor discovery with cheapest-first channel resolution and drift checks.
- Add dag runner, arq workers, REST API and SSE progress streaming.
- Add recommenders, quality gates, creator lists and contract assembly.
- Add additive description refresh and channel hygiene engine.
- Add same-age baselines, outliers, traits, comment signals and pattern lift.
- Add anthropic and openai-compatible LLM providers with grounded card writer.
- Add YouTube data layer with quota buckets, resolver, ingest and snapshots.
- Add news radar with freshness sources, clustering, grounding and demand checks.
- Add frontend shell, onboarding live panel and mock-mode screens.
- Add platform foundation, canonical schema migrations, domain contracts and ports.
- Add master plan integrating all components into one value loop.
- Add implementation plans for 13 components and a cross-plan consistency review.
- Add shared context brief, env template and gitignore.

### Changed
- Hygiene chapter advice carries a marked "0:00 Introduction" scaffold with the video's real limits; the per-run cap keeps pasteable fixes ahead of advice-only notes.
- Cut first-load JS on main screens from ~335 KB to ~180–197 KB gz: lazy toaster, drawers, popovers, charts and settings editors; native tabs and segmented control.
- Align the live panel's stage rail with the backend DAG stage names.
- Restrict thumbnail OCR to own uploads, likely outliers and a stable control sample; read thumbnails concurrently.
- Profile synthesis keeps department, procedure and condition-level offerings with parent links instead of umbrella offerings.
- Parent offering topics carry their child offerings and site short names as aliases; brand and trust keywords are no longer topics.
- Topics stage adjudicates with the workspace's own llm settings.
- Niche discovery baselines the top niche-search channels as creators; topics and analytics include them.
- Niche search rotates daily across the client's offerings instead of the shortest keyword.
- References are picked outliers-first, spreading channels among outliers before weaker videos.
- Card types may exceed their cap by two only when no other type can fill the window.
- News plans the profile's watch topics and picks source packs by industry and country when the profile names none.
- News polls fetch queries concurrently and never hold a transaction across fetches or llm judging.
- Anthropic routes accept workspace extra params (sampling params rejected).
- Move confirm-setup's competitor freeze and topic rebuild into the worker; the endpoint returns 202 with the run.

### Fixed
- Fix build_profile failing with budget_exceeded on uncached profiles: onboarding llm budget is $1.50 (recrawl $1.00) and calls past the remaining budget fall back to the deterministic draft.
- Fix inline svg/css leaking into link labels, ctas and facts; code-like page lines and fact quotes are dropped (extractor v3).
- Fix malformed-link repairs never offered when the repaired site blocks checks, and contact lines printing a bare-number label twice.
- Fix every untrusted llm block being cut to 2,000 chars and flattened to one line (page map, profile reduce, topic adjudication saw a fraction of their input).
- Fix topic alias proposals failing on truncated output and aborting all batches.
- Fix topic adjudication cache and engine version ignoring prompt edits.
- Fix dependent stages starting before an upstream artifact was committed.
- Fix hospitals and societies being proposed as creators.
- Fix news cards counting early videos without a channel baseline as references.
- Accept `window_days` as a query string on every results route (literal ints never coerced).
- Fix write_cards copy never reaching the published card: cards runs read the inherited run's drafts and llm copy is attached to its recommendation rows.
- Fix competitor proposal capped at 7 slots; it now fills up to 10 (floor stays 3).
- Fix competitor ranking that left Narayana Health out: fold legal-name variants and rank by independent web sources before youtube signals.
- Fix Max Healthcare losing its channel: a bare mention no longer renames the brand, and unchecked search guesses no longer evict a checked lead.
- Fix arq workers ignoring inherited settings (on_startup never ran).
- Fix oversized crawl and analytics artifacts by storing gzipped blobs behind pointers.
- Fix rapidocr 3.10 enum params that failed thumbnail enrichment.

### Decisions
- build_profile caps each llm call at the run's remaining llm budget and charges at most what remains — concurrent map calls can overshoot a pre-call cap and the spend already happened.
- Chapter copy lives in the chapters_needed advisory, not an InsertionOp — ops are single-line inserts and public data has no transcript, so real times and titles can't be built.
- A same-host link repair is offered unless the repaired URL is known broken — Facebook, X and LinkedIn block probes, so requiring an ok probe meant Apollo's malformed links never got a fix.
- Child offering names on a parent topic are exempt from alias-collision rejection — the overlap with the child topic is intended (an epilepsy video is also a neurosciences video).
- Untrusted-block caps are per source and callers budget their own text — a single default cap silently truncated batched inputs.
- Creator channels are read at baseline depth by niche discovery and only become visible to analytics from the next run — analytics hides videos fetched after the run's as_of to stay deterministic.
- The competitor sheet's "You" column is computed in the API from 06's per-video metrics — 06 builds scorecards for tracked channels only.
- Thumbnails outside OCR scope keep pixel traits under ocr_engine "skipped" and drop out of text-trait lift — counting them as "no text" would fabricate lift for the outliers that were read; a 20% hash control sample keeps a performance-independent comparison group.
- Competitor source agreement counts independent origins (each web domain once, the rival's own site never) instead of source kinds — almost every rival had the same two kinds (llm, web_search), so the web tier could not tell a widely named national rival from a single-site regional one.
- Confirm-setup work runs in the executor as the first step of the analysis run (run params flag) — the row exists at once for the 202, and requeues or crash recovery can never start the dag before the freeze.
- Recommend writes card copy through 10's CardWritingService and write_cards only retries template-worded cards — avoids paying the writer twice for the same drafts (99 C102).
- write_cards attaches only llm copy to the rows of the run that saved the cards (found via the recommend output's extras.run_id) and the api overlays row text before the run's own copy — a cards run publishes its own run id but owns no rows, and template copy must never replace llm text.
- Refresh runs before recommend — 09's drafts and hygiene go through 07's gates and side surfaces.
- Run artifacts over 2 MB are gzipped into content-addressed run_artifact_blobs — readers keep using the artifact store unchanged.
- Channel handles resolve in resolve_inputs, not at client creation — ownership checks and quota belong to the run.
- Keyword and profile edits write a new profile version and rebuild the topic universe at once (lexical only, no llm) — the next run's snapshot reads the newest universe, so edits apply without a re-crawl.
- Backtest scoring inputs (frozen baselines, universe, T config) are pre-registered in eval_config at record time — the freeze bundle lives 28 days from T and prunes rows on their own 28-day clock, so scoring at T+18 cannot rely on it.
- Outlier baselines drop like-rate-suppressed spikes and re-assess until stable — one promoted spike otherwise inflates peer spread and hides others.
- Anthropic profile synthesis split into two structured calls — one combined schema exceeded structured-output limits.
- Business context uses a tiered source cascade (site → render → reader proxies → search index → archives → knowledge base → mentions → channel → user) — owner requires a profile even when a WAF blocks us or the company changes.
- Follow the assignment's simple-maths guardrails with "calculated by GrowthHacker" disclosures and apply for YouTube's derived-metrics allowance — Developer Policies III.E.4.h forbids derived metrics otherwise.
- No channel scores or leaderboards; evidence sheets and single transparent sorts only — YouTube policy guide forbids ranking channels and custom channel scores.
- Search calls (100/day, own bucket) are the binding quota, so niche search results are cached and shared across clients — search.list no longer costs 100 units.
- Cards with fewer than 3 references go to separate Early signals / Channel hygiene surfaces — the assignment hides any card that fails a rule.
