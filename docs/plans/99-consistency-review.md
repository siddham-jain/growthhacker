# 99 — Consistency review of plans 01–13

Scope: 00-shared-context plus plans 01–13. Section refs (§) point to each plan's own numbering. "Settled" means the orchestrator has already verified the fact; those positions are not up for debate here.

Settled facts used as ground truth:
- **S1** search.list costs 1 unit from a separate bucket of 100 calls/day. It is not 100 units.
- **S2** `videos:batchGetStats` exists and has its own bucket (≤50 ids per call).
- **S3** III.E.4.h forbids derived metrics unless audited under Section L (allowance from June 2026).
  - Acceptable: simple maths (averages, ratios, sorted top lists).
  - Forbidden: ranking or gamifying channels against each other, and custom channel scores.
- **S4** The mostPopular chart is unusable for niches.
- **S5** One API project per app. No key pools.
- **S6** `claude-haiku-4-5` is the low tier and `claude-sonnet-5-5` is medium/high. `claude-haiku-5-5` is a configurable option only.

Resolution principle: prefer the most rigorous, policy-safe and simplest option. A single owner per concept. Consumers adapt names to the owner.

---

## 1. Conflicts table

### 1.1 Policy, quota and platform facts

| # | Topic | Plans (refs) | Positions | Recommended resolution | Rationale |
|---|---|---|---|---|---|
| C1 | search.list cost | 00 §2; 07 §7; 09 §7, §10 vs 01 §3/§7, 02 §5.3, 04 §13.1, 08 §13.3, 11 §13.1, 12 §13.1 | 00, 07 and 09 say "100 units/call" (09: "2 calls = 200 units"). The others say 1 unit from a 100-calls/day bucket. | Use 1 unit from the `search` bucket (100 calls/day). Fix 00 §2, 07 §7 and 09 §7/§10. | Settled S1. |
| C2 | Snapshot endpoint | 02 §5.3, §13.2 (batchGetStats, own bucket) vs 01 §5.3.4/§10, 06 §5.3.7/§10, 11 §5.6/§10, 12 §5.4, 08 §5.10 (videos.list) | Only 02 uses batchGetStats. 01's ledger buckets are only `default` and `search`. | Run all stats-only refreshes (snapshots, retention refresh, demand re-checks) on `video_stats()` → batchGetStats. Keep videos.list for metadata. Ledger buckets: `main`, `search`, `batch_stats`, `analytics`. | Settled S2. Moves the largest recurring cost out of the 10k bucket. |
| C3 | mostPopular use | 06 §5.11, §10 (`TrendingReport.chart_items`); 12 §5.4, §9 #23; 13 §4.3 (Radar "trending chart" tab); 01 §6.1 (`ingest_source 'chart'`, `videos.list:chart`) vs 02 §13.7 (off by default) | 06, 12, 13 and 01 rely on it. 02 rejects it. | Remove it. Define "trending in my niche" as cached niche search (window, order=viewCount) plus same-age velocity (02 §13.7). Drop `chart_items`, `ingest_source='chart'`, the 13 chart tab and the 12 matrix row. | Settled S4. |
| C4 | Custom channel scores and ranking | 04 §5.8 (`youtube_score` = 0.45/0.35/0.20 composite, ranking); 07 §5.5 (`creator_score` = 0.40/0.30/0.20/0.10); 11 §3.4 (`CreatorView.rank`, `CompetitorView.score`, `RecommendationOut.score`); 01 §6.4 (`competitors.score`, `rank`); 13 §4.7 ("YouTube activity & topic match: 0.72"), §4.2 (ranked creators); 06 §3.2 (`size_vs_client`); 12 §7, §9 #21/#36 ("leaderboard OK") vs 01 §13 Q2, 07 §13.7(b), 12 §13.13 | Several plans compute and expose composite channel scores. 12 calls Section L leaderboards acceptable. | **No composite channel score anywhere, displayed or internal.** Channels (competitors, creators) are filtered by explicit threshold rules, then sorted by one transparent raw metric the user can choose (e.g. matching uploads in the window, median own-baseline ratio). Each listed channel shows its separate simple stats. Delete `youtube_score`, `creator_score`, `rank`/`score` fields, `size_vs_client` (show the subscriber band instead), the numeric 0.72 display and 12's "leaderboard" rows. **OWNER** | Settled S3. A sorted list by one simple metric is explicitly acceptable. |
| C5 | Section L / derived-metrics framing | 02 §9, §13.4 (forbids, "tension"); 05 §7, §13.3; 09 §13.8; 12 §13.3 (outlier ratio needs the amendment) vs 04 §4.5 ("disclosure rule"); 12 §7 ("allows leaderboards"); 06 §9 ("Compliant") | Readings range from too strict to too loose. | One shared paragraph in 00 §2: "Displayed figures are API data plus simple maths (counts, sums, medians, means, ratios, sorted lists), labelled 'calculated by GrowthHacker'. No composite or channel score, no channel ranking. Statistical gates (z, CI, q) are internal filters and are never displayed as metrics. Storage beyond 30 days requires an accepted Section L audit." Apply for the audit (12 owns the task). **OWNER** | Settled S3. One interpretation for all 13 plans. |
| C6 | Low-tier default model | 03 §13.1 (haiku-5-5 as default), 04 §13.2, 08 §13.4, 09 §13.10, 12 §13.9 vs 05 §13.1, 10 P1 | Five plans want haiku-5-5 as the default. 05 and 10 keep 4.5. | Default stays `claude-haiku-4-5`. `claude-haiku-5-5` is a one-line config option, gated by 10's golden suite. Cost headlines (03 §10 ≈$0.47; 04 ≈$0.10; 12 ≈$0.03) must quote 4.5 figures. | Settled S6. |
| C7 | Tier set | 01 §3 (`Literal["low","high"]`) vs 10 §3.1 (`low`, `medium`, `high`) | 01 has no medium tier. | Use low/medium/high (10). low = haiku-4-5 with no thinking. medium = sonnet-5-5 at effort low. high = sonnet-5-5 at effort medium. Fix 01 `LLMSettings`. | Owner decision has three reasoning levels. 10 owns the shape. |
| C8 | Sampling params / temperature 0 | 05 §5.4.5, §13.1 (relies on `temperature=0`); 12 §5.4 (replay "Haiku at temperature 0") vs 10 §0 [LIVE], §5.3 (temperature returns 400 on the new API version; sampling params forbidden) | 05 and 12 depend on a parameter that 10 bans. | Send no sampling params. Get determinism from 10's result cache (input hash plus pinned resolved model id). 05 calibration and 12 replay read cached adjudications. | Verified live by 10. Caching is also cheaper. |
| C9 | Key pools / projects | 02 §13.5, 01 §4.8, 11 §10, 12 | All consistent: one project. | Add to 00 §3: "one Google Cloud project; more quota only through the compliance audit". | Settled S5. |
| C10 | Use of `categoryId` / `topicDetails` | 01 §6.1 (stores `category_id`); 03 §7.2 (requests `topicDetails` on channels.list); 05 §5.11 (store raw, never use) vs 02 §13.8 (drop) | Mixed. | Never request `topicDetails` (videos or channels). Drop `categoryId` at ingest. Remove the 01 column. Keep 05's lint rule. | Most policy-safe (no content-category inference). |

### 1.2 Thresholds and defaults

| # | Topic | Plans (refs) | Positions | Recommended resolution | Rationale |
|---|---|---|---|---|---|
| C11 | Outlier threshold | 06 §5.7/§5.13 (ratio ≥3.0, log-robust z ≥2.0, X ≥100; strong ≥5; breakout ≥2); 12 §5.1.2 (θ=3.0 assumed); 08 §5.10 ("06's threshold 2.0"); 07 §5.4 (`views_lens_min_ratio` 2.0); 09 §5.6.1 (`ref_outlier_min` 1.5); 07 §5.5 (hit_rate at ≥0.7) | 08 assumes the wrong value. 07 and 09 add their own cut-offs. | The single source is 06 `AnalyticsSettings.outliers` (ratio 3.0, min views 100, z gate internal). Consumers use `is_outlier` and `ratio` only. 08 uses `is_outlier`. 07's views-lens and 09's "above normal" cut-offs become 06 tiers (`breakout` ≥2.0). 09 refs must be `is_outlier`. 12 tunes θ. | One definition, so cards and backtests agree. |
| C12 | Threshold selection criterion | 06 §5.7.5 (max F0.5 subject to recall ≥0.6; grid ratio×z×floor) vs 12 §5.2 (θ sweep 1.5–6.0, max F0.5 subject to precision ≥0.80) | Different constraints. | Use 12's: max F0.5 subject to precision ≥0.80, sweeping ratio and (optionally) the z gate. Drop the z gate if it does not raise F0.5. | Precision protects card honesty. 12 owns evaluation. |
| C13 | Labelling sample design | 06 §5.7.5 (100 long-form split 35/35/30 by ratio, plus 30 Shorts, "Mira + marketer") vs 12 §5.2 (100 stratified, ratio bands R1–R4 15/30/25/30 × size bands S0–S4, HT weights) | Two designs. | Use 12's design. Add 06's Shorts sub-sample (30) as a separate stratum. | 12 owns evaluation. Its design is more rigorous. |
| C14 | Baseline definition | 06 §5.6 (median of ≤30 most recent same-segment, same-age, same-method trailing videos within 365d; min n 5; floor 10 views; widened 50/730d); 02 §5.5 (`min_per_format` 30, lookback 90+90 d, max 300 videos); 01 §5.9 (90+180 d or 50 most recent); 07 §5.1 (exclude channel if `baseline_n` <5 or baseline <100 views); 02 §8 #22 (dormant: last 30 regardless of age); 12 (format-split median) | The ingest horizon (180/270 d) is shorter than the baseline lookback (365 d). Two floors (10 vs 100). | 06's definition is canonical. 02's lookback planner ingests until 30 videos per segment **or** 365 d, capped at 300 videos. 07's `<100` exclusion becomes a 06 suppression rule (`tiny_baseline`, `min_baseline_views_for_outliers` 100). Keep 06's denominator floor at 10. | Ingest must cover the baseline. One owner for outlier eligibility. |
| C15 | Comparison age per window | 06 §5.3.1 (`{7:3, 30:7, 90:7}`, day 30 secondary); 02 §5.6 (targets 24h, 168h, 720h; no day 3); 12 §3.2 (`outcome_age_days` 7); 07 (variable `comparison_age_days`); 00 contract (`views_day7`) | 02 never measures day 3. | Use 06's map. 02 adds a **72h milestone** (±6h). Backtest stays at a=7. The contract carries `views_at_age.age_days` (see C30). | The 7-day window cannot use day-7 views for most of its videos. |
| C16 | Snapshot cadence | 02 §5.6 (<48h hourly; 2–8d every 6h + 168h milestone; 8–35d daily + 720h milestone; then weekly; collector every 10 min); 06 §5.3.7 (0–48h 6h; 2–10d 24h; 10–35d 72h; then weekly); 11 §5.9 (0–2d 6h; 3–14d daily; 15–90d 3d; >90d weekly; tick 30 min); 12 §5.1.2 (needs 1/3/7/14 d); 01 §10 (avg 1.5/day) | Three schedules. | Use 02's tiers plus the 72h milestone. They meet 06's tolerances and 12's day 14, and run on batchGetStats. 02 owns the cadence. 11's `snapshot_tick` simply calls 02 `SnapshotScheduler.due_batch` every 10 min. Delete the cadence text in 06 and 11. | 02 owns snapshots. It is the densest schedule and it is off the main bucket (C2). |
| C17 | Age tolerances / max gap | 02 §5.6 (tol 24h:2h, 168h:12h, 720h:48h; max_gap 12h/48h/7d; `late_observed` up to 1.25× target) vs 06 §5.3.1/§5.13 (tol 1d:3h, 3d:6h, 7d:12h, 30d:48h; max_gap 12h/36h/72h/240h; no extrapolation) | Different numbers, and 02's `late_observed` is not same-age. | Use 06's `AnalyticsSettings.ages` as the only source, read by a single resolver. A `late_observed` value becomes `age_band_proxy`. | Same-age rigour (assignment quality bar). |
| C18 | Cards per window and type caps | 07 §5.8 (`{7:5, 30:8, 90:10}`; per type: video_idea 4, title 2, thumb 2, format 2, creator 3, news 2; refresh surface 5); 08 §5.1 (up to 5 news opps/day, separate lane); 09 §5.8 (max 10 refreshes/run); 10 §10 (card_write 15–25/analysis); 11 §10 (3–8 cards); 12 §5.4 (~12 cards); 13 §B (5/8/10, top 3 expanded) | Writer volume estimates range from 3 to 25. | Use 07's caps. Writer budget = shown cards for the **selected window only** (≤10) plus ≤5 refresh items. Recompute 10 §10, 11 §10 and 12 §5.4 costs on ≤15 calls. | Fewer LLM calls. All deterministic windows are still computed (11 §13.5). |
| C19 | Minimum reference videos | 00 §2, 01 §3, 07 §5.13 gate 30 (≥3); 08 §5.12 (first-mover allows 0); 07 §13.6 (news: 2 videos + 2 sources); 09 §13.2 (hygiene: 0 refs, "Rule-based"); 10 §B (writer assumes ≥3) | Exceptions conflict with the quality bar. | Every type in the **Recommendations** list needs ≥3 refs. First-mover news and hygiene fixes go to **separate surfaces**: a "News radar: early signals" lane (≥2 independent sources, label Low, `confidence.basis=first_mover`) and a "Channel hygiene" list (09 fallback). They are never mixed into recommendation cards. **OWNER** | Keeps the assignment bar intact while keeping the extensions. |
| C20 | Confidence rules | 00/07 §5.9 (High = ≥5 outliers across ≥3 channels; Medium ≥3/≥2; caps); 08 §5.12 (adds a "First-mover" label); 09 §5.8 (High adds R≥0.75 and snapshot basis; "Rule-based"); 07 §5.5 (`creator_v1`: channels always 1); 06 §13.10 (proxy evidence drops one level) | Extra labels outside the enum. | Labels are `High`, `Medium`, `Low` only. Add `confidence.basis ∈ {standard, first_mover, rule_based}` and `rule_id`. Adopt 06 §13.10's proxy downgrade as a cap in 07 `default_v1`. Show `creator_v1` as a separate rule id. | Honest confidence with one enum the UI can render. |
| C21 | Expected-range format | 07 §5.10 (`ExpectedRange` object; string display "1.2x-2.0x (indicative)"); 08 §5.13 ("P25x–P75x (indicative, from your N news-reaction videos)"); 09 §5.11 (own refresh history string); 01 §6.7 (low/high/label columns); 12 §3.1 (numeric low/high); 10 §3.2 (string) | Five shapes. | The contract field is a nullable string (`display`). `extras.expected_range` = 07 `ExpectedRange{low, high, basis, n_peer, n_peer_effective, n_own, comparison_age_days, size_note}`. 08 and 09 register `ExpectedRangeStrategy`s in 07. 12 reads `extras.expected_range.low/high`. | Backward compatible, and one object serves everyone. |
| C22 | No-copy checks | 07 §G (ratio ≥85, token_sort ≥88, partial ≥95; **no token_set**; own titles ≥80); 08 §5.12 (token_set_ratio ≥0.8, wrong scale); 10 V5 (token_set_ratio ≥85); 09 §5.8.1 (6-gram overlap) | Four implementations. | One `NoCopying` gate pair in 07 (structural plus text). 08 and 10 call it. 09 keeps 6-gram only for its own insertion text. | One calibrated check (07 calibrates on 200 pairs). |
| C23 | Inactive channel | 04 §5.11 (>90d no uploads); 02 §8 (dormant 180d); 07 §5.5 (creator excluded at >30d); 13 §8 D16 (>45d) | Four values. | Competitors: inactive at >90 d (04). Creator eligibility: last upload ≤30 d (07). 02 drops "dormant". 13 reads the flag and does not recompute it. | Two clearly named rules. |
| C24 | Shorts definition | 02 §5.5 (aspect h/w ≥1 and ≤180s from 2024-10-15, else ≤60s; UUSH optional); 06 §5.5 (duration only, pillarbox off); 04 §8 ("≤60s share"); 09 §5.5 (≤180, 61–180 "likely") | Three rules. | One `FormatClassifier` in 02 at ingest, with 06's rule order (OAuth `creatorContentType` > live details > aspect + duration + date). Output `format` uses 06's `FormatSegment`. 06's `SegmentClassifier` and 04's ≤60s rule are deleted. | One classification feeds baselines and cards. |
| C25 | Freshness sensitivity levels | 03 §5.13 (very_low…very_high, 5 levels; `news_scan_interval_hours` 336/168/72/24/6); 08 §3.1, §5.1 (high/medium/low/none, 4 levels; high = 6h); 11 §5.9 (high 6h, medium 24h, low 168h, none disabled); 09 §3.1 (`news_velocity_prior` low/med/high); 04 §8 (`FreshnessProfile.high`) | Levels and cadences disagree (medium = 72h vs 24h). | Keep 03's 5-level `sensitivity` as the stored value. The operating envelope maps very_high/high→high (6h), medium→medium (24h), low→low (168h), very_low→none. 03 `RefreshPolicy.news_scan_interval_hours` = 6/6/24/168/0. 09 derives its prior from this mapping. | One stored value, one mapping, and the two consumers agree. |
| C26 | Stale badge | 11 §8 #31 (`age_hours` >36); 07 §5.13.4 (stale_data >48h); 13 §B (48h) | 36h vs 48h. | 48h, computed by 11 in `ResultEnvelope.freshness.stale`. | A daily refresh plus a margin of one missed day. |
| C27 | Retention timing | 01 §5.3 (T=30d, act at A=28d, fail-closed at T−1); 11 §6 (29d); 12 §6/§5.4 (30d tables; alert >29d); 02 §6 (hard 30d; metadata refresh ≤25d); 05 §6 (≤30d); 01 comments 28d vs 02 30d | Margins of 1–2 days, or none. | One `RetentionSettings` (01): `max_age_days`=30, `safety_margin_days`=2, so every owner acts at 28 d. The 12 alert fires on any row >28 d. Comments delete at 28 d. | Fail-closed and a single knob. |
| C28 | Storage-mode flag | 01 §3 `retention.stats_storage_mode` (standard/derived_metrics_approved) vs 06 §5.3.8 `policy.retention_mode` (strict_30d/derived_metrics_36m) | Two flags. | Keep 01's flag only (`standard` default). 06 reads it. | One switch for Section L approval. |
| C29 | crawled_pages retention / zone | 01 §6.4 (tenant, 180d, text ≤100k); 03 §6 (text ≤60k, kept "while client active", cross-client reuse by domain if <7d) | Zone and TTL differ. | Tenant-scoped, 180 d, text ≤60k (03). No cross-client reuse for now. | The simplest correct zone. Crawls are cheap. |

