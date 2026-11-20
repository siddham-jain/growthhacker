# Runbook — running GrowthHacker locally

This is how to run the backend on a laptop with real keys and do the Apollo demo end to end through the REST API.

## 1. Prerequisites

- Python 3.12 and [uv](https://docs.astral.sh/uv/).
- PostgreSQL 18 on `localhost:5432` with role `growth`/`growth` and a database `growthhacker`. Homebrew works, and so does `docker compose up postgres redis`.
- Redis 8 on `localhost:6379`.
- A repo-root `.env` holding `YOUTUBE_API_KEY`, `ANTHROPIC_API_KEY` and `TAVILY_API_KEY`. Never commit this file.
- Also set `GH_SECURITY__HASH_PEPPER` in `.env` or your shell. It is a random secret used to hash API keys, for example from `python -c 'import secrets; print(secrets.token_urlsafe(32))'`. Keep it stable, because changing it invalidates every API key.

Every other setting has a default in `backend/app/core/config.py`. Override any of them with `GH_<SECTION>__<FIELD>`, e.g. `GH_REDIS__URL`.

## 2. Database

```bash
cd backend
uv sync
uv run alembic upgrade head        # single head: 0007_integration
uv run alembic heads               # should print exactly one head
```

## 3. Start the API and the workers

Use four terminals, or run each with `nohup … &`:

```bash
cd backend
uv run uvicorn app.api.app:create_app --factory --port 8000
uv run arq app.workers.settings.InteractiveWorkerSettings   # onboarding, analysis, cards
uv run arq app.workers.settings.BatchWorkerSettings         # scheduled daily refreshes, recrawls, backtests
uv run arq app.workers.settings.MaintenanceWorkerSettings   # every cron (see §7)
```

`GET /healthz` returns `{"status":"ok"}`. Each worker logs `worker.started` once its startup has run: registrations loaded, container built, embedding model warmed. If that line is missing, jobs fail with `KeyError: 'state'`.

## 4. Demo workspace and API key

```bash
uv run python -m app.api.cli bootstrap --workspace "Demo" --email owner@example.com \
  --password 'choose-a-long-one' --auto-confirm
```

- The key (`ghk_local_…`) is printed once. Store it and send it as `Authorization: Bearer <key>`.
- `--auto-confirm` marks the workspace as a demo. A client's onboarding output (profile claims, keywords, buyer questions and the proposed competitor set) is then accepted automatically, and the analysis run starts by itself.
- Re-running the command on an existing workspace mints another key.

Every POST needs an `Idempotency-Key` header, any unique string.

## 5. Demo: Apollo Hospitals

```bash
API=http://localhost:8000/api/v1; KEY=…; H=(-H "Authorization: Bearer $KEY" -H "Content-Type: application/json")

# onboarding (website + channel); returns the client and the queued onboarding run
curl -s -X POST $API/clients "${H[@]}" -H "Idempotency-Key: $(uuidgen)" -d '{
  "name": "Apollo Hospitals", "website_url": "https://www.apollohospitals.com",
  "channel_ref": "@ApolloHospitalsIndia",
  "market": {"country": "IN", "language": "en-IN", "timezone": "Asia/Kolkata"}, "window_days": 30}'

# live progress (server-sent events, Last-Event-ID replay)
curl -N $API/runs/<run_id>/events -H "Authorization: Bearer $KEY" -H "Accept: text/event-stream"
curl -N $API/clients/<client_id>/events -H "Authorization: Bearer $KEY" -H "Accept: text/event-stream"
```

**Onboarding.** The DAG is `resolve_inputs → crawl_site → build_profile → {discover_competitors, seed_topic_refs} + prefetch_own_channel → finish_onboarding`.

- `resolve_inputs` resolves the handle through 02's resolver. It then writes the channel and 02's ownership verdict onto the client. For Apollo, JSON-LD `sameAs` on the confirmed official site gives `verified`.
- When the run finishes, the client is `awaiting_confirmation` and the stream emits `run.awaiting_confirmation`.

**Confirm.**

- In a demo workspace this is automatic.
- Otherwise, review the profile, keywords, questions and competitors through the setup endpoints, then call `POST $API/clients/<client_id>/confirm-setup`. It returns 202 with the queued analysis run. The worker then freezes the competitor set, marks the profile confirmed and rebuilds the topic refs before it validates (3–10 competitors and at least one topic). A failed validation fails that run with the reason in `error`, and the client returns to `awaiting_confirmation`.

**Editing the proposed set.** Suppose a rival the proposal ranked just outside its slots should be tracked; on Apollo this was Narayana Health. Promote it, confirm it, then confirm the setup:

```bash
curl -s -X PATCH $API/clients/<client_id>/competitors/<cmp_id> "${H[@]}" -H "Idempotency-Key: $(uuidgen)" -d '{"role":"competitor_set"}'
curl -s -X POST  $API/clients/<client_id>/competitors/<cmp_id>/confirm "${H[@]}" -H "Idempotency-Key: $(uuidgen)" -d '{}'
curl -s -X POST  $API/clients/<client_id>/confirm-setup "${H[@]}" -H "Idempotency-Key: $(uuidgen)" -d '{}'
```

**Analysis.** The DAG is `snapshot_config → {ingest, niche_discovery, news, own_analytics, competitor_drift} → enrich → topics → analytics → refresh → recommend → write_cards → finalize`.

- `finalize` publishes all three windows atomically and the stream emits `results.published`.
- Read the results under `/clients/<client_id>/recommendations?window_days=30` plus `/early-signals`, `/hygiene`, `/refresh-suggestions`, `/creators`, `/news/opportunities` and `/analytics/*`.

**Name-only onboarding (any niche).** Send only `name` and `market`. `resolve_inputs` then finds the site through the knowledge base or web search, and the channel through the site's links:

```bash
curl -s -X POST $API/clients "${H[@]}" -H "Idempotency-Key: $(uuidgen)" -d '{
  "name": "Clove Dental", "market": {"country": "IN", "language": "en-IN", "timezone": "Asia/Kolkata"}}'
```

## 6. Warm daily refresh

- `schedule_daily_refreshes` enqueues one per active client at its local `refresh_hour_local` (default 06:00).
- To trigger one by hand: `POST $API/clients/<client_id>/runs` with `{"kind": "daily_refresh"}`.
- It reuses unchanged artifacts and ingests incrementally. The run's wall time is `finished_at - started_at` on the run resource.

Measured on Apollo (2026-11-20):

- Cold onboarding: about 4.5 min.
- Cold analysis: about 5–6 min with 10 competitors (topic adjudication ~3 min is now the longest stage; OCR is restricted to likely outliers, own uploads and a control sample).
- Warm daily refresh: 10–15 s and 0 YouTube units within the same 6 h fingerprint bucket; about 2.4 min when ingest, enrich and analytics must recompute (new uploads or creators), most of it card writing.

## 7. Crons (maintenance worker)

| Job | Cadence | Owner |
|---|---|---|
| reconcile_runs | every 2 min | 11: stale leases, lost jobs, stuck cancels, purges |
| schedule_daily_refreshes | every 10 min | 11 |
| snapshot_tick | every 10 min | 02: batchGetStats snapshots |
| news_poll | every 15 min | 08: shared news fetch for due queries |
| news_radar_tick | hourly | 11/08: per-client news scans by cadence |
| ops_alerts | every 15 min | 12 |
| retention_sweep | hourly | 01: 28-day refresh-or-delete, fail closed |
| cache_hits_flush | 00:20 daily | 02 |
| ops_rollup | 00:40 daily | 12 |
| competitor_rediscovery | 02:30 daily (each client monthly) | 04 |
| snapshot_maintenance | 03:10 daily | 02 |

Competitor ids are re-verified at 30 days by the retention sweep's `reverify_ids` rule on `competitor_sets`.

## 8. Tests

Tests use their own database and Redis, never `growthhacker`:

```bash
createdb -U growth growthhacker_test_11   # once
GH_TEST_DATABASE_URL=postgresql+asyncpg://growth:growth@localhost:5432/growthhacker_test_11 \
GH_TEST_REDIS_URL=redis://localhost:6379/11 uv run pytest -q -m "not live"
uv run ruff check app tests && uv run ruff format --check app tests
```

`-m live` runs the smoke tests against real APIs. They spend real quota; see each test for its budget.

## 9. Troubleshooting

- **Run stuck in `queued`.** Check that the interactive worker logged `worker.started`. The reconciler re-enqueues a queued run within 2 minutes once its arq job is gone.
- **`artifact_too_large`.** Artifacts hold pointers, at most 2 MB. Bulky outputs, such as the crawl acquisition on `crawl_runs.acquisition_gz`, are stored next to their owner's rows.
- **Quota.** Daily YouTube usage is in the quota ledger. A run's spend is in `usage` on the run resource (units, search calls, Tavily credits, LLM USD).
