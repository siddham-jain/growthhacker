# Runbook — running GrowthHacker locally

How to run the full stack (API, workers, UI) on a laptop with real keys, sign in, and run the Apollo and Replit demos.

## 1. Prerequisites

- Python 3.12 and [uv](https://docs.astral.sh/uv/); Node.js and pnpm for the UI.
- PostgreSQL 18 on `localhost:5432` with role `growth`/`growth` and a database `growthhacker`. Homebrew works, and so does `docker compose up postgres redis`.
- Redis 8 on `localhost:6379`.
- A repo-root `.env` (never committed) with:
  - `YOUTUBE_API_KEY`, `ANTHROPIC_API_KEY`, `TAVILY_API_KEY`;
  - `GH_SECURITY__HASH_PEPPER`: a random secret that hashes API keys, e.g. `python -c 'import secrets; print(secrets.token_urlsafe(32))'`. Keep it stable; changing it invalidates every API key;
  - `GH_SECURITY__TOKEN_ENCRYPTION_KEYS=[{"kid":"k1","key":"<fernet key>"}]`, needed to store OAuth tokens and workspace LLM keys. Generate the key with `uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`;
  - `GH_API__SESSION_COOKIE_SECURE=false`, because the local UI is plain http.

Every other setting has a default in `backend/app/core/config.py`. Override any of them with `GH_<SECTION>__<FIELD>`, e.g. `GH_REDIS__URL`.

## 2. Database

```bash
cd backend
uv sync
uv run alembic upgrade head        # single head: 0012_citations
uv run alembic heads               # should print exactly one head
```

## 3. Start the stack

Run each in its own terminal, or in the background with logs:

```bash
mkdir -p /tmp/gh-run
cd backend
nohup uv run uvicorn app.api.app:create_app --factory --host 127.0.0.1 --port 8000 > /tmp/gh-run/api.log 2>&1 &
nohup uv run arq app.workers.settings.InteractiveWorkerSettings > /tmp/gh-run/interactive.log 2>&1 &  # onboarding, analysis, cards
nohup uv run arq app.workers.settings.BatchWorkerSettings > /tmp/gh-run/batch.log 2>&1 &              # daily refreshes, backtests
nohup uv run arq app.workers.settings.MaintenanceWorkerSettings > /tmp/gh-run/maintenance.log 2>&1 &  # every cron (§8)

cd ../frontend
pnpm install
pnpm build && nohup pnpm start -p 3000 > /tmp/gh-run/web.log 2>&1 &
```

- `GET http://localhost:8000/healthz` returns `{"status":"ok"}`.
- Each worker logs `worker.started` once registrations are loaded, the container is built and the embedding model is warm. Without that line, jobs fail with `KeyError: 'state'`.
- The UI talks to `http://localhost:8000/api/v1` by default (`NEXT_PUBLIC_API_BASE`). `pnpm dev:mock` serves every screen from built-in Apollo fixtures with no backend.
- Stop everything with `pkill -f "uvicorn app.api.app"; pkill -f "arq app.workers.settings"; pkill -f "next start"`.

## 4. Demo login and API key

```bash
cd backend
uv run python -m app.api.cli bootstrap --workspace "Demo" --email demo@growthhacker.local \
  --password 'growthhacker-demo' --auto-confirm
```

- Open <http://localhost:3000> and sign in as `demo@growthhacker.local` / `growthhacker-demo`. This is a local demo account; use a real password anywhere else.
- The command also prints an API key (`ghk_local_…`) once. Send it as `Authorization: Bearer <key>`.
- `--auto-confirm` marks the workspace as a demo: a client's onboarding output (profile claims, keywords, buyer questions, proposed competitors) is accepted automatically and the analysis starts by itself.
- Re-running the command on an existing workspace adds the user if new and mints another key. It never changes an existing user's password.

Every POST needs an `Idempotency-Key` header, any unique string.

## 5. Demo: Apollo Hospitals

In the UI: **Clients → Add client**, enter `apollohospitals.com`, and watch the live analysis. Through the API:

```bash
API=http://localhost:8000/api/v1; KEY=…; H=(-H "Authorization: Bearer $KEY" -H "Content-Type: application/json")

curl -s -X POST $API/clients "${H[@]}" -H "Idempotency-Key: $(uuidgen)" -d '{
  "name": "Apollo Hospitals", "website_url": "https://www.apollohospitals.com",
  "channel_ref": "@ApolloHospitalsIndia",
  "market": {"country": "IN", "language": "en-IN", "timezone": "Asia/Kolkata"}, "window_days": 30}'

# live progress (server-sent events, Last-Event-ID replay)
curl -N $API/runs/<run_id>/events -H "Authorization: Bearer $KEY" -H "Accept: text/event-stream"
```

**Onboarding.** `resolve_inputs → crawl_site → build_profile → {discover_competitors, seed_topic_refs} + prefetch_own_channel → finish_onboarding`. For Apollo, JSON-LD `sameAs` on the official site verifies the channel. The client then becomes `awaiting_confirmation` (or moves straight on in a demo workspace).

**Confirm.** Outside demo mode, review the profile, keywords, questions and competitors in the UI (or the setup endpoints), then `POST $API/clients/<client_id>/confirm-setup`. It returns 202 with the queued analysis run; the worker freezes the competitor set and rebuilds topics before it validates (3–10 competitors, at least one topic). To promote a rival the proposal ranked just outside its slots:

```bash
curl -s -X PATCH $API/clients/<client_id>/competitors/<cmp_id> "${H[@]}" -H "Idempotency-Key: $(uuidgen)" -d '{"role":"competitor_set"}'
curl -s -X POST  $API/clients/<client_id>/competitors/<cmp_id>/confirm "${H[@]}" -H "Idempotency-Key: $(uuidgen)" -d '{}'
curl -s -X POST  $API/clients/<client_id>/confirm-setup "${H[@]}" -H "Idempotency-Key: $(uuidgen)" -d '{}'
```

**Analysis.** `snapshot_config → {ingest, niche_discovery, news, own_analytics, competitor_drift} → enrich → topics → analytics → refresh → recommend → write_cards → finalize`. `finalize` publishes all three windows at once. Results: `/clients/<id>/recommendations?window_days=30` (add `&format=contract` for the strict assignment shape), `/early-signals`, `/hygiene`, `/refresh-suggestions`, `/creators`, `/news/opportunities`, `/answer-citations`, `/analytics/*`.

Re-run an analysis on an existing client with `POST $API/clients/<id>/runs` and `{"kind": "analysis", "force": true}` (`force` recomputes every stage).

**Name-only onboarding.** Send only `name` and `market`; `resolve_inputs` finds the site through the knowledge base or web search, and the channel through the site's links:

```bash
curl -s -X POST $API/clients "${H[@]}" -H "Idempotency-Key: $(uuidgen)" -d '{
  "name": "Clove Dental", "market": {"country": "IN", "language": "en-IN", "timezone": "Asia/Kolkata"}}'
```

**Replit.** Onboard `replit.com` (US market). It shows the thin-data path: the competitor set (OpenAI, GitHub, Anthropic, Bolt.new, Emergent…) has outliers, but no Replit topic has three outlier references in the window, so the card list stays empty with "Not enough outliers… We'll check again", while hygiene, answer citations and the radar still fill in.

**Answer citations** run weekly (Monday 04:40). To run them now for chosen clients, see the `AnswerCitationRunner` in `app/services/answer_citations/task.py` (10 Tavily credits per client).

## 6. Warm daily refresh

- `schedule_daily_refreshes` enqueues one per active client at its local `refresh_hour_local` (default 06:00).
- By hand: `POST $API/clients/<client_id>/runs` with `{"kind": "daily_refresh"}`.
- It reuses unchanged artifacts and ingests incrementally. Wall time is `finished_at - started_at` on the run.

Measured on 2026-11-20 (details in `docs/evaluation/EVALUATION.md`):

| | Apollo Hospitals | Replit |
|---|---|---|
| onboarding | 3.3–4.5 min, 54–60 units, 2–6 searches | 7.7 min, 62 units, 4 searches |
| analysis | 98 s forced, 73 units | 219 s, 66 units, 1 search |
| warm daily refresh | 77 s, 10 units; 10 s, 0 units when repeated | 109 s, 11 units; 23 s, 0 units when repeated |

## 7. LLM budgets

Caps in `llm.budgets`: $1.50 per onboarding or analysis run, $0.75 per daily refresh, $3.00 per client per UTC day, $50 global per day. When a cap is reached, cards fall back to template wording (same numbers and evidence) and topic adjudication is deferred; the UI shows "Some cards use plain wording today". Raise a cap for a session with e.g. `GH_LLM__BUDGETS__CLIENT_DAILY_MAX_USD=6` on the worker processes.

## 8. Crons (maintenance worker)

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
| answer_citations | Monday 04:40 | 03/07: web answer search for the top 10 buyer questions |

Competitor ids are re-verified at 30 days by the retention sweep's `reverify_ids` rule on `competitor_sets`.

Keep the maintenance worker running while a backtest is pending: `snapshot_tick` captures the day-7 views its outcomes need.

## 9. Evaluation

All commands run from `backend/` as `uv run python -m app.evaluation <group> <command>`; outputs that hold API data go to `var/` (git-ignored).

| task | command |
|---|---|
| ops report | `ops report --from <iso>` → `docs/evaluation/ops-report.md` |
| freeze inputs at T | `freeze create --client <uuid>` (then `POST /api/v1/evaluation/backtests` to pre-register) |
| score a backtest | `backtest score --eval-id <id>` once outcomes mature |
| outlier labelling pack | `labels export --labeller a --labeller b` |
| human review pack | `review export --rater mira --rater marketer` |
| policy matrix | `policy render`, `policy check` |

Current status, ids and dates: `docs/evaluation/EVALUATION.md`.

## 10. Tests

Tests use their own database and Redis, never `growthhacker`:

```bash
cd backend
createdb -U growth growthhacker_test_11   # once
GH_TEST_DATABASE_URL=postgresql+asyncpg://growth:growth@localhost:5432/growthhacker_test_11 \
GH_TEST_REDIS_URL=redis://localhost:6379/11 uv run pytest -q -m "not live"
uv run ruff check app tests && uv run ruff format --check app tests

cd ../frontend
pnpm test && pnpm lint && pnpm typecheck && pnpm build
pnpm test:e2e                             # playwright against the mock build
```

`-m live` runs the smoke tests against real APIs. They spend real quota; see each test for its budget.

## 11. Troubleshooting

- **Run stuck in `queued`.** Check that the worker for its queue logged `worker.started`. The reconciler re-enqueues a queued run within 2 minutes once its arq job is gone.
- **Sign-in works but every call is 401.** The session cookie is marked `Secure`; set `GH_API__SESSION_COOKIE_SECURE=false` for http.
- **API keys stop working.** `GH_SECURITY__HASH_PEPPER` changed. Mint a new key with the bootstrap command.
- **`artifact_too_large`.** Artifacts hold pointers, at most 2 MB. Bulky outputs, such as the crawl acquisition on `crawl_runs.acquisition_gz`, are stored next to their owner's rows.
- **Quota.** Daily YouTube usage is in `yt_quota_ledger`; a run's spend is in `usage` on the run resource (units, search calls, Tavily credits, LLM tokens and USD from the provider ledger). `GET /api/v1/ops/quota` shows today's buckets.