### 1.3 Contract

| # | Topic | Plans (refs) | Positions | Recommended resolution | Rationale |
|---|---|---|---|---|---|
| C30 | `views_day7` vs `views_at_age` | 00 §2, 01 §6.7, 09 §5.8, 10 §3.2 (`PackVideo.views_day7: int`), 11 §3.4 (views_day7 only); 02 §13.3 (replace with `views_at_age{views, age_days, method, label}`); 06 §13.1 (keep views_day7 nullable plus `views_at_age{age_days, value, method}` + `outlier_ratio_method`); 07 §5.14 (views_day7 nullable + `views_at_age{age_days, views, basis}`); 08 §3.3 (`{age_hours, views}`); 12 (`views_at_age:int`); 13 §13.2 (needs `views_at_age` + `comparison_age_days`) | Six shapes. | Keep `views_day7: int \| null`, set only when `age_days==7` and the method is observed, interpolated or owner_analytics. Add `views_at_age: {age_days, views, method, label}` using 06's `AgeMethod`. `outlier_ratio` uses the same method. 10 `PackVideo` carries `views_at_age`. 08 converts hours to `age_days`. | The 30-day rule makes day-7 values impossible for older references. Backward compatible. |
| C31 | Contract extensions | 07 §5.14 (`extras` object + `contract_version` "1.1"); 08 §13.1 (top-level optional fields); 09 §13.1 (`details` object); 11 §3.4 (top-level extensions, no version); 01 §6.7 (`details` jsonb); 10 §F ("contract unchanged") | Four styles. | 07's style: assignment fields, plus extra keys inside `reference_videos[]` and `confidence`, plus one `extras` object (with `contract_version`). 08's fields go in `extras.news`, 09's in `extras.refresh`, 11's in `extras` (`stable_key`, `run_id`, `rendered_by`). The `?format=contract` view strips `extras`. | One extension point. The contract test stays simple (07 §11). |
| C32 | Recommendation id | 07 §5.14 (`rec_`+base32(sha1(stable_key))[:12], stable across runs); 08 §3.3 (`rec_`+ULID); 11 §3.4 (adds `stable_key`) | Stable id vs random id. | 07's stable id. `stable_key` = sha1(client \| type \| canonical_subject_key \| window). | Feedback and dedupe across daily runs. |
| C33 | `reference_videos[].channel` | 00 (channel); 07 §13.5 (= title + `channel_id`); 11 (channel + channel_id); 10 (`channel_name`) | Name vs id is ambiguous. | `channel` = channel title, and add `channel_id`, `url`, `title`. | The assignment example shows a name. The id is needed for links. |
| C34 | `buyer_question` | 01 (id FK + text); 07 (text, nullable); 11 (string; filter by id) | — | The contract is a nullable string. `extras.buyer_question_id` holds the id. | Contract fidelity. |

### 1.4 Domain objects and enums

