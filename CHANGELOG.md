# Changelog

## [Unreleased]

### Added
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

### Fixed
- Fix arq workers ignoring inherited settings (on_startup never ran).
- Fix oversized crawl and analytics artifacts by storing gzipped blobs behind pointers.
- Fix rapidocr 3.10 enum params that failed thumbnail enrichment.

### Decisions
- Recommend writes card copy through 10's CardWritingService and write_cards only retries template-worded cards — avoids paying the writer twice for the same drafts (99 C102).
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
