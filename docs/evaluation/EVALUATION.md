# Evaluation

Status of the four evaluation items from the assignment, measured on the local stack on 2026-11-20 against the real `growthhacker` database (clients: Apollo Hospitals, Replit, plus Clove Dental for the labelling universe). Nothing here is estimated unless it says so, and no label or rating has been filled in by the system.

| # | Item | Status | What is left |
|---|---|---|---|
| 1 | Backtest vs copy-top and random | Frozen and pre-registered at T = 2026-11-20 08:20 UTC. Not scoreable yet. | Score between 2026-12-08 and 2026-12-17 |
| 2 | Outlier precision and recall | Labelling pack generated (115 videos). | Two people label it, then import and score |
| 3 | Human review of cards | Review pack generated (5 real cards + 6 decoys). Short of 30. | More published cards, then two raters |
| 4 | Operations | Measured. Targets met for the post-fix period. | Re-run after a week of scheduled refreshes |

Bundles and packs hold YouTube API data, so they live in `var/` (git-ignored) and expire with the 30-day rule. Regenerate them rather than committing them.

## 1. Backtest

**Design.** Forward-looking by construction (plan 12 §5.1). At T we freeze every input the pipeline reads, replay it with a clock fixed at T, and record the gated specs of three arms:

- `system`: the top gated drafts per type, as the client would see them;
- `copy_top`: "copy the most-viewed recent video" from the same channel universe;
- `random`: 1,000 random specs of the same type and specificity.

Outcomes are videos those channels publish in (T, T + 10 days], measured at day 7 against each channel's baseline frozen at T. The config is hashed at registration; the scorer refuses to run if it changes.

**What exists now.**

| client | freeze id | T (UTC) | point-in-time | videos frozen | leakage canary | eval id | specs (system / copy_top / random) |
|---|---|---|---|---|---|---|---|
| Apollo Hospitals | `69e21a10-d76e-4b4f-a43d-e53a07110a55` | 2026-11-20 08:20:02 | strict | 3,320 | passed (3,469 planted rows) | `beda7b86-2c1d-5a70-af85-f9e35b55297f` | 4 / 4 / 4,000 |
| Replit | `3993415c-3f32-43a1-8b3a-ab55f2ef09b0` | 2026-11-20 08:20:06 | strict | 1,648 | passed | `17951e86-add1-5b59-be23-30f6a6f29eb0` | 0 (abstention: no gated cards at T) |

**When it can be scored.** Outcomes mature at T + horizon (10 d) + outcome age (7 d) + 1 d = **2026-12-08 08:20 UTC**. The freeze bundles, and the channel ids on the recorded creator specs, expire with the 30-day rule on **2026-12-18 08:20 UTC**. Score inside that window:

```bash
cd backend
uv run python -m app.evaluation backtest list
uv run python -m app.evaluation backtest score \
  --eval-id beda7b86-2c1d-5a70-af85-f9e35b55297f \
  --eval-id 17951e86-add1-5b59-be23-30f6a6f29eb0
# writes docs/evaluation/backtest-report.md
```

Requirements until then: the maintenance worker keeps running (its `snapshot_tick` cron captures the day-7 views of every new upload; a gap leaves outcomes `missing_snapshot`, and more than 20% missing makes the verdict `invalid`), and the daily refreshes keep ingesting the tracked channels' uploads. Scoring earlier raises `OutcomesNotMature`.

**Expected power.** One client with 4 system specs is small. The pre-registered verdict will likely be `inconclusive_underpowered` unless more clients are frozen. To strengthen it, onboard the three ICP niches (dental, auto dealers, home services, 5 competitors each), run their analyses, then `uv run python -m app.evaluation freeze create --all` and register a backtest per client with `POST /api/v1/evaluation/backtests`.

## 2. Outlier precision and recall

**Pack.** Batch `01a1f809-4474-763a-912f-ddb0239f9298`, sampled with stratification by channel size and detector ratio band (seed 20261119), across Apollo, Replit and Clove Dental channels:

| stratum | videos | channels | flagged by the detector |
|---|---|---|---|
| S1 (smallest channels) | 21 | 8 | 0 |
| S2 | 28 | 12 | 4 |
| S3 | 24 | 11 | 2 |
| S4 (largest channels) | 15 | 5 | 8 |
| Shorts | 27 | 16 | 4 |
| total | 115 | 42 | 18 |

The main sample is 88 long-form videos, 12 short of the 100 target: the 3-per-channel cap and small cells left 15 slots unfilled, and 454 videos had no detector ratio (too young or no baseline). Adding the ICP niche clients and re-exporting fills the gap.

Files: `var/eval_packs/label-pack-01a1f809-labeller_a.html` and `…-labeller_b.html`. Each is a self-contained page with the guidelines (`docs/evaluation/labelling-guidelines.md`). The pack expires on 2026-12-18.

**How to complete it.**

1. Two labellers calibrate on 10 anchor videos, then label every item independently (`outlier`, `not_outlier`, `unsure`, with reason codes) and use the page's export button to save a CSV.
2. Import each CSV:
   ```bash
   uv run python -m app.evaluation labels import labeller_a.csv --batch 01a1f809-4474-763a-912f-ddb0239f9298 --labeller labeller_a --round 1
   uv run python -m app.evaluation labels import labeller_b.csv --batch 01a1f809-4474-763a-912f-ddb0239f9298 --labeller labeller_b --round 1
   ```