| # | Topic | Plans (refs) | Positions | Recommended resolution | Rationale |
|---|---|---|---|---|---|
| C35 | ViewsAtAge owner and shape | 00 §5 (06); 02 §3.2 (claims ownership; hours; methods analytics_exact/observed/interpolated/late_observed/age_band_proxy/immature/unavailable); 06 §3.2 (days; observed/interpolated/owner_analytics/age_band_proxy/pending/unavailable); 07 §3.1 (basis observed/interpolated/backfilled/unknown); 01 §6.1 (observed/interpolated/estimated); 12 (observed/interpolated/missing) | Five method enums. | 06 owns `ViewsAtAge` and `ViewsAtAgeResolver`. 02 `SnapshotReader` exposes snapshots only (`snapshots(video_id, until)`). Canonical `AgeMethod` = 06's. Mapping: analytics_exact→owner_analytics, immature→pending, late_observed/estimated→age_band_proxy, backfilled→owner_analytics, missing/unknown→unavailable. | One resolver and one enum. |
| C36 | Format / segment naming | 02 `format` (short, long_form, live_replay, live_now, upcoming, unknown); 06 `FormatSegment` (short_form, long_form, live); 12 (short, long, live); 07 `format_key`; 01 `length_band` | — | `FormatSegment = short_form \| long_form \| live`, plus excluded states `upcoming \| unknown`. `format_key = f"{segment}\|{length_band}"`, owned by 06. | One vocabulary. |
| C37 | Channel role vocabulary | 02 §6 `client_channel.role` (own/competitor/niche_creator); 04 §3.3 (competitor_set/niche_set/benchmark_set/creator_seed/excluded); 07 (competitor/niche/client/creator); 01 `v_tracked_channels` (own/competitor) | Four sets. | `ChannelRole = own \| competitor \| niche \| benchmark \| creator`. 04's sets map 1:1. "excluded" is a candidate status, not a role. | — |
| C38 | Ownership verification | 02 §5.2 (`OwnershipAssessment`: verified_oauth/verified_link/likely/unverified/mismatch; weights 0.6/0.4/…; own channel only); 03 §5.2 (`ChannelLink.verification`: verified/probable/user_asserted/mismatch); 04 §5.6 (`OwnershipVerdict`: verified/likely/uncertain/rejected; weights 0.5/0.3/…; verified ≥0.80); 01 §6.4 (`own_channel_status` unresolved/proposed/verified/rejected/needs_attention); 11 §13.9 (belongs to 02) | Three verifiers and three weight sets. | One `OwnershipVerifier` in 02 with an `OwnershipSignalProvider` registry. 03 contributes site-side signals (sameAs, header links, embeds). 04 contributes web-search and LLM adjudication. Labels: `verified \| likely \| uncertain \| rejected`, with `method` (oauth/link/user). Use 04's stricter thresholds (verified ≥0.80 plus a link signal). Rename both `OwnershipSignal`s to one `{name, weight, detail, source_url}`. | The demo depends on verifying ownership (the Apollo247 trap). The strictest rule wins. |
| C39 | BuyerQuestion enums | 01 §6.4 (intent_stage awareness/consideration/decision; status proposed/confirmed/rejected/retired; source crawl/user/llm_suggested/spotlit_ai_tracking/comments); 03 §3.2 (funnel_stage + post_purchase; status proposed/tracked/rejected; intent 12 values); 05 (FunnelStage …/retention); 07 (intent_stage; `tracked: bool`; source crawler/user/spotlit); 11 (status suggested/active/rejected; source profile/user/spotlit_ai_tracking) | Five variants. | `funnel_stage = awareness \| consideration \| decision \| post_purchase`. `status = proposed \| confirmed \| rejected \| retired`. `source = website_faq \| website_derived \| user \| spotlit \| comments \| import`. The API DTO uses the same values (no `active`/`suggested`). | 03 owns it. 01's status set adds `retired` for history. |
| C40 | Keyword status / source | 01 §6.4 (`keywords` table: proposed/confirmed/rejected/retired); 03 (`NicheKeyword` inside profile JSON; tier, source …); 11 (KeywordItem suggested/active/rejected; priority 1–5) | — | 03 owns the `keywords` table (rows like buyer_questions; mirrored from `niche_keywords` on confirm). Status as in C39. Priority 1–3 (match BQ). | One store referenced by 05. |
| C41 | Competitor status | 01 §6.4 (proposed/confirmed/rejected/archived/needs_attention); 04 §3.3 (suggested/confirmed/auto_confirmed/rejected/unresolved/no_channel/excluded/user_added); 11 (suggested/confirmed/rejected) | — | Use 04's enum, owned by 04 tables. 11's DTO exposes it. Drop 01's `competitors` table (C62). | 04 owns discovery. |
| C42 | TopicMatch shape and method | 01 §6.7 (one row per (client, video), `matches` jsonb; method lexical/embedding/llm); 05 §3.2 (one row per (client, video, topic_ref, universe); method lexical/semantic/lexical+semantic/llm_adjudicated/implied/override; decision, confidence, review_reasons); 07 (accepted bool, review_flag, method lexical/embedding/llm); 06 (score, kind, matched_terms) | — | 05's shape. Add the derived `review_flag = bool(review_reasons)`, `accepted = decision=='accepted'`, `topic_kind`, `matched_terms[]`. "embedding" → "semantic". | 05 owns topics. |
| C43 | Answer directness owner | 05 §5.7 (`AnswerDirectness` direct/partial/mention/none, weighted); 06 §5.8.1 (own directness direct/partial/tangential); 07 §5.4 (directness ≥0.6) | Duplicated logic. | 05 owns directness. 06 consumes it through `TopicTrait.directness`. 07's lens rule uses `level ∈ {direct}` or `score ≥ 0.70` (05's band). | 06 §13.9 asks for one owner. |
| C44 | Gap matrix owner | 06 §3.2 (`GapCell`, `gap_matrix`); 07 §5.2 (`GapReport`, `GapEvidence`, `CoverageMatrixBuilder`) | Two builders. | 06 produces the cells (counts only). 07 classifies the gaps (`GapEvidence`) from them and drops `CoverageMatrixBuilder`. | Counts come from analytics, judgement from the recommender. |
| C45 | Name collisions | `DataClass` (01 retention classes vs 10 LLM data policy); `OwnershipSignal` (02 vs 04); `CandidateSource` (04 domain object vs 04 Protocol); `ChannelRef` (02 parsed input vs 11 channel DTO); `PatternSpec` (07 pattern subject vs 12 backtest match spec) | — | Rename 10's to `PayloadClass`, 04's Protocol to `CandidateGenerator`, 11's DTO to `ChannelSummary`, 12's to `MatchSpec` (emitted by 07 as `draft.match_spec`). Merge `OwnershipSignal` (C38). | Avoid import clashes. |
| C46 | WriterBrief vs EvidencePack; WrittenCard vs CardCopy | 07 §5.14 (WriterBrief, WrittenCard, `write_cards(briefs)`); 10 §3.2 (EvidencePack, EvidenceFact, CardCopy, `CardCopyWriter.write(pack, ctx)`); 07 Reason{code, params} vs 10 EvidenceFact{kind, statement, numbers} | Duplicate concepts. Writer length limits differ (07: summary 110 / fit 160 / why 3; 10: 140 / 200 / 2–4). | 10's `EvidencePack` and `CardCopy` are canonical, built by 10's `EvidencePackBuilder` from 07 drafts. Extend 07 `Reason` with `statement` and `numbers: list[EvidenceNumber]` (one concept, replacing a separate EvidenceFact list). Limits: summary ≤140, why_it_works 2–3 items ≤160 each, fit ≤200. | One writer contract. |
| C47 | Draft subtypes for news and refresh | 07 §3.2 (expects `NewsSubject`, `RefreshSubject`; gate names `NewsSourceCorroboration`, `AdditiveDiffOnly`, `MinimalEdit`); 08 §3.3 (`NewsOpportunityDraft` subclass; `NewsEvidenceGate`); 09 §4 (`to_recommendation_draft`) | Undefined types and name mismatches. | 08 defines `NewsSubject{news_signal_id, story_id, key_entity, angle, urgency, expires_at, reference_sources[], format_suggestion, conversion_action_id, compliance}`. 09 defines `RefreshSubject{video_id, operations[], advisories[], review_required}`. Both use plain `RecommendationDraft` (no subclass). Gate names: `NewsEvidenceGate` (08) and `AdditiveDiffOnly` (09), registered in 07's GateRegistry. | 07 owns the framework. Subjects are type payloads. |
| C48 | Lens assignment for news | 08 §3.3 (computes lens itself) vs 07 §5.4 (LensAssigner) | — | 08 supplies the reasons. 07 `LensAssigner` decides the lens. | One rule. |
| C49 | NewsSignal fields | 08 §3.2 vs 09 §3.1 (entity, aliases, entity_kind, is_competitor_entity, topic_ref_ids, first_seen_at, event_date, sources[], status active/disputed/retracted/superseded, superseded_by, demand_videos) vs 01 §6.7 (columns) | 09 consumes fields 08 doesn't name. | Canonical list in §4. 08 adds flattened accessors and a `verification_status` field. 01's columns are dropped in favour of 08's table. | 08 owns it. |
| C50 | Refresh insertion shape | 09 §3.2 `InsertionOp` (positions top_line/after_first_paragraph/before_hashtag_line/before_links_block/append; kind insert/link_fix; claim_level; ≤3 ops, ≤200 chars/op, ≤300 total, 5000 **bytes**, subsequence invariant); 10 §5.1, §8 #70 (≤2 lines, ≤200 chars, "exact substring", 5000 chars); 11 §3.4 (anchor append/after_line, line_index); 01 §6.7 (anchor/position/keyword_id/news_signal_id) | Four shapes and two invariants. | 09's `InsertionOp` and limits (bytes, subsequence; `link_fix` is the only non-additive op). 10 deletes its own refresh limits and calls 09's validator. 11's DTO exposes `operations[]` unchanged. | 09 owns the refresh logic. Bytes match YouTube's limit. |
| C51 | refresh_phrasing tier and degrade path | 09 §5.10 (low, haiku-4-5; fallback template; 10 s, concurrency 5); 10 §5.1 (medium, sonnet-5-5; 30 s; fallback "Add keyword: …") | — | Low tier (haiku-4-5), timeout 20 s, 09's template fallback. Regulated-domain safety comes from 09's deterministic validators. | Short constrained rephrasing is low reasoning. The template is better UX. |
| C52 | News judgement tier | 08 §5.8 (Sonnet, judgement of relevance + offering links + safety); 10 §5.1 (`news_relevance` low, haiku) | — | Medium (sonnet-5-5 at effort low), as in 08. Add a `news_judgement` route to 10. | Health safety flags and grounded entity extraction are medium reasoning. |
| C53 | Missing LLM routes | 03 §5.8–5.10 (page selection low, page map low, reduce sonnet 16k max_tokens + thinking); 04 §7.3 (candidate generation medium, relation classification medium, entity extraction low, ownership adjudication low, evidence summary low, owner_kind low); 05 (alias proposals low, tie-break low) vs 10 §5.1 (website_profile_extraction high 8,192; competitor_adjudication medium only) | 10's table is incomplete, and 03's reduce output does not fit in 8,192. | Add every task to 10's routing table (list in §3, D-LLM-3). `website_profile_reduce`: high, max_output 16,384. | 10 is the single router. |
| C54 | LLM run budgets | 10 §5.2 (onboarding 0.75, analysis 1.50, daily 0.75, client/day 3.00); 11 §5.4 (onboarding 0.60, analysis 1.00, daily 0.30); estimates: 03 §10 onboarding ≈$1.10, 04 ≈$0.13, 12 §5.4 daily ≈$0.30, 10 §10 analysis ≈$0.55, daily ≈$0.20 | 11's caps sit below the plans' own estimates. Onboarding needs ≈$1.25. | 10 owns budgets and 11 reads them: onboarding $1.50, analysis $1.50, daily_refresh $0.75, client/day $3.00. | Avoid routine template fallbacks. |
| C55 | YouTube and Tavily run budgets | 02 §5.3 (onboarding 400 main + 15 search; daily 80 + 5; client caps 600 / 20); 11 §5.4 (onboarding 150 / 6; analysis 900 / 4; daily 120 / 1; Tavily onboarding 40, daily 10, news_scan 20); 04 §10 (25–60 units, 0–9 search, 12–20 credits); 03 (≤30 credits); 08 §10 (high ≈48 credits/day) | 11's search and Tavily caps are below the plans' needs. | 02 owns YouTube budgets (11 reads them): onboarding 400 + 15 search, daily 80 + 5 search. Tavily onboarding 60 credits. news_scan uses an 08 envelope per sensitivity, plus a global cap of 300/day (08). | Budgets live with the resource owner. |
| C56 | Per-client daily quota estimate | 02 §10 (25–30 main + 3–5 search + 10–15 batch); 11 §10 (20–30 + 0–1 search; 150–200 clients/day); 12 §5.4 (≈50; ~150+ clients; alert >150); 07 §10 (indirect ≈81 for creators); 06 §10 (snapshots 10–30 + comments 20–60) | 20 to 140 units. | Recompute in 12's ops report from 02's ledger after C2 (snapshots off the main bucket). Interim planning figure: ≈50 main + ≤5 search per client per day. Alert at >150 (12). | Ledger is the source of truth. |
| C57 | Search-bucket allocation | 02 (onboarding 15, per-client cap 20; "binding constraint", ~25 clients/day without sharing); 08 (reserve 15/day for demand checks); 04 (≤9/discovery); 09 (0–2/client/day); 11 (daily 1, analysis 4) | No global plan. | 02 allocates a 100/day pool: news demand reserve 15, onboarding reserve 30, shared cached niche queries for daily refresh 55, 09 = 0. **OWNER** to accept the onboarding throughput (~3–4 cold onboardings/day/project). | S1 makes search the binding constraint. |
| C58 | Comment sampling | 02 §5.5 (1 page × 50, order=relevance; top 5 per competitor; 30 calls/onboarding, 10/daily); 06 §5.9, §10 (≤100/video; 2 fetches per video at ~3d and ~10d for all window videos); 01 §6.1 (text_redacted, author_hash, `is_channel_owner_reply`, 28d) vs 02 (text with no author fields, 30d) | Volume and fields conflict. | 02's caps (cost), extended to the outliers in the window (06's candidate list) with 1 page × 100. 02 computes `is_channel_owner_reply` and drops author ids at ingest (01 schema). Delete at 28 d. | Quota-safe and privacy-safe. 06 gets the owner-reply signal. |

### 1.5 Runs, pipeline, workers

| # | Topic | Plans (refs) | Positions | Recommended resolution | Rationale |
|---|---|---|---|---|---|
| C59 | Run kinds | 00 §4 (onboarding, analysis, daily refresh); 01 §6.6 (+backtest); 11 §3.2 (+cards, news_scan, recrawl, backtest); 12 §13.7 (`evaluation`) | — | 11's set (`backtest`, not `evaluation`). | 11 owns orchestration. |
| C60 | Run statuses | 01 §6.6 (queued, running, awaiting_confirmation, succeeded, partial, failed, cancelled, superseded; §5.7 also "superseding"); 11 §3.2 (queued, running, cancelling, succeeded, partial, failed, cancelled, superseded); 10 (`completed_degraded`); 02 (`waiting_for_quota`); 07 (`degraded`); 04/03 (run blocks at awaiting_confirmation) | — | 11's run statuses. `awaiting_confirmation` is a **client lifecycle** state (11 `ClientLifecycle`), not a run status. Degradation = `partial` plus `RunNotice` codes (`llm_fallback`, `quota_limited`). Quota waits = stage `degraded` + notice + auto-resume. | One state machine. |
| C61 | Stage statuses | 01 §6.6 (pending, running, succeeded, skipped, failed, blocked); 11 §3.2 (pending, running, succeeded, reused, degraded, failed, skipped, cancelled, interrupted) | — | 11's set. | — |
| C62 | Run and artifact tables | 01 §6.6–6.7 (`runs`, `run_stages`, `run_events`, `analysis_artifacts`) vs 11 §6 (`pipeline_runs`, `stage_runs`, `run_events`, `run_artifacts`, `active_result_sets`); 06 §6 (`analytics_reports`); 07 §6 (`recommendation_runs`) | Duplicate tables. | 11's tables. 06 reports and 07 run metrics are stored as `run_artifacts` / `stage_runs.counts`. Drop `analysis_artifacts`, `analytics_reports` and `recommendation_runs`. | Fewer tables. 11 already tracks `contains_api_data` and expiry. |
| C63 | Active-run constraint | 01 §6.6 (one per client × run_family); 11 §6 (one active run per client) | — | 11's (one per client), with dedupe rules from 11 §5.7. | Simplest. 01 §13 Q12 asks 11 to confirm. |
| C64 | Stage order (analysis) | 00 §4 (ingest → enrich → topics → analytics → news radar → recommend → refresh → finalize); 11 §5.6 (news parallel; write_cards split from recommend; snapshot_config, niche_discovery, own_analytics); 05 §13.7 (extra post-analytics topics step); 09 (refresh after news and recommend) | — | 11's DAG: `snapshot_config → {ingest ∥ niche_discovery ∥ news ∥ own_analytics} → enrich → topics → analytics → recommend → write_cards ∥ refresh → finalize`. Reject the second topics step: 07 calls `request_tiebreak` inside `recommend`. | Fewer stages. |
| C65 | Onboarding stage names | 03 §4.4 (resolve_identity → crawl_site → extract_profile → finalize_profile); 11 §5.6 (resolve_inputs → crawl_site → build_profile → {discover_competitors ∥ seed_topic_refs} + prefetch_own_channel → finish_onboarding) | — | 11's names. 03's `extract_profile` + `finalize_profile` are `build_profile` sub-steps. | 11 owns the DAG. |
| C66 | Event types | 11 §3.3 (`RunEventType` + payload kinds); 03 §3.2 (`crawl.started`, `profile.ready` …); 06 §4.4 (`analytics.baselines_done`); 10 P7 (`cards_written`, `cards_template_fallback`); 02 (`video_metadata_changed`); 13 §13.8 (`results.published`, `end`, `reconnect`) | — | 11's enum plus `results.published`. Component events become `stage.step` with a dotted `step` (e.g. `crawl.page`, `write.card`), or payload kinds. `video_metadata_changed` is an internal domain event (02→09), not a RunEvent. `end`/`reconnect` are SSE control frames, not persisted. | One stream schema. |
| C67 | Queues | 01 §5.7 (`gh:queue:default`, `ingest`, `cpu`); 11 §4.5 (`gh:interactive`, `gh:batch`, `gh:maintenance`); 03 §10 (`crawl`, `render`, `llm`); 12 §10 (`evaluation`) | Four schemes. | 11's three queues, plus per-resource semaphores (`sem:crawl:{domain}`, `sem:llm:{model}`) instead of per-resource queues. Backtests run on `gh:batch` with a kind concurrency of 1. | Fewer workers. Limits are already semaphores in 11. |
| C68 | Cron schedules | `retention_sweep`: 01 hourly at :17 vs 11 daily 03:30 UTC. Snapshots: 02 every 10 min vs 11 30 min. Daily refresh: 01 05:00 local vs 11 `refresh_hour_local` 6. Ops rollup: 11 hourly vs 12 nightly + 15-min alerts. News: 08 `news_poll` 15 min (global fetch) vs 11 `news_radar_tick` hourly (per client) | — | retention hourly (01). Snapshots every 10 min (02). Daily refresh at 06:00 local (11, user-editable). Ops: nightly rollup + 15-min alert cron (12). News: both (08 global fetch every 15 min; 11 per-client scan per C25 cadence). 11 registers every cron. | Owners set cadence. 11 only hosts. |
| C69 | "< 2 min per client" metric | 11 §5.15 (`reco_ms` from end of ingest to finalize, "agreed with 12"); 12 §5.4 (wall time of the daily_refresh run, first stage to finalize, excluding queue); 03 §13.8 (onboarding takes 3–8 min) | Not agreed. | 12's stricter definition: daily_refresh total wall time p95 <120 s, warm cache. Track onboarding separately (target p95 ≤8 min; time to first card reported). | Most rigorous reading of the assignment. |
| C70 | Crawl budget | 03 §5.16 (client: discovery 25, fetch 90, map 60; phase timeouts 60/150 s); 11 §5.6 (onboarding crawl soft 60 s, hard 120 s, 40 pages; recrawl 200 pages / 10 min) | — | 03's settings own page and time budgets. 11 enforces only the run wall time (600 s onboarding). | Owner of the crawl decides. |
| C71 | Daily-refresh LLM cache and cards | 11 §5.6 (cards rewritten only if the evidence hash changed); 10 §5.12 (result cache 7 d) | Consistent. | Keep. | — |

