# Operations report

- Period (UTC): 2026-11-20T08:00:00+00:00 to 2026-11-20T08:26:14.398904+00:00
- Quota days (America/Los_Angeles, Google's reset): 2026-11-20 to 2026-11-20
- Generated: 2026-11-20T08:26:14.594295+00:00
- Clients with activity: 2
- Measured runs present: yes
- Every figure is calculated by GrowthHacker from its own ledgers; none is a YouTube metric.

## Targets

| target | status |
|---|---|
| Daily refresh p95 wall time < 120 s (warm cache) | met |
| Onboarding p95 wall time ≤ 8 min (tracked separately) | no data |
| Median main-bucket units per client-day within budget | met |
| LLM price table verified recently | met |
| No API data past the retention limit | met |

## Time to generate recommendations

Definition (99 C69): wall time of a `daily_refresh` run from its first stage start to finalize, excluding queue wait, p95 < 120 s with a warm cache. Onboarding is reported separately.

### daily_refresh

| stage | n | p50 s | p95 s | max s | failure rate |
|---|---|---|---|---|---|
| analytics | 2 | 17.5 | 25.5 | 26.4 | 0.0% |
| competitor_drift | 2 | 0.5 | 0.7 | 0.7 | 0.0% |
| enrich | 2 | 37.2 | 59.1 | 61.5 | 0.0% |
| finalize | 4 | 0.0 | 0.1 | 0.1 | 0.0% |
| ingest | 2 | 9.3 | 9.9 | 9.9 | 0.0% |
| news | 1 | 11.4 | 11.4 | 11.4 | 0.0% |
| niche_discovery | 2 | 15.9 | 18.1 | 18.3 | 0.0% |
| recommend | 3 | 9.2 | 9.6 | 9.7 | 0.0% |
| refresh | 3 | 21.1 | 21.7 | 21.8 | 0.0% |
| snapshot_config | 4 | 0.0 | 0.0 | 0.0 | 0.0% |
| topics | 2 | 3.1 | 4.6 | 4.7 | 0.0% |
| write_cards | 2 | 0.2 | 0.3 | 0.3 | 0.0% |
| total | 4 | 50.0 | 104.6 | 109.4 | 0.0% |

### analysis

| stage | n | p50 s | p95 s | max s | failure rate |
|---|---|---|---|---|---|
| analytics | 3 | 22.8 | 25.4 | 25.7 | 0.0% |
| competitor_drift | 3 | 0.8 | 0.8 | 0.8 | 0.0% |
| enrich | 3 | 18.9 | 94.6 | 103.0 | 0.0% |
| finalize | 3 | 0.0 | 0.0 | 0.0 | 0.0% |
| ingest | 3 | 12.2 | 16.7 | 17.2 | 0.0% |
| news | 1 | 17.2 | 17.2 | 17.2 | 0.0% |
| niche_discovery | 3 | 14.1 | 23.8 | 24.8 | 0.0% |
| recommend | 3 | 5.8 | 25.2 | 27.4 | 0.0% |
| refresh | 3 | 3.2 | 19.2 | 21.0 | 0.0% |
| snapshot_config | 3 | 0.0 | 0.0 | 0.0 | 0.0% |
| topics | 3 | 5.1 | 54.9 | 60.4 | 0.0% |
| write_cards | 3 | 0.1 | 9.3 | 10.3 | 0.0% |
| total | 3 | 98.6 | 207.3 | 219.4 | 0.0% |

### backtest

| stage | n | p50 s | p95 s | max s | failure rate |
|---|---|---|---|---|---|
| analytics | 2 | 14.5 | 20.6 | 21.3 | 0.0% |
| enrich | 2 | 8.0 | 11.4 | 11.8 | 0.0% |
| frozen_corpus | 2 | 38.8 | 54.4 | 56.2 | 0.0% |
| recommend | 3 | 9.5 | 24.4 | 26.1 | 0.0% |
| record_backtest | 2 | 20.0 | 21.5 | 21.6 | 0.0% |
| snapshot_config | 4 | 0.0 | 0.0 | 0.0 | 0.0% |
| topics | 2 | 9.0 | 14.2 | 14.8 | 0.0% |
| write_cards | 0 | n/a | n/a | n/a | 100.0% |
| total | 2 | 25.6 | 28.3 | 28.6 | 50.0% |

### cards

| stage | n | p50 s | p95 s | max s | failure rate |
|---|---|---|---|---|---|
| finalize | 2 | 0.0 | 0.0 | 0.0 | 0.0% |
| snapshot_config | 2 | 0.0 | 0.0 | 0.0 | 0.0% |
| write_cards | 2 | 9.1 | 17.2 | 18.2 | 0.0% |
| total | 2 | 9.9 | 18.0 | 18.9 | 0.0% |

### Queue wait (not counted in wall time)

| run kind | n | p50 s | p95 s | max s |
|---|---|---|---|---|
| analysis | 3 | 0.5 | 0.5 | 0.5 |
| backtest | 4 | 0.1 | 0.1 | 0.2 |
| cards | 2 | 0.2 | 0.3 | 0.3 |
| daily_refresh | 4 | 0.4 | 0.5 | 0.5 |

## Quota units per client per daily refresh

| day (PT) | client | refresh runs | main / refresh | search / refresh | batch_stats / refresh | main amortised / refresh | search amortised / refresh | batch_stats amortised / refresh | cache hit rate |
|---|---|---|---|---|---|---|---|---|---|
| 2026-11-20 | 01a1f71a-36b5-72ad-8fac-65cf9d7a2f3a | 2 | 5.0 | 0.0 | 31.0 | 5.0 | 0.0 | 31.0 | 0.0% |
| 2026-11-20 | 01a1f793-d7df-76fc-8c29-4b51c6051e89 | 2 | 5.5 | 0.0 | 12.0 | 5.5 | 0.0 | 12.0 | 0.0% |

Marginal units are spent inside the client's daily_refresh runs; amortised adds the client's share of shared-cache fills, split by that day's cache hits.

### Project totals (whole Google Cloud project)

| day (PT) | main | search calls | batch_stats | analytics | unattributed shared main |
|---|---|---|---|---|---|
| 2026-11-20 | 240 | 1 | 93 | 0 | 0 |

## Capacity estimate

Clients one project can refresh per day = bucket daily limit ÷ median amortised units per client-day.

| bucket | clients per day |
|---|---|
| main | 83.3 |
| search | 200.0 |
| batch_stats | 232.6 |

## LLM cost

Prices from the versioned price table, last verified 2026-11-20.

| client | calls | usd | usd / daily refresh | retries | errors | price missing |
|---|---|---|---|---|---|---|
| 01a1f71a-36b5-72ad-8fac-65cf9d7a2f3a | 89 | 0.6533 | 0.0000 | 1 | 1 | 0 |
| 01a1f793-d7df-76fc-8c29-4b51c6051e89 | 34 | 0.2406 | 0.0070 | 0 | 0 | 0 |
| project total | 123 | 0.8939 | 0.0035 | 1 | 1 | 0 |

| model \| operation | calls | input | output | cache read | cache write | usd |
|---|---|---|---|---|---|---|
| claude-haiku-4-5-20251001\|card_critique | 7 | 19341 | 2341 | 0 | 0 | 0.0310 |
| claude-haiku-4-5-20251001\|topic_adjudication | 33 | 161919 | 12908 | 0 | 0 | 0.2265 |
| claude-sonnet-5-5\|card_write | 76 | 124244 | 11478 | 195534 | 46008 | 0.4978 |
| claude-sonnet-5-5\|news_judgement | 7 | 3996 | 3143 | 9780 | 39258 | 0.1385 |

## Tavily credits

| client | calls | credits | usd |
|---|---|---|---|
| 01a1f71a-36b5-72ad-8fac-65cf9d7a2f3a | 10 | 10.00 | 0.0800 |
| 01a1f793-d7df-76fc-8c29-4b51c6051e89 | 10 | 10.00 | 0.0800 |
| project total | 20 | 20.00 | 0.1600 |

## Crawl

No crawl ledger rows recorded.

## Failures

| run kind | failed runs | failure rate |
|---|---|---|
| analysis | 0 | 0.0% |
| backtest | 2 | 50.0% |
| cards | 0 | 0.0% |
| daily_refresh | 0 | 0.0% |

| provider | error | count |
|---|---|---|
| anthropic | invalid_output | 1 |

## Retention compliance

45 API-data rules audited: compliant.

## Planning figures (estimates, not measurements)

From MASTER_PLAN §8, per client.

| item | estimate |
|---|---|
| YouTube main bucket per daily refresh | ~25-30 units (interim planning figure ≈50, 99 C56) |
| YouTube search bucket per daily refresh | ~3-5 calls (0 when the niche cache is hit) |
| batchGetStats per daily refresh | ~10-15 calls |
| LLM per daily refresh | ~$0.20-0.30 (Haiku 4.5 + Sonnet 5.5) |
| Tavily news credits per day | niche-dependent (AI ≈ daily polling, dental ≈ weekly) |
| Daily refresh wall time | ≈35 s typical, ≤110 s worst (target < 120 s) |
| Onboarding wall time | crawl 3-8 min + analysis 1-2 min (tracked separately) |