3. If Cohen's kappa is below 0.6, revise the guidelines and relabel a fresh 30. Disagreements go to a third labeller with `--round 2`.
4. Score: `uv run python -m app.evaluation labels score --batch 01a1f809-4474-763a-912f-ddb0239f9298`. It reports agreement (percent, kappa, Gwet's AC1), Horvitz-Thompson weighted precision and recall with bootstrap intervals, and a threshold sweep (best F0.5 with precision ≥ 0.80).

## 3. Human review

**Pack.** Batch `01a1f80a-1086-7224-8e54-d7350b291c38`: 5 real cards and 6 blinded decoys ("copy the most-viewed recent video"), one file per rater: `var/eval_packs/review-pack-01a1f80a-rater_mira.html` and `…-rater_marketer.html`. The rubric is `docs/evaluation/review-rubric.md`. Expires on 2026-12-18.

**Why only 5.** The sampler takes each client's latest published result set. Today that is Apollo's 4 video ideas (30-day window) plus one more card; Replit and Clove Dental published no cards because their windows are thin (see below). The 30-card target needs more published cards: onboard the ICP niche clients, let a week of refreshes fill the windows, then export a new batch:

```bash
uv run python -m app.evaluation review export --rater rater_mira --rater rater_marketer
```

**How to complete it.** Each rater fills the pack alone, exports a CSV, then:

```bash
uv run python -m app.evaluation review import mira.csv --batch <batch_id> --rater rater_mira
uv run python -m app.evaluation review import marketer.csv --batch <batch_id> --rater rater_marketer
uv run python -m app.evaluation review score --batch <batch_id>
```

Pass rule: overall mean ≥ 4.0, every criterion ≥ 3.5, real cards beating decoys by ≥ 0.3, Krippendorff's alpha ≥ 0.667 per criterion. The scorer writes gate-tuning proposals for low criteria; a person approves any change.

## 4. Operations

Report: `docs/evaluation/ops-report.md` (JSON alongside), generated with `uv run python -m app.evaluation ops report --from 2026-11-20T08:00:00`. The period starts when the final code was deployed; it covers 3 analyses, 4 daily refreshes, 2 on-demand card runs and 4 backtest runs.

**Time to generate recommendations** (warm `daily_refresh`, first stage start to finalize, queue wait excluded):

| client | run 1 | run 2 (same 6 h data bucket) |
|---|---|---|
| Apollo Hospitals | 77 s | 10 s |
| Replit | 109 s | 23 s |

p50 50 s, p95 105 s, max 109 s: under the 120 s target. The longest stages are thumbnail enrichment (up to 62 s on Replit) and the refresh engine's link checks (~21 s). A full analysis took 98 s (Apollo, forced recompute) and 219 s (Replit).

For context, the whole of 2026-11-20 including earlier development runs gives a daily-refresh p95 of 153 s. Those include forced full recomputes (`force: true`, which are not warm by definition) and runs on code before the news-prefilter fix.

**Quota units per client per daily refresh:**

| client | main bucket | search.list calls | batchGetStats calls |
|---|---|---|---|
| Apollo Hospitals | 10 (first), 0 (repeat) | 0 | 31 per refresh (snapshot cron, own bucket) |
| Replit | 11 (first), 0 (repeat) | 0 | 12 per refresh |

A full analysis costs 66–76 main units and 0–1 search calls. The project total for the whole session (3 analyses, 4 refreshes, 20 answer-citation checks, backtests) was 240 main units, 1 search call and 93 batchGetStats calls. At ~11 main units per warm refresh, the 10,000-unit bucket covers several hundred clients; the capacity table in the report (83 clients/day) is lower because it amortises the analyses run on the same day.

**Cost.**

| item | measured |
|---|---|
| LLM per warm daily refresh | $0.00 (Apollo, cards unchanged and copied forward), $0.014 (Replit, news judging) |
| LLM per analysis | $0.32 (Apollo: 13 card writes + critic), $0.23 (Replit: topic adjudication) |
| Tavily per weekly answer-citation check | 10 credits per client ($0.08) |
| Session total | $0.89 LLM, 20 Tavily credits |

Prices come from the versioned table in `app/core/config.py`, verified 2026-11-20.

**Compliance.** 45 API-data retention rules audited: compliant. The feature-policy matrix (`docs/evaluation/feature-policy-matrix.md`) passes `uv run python -m app.evaluation policy check`.

## Current outputs on real data

| | Apollo Hospitals | Replit |
|---|---|---|
| Cards, 7-day window | 0 (thin: 11 outliers across 5 channels; "Widen to 30?" projects 4) | 0 |
| Cards, 30-day window | 4 video ideas | 0 (21 outliers across 8 channels, but no topic has 3 outlier references) |
| Cards, 90-day window | 6 video ideas + 1 creator | 0 |
| Early signals | 0 | 0 (5 news signals drafted, all held back as uncorroborated) |
| Channel hygiene | 10 description fixes, all flagged for medical review | 10 description fixes with paste-ready lines |
| Answer citations | 10 questions checked; none cite Apollo's own channel | 10 questions checked; 2 cite Replit's channel |

Replit's empty card list is the thin-data rule working: its competitors (OpenAI, GitHub, Anthropic, Bolt.new, Emergent…) publish product videos that rarely match one of Replit's 202 topics three times among outliers.