### 1.6 Storage, ids, auth, config

| # | Topic | Plans (refs) | Positions | Recommended resolution | Rationale |
|---|---|---|---|---|---|
| C72 | Table names for the shared YouTube cache | 01 §6.1 (`yt_channels`, `yt_videos`, `yt_video_snapshots`, `yt_comments`, `yt_search_cache` (params_hash), `yt_channel_resolutions`, `yt_quota_ledger`) vs 02 §6 (`yt_channel`, `yt_video`, `yt_video_snapshot`, `yt_comment`, `yt_comment_fetch`, `yt_search_cache` (query_key) + `yt_search_result`, `yt_channel_alias`, `yt_channel_snapshot`, `yt_snapshot_schedule`, `yt_http_etag`, `quota_ledger`, `quota_cache_hits_daily`) | Plural vs singular names, and different keys. | 01's plural names with 02's columns and extra tables. Snapshot PK (video_id, slot_at), with slot = 1h for idempotency (01). Search cache: 02's `query_key` + `yt_search_result`. | 01 owns migrations and naming. 02 owns semantics. |
| C73 | Derived-metric table names | 01 §6.1 (`yt_video_metrics`, `yt_channel_baselines` PK (channel, age_days), `yt_video_traits`, `yt_thumbnail_traits`) vs 06 §6 (`video_metrics`, `channel_baselines` per segment/method, `video_age_values`, `video_outliers`, `video_traits`, `thumbnail_traits`, `channel_recurring_text`, `comment_signals`) | Different keys. 01's baseline lacks segment and method. | 06's tables and keys, with 01's `yt_` prefix. | 06 owns the logic. |
| C74 | Recommendation and feedback tables | `recommendations`: 01 §6.7 vs 07 §6 (07 adds payload/draft/stable_key). `recommendation_feedback`: 01 (kind thumbs/dismiss/saved/made_it… + rubric) vs 07 (action done/irrelevant/save/snooze/more_like_this + reason) vs 11 §6 (rating up/down + reasons + status). Also 08 `news_feedback`, 09 `refresh_feedback`. 13 §13.3 proposes one FeedbackIn | Three to five feedback stores. | 07 owns `recommendations` (07 schema, 01 retention class) and a single `recommendation_feedback{client_id, recommendation_id, stable_key, user_id, status: saved\|dismissed\|done\|snoozed\|null, reason, linked_video_id, note}` (13 §13.3). News and refresh feedback use the same table through `recommendation_id`. The rubric goes to 12 `review_rating`. | One feedback loop. |
| C75 | Refresh tables | 01 §6.7 (`refresh_suggestions`) vs 09 §6 (`description_refresh`, `refresh_eligibility`, `refresh_impact`, `link_check_cache`, `refresh_feedback`) | — | 09's tables minus `refresh_feedback` (C74). Plural naming: `description_refreshes`. | 09 owns them. |
| C76 | News tables | 01 §6.2/§6.7 (`news_items`, `news_query_cache`, `news_signals`, `news_opportunities`) vs 08 §6 (11 tables) | — | 08's tables. Drop `news_query_cache` (→ `news_queries`/`news_query_runs`) and `news_opportunities` (they are `recommendations` rows). | 08 owns them. |
| C77 | OAuth / owner analytics tables | 01 §6.5 (`oauth_tokens`, `own_analytics_reports` jsonb rows) vs 02 §6 (`oauth_connection`, `own_analytics_daily`, `own_traffic_source`, `own_search_term`) | — | 01's `oauth_tokens` (encryption, viewer list, purge fields) plus 02's three typed analytics tables (they replace `own_analytics_reports`). | Security in 01, typed data in 02. |
| C78 | Embedding / text caches | 01 §6.2 (`embedding_cache` keyed by model + text_sha256); 05 §6 (`text_embeddings`, `ref_embeddings`, `video_prepared_text`); 08 (embedding float4[] on news_items, centroid); 03 (`vector(384) or float4[]`) | — | 05's tables (no pgvector; float32 bytea). 08 stores bytea too. Drop 01's `embedding_cache`. | 05 owns the Embedder. 01 §13 Q11 and 05 agree on no pgvector. |
| C79 | Boilerplate tables | 05 §6 `channel_boilerplate` (shared, ≤30 d); 09 §6 `channel_boilerplate` (line_hashes, 30 d) | Same name twice. | One table owned by 05. 09 consumes `PreparedVideoText.removed_spans`. | Duplicate. |
| C80 | Resolution caches | 01 `yt_channel_resolutions` (≤7 d); 02 `yt_channel_alias` (7 d, hard 30 d); 04 `brand_channel_resolutions` (global, 30 d, "02 should own") | Three caches. | Two: `yt_channel_resolutions` (02: URL/handle → id, 7 d) and `brand_channel_resolutions` (04: domain/name → id with ownership verdict, owned by 04, 30 d). | Different keys and purposes. One owner each. |
| C81 | Tracked-channel links | 01 (`competitors` + view `v_tracked_channels`); 02 (`client_channel`); 03 (`channel_links`); 04 (`competitor_channel_matches`, `competitor_sets`) | Four link tables. | 04 `competitor_sets` (versioned lists) plus `competitor_channel_matches`. The own channel lives on `clients.own_channel_id` + `own_channel_verification` (01). Drop 02 `client_channel`, 03 `channel_links` and 01 `competitors`. Keep the view `v_tracked_channels` (01) over these. | One source per relation. |
| C82 | LLM / external usage ledger | 01 §6.7 (`provider_usage_ledger`, 400 d); 10 §6 (`llm_calls` per attempt, 180 d; `llm_usage_daily` 400 d); 12 §13.6 (unified `external_usage`); 11 `UsageLedger` json | Three ledgers. | 01's `provider_usage_ledger` becomes the unified external-usage ledger (LLM, Tavily, crawl). Add 10's per-attempt columns plus `scope` and `cache_hit` (12). YouTube stays in `yt_quota_ledger` (02 semantics + `scope`, `cache_outcome`). | 12 needs one shape. |
| C83 | Evaluation tables | 01 §6.7 (`evaluation_label_sets`, `evaluation_labels` (label outlier/not/unsure), `evaluation_reports`) vs 12 §6 (14 `eval_*`/`label*`/`review_*` tables) | — | 12's tables. Drop 01's three. | 12 owns evaluation. |
| C84 | Ids | 01 (uuid, PG18 `uuidv7()`; API prefixes `rec_`, `run_`, `cl_`, `ws_`); 11 (`run_`+ULID text pk); 12 (UUID) | — | UUIDv7 primary keys everywhere (01). Prefixed string ids only at the API boundary (`run_…`). | Index-friendly and one type. |
| C85 | Auth roles / key format | 01 §3 (owner/admin/analyst/viewer; `ghk_<prefix12>_<secret43>`); 11 §12.1 (owner/editor/viewer; `gh_<env>_<id>.<secret>`) | — | Roles `owner \| editor \| viewer`. Key format `ghk_<env>_<prefix12>_<secret43>` (01 owns auth). **OWNER** (whether viewers may save or dismiss: proposed no). | Simplest. 13 §13.5 asks. |
| C86 | API paths for eval / ops | 11 §12.1 (`/api/v1/evaluation/*`, `/api/v1/ops/*`) vs 12 §G (`/api/eval/*`, `/api/ops/*`); label and review payload shapes differ | — | 11's paths. Payloads are 12's objects (`Label`, `ReviewRating` with `would_ship`; pass rule overall ≥4.0 and each criterion ≥3.5). | 11 owns the API. 12 owns the semantics. |
| C87 | Env var naming | 01 §3 (`GH_` prefix, `__` nesting; also `DATABASE_URL`); 02 (`YT_`); 03 (`WEBINTEL_`); 11 (`PIPELINE__…`); 10 (`ANTHROPIC_API_KEY`, `LLM_FALLBACK_*`, `LLM_ENABLED`); 12 (`OTEL_ENABLED`, `EVAL_BUNDLE_DIR`); existing `.env.example` (`YOUTUBE_API_KEY`, `ANTHROPIC_API_KEY`, `TAVILY_API_KEY`, `GOOGLE_OAUTH_*`, `NCBI_API_KEY`, `DATABASE_URL`, `REDIS_URL`) | Six schemes. | One `Settings` root (01). Sections are `GH_<SECTION>__<KEY>` (e.g. `GH_PIPELINE__BUDGETS__ONBOARDING`). Provider secrets and infra URLs keep the existing `.env.example` names through `validation_alias`. Add `LLM_FALLBACK_*`, `OPENFDA_API_KEY` and `EVAL_BUNDLE_DIR` to `.env.example`. | Matches the existing file. One loader. |
| C88 | Client lifecycle | 01 §6.4 `clients.status` (onboarding/awaiting_confirmation/active/paused/archived/deleting) vs 11 §3.2 `ClientLifecycle` (created/onboarding/onboarding_failed/awaiting_confirmation/analyzing/active/paused/deleting) | — | 11's enum plus `archived`, stored in 01's `clients.lifecycle`, driven by 11. | — |
| C89 | Client timezone source | 01 §5.8 (03 infers it); 11 §3.4 (user input `market.timezone`); 06 §5.8.4 (fallback from country) | — | User input (11), pre-filled from 03 inference, with 06's fallback. | The user can correct it. |
| C90 | Ingest field names (02 vs 01 columns) | `duration_s` vs `duration_seconds`; `subscriber_count_hidden` vs `hidden_subscriber_count`; `keywords` vs `branding_keywords`; availability `available/missing/removed` vs `available/unavailable`; `comments_state` vs `comments_disabled`; `localized` vs `localizations` (09) | — | `duration_s`, `hidden_subscriber_count` (API name), `keywords`, `availability: available\|missing\|removed`, `comments_state`, `localizations`. | Domain names win. Columns follow. |

### 1.7 Dependency versions

| # | Package | Plans | Positions (current pyproject → asks) | Recommended |
|---|---|---|---|---|
| C91 | anthropic | pyproject `>=0.70`; 10 P4 `>=1,<3` (major chosen in T1); 00 §3 says SDK 1.x | Open major version. | `anthropic>=1,<2`. Moving to 2.x (API version 2026-10-21) is a later, deliberate change. |
| C92 | tavily-python | pyproject `>=0.5`; 08 `>=0.8.5`; 03 calls REST directly | 03 skips the SDK. | `tavily-python>=0.8.5`, used through the single `WebSearch` adapter (G1). 03 uses the SDK. |
| C93 | OCR | pyproject `rapidocr-onnxruntime>=1.4`; 06 §13.6 `rapidocr>=3.10` + `onnxruntime` + `opencv-python-headless` | — | 06's (the legacy package caps Python <3.13). |
| C94 | selectolax / trafilatura | pyproject `>=0.3` / `>=2.0`; 03 `>=1.0` / `2.3.1` | — | 03's. |
| C95 | fastembed / rapidfuzz | pyproject `>=0.4` / `>=3.10`; 05 0.9.1 / 3.14; 07 rapidfuzz `>=3.10` | — | `fastembed>=0.9.1,<0.10`, `rapidfuzz>=3.14`. |
| C96 | Embedding model | 05 §D (paraphrase-multilingual-MiniLM-L12-v2 384-d; potion for other Indic scripts); 08 §G (bge-small-en-v1.5 / e5-small); 03 ("same as 05") | — | 05's router for everything, including news. | One vector space and cache. |
| C97 | Language detection | 03 `py3langid`; 08 `lingua-language-detector` or `fast-langdetect`; 09 `lingua` or fastText lid.176 | Three libraries. | `py3langid` (small, offline) for all, with 05's script profiler for Hinglish. | One dependency. |
| C98 | sse-starlette / arq / fastapi | pyproject `sse-starlette>=2.1`, `arq>=0.26`, `fastapi>=0.115`; 11 `>=3.0,<4`, `>=0.28,<0.29`, ok | — | 11's pins. arq stays behind the `JobQueue` port (maintenance-only upstream). | — |
| C99 | New deps | argon2-cffi (01); protego 0.7.0, tldextract, pypdf, phonenumbers (03); regex (05/06/09); tzdata, opencv-python-headless (06); feedparser, python-dateutil (08); Jinja2 (12); hypothesis, fakeredis (tests); authlib vs google-auth-oauthlib (02) | — | Accept all. OAuth uses `google-auth-oauthlib` (already pinned). | Use what is already pinned. |
| C100 | PostgreSQL / pgvector | 01 Q9 (PG ≥18); 05/01 (no pgvector); 08 (pgvector above 50k items/week) | — | PG 18 required. No pgvector until the 08 threshold is reached. | — |

### 1.8 Ports

| # | Port | Plans | Conflict | Recommended |
|---|---|---|---|---|
| C101 | YouTubeGateway methods | 02 §4 (`channels_by_id`, `videos_by_id`, `uploads_page`, `search(SearchQuery)`, `video_stats`, `most_popular`, `playlist_owner`) vs 04 §4.2/§13.6 (`channels_by_ids`, `videos_by_ids`, `recent_uploads`, `search_channels`, `niche_search`, `playlists_by_ids`); 08 (`search_videos`, `videos_list`); 11 (`QuotaManager.reserve/remaining_today`, `SnapshotScheduler.due_batch`) | Name mismatches. | 02's names are canonical. Add convenience wrappers in services, not in the port: `recent_uploads` (IngestService) and `niche_search` (CachedSearchService). Remove `most_popular` (C3). 02 exposes `QuotaManager.reserve(bucket, units, ctx)`, `remaining(bucket)` and `SnapshotScheduler.due_batch(limit)`. |
| C102 | LLMClient | 10 §G (`generate_structured(task, schema, system, input, context, tier, budget)`, `generate_text`); 04 §B (`structured(prompt_id, schema, tier, inputs)`); 07 (`write_cards(briefs)`) | — | 10's signatures. Writing goes through `CardWritingService.write(packs)`. |
| C103 | Clock | 01 §4.2 (`Clock`); 12 §13.5 (proposes `Clock`); 06 (`AgeClock`); 07 §13.9 (`as_of` everywhere) | Already exists. | 01's `Clock`. `RunContext.clock`/`as_of`. 06's `AgeClock` is a helper on top of it. |
| C104 | Embedder | 05 (specifies + adapter) vs 01 (container entry) | — | 05 owns the Protocol and adapter. 01 only wires it. |

