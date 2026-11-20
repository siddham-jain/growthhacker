# Operations report

- Period (UTC): 2026-11-13T04:02:27.994605+00:00 to 2026-11-20T04:02:27.994605+00:00
- Quota days (America/Los_Angeles, Google's reset): 2026-11-12 to 2026-11-19
- Generated: 2026-11-20T04:02:28.114824+00:00
- Clients with activity: 0
- Measured runs present: no
- Every figure is calculated by GrowthHacker from its own ledgers; none is a YouTube metric.
- Note: No daily_refresh runs were recorded in this period; the planning figures below are estimates.

## Targets

| target | status |
|---|---|
| Daily refresh p95 wall time < 120 s (warm cache) | no data |
| Onboarding p95 wall time ≤ 8 min (tracked separately) | no data |
| Median main-bucket units per client-day within budget | no data |
| LLM price table verified recently | met |
| No API data past the retention limit | met |

## Time to generate recommendations

Definition (99 C69): wall time of a `daily_refresh` run from its first stage start to finalize, excluding queue wait, p95 < 120 s with a warm cache. Onboarding is reported separately.

### daily_refresh

No daily_refresh runs measured.

## Quota units per client per daily refresh

No client quota usage recorded.

## Capacity estimate

Clients one project can refresh per day = bucket daily limit ÷ median amortised units per client-day.

| bucket | clients per day |
|---|---|
| main | n/a |
| search | n/a |
| batch_stats | n/a |

## LLM cost

No LLM calls recorded.

## Tavily credits

No Tavily calls recorded.

## Crawl

No crawl ledger rows recorded.

## Failures

No runs recorded.

## Retention compliance

40 API-data rules audited: compliant.

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