---

## 2. Gaps

### 2.1 Responsibilities nobody owns

| # | Gap | Who needs it | Proposed owner |
|---|---|---|---|
| G1 | `WebSearch` port + Tavily adapter (search, extract, map, crawl; caching and credit metering). 00 lists the port, but no plan defines it. 03 calls REST, 04 consumes it, 08 builds its own Tavily sources. | 03, 04, 08 | 03 (`adapters/search/tavily.py`). 08's `TavilyNewsSource` wraps it. |
| G2 | Section L derived-metrics audit application and policy paragraph for 00 §2. | All | 12 (ops task). The owner signs. |
| G3 | Retention rule registration across owners (01 sweeper exists; 02, 05, 06, 07, 08, 09, 10, 11 and 12 each have tables). | All | Each owner registers a `RetentionRule` with 01's `retention_registry`. 01 CI fails on any API-data table without a rule. |
| G4 | Ingest of a "lightweight baseline" for channels seen only in news demand checks or creator candidates (08 §5.10 ≈2 units/channel; 07 §5.5 last 50 uploads for ~40 creators). | 07, 08 | 02 `IngestService.ensure_baseline(channel_ids, priority=P3)`. |
| G5 | Owner-confirmation UX, privacy policy, ToS link, revoke UI, ownership attestation checkbox (03 §13.3, 09 §13.11, 01 §9). | 01, 03, 09 | 13 (UI) + 11 (endpoints). |
| G6 | Auth endpoints (`/auth/*`, `GET /me`, workspace switch). | 13 §13.6 | 11 (API) on 01's auth model. |
| G7 | `GET /clients/{id}/brief` and since-previous-run counts. | 13 §13.1, §13.20 | 11. Defer the brief: 13 composes it client-side. 11 adds `prev_run` counts to the envelope. |
| G8 | API surface for `InsufficiencyNotice`, `WhiteSpaceNote` and `suppressed_counts`. | 13 §13.7 | 11 puts them in the recommendations `ResultEnvelope`. |
| G9 | Demo channel onboarding and the freeze schedule (snapshots must start before Week 2 day 1). | 12 §13.8 | Owner + 12. **OWNER** |
| G10 | Captions / transcripts (`CaptionTrack`, 09 §3.1; `missing_chapters` uses a transcript). 02 defers captions. | 09 | Nobody for now. 09 drops the transcript-based paths (L2 chapters off) until 02 adds OAuth captions. |
| G11 | Website analytics (GA/GSC) "side by side" lens. | 00 §2, 03 §13.14 | Deferred: a future component and port. |
| G12 | Spotlit AI-answer tracking. | 01, 03, 05, 07, 11, 12 | Deferred. Only the `BuyerQuestionSource` stub (03) and the `ai_cited_video_ids` field are kept. |
| G13 | Notifier (email) port. | 13 §13.17 | Deferred (11/01). |
| G14 | IndustryCode table and the source-pack YAML registry. | 03, 04, 08, 09 | 03 owns IndustryCode. 08 owns source packs. |

### 2.2 Interface gaps (consumer expects, producer doesn't provide)

| # | Consumer (ref) | Field / method expected | Producer | Fix |
|---|---|---|---|---|
| I1 | 05 §3.1, 06 §3.1, 07 §3.1, 08 §3.1, 09 §3.1, 04 §3.2 | BusinessProfile: `offerings[].aliases`, `offerings[].definition`, `offerings[].priority` (04), `brand_aliases` (04/07/08), `website_domains[]` (06/07), `primary_markets` (04), `business_model` b2c/b2b enum (04, which differs from 03's), `primary_languages[]` (05), `timezone`/`country` (06), `competitor_hints[].url` (04), profile `completeness` (04/11), embedded `buyer_questions[]` (05/07; 03 has ids only), `value_props` (07) vs `value_propositions`, `domains` (07) vs `website_domain` | 03 | 03 adds aliases, definition, priority, `identity.brand_aliases`, `identity.website_domains`, `identity.primary_markets`, `identity.country`, `identity.timezone`, `quality.completeness` and `competitor_hints[].url`. Consumers use 03's names (`value_propositions`, `website_domains`, `languages`) and load BuyerQuestions by id. 04 uses 03's `business_model` enum. |
| I2 | 05 §5.2 | doctor/centre names in `brand_terms` | 03 (excludes person data) | Centre names: yes (`kind=sub_brand`). Doctor names: no (person data). 05 drops that expectation. |
| I3 | 08 §3.1, 09 §3.1 | FreshnessProfile: `rationale`, `source_packs`, `regions`, `languages`, `excluded_topics`, `regulated_domain`, `domain_kind`, `evergreen_expected`, `WatchEntity.importance`, `SeasonalWindow.query_terms` | 03 | 03 adds them (see §4). |
| I4 | 06 §5.8.3 | `BuyerQuestion.core_terms[]`, `TopicMatch.matched_terms[]` | 05 (`core_entity_terms` on TopicRef; terms only in `evidence`) | 05 exposes `TopicRef.core_entity_terms` (06 renames) and a derived `TopicMatch.matched_terms`. |
| I5 | 07 §13.4 | TopicRef↔TopicRef similarity | 05 (not in facade) | 05 adds `similar_refs(client_id, ref_id, k)` from cached ref embeddings. |
| I6 | 07 §5.4, §13.4 / 05 §13.7 | `request_tiebreak`: signature mismatch (`(video_ids, bq_ref_id)` vs `(client_id, pairs)`); `prioritise_review` undefined | 05 | `request_tiebreak(client_id, pairs)`, plus `prioritise_review(client_id, video_ids)` added to the facade. |
| I7 | 05, 06, 07, 09, 12 | VideoRecord: `content_hash` (05; 02 has only `title_hash`), `description_hash` (09), `live_broadcast_content` (06/07/09; 02 only has `format`), `live_actual_start_at`/`end_at`/`scheduled_start_at` (06; 02 only has `effective_published_at`), `thumbnail_url` (07/12; 02 has `thumbnails{}`), `view_count`/`like_count`/`comment_count` (06; 02 has `latest`), `is_available` (06) | 02 | 02 adds the hashes, the raw live fields, `thumbnail_url` (best key) and flattened latest counts. 06 uses `availability`. |
| I8 | 04 §3.3, 07 §3.1 | ChannelRecord `uploads_90d`, `last_upload_at`, `recent_upload_ids`, `made_for_kids` | 02 | 02 computes them from the uploads cache. |
| I9 | 06 §5.9 | CommentRecord `author_channel_id`, `visible_reply_author_channel_ids` (owner-reply detection) | 02 (stores no author fields) | 02 stores `is_channel_owner_reply` (01 column) and `owner_replied: bool` per thread. No author ids. |
| I10 | 06 §5.3 | `OwnAnalytics.daily_views[video][date_pt]`, `creator_content_type` | 02 (`OwnVideoDayViews`, `OwnVideoMetrics.creator_content_type`) | Already present under different names. 06 adapts. |
| I11 | 07 §13.2 | Per-video `outlier_ratio`, `comparison_age_days`, method, `channel_baseline_views_at_age`, `baseline_n` for **every** video; `format_key`; per-format baselines; TraitLift `ci_low`/`ci_high`/`supporting_video_ids`; OwnPerformanceReport `by_format`/`by_trait` with n and usage share | 06 | 06 already provides most via `VideoMetrics.outlier: OutlierAssessment` (required for every eligible video) and `TraitLift` (`lift_ci`, `reference_video_ids`). 06 adds `format_key`, `OwnPerformanceReport.by_trait{key: (median_ratio, n, usage_share)}` and renames `by_segment`/`by_length_band` → `by_format`. 07 adapts the TraitLift names. |
| I12 | 07 §3.1 ChannelScorecard | `role`, `gap_days_cv`, `median_outlier_ratio`, `median_views_at_age` | 06 (`gap_cv`, `median_ratio`, per-segment tuple) | 07 adapts to 06's names. 06 adds `role`. |
| I13 | 08 §3.1 | `ViewsAtAge.baseline_at_age(channel_id, age_hours)`; `views_at_day7`, `client_baseline_day7` | 06 (no such method) | 06 exposes `BaselineStore.get(channel_id, segment, age_days, as_of)`. 08 works in days. |
| I14 | 04 §3.3, 07 §3.1, 08 §3.1, 06 §3.1 | CompetitorCandidate `entity_kind` (07), `brand_aliases`/`domains` (07), `aliases`/`domain` (08), `website_domains[]` (06), `channel_id` (08; 04 has `channels[]`) | 04 (`owner_kind`, `aliases`, `official_domain`, `channels[]`) | Canonical names: `aliases`, `official_domain`, `owner_kind` (organisation/individual/unknown; 07's `entity_kind` = owner_kind + relation), `primary_channel_id` (derived). |
| I15 | 09 §3.1 | NewsSignal entity, aliases, entity_kind, is_competitor_entity, topic_ref_ids, first_seen_at, event_date, sources[], verification status, superseded_by, demand_videos | 08 | 08 adds them (see §4). |
| I16 | 10 P2 | Draft `facts`, `forbidden_titles`, `allowed_dates`, `entities`, fact `priority`; accept `critic_flags` | 07 | 07 adds `Reason.statement` and `numbers`, plus `forbidden_titles` (own window titles and ref titles), `allowed_dates`, `entities`, `critic_flags` (C46). |
| I17 | 12 §3.1 | Draft `match_spec`, numeric expected range, per-gate feature values | 07 | 07 adds `match_spec: MatchSpec`, keeps `extras.expected_range.low/high` and stores `gate_results[{gate, action, features}]`. |
| I18 | 12 §3.1 | Ledgers: `scope` (shared/client/evaluation), `cache_hit`, `cache_creation_input_tokens`, `run_kind`, `error_type`, `queue_wait_ms` | 02, 10, 11 | Add `scope`/`cache_outcome` to `yt_quota_ledger` and the unified ledger (C82). 11 exposes `kind`, `error.code`, `timings.queued_ms`, and 12 adapts. 11 `UsageLedger` adds `llm_cache_write_tokens`. |
| I19 | 12 §4 | Repository Protocols swappable for `Frozen*Repository` and `as_of`-aware reads | 01 | 01's `TenantRepository` gains `as_of` filters (`fetched_at`/`captured_at ≤ as_of`). The Protocols are published in `ports/`. |
| I20 | 13 §13.2, §13.4 | Rich `reference_videos` (`thumbnail_url`, `comparison_age_days`, `views_at_age`, method, `channel_role`, `channel_baseline_views_at_age`, `is_outlier`); `confidence.caps_applied`; range basis/n; `rendered_by`; rich CompetitorView | 11 DTOs | 11's `RecommendationOut` mirrors 07's contract + `extras`. `CompetitorView` mirrors 04's `CompetitorCandidate` (with no score, C4). |
| I21 | 10 §B | Writer path for drafts with refs=[] (hygiene) | 07/09 | Not needed after C19: hygiene items bypass the writer (template copy). |
| I22 | 11 §13.9 | `ChannelResolver.ownership_evidence` | 02 | Exposed via `ChannelResolution.ownership.signals`. |
| I23 | 03 §5.2 | Upload descriptions at onboarding ("02 fetches anyway in ingest, so zero cost"). Ingest runs in the analysis run, after onboarding. | 02/11 | 11's `prefetch_own_channel` stage fetches the last 50 uploads (≈2 units). 03 reads them from the cache. |
| I24 | 09 §3.1 | `VideoMetrics.velocity_recent` over a ≥7-day span; `velocity_basis` snapshots/lifetime_avg; `ChannelBaseline` median velocity per age band | 06 (`velocity_recent` uses a 20–30h pair; baselines are views_at_age, like_rate, engagement_rate, velocity) | 06 adds `velocity_7d` (latest pair spanning ≥7 d) and a `velocity` baseline per age band (κ bands). 09 uses `velocity_basis = snapshots \| lifetime_avg_provisional` (09's enum). |

---

## 3. Cross-cutting decisions (deduplicated proposals to change shared context)

Legend: **A** = accept, **R** = reject, **D** = defer. **OWNER** = needs human-owner sign-off.

### 3.1 Policy and compliance

| Id | Proposal (sources) | Decision | Reason | Sign-off |
|---|---|---|---|---|
| D-POL-1 | Fix search.list to 1 unit / 100-calls bucket (02 §13.1, 04 §13.1, 08 §13.3, 11 §13.1, 12 §13.1) | A | Settled S1. | — |
| D-POL-2 | Add batchGetStats to the vocabulary; run snapshots on it (02 §13.2) | A | Settled S2. | — |
| D-POL-3 | One GCP project, no key pools (02 §13.5) | A | Settled S5. | — |
| D-POL-4 | mostPopular off; trending = niche search + velocity (02 §13.7) | A | Settled S4. Removes 06, 12 and 13 uses. | — |
| D-POL-5 | Derived-metrics paragraph + Section L audit application + storage-mode flag (01 Q1, 06 §13.2, 12 §13.2/§13.3, 02 §13.4, 05 §13.3, 09 §13.8) | A (text in C5) | One interpretation. Gives a path to 36-month storage. | OWNER |
| D-POL-6 | No composite channel scores or rankings; single-metric sorted lists only (01 Q2, 07 §13.7(b), 04 §13.7 lexicographic) | A | Settled S3. Affects 04, 06, 07, 11, 13 (C4). | OWNER |
| D-POL-7 | "No blended number displayed; every displayed figure single-source and labelled" (12 §13.13, 08 §13.10, 09 §13.9) | A. Composites that mix YouTube with non-YouTube data (08 priority, 09 priority) become lexicographic ordering. 07's internal recommendation ordering may stay a composite (it ranks cards, not channels; never displayed). | 00 §2 forbids merging sources into one metric. | OWNER |
| D-POL-8 | III.E.2 cross-owner aggregation flagged; keep per-channel numbers beside aggregates; never aggregate Authorized data across clients (01 Q3, 02 §13.4, 06 §13.3) | A (flag for legal) | Low cost mitigation. | OWNER |
| D-POL-9 | Label derived numbers "calculated by GrowthHacker from public YouTube data" (06 §13.3, 07 §13.7(a), 05 §13.3) | A | Transparency. | — |
| D-POL-10 | Retention wording: act at 28 d, fail closed, derived artifacts expire via `api_data_observed_at`/`expires_at` (01 Q6, 11 §13.6) | A | C27. | — |
| D-POL-11 | Bare ids and booleans beyond 30 d (`produced_video_id`, labels, overrides, outcomes) (01 Q8, 05 §13.11, 07 §13.11, 12 §13.11, 04 §13.8) | A, with a one-rule compromise: ids stored >30 d must be re-verified (`videos.list` id-only, 1 unit/50) within each 30-day window or nulled. Aggregates and booleans with no API values may be kept. | Ids are API data under a strict reading. Re-verification is cheap. | OWNER |
| D-POL-12 | Comments: delete-only at 28 d, PII-redacted, no author identity; excerpts off for healthcare/finance (01 Q7, 02 §13.12, 06 §13.11) | A | Privacy. DPDP. | — |
| D-POL-13 | Authorized-data visibility limited to the authorizing user + approved members (01 Q4) | A | Policy-safe. | OWNER |
| D-POL-14 | No `topicDetails`; drop `categoryId` (02 §13.8, 05 §13.5) | A | C10. | — |
| D-POL-15 | LLM classification restricted to client-provided ids with verified quotes is allowed in topic matching and disclosed (05 §13.2); "deterministic before recommender" wording covers evidence-bound LLM extraction (03 §13.10) | A | Matches the hybrid topic decision. | OWNER (design-doc wording) |
| D-POL-16 | Crawler UA: honest bot UA; compat browser UA after a WAF 403 (03 §13.2) | Accept the honest UA. **Reject the compat UA.** | Spoofing a browser UA is the least policy-safe option. | OWNER |
| D-POL-17 | Ownership-attestation checkbox gating Tavily fallbacks (03 §13.3) | A | Audit trail. | OWNER |
| D-POL-18 | Tavily robots compliance: send only URLs our robots check allows (03 §13.13) | A | — | — |
| D-POL-19 | Healthcare news cards `review_required=true` by default (08 §13.12); regulated refresh forces review (09 §5.7) | A | Medical-claims risk. | OWNER |
| D-POL-20 | Legal: Tavily cross-tenant caching, PIB/HF terms, WHO metadata-only, DMR Act, NCBI disclaimer, NMC/CCPA (08 §13.9, 09 §13.13) | D (legal review) | Not blocking the build. | OWNER |
| D-POL-21 | `views_without_engagement` wording (e.g. "possibly promoted") (06 §13.12) | A only as an internal suppression. The UI says "unusually low engagement for its views". | Never implies ads. | OWNER |
| D-POL-22 | `owner_kind` (organisation/individual) classification of channel owners (04 §13.11) | A (not an audience inference) | Needed for creator routing. | — |
| D-POL-23 | API-derived `brandingSettings.keywords` provenance-tagged and purged in 30 d unless user-confirmed (03 §13.9) | A | 30-day rule. | — |
| D-POL-24 | Thumbnail hotlink only, never proxied or cached; no API data in persistent browser storage (13 §13.12–13) | A | Policy-safe. | — |

### 3.2 Contract and domain

| Id | Proposal (sources) | Decision | Reason | Sign-off |
|---|---|---|---|---|
| D-CON-1 | `views_day7` nullable + `views_at_age{age_days, views, method, label}`; comparison age per window (02 §13.3, 06 §13.1, 07 §13.5, 08 §13.1) | A (C30) | 30-day rule. | OWNER (contract change) |
| D-CON-2 | `channel` = title + `channel_id`, `url`, `title` on references (07 §13.5) | A | — | — |
| D-CON-3 | `expected_range_vs_baseline` nullable string + `extras.expected_range` (07 §13.5, 12 §3.1) | A (C21) | — | — |
| D-CON-4 | `extras` object + `contract_version` (07 §13.5) vs top-level optional fields (08 §13.1) vs `details` (09 §13.1) | Accept 07. Reject 08's and 09's placement. Their fields go under `extras.news` / `extras.refresh`. | C31. | — |
| D-CON-5 | Confidence `basis` (first_mover/rule_based) instead of new labels (08 §13.1, 09 §13.2) | A (C20) | — | — |
| D-CON-6 | First-mover news lane and hygiene list outside recommendations (08 §13.2, 09 §13.2, 13 §13.9) | A (C19) | Keeps the ≥3-refs bar. | OWNER |
| D-CON-7 | News minimum: 2 YouTube videos + 2 sources (07 §13.6) | R for main cards (≥3). Use ≥2 independent sources for the early-signal lane. | — | — |
| D-CON-8 | Stable `rec_` id from `stable_key` (07 §5.14, 11 §13.8) | A | C32. | — |
| D-CON-9 | Description limit is 5000 **bytes** (09 §13.4) | A | — | — |
| D-CON-10 | `pattern_spec` on drafts (12 §13.4) | A, renamed `match_spec` | C45. | — |
| D-CON-11 | Typed evidence on drafts (10 P2) | A via extended `Reason` (C46) | — | — |
| D-CON-12 | Baseline wording "own-channel median at the same age (mean shown for reference)" (06 §13.4) | A | Median is more robust. | OWNER (wording) |
| D-CON-13 | Shorts = short-form ≤180 s from 2024-10-15 / ≤60 s before; aspect cross-check; no UUSH by default; premieres treated as live (02 §13.6, 06 §13.5) | A | C24. | — |
| D-CON-14 | View-counting regime dates in shared config (02 §13.11) | A | Backtests and velocity need them. | — |
| D-CON-15 | 06 low-engagement flag (02 §13.10) | A (exists) | — | — |
| D-CON-16 | White space as its own panel, not a card (07 §13.13) | A | — | — |
| D-CON-17 | Hashtag wording: don't promise placement (09 §13.3) | A | — | — |
| D-CON-18 | Proxy evidence lowers confidence one level; exploratory lifts never High (06 §13.10) | A | Honest confidence. | — |
| D-CON-19 | News-entity TopicRefs anchored to a confirmed offering/keyword with TTL (05 §13.8, 08 §13.11) | A | — | — |
| D-CON-20 | Keyword suggestions from unmatched videos default off (05 §13.9) | A (off) | — | OWNER |
| D-CON-21 | Machine-translated insertions (09 §13.12) | R for regulated domains. D for others. | — | — |
| D-CON-22 | L3 claims unlocked by an owner checkbox (09 §13.11) | D | Not needed for the demo. | — |
| D-CON-23 | `allow_search_calls` for 09 (09 §13.15) | R (keep 0; use 08's demand checks) | Search is the binding constraint. | — |
| D-CON-24 | Ingest 90 d of competitor uploads (07 §13.8); `paidProductPlacementDetails` in every videos.list (07 §13.1) | A | 0 extra units. | — |
| D-CON-25 | Benchmarks ingested by default (≤3, labelled); public institutions never auto-confirmed (04 §13.12) | A | — | — |
| D-CON-26 | CompetitorSet with four lists; 3–10 applies to competitors only; per-client `auto_confirm` (04 §13.5) | A | — | — |
| D-CON-27 | `buyer_questions` as its own table (03 §13.5); IndustryCode owned by 03 (03 §13.11) | A | — | — |
| D-CON-28 | Sister brands excluded from competitors (03 §13.7) | A | — | — |

### 3.3 Vocabulary additions (00 §5)

| Id | Proposal | Decision |
|---|---|---|
| D-VOC-1 | 01: TenantScope, DataClass, ArtifactEnvelope, EffectiveConfig (Q5) | A. Drop ArtifactEnvelope (use 11 `ArtifactKey`). |
| D-VOC-2 | 03: CrawlRun, CrawledPage, PageFacts, Evidence, ClaimMeta, ChannelLink, RelatedBrand, ProfileDiff, RefreshPolicy, WatchEntity, StatedAudience, PriceSignal, Location, IndustryCode (§13.6) | A for cross-boundary names: Evidence, ClaimMeta, CrawledPage, RefreshPolicy, WatchEntity, IndustryCode, ProfileDiff. Keep the rest 03-internal. |
| D-VOC-3 | 04: CompetitorSet, ChannelMatch, OwnershipVerdict, OwnershipSignal, CandidateSource, BusinessRelevance, YouTubeRelevance, DriftEvent (§13.9) | A: CompetitorSet, OwnershipVerdict (unified, C38), BusinessRelevance, DriftEvent. YouTubeRelevance stays internal and has no `score` (C4). |
| D-VOC-4 | 06: ConversionLens, OutlierRecord, GapCell, AnalyticsResult, proxy-validation stats → 12 (§13.13) | A |
| D-VOC-5 | 08: NewsStory, NewsJudgement, GroundedEntity, CredibilityAssessment, DemandCheck, EarlyVideo, SafetyDecision, NewsOpportunityDraft, SourceTier, EventType (§13.6) | A except `NewsOpportunityDraft`, which becomes `NewsSubject` (C47). |
| D-VOC-6 | 10: EvidenceFact, EvidenceNumber, EvidencePack, CardCopy (P2) | A: EvidenceNumber, EvidencePack, CardCopy. EvidenceFact merges into `Reason`. |
| D-VOC-7 | 12: PatternSpec (§13.4) | A as `MatchSpec`. |
| D-VOC-8 | 05 TopicUniverse, AnswerDirectness, VideoTopicAssignment | A (consumed by 06, 07, 12). |

### 3.4 Architecture, infra, models

| Id | Proposal (sources) | Decision | Reason | Sign-off |
|---|---|---|---|---|
| D-ARC-1 | Run kinds + `partial` + ClientLifecycle (11 §13.3) | A | C59–C60, C88. | — |
| D-ARC-2 | DAG naming and parallel news; write_cards separate (11 §13.4) | A | C64. | — |
| D-ARC-3 | Second post-analytics topics step (05 §13.7) | R. Tie-break runs inside `recommend`. | Fewer stages. | — |
| D-ARC-4 | Compute all 3 windows deterministically; LLM for the chosen window only; 06/07 accept `windows: list[int]` (11 §13.5) | A | Cost. | — |
| D-ARC-5 | Evaluation run kind + queue (12 §13.7) | R for the kind (use `backtest`) and the queue (use `gh:batch`, concurrency 1). A for the quota scope `evaluation` (capped). | C59, C67. | — |
| D-ARC-6 | DB-authoritative quota ledger (11 §13.7) | R. Redis counters stay authoritative for admission. The DB ledger is the audit trail and rebuilds counters after a Redis loss (02 design). | Latency. Same safety. | — |
| D-ARC-7 | Clock port; `as_of` everywhere; no wall clock (12 §13.5, 07 §13.9, 01 §4.2) | A (01's `Clock`) | Backtests. | — |
| D-ARC-8 | Shared ports Clock, LockManager, SecretCipher, UnitOfWork (01 Q5) | A | — | — |
| D-ARC-9 | Unified external-usage ledger (12 §13.6) | A via 01 `provider_usage_ledger` (C82) | — | — |
| D-ARC-10 | arq is maintenance-only; keep it behind `JobQueue` (11 §13.2) | A | — | OWNER (aware) |
| D-ARC-11 | PG ≥18 required (01 Q9) | A | — | — |
| D-ARC-12 | No pgvector; numpy similarity (01 Q11, 05, 08 §13.8) | A until >50k news items/week | — | — |
| D-ARC-13 | PITR backups ≤14 d (01 Q13) | A | Within the 30-day rule. | OWNER |
| D-ARC-14 | argon2-cffi passwords vs Google Sign-In only (01 Q10) | A argon2 + API keys now. Google Sign-In later. | Smallest scope. | OWNER |
| D-ARC-15 | Prod infra: Redis AOF, nginx `proxy_buffering off` for events, PgBouncer txn mode with `statement_cache_size=0`, one origin `/api/*` → FastAPI (11 §13.12, 13 §13.11) | A | — | — |
| D-ARC-16 | Embedder port specified by 05; Docker bakes models (05 §13.6) | A | — | — |
| D-ARC-17 | Spotlit stub + optional `ai_citations` stage (03 §13.12, 05 §13.12, 11 §13.14) | D (stub only) | No access. | — |
| D-ARC-18 | Website analytics component later (03 §13.14) | D | — | — |
| D-ARC-19 | SiteCrawler exposes raw HTML, outbound links, JSON-LD, `blocked` (04 §13.3) | A | — | — |
| D-ARC-20 | 02 owns `brand_channel_resolutions` (04 §13.6) | R. 04 owns it (C80). | Different semantics. | — |
| D-ARC-21 | `link_check_cache` moves to 03 (09 §13.14) | R. Keep it in 09 (its only consumer). | — | — |
| D-ARC-22 | Ops report shows direct and amortised units across all buckets + Analytics requests (02 §13.13); LLM ops fields (10 P8) | A | — | — |
| D-ARC-23 | OAuth readiness: sensitive-scope verification, Testing-mode 7-day tokens, captions deferred (02 §13.9) | A (build-order note) | — | OWNER |
| D-ARC-24 | Demo timeline is a hard dependency (12 §13.8) | A | Backtest needs post-T outcomes. | OWNER |
| D-ARC-25 | Demo clients: dental group, larger home-services brand, Runable's official channel (12 §13.10) | D (owner to choose) | `@AspenDental` didn't resolve; Mr Rooter has 17 videos; Runable has 0. | OWNER |
| D-ARC-26 | 13 shared-context corrections on UI kits (Zeus primary, BoardUI free tier only, rareui.com licence, libraries.dev effects only) (13 §13.10) | A | Verified by 13. | OWNER |
| D-ARC-27 | Number locale: compact K/M everywhere, lakh/crore in tooltips (13 §13.15) | A | — | OWNER |
| D-ARC-28 | Indic script subsets for the demo (13 §13.14) | D (Devanagari now) | — | — |
| D-ARC-29 | Latency scope: <2 min = daily refresh total; onboarding tracked separately (03 §13.8, 11 §5.15, 12 §5.4) | A (C69) | — | — |

### 3.5 LLM

| Id | Proposal (sources) | Decision | Reason | Sign-off |
|---|---|---|---|---|
| D-LLM-1 | haiku-5-5 as default low tier (03 §13.1, 04 §13.2, 08 §13.4, 09 §13.10, 12 §13.9, 07 §13.10 for cards) | R. Ship it as a config option. Evaluate with 10's golden suite. | Settled S6. | — |
| D-LLM-2 | Tier definitions in 00 §3: low = haiku-4-5 no thinking; medium = sonnet-5-5 at effort low; high = sonnet-5-5 at effort medium; no sampling params; no forced tool_choice (10 P3) | A | C7, C8. | — |
| D-LLM-3 | Complete routing table (C53). Tasks: website_page_selection (low), website_page_map (low), website_profile_reduce (high, 16k), competitor_candidate_generation (medium), competitor_relation_classification (medium), competitor_entity_extraction (low), competitor_ownership_adjudication (low), competitor_evidence_summary (low), topic_adjudication (low), topic_alias_proposal (low), directness_tiebreak (low), news_judgement (medium), refresh_phrasing (low), card_write (medium), card_critique (low), research_query_planning (low) | A | One router. | — |
| D-LLM-4 | Pin anthropic SDK major (10 P4) | A: `>=1,<2` | 00 §3 names 1.x. | — |
| D-LLM-5 | `.env.example` LLM fallback keys (10 P5) | A | — | — |
| D-LLM-6 | Retention for `llm_result_cache` ≤30 d, debug payloads 7 d (10 P6) | A | — | — |
| D-LLM-7 | Writer deadline 45 s; `completed_degraded` (10 P7) | Accept the deadline. Degradation is `partial` + notice (C60). | — | — |
| D-LLM-8 | Fallback providers get no OAuth analytics or comments (10 P10) | A | Data minimisation. | OWNER |
| D-LLM-9 | Critic advisory; 07 accepts `critic_flags` (10 P11) | A | — | — |
| D-LLM-10 | Comment intent stays a deterministic lexicon (10 P9, 06) | A | — | — |
| D-LLM-11 | LLM budgets (C54) | A | — | OWNER (spend caps) |

---

## 4. Canonical domain object field lists

Notation: `name: type`. A trailing ★ marks a field added by this review to close a gap or resolve a conflict. Owner component in brackets.

### BusinessProfile [03]
- `profile_id: UUID`, `client_id: UUID`, `version: int`, `parent_version: int|None`
- `crawl_run_id: UUID|None`
- `status: draft|awaiting_confirmation|confirmed|superseded`
- `schema_version: "1"`, `generated_at: datetime`
- `identity: BusinessIdentity`
  - `name`, `legal_name`, `one_liner`, `description`
  - `industry: IndustryCode`, `niche`
  - `business_model` (03 enum)
  - `geo_scope`
  - `primary_language`, `languages[]`
  - `country`★, `timezone`★ (IANA)
  - `website: SiteRef`, `website_domains[]`★
  - `brand_aliases[]`★, `primary_markets[]`★
  - `channel: ChannelLink|None`
  - `related_brands[]`, `country_sites[]`
  - each core claim has a `*_meta: ClaimMeta`
- `offerings[Offering]`:
  - `id`, `name`, `name_original`, `kind`
  - `description`, `definition`★, `aliases[]`★, `priority: 1..3`★
  - `url`, `parent_id`
  - `price_signals[]`, `audience_ids[]`, `location_ids[]`
  - `is_primary`, `meta`
- `value_propositions[ValueProposition]`: `id`, `statement`, `kind`, `proof_points[]`, `offering_ids[]`, `meta`
- `target_customers[StatedAudience]`
- `locations: LocationSummary`: `count`, `cities[]`, `regions[]`, `countries[]`, `items[Location + aliases[]★]`
- `conversion_actions[ConversionAction]`: `id`, `type`, `label`, `target`, `medium`, `prominence`, `is_primary`, `offering_ids[]`, `location_ids[]`, `meta`
- `brand_terms[BrandTerm]`
- `niche_keywords[NicheKeyword]` (rows mirrored to the `keywords` table on confirm)
- `buyer_question_ids[]` (rows in `buyer_questions`)
- `competitor_hints[CompetitorHint]`: `name`, `domain`, `url`★, `hint_type`, `meta`
- `content_pillars[]`
- `freshness: FreshnessProfile`
- `coverage: CoverageReport`
- `quality: ProfileQuality + completeness: float`★

### FreshnessProfile [03]
- `sensitivity: very_low|low|medium|high|very_high`
- `sensitivity_score: float`, `score_components{llm, industry_prior, site_signal}`, `rationale: str`★
- `drivers[]`
- `watch_entities[WatchEntity]`: `name`, `aliases[]`, `kind`, `relation_to_client`, `importance: 1..3`★, `linked_offering_ids[]`, `meta`
- `watch_topics[]`
- `event_types[EventType]` (08's enum is canonical)
- `seasonality[SeasonalWindow]`: `label`, `months[]`, `geo`, `query_terms[]`★, `meta`
- `suggested_sources[]`, `source_packs[]`★
- `regions[]`★ (ISO-3166 a2), `languages[]`★, `excluded_topics[]`★
- `regulated_domain: bool`★, `domain_kind: healthcare|finance|legal|none`★
- `evergreen_expected: bool`★
- `refresh_policy: RefreshPolicy`: `news_scan_interval_hours` (6/6/24/168/0 by level), `refresh_check_interval_days`, `description_refresh_eligible`, `min_signal_strength`
- `site_change_stats`, `meta`
- Derived (not stored): `operating_level = high|medium|low|none` (C25)

### BuyerQuestion [03; table `buyer_questions`]
- `id: UUID`, `client_id: UUID`
- `text`, `normalized_text`, `language`
- `funnel_stage: awareness|consideration|decision|post_purchase`
- `intent` (03 enum, 12 values)
- `offering_ids[]`, `conversion_action_id: str|None`★
- `geo_modifier: str|None`
- `source: website_faq|website_derived|user|spotlit|comments|import`, `source_ref`
- `status: proposed|confirmed|rejected|retired`
- `priority: 1|2|3`
- `ai_cited_video_ids: list[str]|None`, `ai_citations_observed_at`
- `meta: ClaimMeta`

### TopicRef [05]
- `id: str` (`tr_`+sha1[:12]), `client_id`, `profile_version: int`
- `kind: keyword|offering|buyer_question|news_entity`
- `source_item_id`
- `label`, `definition`
- `aliases[TopicAlias]`, `negative_contexts[]`, `parent_ids[]`
- `offering_ids[]`★
- `funnel_stage: FunnelStage|None` (03's enum)
- `facets[Facet]`
- `core_entity_terms[]`, `locality_terms[]`
- `weight: float = 1.0`
- `status: active|disabled`
- `expires_at: datetime|None`★ (news_entity only)
- `fingerprint`

### TopicMatch [05]
- `client_id`, `video_id`, `topic_ref_id`, `topic_kind`★
- `profile_version`, `universe_hash`, `prepared_hash`, `engine_version`
- `decision: accepted|rejected|unadjudicated`; `accepted: bool` (derived)★
- `lexical_score`, `semantic_score|None`, `combined_score` (the "score" every consumer reads)
- `method: lexical|semantic|lexical+semantic|llm_adjudicated|implied|override`
- `confidence: high|medium|low`
- `is_primary`, `rank`, `implied_from`
- `evidence[MatchEvidence]`, `matched_terms[]`★ (derived)
- `review_reasons[]`, `review_flag: bool` (derived)★
- `degraded[]`, `explanation`, `matched_at`

### ChannelRecord [02]
- `channel_id`, `title`, `handle`, `description`
- `country`, `default_language`, `published_at`
- `uploads_playlist_id`
- `subscriber_count: int|None`, `hidden_subscriber_count: bool`
- `view_count`, `video_count`
- `keywords[]` (provenance: API; 30-day rule)
- `thumbnail_url`, `made_for_kids`★
- `availability: available|unavailable`, `unavailable_at`
- `uploads_90d`★, `last_upload_at`★, `recent_upload_ids[]`★ (≤50)
- `cadence_flags[]`
- `metadata_fetched_at`, `stats_fetched_at`

### VideoRecord [02]
- `video_id`, `channel_id`, `channel_title`
- `published_at`, `effective_published_at` (age anchor)
- `live_broadcast_content: none|live|upcoming`★, `live_actual_start_at`★, `live_actual_end_at`★, `live_scheduled_start_at`★
- `title`, `description`, `tags[]`
- `content_hash`★, `description_hash`★
- `default_language`, `default_audio_language`, `localizations{lang:{title,description}}`
- `duration_s: int|None`
- `embed_width`, `embed_height`, `aspect`
- `format: short_form|long_form|live|upcoming|unknown`, `format_basis[]`, `format_confidence`
- `has_captions`, `licensed_content`, `age_restricted`, `region_restriction`, `made_for_kids`
- `privacy_status`, `upload_status`, `embeddable`, `public_stats_viewable`
- `has_paid_product_placement: bool|None`
- `thumbnails{key:{url,width,height}}`, `thumbnail_url`★
- `comments_state: enabled|disabled|unknown`, `likes_visible`
- `view_count`★, `like_count|None`★, `comment_count|None`★ (latest)
- `availability: available|missing|removed`, `first_missing_at`
- `ingest_source: uploads|search|manual|recommendation_ref`
- `quality_flags[]`
- `metadata_fetched_at`
- No `category_id`.

### VideoSnapshot [02]
- `video_id`, `slot_at` (1h slot, PK with video_id), `captured_at`
- `age_hours: float`
- `view_count`, `like_count|None`, `comment_count|None`
- `source: batch_stats|videos_list|backtest_freeze`
- `counting_regime: str`

### ViewsAtAge [06]
- `video_id`
- `age_days: int` (target: 1|3|7|30)
- `views|None`, `likes|None`, `comments|None`
- `method: observed|interpolated|owner_analytics|age_band_proxy|pending|unavailable`
- `observed_age_days: float|None`
- `bracket_lo_at`, `bracket_hi_at`, `bracket_spread`
- `source: data_api_snapshot|analytics_api`
- `label: str`★ (e.g. "views at day 7 (interpolated)")
- `as_of`, `source_oldest_at`

### VideoMetrics [06]
- `video_id`, `channel_id`, `as_of`
- `segment: FormatSegment`, `segment_evidence{}`, `format_key`★
- `age_anchor_at`, `age_days`
- `views_now`, `likes_now`, `comments_now`
- `views_at_age: dict[int, ViewsAtAge]`
- `comparison_age_days`★
- `views_per_day_lifetime`, `velocity_recent: Velocity|None`, `velocity_7d: Velocity|None`★
- `engagement_rate`, `like_rate`, `comment_rate`, `engagement_basis`
- `outlier: OutlierAssessment` (required for every eligible video): `age_days`, `method`, `value`, `baseline_median`, `baseline_n`, `baseline_id`, `ratio`, `log_robust_z` (internal), `tier`, `is_outlier`, `suppressed_reason`
- `duplicate_group_id`, `flags[MetricFlag]` (+ `cross_channel_duplicate`, `degenerate_frame`, `tiny_baseline`★)
- `metrics_version`

### VideoTraits [06]
- `video_id`, `traits_version`
- `segment`, `length_band`, `format_key`★
- `title: TitleTraits`
- `description: DescriptionTraits`
- `thumbnail: ThumbnailTraits|None`: `text_present`, `word_count`, `word_count_band`, `text_area_ratio`, `brightness`, `rms_contrast`, `colorfulness` (+bands), `dominant_colour_count`, … (no face fields)
- `topics[TopicTrait{topic_ref_id, kind, score, review_flag, directness (from 05 AnswerDirectness)}]`
- `timing: TimingTraits` (client-context)

### CommentSignals [06]
- `video_id`, `as_of`
- `comment_count_api`, `sampled_n`, `eligible_n`, `sample_coverage`
- `question_n`, `question_share`
- `intent_counts{IntentClass:int}`
- `buyer_intent_n`, `buyer_intent_share`
- `unanswered_question_n`, `unanswered_question_share`
- `owner_reply_visible_n` (from 02 `is_channel_owner_reply`)
- `lexicon_version`, `sufficient: bool`
- Client-scoped, in reports only: `matched_buyer_questions[]`, `excerpts[]` (default off for regulated domains)

### TraitLift [06]
- `scope: competitor_set|niche|own|channel`★
- `window_days`, `as_of`, `comparison_age_days`, `comparison_method`
- `trait_key`, `trait_value`, `interaction|None`
- `n_with`, `n_without`, `outliers_with`, `outliers_without`
- `outlier_rate_with`, `outlier_rate_without`, `rate_with_wilson`
- `lift`, `lift_smoothed` (internal), `lift_ci (low, high)`, `channel_balanced_lift`
- `p_value`, `q_value` (internal)
- `outlier_channels_with`, `max_channel_share`
- `ratio_quantiles_with (p25, p50, p75)`
- `reference_video_ids[]`
- `status: supported|exploratory|insufficient_support|no_contrast|dominated|redundant`, `redundant_with`
- Displayed: rates, lift and n only (C5).

### NewsSignal [08]
- `id`, `client_id`, `story_id`, `profile_version`
- `judgement: NewsJudgement`: `relevance_band`, `key_entity`, `other_entities`, `event_type`, `event_date`, `confirmation`, `offering_links[]`, `buyer_question_ids[]`, `angle`, `region_relevance`, `about_client`, `about_competitor_ids[]`, `safety_flags[]`, `conflicting_reports`, `conversion_action_id`
- `credibility: CredibilityAssessment`
- `demand: DemandCheck` (`early_videos[EarlyVideo{…, views_at_age: ViewsAtAge (age_days)}]`, `saturation`, …)
- Flattened accessors for 09★:
  - `entity` (= key_entity.canonical), `aliases[]`, `entity_kind`
  - `is_competitor_entity`, `topic_ref_ids[]`
  - `first_seen_at` (= story.first_published_at), `event_date`
  - `sources[{url, publisher, published_at}]`
  - `demand_video_ids[]`
- `verification_status: active|disputed|retracted|superseded`★, `superseded_by`★
- `relevance_score`
- `urgency: now|this_week|evergreen_window`
- `half_life_hours`, `expires_at`
- `refresh_keywords[]`, `refresh_eligible`
- `safety: SafetyDecision`
- `status: new|drafted|shown|dismissed|used|suppressed|expired`, `suppression_reason`
- `created_at`
- Ordering is lexicographic (relevance band, credibility tier, freshness, saturation). There is no blended `priority` (D-POL-7).

### RecommendationDraft [07]
- `draft_id`, `stable_key`, `client_id`
- `type: RecType`, `window_days`, `as_of`, `produced_by`
- `subject: VideoIdeaSubject|PatternSpec|CreatorSubject|NewsSubject (08)★|RefreshSubject (09)★`
- `topic_ids[]`, `buyer_question_id`, `related_buyer_question_ids[]`
- `reference_pool[ReferenceVideo]` (≤12), `references[ReferenceVideo]` (3..5)
- `reasons[Reason]`, `fit_reasons[Reason]`
  - `Reason`: `reason_id`, `code`, `params`, `statement`★, `numbers[EvidenceNumber]`★, `ref_video_ids[]`, `source`
- `lenses[LensAssignment]`
- `gap: GapEvidence|None`
- `expected_range: ExpectedRange|None`
- `confidence: Confidence|None`
- `score: ScoreBreakdown|None` (internal; never on channels)
- `match_spec: MatchSpec`★ (12)
- `forbidden_titles[]`★, `allowed_dates[]`★, `entities[]`★
- `gate_results[{gate, action, reason_code, features}]`★
- `critic_flags[]`★
- `flags`
- `CreatorSubject` has **no** `creator_score`. It carries the separate simple stats only (C4).

### Recommendation (contract) [07]
- `id` (`rec_`+base32(sha1(stable_key))[:12])
- `type: video_idea|title_pattern|thumbnail_pattern|format|creator|news_opportunity|description_refresh`
- `summary`
- `lens[]: views|conversion`
- `buyer_question: str|null`
- `why_it_works[]: str`
- `reference_videos[ReferenceVideo]`
- `fit_to_client: str`
- `expected_range_vs_baseline: str|null`
- `confidence: Confidence`
- `window_days`, `generated_at`
- `extras`:
  - `contract_version: "1.1"`, `stable_key`, `run_id`, `rendered_by: llm|template`
  - `status: new|ongoing|updated`, `first_seen_at`, `expires_at`
  - `buyer_question_id`, `lens_reasons[]`, `why_it_works_structured[]`, `fit_structured`
  - `expected_range: ExpectedRange`
  - `gap`, `subject`, `related_buyer_questions[]`, `example_titles[]`, `related[]`, `flags[]`
  - `news{reference_sources[], urgency, format_suggestion, conversion_action, compliance{disclaimers, review_required, safety_flags}}` (news_opportunity)
  - `refresh{operations[], advisories[], review_required, inserted_bytes, resulting_bytes}` (description_refresh)
  - `data_disclosure`
- No `rank` or `score` is exposed.

### ReferenceVideo [07]
- `video_id`, `url`, `title`
- `channel` (= channel title), `channel_id`, `channel_role: own|competitor|niche|benchmark|creator`
- `published_at`
- `views_day7: int|null` (only when `views_at_age.age_days==7` and method ∈ {observed, interpolated, owner_analytics})
- `views_at_age{age_days, views, method, label}`
- `outlier_ratio: float|null`
- `channel_baseline_views_at_age`, `is_outlier`
- `format_key`, `length_band`, `matched_topic_ids[]`
- `thumbnail_url`, `has_paid_product_placement`

### Confidence [07]
- `label: High|Medium|Low`
- `outliers: int`, `channels: int`
- `basis: standard|first_mover|rule_based`★
- `matching_videos`, `rule_id` (`default_v1`|`creator_v1`|`news_v1`|`refresh_v1`), `caps_applied[]`

### DescriptionRefresh [09]
- `id`, `client_id`, `video_id`, `run_id`
- `recommendation_id|None`
- `triggers[RefreshTrigger]`
- `operations[InsertionOp]` (≤3):
  - `op_id`, `kind: insert|link_fix`
  - `position: top_line|after_first_paragraph|before_hashtag_line|before_links_block|append`
  - `anchor{strategy, anchor_excerpt, anchor_hash, char_offset_hint}`
  - `old_text` (link_fix only), `text` (≤200 chars), `text_bytes`
  - `keywords_added[]`, `hashtags_added[]`
  - `claim_level`, `trigger_ids[]`
  - `phrased_by: template|llm`
- `advisories[Advisory]`
- `suggested_tags[]`, `keywords_added[]`
- `inserted_bytes`, `resulting_bytes` (≤5000 bytes)
- `expected_effect: str`
- `confidence: Confidence` (07; hygiene → `basis=rule_based`)
- `priority` (internal, ordering only)
- `expires_at`
- `status: proposed|dismissed|applied_claimed|applied_detected|expired|withdrawn|superseded`
- `description_hash_at_generation`
- `review_required: bool`
- `generated_at`
- `preview_after` (rendered at read time)

### RunEvent [11]
- `run_id`, `client_id`
- `seq: int` (gap-free per run; SSE id)
- `type: run.queued|run.started|run.progress|run.awaiting_confirmation|run.succeeded|run.partial|run.failed|run.cancelled|run.superseded|stage.started|stage.step|stage.progress|stage.retrying|stage.reused|stage.succeeded|stage.degraded|stage.failed|stage.skipped|insight|notice|results.published`★
- `stage|None`, `step|None` (dotted, e.g. `crawl.page`, `write.card`)
- `status: StageStatus|RunStatus|None`
- `level: info|warning|error`
- `message` (template, ≤200 chars)
- `counts{done, total, unit}|None`
- `percent|None`, `stage_percent|None`
- `ts`
- `payload: EventPayload|None` (11's kinds; `competitor_candidate` has **no** `score`★; `outlier_found` carries `views_at_age` instead of `views_day7`★)

---

## 5. Canonical table list

Zones: **S** = shared public cache (no tenant columns), **T** = tenant-scoped (`workspace_id`/`client_id`), **G** = global non-YouTube or ops, **E** = evaluation/admin.

Retention: "28d" means acted on at 28 d under the 30-day rule. "refresh-or-delete" means refreshed if still referenced, otherwise deleted.

| Table | Owner (schema) | Zone | Retention |
|---|---|---|---|
| workspaces, users, workspace_members, user_sessions, api_keys, audit_log, deletion_requests | 01 | T/G | Account lifetime. Revoked keys 90 d. Sessions 14 d. |
| clients (incl. `lifecycle`, `own_channel_id`, `own_channel_verification`, settings) | 01 (lifecycle driven by 11) | T | Client lifetime. Purge ≤30 d after delete. |
| oauth_tokens | 01 | T (authorized) | Until revoke. Purge ≤1 h target. Re-verify ≤25 d. |
| own_analytics_daily, own_traffic_source, own_search_term | 02 | T (authorized) | While authorization is re-verified every 30 d. Purge on revoke. |
| yt_channels | 02 semantics / 01 DDL | S | 28d refresh-or-delete |
| yt_channel_snapshots | 02 | S | 28d (partition drop) |
| yt_channel_resolutions | 02 | S | 7 d TTL (hard ≤28 d) |
| yt_videos | 02 | S | 28d refresh-or-delete. Removed videos purged 24 h after confirmation. |
| yt_video_snapshots (PK video_id, slot_at; daily partitions) | 02 | S | 28d partition drop. 36 months only in `derived_metrics_approved` mode. |
| yt_snapshot_schedule | 02 | S | Deleted when untracked |
| yt_comments (redacted, no author identity) | 02 | S | 28d delete-only |
| yt_comment_fetch | 02 | S | 28d |
| yt_search_cache + yt_search_result | 02 | S | TTL 12–72 h by span (6 h for news demand). Hard ≤28 d. |
| yt_http_etag | 02 | S | 28d |
| yt_quota_ledger (buckets main/search/batch_stats/analytics; scope; cache_outcome) | 02 | G | 400 d. Tenant ids nulled on client purge. |
| quota_cache_hits_daily | 02 | G | 400 d |
| yt_video_age_values | 06 | S | 28d (by `source_oldest_at`) |
| yt_video_metrics | 06 | S | Recomputed daily. 28d by `source_oldest_at`. |
| yt_channel_baselines (per channel, segment, metric, age, method, as_of) | 06 | S | 28d |
| yt_video_outliers | 06 | S | 28d |
| yt_video_traits | 06 | S | 28d recompute-or-delete |
| yt_thumbnail_traits | 06 | S | 28d (clock `source_verified_at`) |
| yt_channel_recurring_text | 06 | S | Weekly refresh. 28d. |
| yt_comment_signals (counts only) | 06 | S | 28d |
| yt_demand_checks | 08 | S | 28d hard delete |
| channel_boilerplate | 05 | S | 28d |
| video_prepared_text | 05 | S | 28d |
| text_embeddings | 05 | S | 28d, cascade on video |
| llm_adjudications (keyed by input hash, client_id kept) | 05 | T | 28d |
| websites, crawl_runs, url_inventory | 03 | T | Client lifetime |
| crawl_frontier | 03 | T | 7 d after crawl |
| crawled_pages (text ≤60k) | 03 | T | 180 d. Re-crawl every 90 d. |
| page_blobs | 03 | T | 30 d |
| page_facts | 03 | T | Superseded + 90 d |
| business_profiles, profile_edits, profile_diffs, profile_tombstones | 03 | T | All versions kept (tenant-owned) |
| keywords | 03 | T | Kept. API-derived keywords 28d unless user-confirmed. |
| buyer_questions | 03 | T | Kept |
| competitor_discovery_runs, competitor_candidates, competitor_drift_events | 04 | T | Client lifetime |
| competitor_candidate_sources (web provenance) | 04 | T | 180 d |
| competitor_channel_matches | 04 | T | YouTube-derived jsonb refreshed or nulled at 28d |
| competitor_sets (versioned four lists) | 04 | T | Ids re-verified within each 28-day window |
| brand_channel_resolutions | 04 | S | 30 d expiry (verdict re-verified) |
| v_tracked_channels (view) | 01 | T | — |
| topic_universes, topic_refs, topic_aliases, ref_embeddings | 05 | T | Kept (client data) |
| topic_matches, answer_directness | 05 | T | 28d |
| topic_match_overrides | 05 | T | Kept. Ids re-verified per D-POL-11. |
| topic_review_items | 05 | T | Purged with the video |
| topic_eval_labels | 05 → 12 data | E | Labels kept. Text re-hydrated, never stored >28d. |
| news_queries, news_query_runs, news_items, news_stories, source_tiers | 08 | G | Queries deleted 30 d after their last subscriber. Runs, items and stories 90 d. Tiers permanent. |
| news_query_subscriptions | 08 | T | With client / re-plan |
| news_judgements, news_signals, news_review_queue | 08 | T | 180 d. Demand-derived fields nulled at 28d. |
| recommendations (07 schema; payload/draft redacted at 28d, row kept) | 07 | T | 28d for API-derived fields |
| recommendation_references | 07 | T | 28d |
| recommendation_suppressions | 07 | T | Draft 28d. Counts kept. |
| recommendation_notices, white_space_notes | 07 | T | 28d |
| recommendation_feedback (single, all types) | 07 | T | Client lifetime. `linked_video_id` per D-POL-11. |
| recommendation_outcomes | 07 | T | observed_ratio 28d. Booleans kept (D-POL-11). |
| creator_collaborations | 07 | S | 28d |
| client_recommend_config | 07 | T | Kept |
| description_refreshes | 09 | T | ≤28d unless applied. Applied rows keep own fields only. |
| refresh_eligibility | 09 | T | 28d |
| refresh_impact | 09 | T | 28d |
| link_check_cache | 09 | G | 7 d |
| llm_result_cache | 10 | G (keyed by input hash) | Default 7 d, topic 14 d, hard ≤28d |
| recommendation_copy | 10 | T | With recommendations (28d) |
| llm_debug_payloads (dev/staging only) | 10 | G | 7 d |
| provider_usage_ledger (unified LLM/Tavily/crawl; per attempt) | 01 schema, 10 writer | G | 400 d (no content, hashes only) |
| pipeline_runs, stage_runs | 11 | T | 180 d (no API content in notices) |
| run_events | 11 | T | 28d. Cap 5,000/run. |
| run_artifacts (incl. 06 reports) | 11 | T | 28d if `contains_api_data`, else 90 d |
| idempotency_keys | 11 | T | 24 h |
| client_schedules | 11 | T | Client lifetime |
| active_result_sets | 11 | T | Superseded history 28d |
| eval_config, eval_result, eval_pattern_spec (creator channel_id nulled at 28d) | 12 | E | Kept |
| eval_freeze (bundle deleted at expiry) | 12 | E | Bundle ≤30 d from oldest fetch. Row kept as `purged`. |
| eval_outcome_video | 12 | E | 28d |
| label_task (detector_ratio and ids 28d), label, label_gold, outlier_eval_result | 12 | E | Labels kept. Ids per D-POL-11. |
| review_item (card_snapshot nulled at 28d), review_rating, gate_tuning_proposal | 12 | E | Kept |
| ops_daily_rollup | 12 | G | 400 d |
| alert_event | 12 | G | 90 d |

Tables dropped by this review:
- 01: `runs`, `run_stages`, `analysis_artifacts`, `competitors`, `refresh_suggestions`, `news_query_cache`, `news_opportunities`, `embedding_cache`, `own_analytics_reports`, `evaluation_label_sets`, `evaluation_labels`, `evaluation_reports`
- 02: `client_channel`, `oauth_connection`
- 03: `channel_links`
- 06: `analytics_reports`
- 07: `recommendation_runs`
- 08: `news_feedback`
- 09: `refresh_feedback`, `channel_boilerplate` (merged into 05's)
- 10: `llm_calls`, `llm_usage_daily` (merged into the ledger; daily figures come from a view)

Their columns move to the owners listed above.
