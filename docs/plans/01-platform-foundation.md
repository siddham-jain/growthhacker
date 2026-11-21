# 01 — Platform foundation

Data model, tenancy, auth, config, DI, retention, deployment. Every other component builds on the tables, repositories, settings and container defined here. Where another component owns the logic for a table (for example 06 owns `yt_video_metrics`), this plan gives the storage shape. The column lists are authoritative; the owning plan may add columns through a migration but must not change keys or retention class.

Sources were checked on 2026-11-20. Policy citations use the section numbers of the [YouTube API Services Developer Policies](https://developers.google.com/youtube/terms/developer-policies) ("DP §x").

---

## 1. Purpose & business value

GrowthHacker sells recommendations that are evidenced, fresh and specific to the client. The foundation makes that possible in four ways:

- **Quota leverage.** All tenants share one cache of public YouTube data. The second dental clinic that tracks the same competitor costs zero extra units. That keeps the 10k units/day project quota spread across many clients and keeps per-client refresh well under 2 minutes, which is the ops target in the assignment.
- **Trust and compliance.** If we break YouTube policy (30-day storage, revocation deletion, Authorized Data visibility), we can lose API access and the product stops working. The retention engine enforces policy by construction: a table with no data-class classification fails CI.
- **Tenant isolation.** An agency workspace runs many client businesses. A client's keywords, buyer questions, competitor list, OAuth analytics and recommendations must never leak to another client. Isolation is enforced in the repository layer, with optional Postgres RLS as defence in depth.
- **Extensibility without rewrites.** Registries plus ports and adapters let us add a new LLM provider, freshness source, recommender type, retention rule or JSONB schema version without editing core code. This supports the "beyond the assignment" features (news radar, description refresh) and future ones.

Link to conversions: the conversion lens depends on tenant-owned buyer questions, conversion actions and own-channel OAuth analytics. The data model gives these first-class rows with stable ids so topic matches, recommendations and feedback can point at the exact question a video answers. Feedback rows (`made_it`, `produced_video_id`) close the loop from recommendation to outcome.

## 2. Scope / non-scope

**In scope**
- Full relational schema: the shared public YouTube cache, shared non-YouTube caches (news, embeddings), tenant data, auth, ops ledgers.
- YouTube policy design for sharing cached public data (answers design-doc question 2).
- Retention, purge, refresh and redaction jobs; deletion requests; OAuth revocation handling.
- Multi-tenancy model, authentication (sessions plus API keys), authorization roles, row scoping, secrets, OAuth token encryption and key rotation.
- `Settings` (pydantic-settings), composition root/container, ports and registries base, repository and unit-of-work pattern, Alembic strategy, transaction boundaries.
- Deployment topology, Redis roles, Postgres sizing, partitioning, pooling, docker compose, prod outline, local Homebrew setup.
- `Clock` port (frozen time for backtests and tests), `LockManager`, `SecretCipher`.

**Not in scope** (owned elsewhere)
- YouTube gateway, quota manager algorithm, snapshot cadence (02). Here: tables, ledger and the Redis primitives they use.
- Crawler, profile extraction (03); competitor discovery (04); topic matching logic (05); metric formulas (06); recommenders and gates (07); news (08); refresh engine (09); LLM layer (10); pipeline runner, REST/SSE endpoints (11); evaluation methodology (12); frontend (13).
- "Ask your analyst" chat (paused). Billing and plans beyond a `plan` text column.

## 3. Inputs & outputs

This component consumes nothing from the others. It produces the following domain and infrastructure objects (pydantic v2 style; `Annotated` constraints shown informally).

```text
Settings (BaseSettings, env_nested_delimiter="__", env_prefix="GH_")
  app: AppSettings            env: Literal["local","test","staging","prod"]; base_url; cors_origins: list[str]; log_level; log_json: bool
  db: DatabaseSettings        url: PostgresDsn (SecretStr); pool_size=5; max_overflow=5; pool_timeout_s=10; statement_timeout_ms=30000;
                              idle_in_tx_timeout_ms=60000; use_external_pooler: bool=False; echo=False
  redis: RedisSettings        url (SecretStr); queue_name="gh:queue"; key_prefix="gh"; lock_default_ttl_s=60
  youtube: YouTubeSettings    api_key: SecretStr; quota_daily_units=10000; search_daily_calls=100; quota_timezone="America/Los_Angeles";
                              compliance_reserve_pct=10
  google_oauth: GoogleOAuthSettings  client_id; client_secret: SecretStr; redirect_uri; scopes: list[str]; risc_enabled=False
  llm: LLMSettings            providers: dict[str, LLMProviderSettings]; tiers: dict[Literal["low","high"], LLMTierSettings]   (shape owned by 10)
  tavily: TavilySettings      api_key: SecretStr; ... (owned by 03/08)
  security: SecuritySettings  token_encryption_keys: list[EncryptionKey] (first = primary); hash_pepper: SecretStr;
                              comment_author_pepper: SecretStr; session_ttl_h=336; session_idle_ttl_h=72; api_key_prefix="ghk"
  retention: RetentionSettings  max_age_days=30; safety_margin_days=2; snapshot_partition_days_ahead=7; comments_ttl_days=28;
                              search_cache_default_ttl_h=72; news_items_ttl_days=90; crawled_pages_ttl_days=180; run_events_ttl_days=30;
                              ledger_ttl_days=400; refresh_batch_size=50; delete_batch_size=1000; stats_storage_mode: Literal["standard","derived_metrics_approved"]="standard"
  workers: WorkerSettings     max_jobs_default=10; max_jobs_ingest=20; max_jobs_cpu=2; job_timeout_s=900; health_check_interval_s=60
  features: FeatureFlags      rls_enabled=False; news_radar=True; description_refresh=True; oauth_analytics=True

EncryptionKey: kid: str (e.g. "k2026a"); key: SecretStr (urlsafe b64 32 bytes)

Principal                     kind: Literal["user","api_key","system"]; id: UUID; workspace_id: UUID; role: Role; scopes: frozenset[str]
Role (StrEnum)                owner | admin | analyst | viewer
TenantScope                   workspace_id: UUID; client_id: UUID | None   # required by every tenant repository
DataClass (StrEnum)           yt_public_content | yt_public_stats_series | yt_public_derived | yt_authorized_analytics |
                              tenant_derived_from_api | tenant_owned | external_non_youtube | ops_ledger | auth_secret
RetentionRule                 table: str; data_class: DataClass; clock_columns: tuple[str,...]; max_age: timedelta;
                              action: Literal["refresh_or_delete","delete","drop_partition","redact","verify_auth"];
                              selector: RetentionSelector (registry name)
RetentionReport               started_at; finished_at; per_table: dict[str, {scanned, refreshed, deleted, redacted, oldest_age_s}];
                              violations: list[str]   # any row older than max_age → alert
ArtifactEnvelope[T]           kind: str; scope_key: str; schema_version: int; payload: T; referenced_video_ids: list[str];
                              referenced_channel_ids: list[str]; api_data_observed_at: datetime | None
ClientSettings (JSONB, versioned)  window_days: Literal[7,30,90]=30; min_reference_videos=3; refresh_cadence: Literal["daily","weekly","manual"]="daily";
                              thresholds: dict[str, float] (overrides of component defaults); news_radar_enabled: bool; refresh_engine_enabled: bool
EffectiveConfig               merged defaults → workspace.settings → client.settings → run.params; frozen into runs.config_snapshot
```

Storage shapes for objects owned by other components are in §6. Their names follow the shared vocabulary: BusinessProfile, BuyerQuestion, ChannelRecord, VideoRecord, VideoSnapshot, CommentRecord, TopicMatch, VideoMetrics, ChannelBaseline, VideoTraits, ThumbnailTraits, NewsItem, NewsSignal, Recommendation, DescriptionRefresh, RunEvent.

## 4. Internal design

### 4.1 Package layout (foundation parts)

```text
backend/app/core/
  config.py        Settings + sub-models; get_settings() cached; settings_customise_sources: init > env > .env > secrets_dir
  clock.py         Clock Protocol; SystemClock; FrozenClock(at) for backtests/tests
  db.py            create_engine(settings) → AsyncEngine; session_factory; naming_convention MetaData; UnitOfWork
  redis.py         redis pool; LockManager; RateLimiter; QuotaCounter primitives (Lua scripts)
  security.py      SecretCipher (MultiFernet + row binding); hash_token(); api key/session minting; password hashing
  tenancy.py       TenantScope, Principal, require_role(), scoped select helpers
  logging.py       structlog JSON; redaction processor (SecretStr, tokens, emails)
  registry.py      Registry[T] generic
  container.py     Container dataclass + build_container(settings, clock) (composition root)
app/models/        ORM tables (one module per bounded area: youtube_cache.py, tenancy.py, profile.py, runs.py, recs.py, eval.py, ledgers.py)
app/repositories/  shared/ (no scope) and tenant/ (scope-required) repositories
app/retention/     rules.py (registry), sweeper.py, partitions.py, redaction.py, oauth_revocation.py, deletion_requests.py
app/workers/       arq WorkerSettings per queue; cron registration (owned by 11; foundation provides retention/partition crons)
migrations/        alembic (async env.py)
```

### 4.2 Ports added by the foundation (to `app/ports/`)
- `Clock`: `now() -> datetime` (aware UTC), `today(tz: ZoneInfo) -> date`. Every service gets time from here, never from `datetime.now()`. A backtest at frozen T injects `FrozenClock(T)`.
- `LockManager`: `acquire(key, ttl, wait=None) -> AsyncContextManager[Lease]`, `single_flight(key, ttl, fn)`. Redis adapter (`SET key token NX PX`, Lua compare-and-delete release, renew task).
- `SecretCipher`: `encrypt(plaintext: bytes, *, binding: str) -> (kid, ciphertext)`, `decrypt(kid, ciphertext, *, binding) -> bytes`, `rotate(...)`. MultiFernet adapter now; a KMS envelope adapter can be swapped in later.
- `UnitOfWork`: `async with uow: ...; await uow.commit()`. Exposes repositories bound to its session.
- The existing shared ports (YouTubeGateway, LLMClient, ...) are defined by their owners. The foundation only provides the `Registry` they register into.

### 4.3 Registries (open/closed)
`Registry[T]` is a name → factory map with `register(name)` decorator, `get(name)`, `all()`. Duplicate names raise at import time.

| Registry | Key | Used by |
|---|---|---|
| `adapter_registry[port]` | provider name from settings (`anthropic`, `openai_compat`, `tavily`, `fastembed`) | container picks implementations |
| `stage_registry` | stage name | 11 pipeline DAG |
| `recommender_registry` | recommendation type | 07 |
| `quality_gate_registry` | gate name | 07 |
| `trait_extractor_registry` | trait name | 06 |
| `freshness_source_registry` | source name | 08 |
| `artifact_schema_registry` | (kind, schema_version) → pydantic model + upcaster to next version | everyone writing JSONB |
| `retention_registry` | table name → RetentionRule | retention sweeper; CI invariant: every ORM table is registered |
| `redactor_registry` | table/kind → redaction function | retention redact action |

### 4.4 Composition root
`build_container(settings, clock=SystemClock()) -> Container` is the only place that knows concrete classes. The `Container` holds: `settings`, `clock`, `engine`, `session_factory`, `redis`, `http` (shared `httpx.AsyncClient` with limits), `lock_manager`, `cipher`, and lazily built adapters (`youtube`, `llm`, `search`, `crawler`, `embedder`, `freshness_sources`, `buyer_question_source`, `progress_reporter`), each resolved from `adapter_registry` by settings key. No DI framework: plain functions and `functools.cached_property`.

- **API:** the FastAPI `lifespan` builds the container and stores it on `app.state.container`; shutdown disposes the engine, redis and http. Dependencies are `get_container`, `get_uow` (request scoped), `get_principal` (auth) and `get_scope(client_id)` (checks the client belongs to the principal's workspace).
- **Workers:** arq `on_startup(ctx)` builds the same container into `ctx["container"]`; `on_shutdown` disposes it. Each job builds its own UoW(s).
- **Tests:** `build_container(test_settings, FrozenClock(...))` with adapter overrides via `container.override(port, fake)`.

### 4.5 Repository and unit-of-work pattern
- Repositories wrap an `AsyncSession` and return domain objects (pydantic) or ORM rows inside the service layer only. They never commit; the UoW commits.
- **Shared repositories** (`ChannelRepo`, `VideoRepo`, `SnapshotRepo`, `CommentRepo`, `SearchCacheRepo`, `NewsItemRepo`, `EmbeddingCacheRepo`, `QuotaLedgerRepo`) take no scope.
- **Tenant repositories** subclass `TenantRepository[Model]`. The constructor requires a `TenantScope`. Every query goes through `self._scoped(select(Model))`, which adds `Model.workspace_id == scope.workspace_id` (and `client_id` when the model has it). `get(id)` with an id from another tenant returns `None`, so the API answers 404 and never reveals existence.
- Bulk writes use `insert(...).on_conflict_do_update(index_elements=..., set_=..., where=...)` ([SQLAlchemy PG dialect](https://docs.sqlalchemy.org/en/20/dialects/postgresql.html)). `set_` must list `updated_at` explicitly because Python-side `onupdate` is not applied. Batches are ≤ 1000 rows per statement (asyncpg parameter limit 32767 ÷ columns).
- **Monotonic upsert rule for the shared cache:** `WHERE excluded.metadata_fetched_at >= yt_videos.metadata_fetched_at`. A slow worker holding an older API response cannot overwrite newer data.

### 4.6 Session and transaction boundaries
- `AsyncSession` is not safe across concurrent tasks ([SQLAlchemy asyncio](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html)). Each asyncio task that touches the DB gets its own session. `expire_on_commit=False`.
- **API request:** one UoW per request. GET requests run in a read-only transaction (`SET TRANSACTION READ ONLY`). Mutations commit once at the end. Long work is never done in the request; the request enqueues a run and returns `202` with the run id.
- **Worker jobs:** never hold a DB transaction open across an external call (YouTube, LLM, Tavily, crawl). The pattern is: short tx (read what to do) → close → external I/O → short tx (idempotent upsert + ledger + run event) → commit. Stage outputs are committed per stage so a retry resumes (checkpointing, §5.7).
- **Retention jobs:** each batch is its own transaction (≤ 1000 rows) so locks stay short and progress is durable.
- Server-side guards per role: `statement_timeout` 30s for app (retention/migrations use a separate role or `SET LOCAL` with a higher value), `idle_in_transaction_session_timeout` 60s, `lock_timeout` 5s for app, 2s for partition DDL.
- Connection `server_settings={"timezone": "UTC", "application_name": "gh-api|gh-worker-…"}`.

### 4.7 Multi-tenancy and auth
- **Hierarchy:** `workspace` (an agency or a direct business account) → `clients` (businesses analysed, e.g. Apollo Hospitals) → all client data. Users join workspaces through `workspace_members` with a role.
- **Roles:** owner (billing, delete workspace, keys), admin (manage clients, members, OAuth connect/revoke), analyst (create clients, confirm competitors, trigger runs, feedback), viewer (read only). `require_role(Role.analyst)` is a FastAPI dependency.
- **Authentication paths:**
  1. **User sessions** for the Next.js app: email + password (argon2id) to start. Google Sign-In can be added as an adapter later. Login issues an opaque 256-bit token in an `HttpOnly; Secure; SameSite=Lax` cookie. The DB stores `sha256(token)` only. Sessions expire after 14 days absolute or 72h idle. `last_seen_at` is updated at most every 5 minutes, through a Redis cache of `session_hash → principal` with a 5-minute TTL.
  2. **API keys** for server-to-server use (e.g. Spotlit calling our recommendations API): `ghk_<prefix12>_<secret43>`. The DB stores the prefix (indexed, shown in UI) and `sha256(pepper || secret)`. Constant-time compare. Keys carry scopes (`read`, `runs:write`, `clients:write`, `admin`), are shown once, and can be revoked.
  3. **System principal** for cron jobs (no HTTP).
- CSRF: the cookie session is `SameSite=Lax`, and mutating endpoints also require an `X-Requested-With` header or double-submit token. API keys use `Authorization: Bearer` and are not exposed to CSRF.
- **Row scoping:** every tenant table carries `workspace_id` (denormalized even when `client_id` implies it). This makes repository filters and optional RLS trivial. Composite FKs `(workspace_id, client_id) → clients(workspace_id, id)` stop a row from pointing at another workspace's client.
- **RLS (feature-flagged hardening, phase 2):** `ALTER TABLE … ENABLE ROW LEVEL SECURITY` with policy `workspace_id = current_setting('app.workspace_id', true)::uuid` on tenant tables. The UoW runs `SET LOCAL app.workspace_id = …` at transaction start. Workers that do cross-tenant work (cron fan-out, retention) use a separate `gh_maintenance` role with `BYPASSRLS`. `SET LOCAL` is transaction scoped, so it is safe with transaction-mode poolers ([PG RLS](https://www.postgresql.org/docs/18/ddl-rowsecurity.html)).
- **Authorized Data visibility (DP §III.E.3.b):** own-channel OAuth analytics may only be shown to the authorizing user and "agents expressly approved by that user". On OAuth connect, the consent screen lists the workspace members who will see the data, and that list is recorded in `oauth_tokens.approved_viewer_user_ids`. Endpoints that serve `own_analytics_reports` check the viewer is in that list. Changing the list requires the granting user or an owner (see §13).

### 4.8 Secrets handling
- `SecretStr` for every credential in `Settings`. A structlog processor drops or redacts known secret keys, bearer tokens, cookies, `refresh_token`, and email/phone patterns.
- **Local:** `.env` (gitignored; `.env.example` lists names only). **Prod:** cloud secret manager mounted as files (`secrets_dir=/run/secrets`; pydantic-settings reads one file per field, per the [pydantic-settings docs](https://pydantic.dev/docs/validation/latest/concepts/pydantic_settings/)) or injected env. Separate keys per environment.
- Exactly one Google Cloud project for the API client (DP §III.D.1.c). No extra keys or projects to stretch quota (DP §III.D.3). The YouTube API key is restricted to the YouTube Data API and to server egress IPs in prod.
- Never log or store raw OAuth tokens, API keys or session tokens. Never send them to the LLM.

### 4.9 OAuth token encryption at rest and rotation
- Ciphertext is `MultiFernet([primary, *older])` over the JSON `{"refresh_token": ..., "binding": "<client_id>:<provider>:<google_subject>"}`. Fernet is AES-128-CBC + HMAC-SHA256 with an embedded timestamp ([cryptography Fernet](https://cryptography.io/en/latest/fernet/)). Fernet has no associated data, so the binding is put inside the plaintext and checked on decrypt. A DB-level attacker who swaps ciphertexts between rows gets an `InvalidBinding` error, not another tenant's token.
- `oauth_tokens.key_id` records which kid encrypted the row. Access tokens are not persisted. They live in Redis (`gh:oauth:at:{token_id}`, encrypted with the same cipher, TTL = expires_in − 60s).
- **Rotation runbook:** (1) generate a new key and prepend it to `GH_SECURITY__TOKEN_ENCRYPTION_KEYS` → deploy (all processes can now decrypt both and encrypt with the new key); (2) run arq job `rotate_token_encryption`, which re-encrypts rows `WHERE key_id <> primary_kid` in batches with `MultiFernet.rotate` (the original timestamp is kept); (3) when `count(*) WHERE key_id <> primary` = 0, remove the old key → deploy. The job is idempotent and resumable. An audit log entry is written per batch.
- **Compromise:** rotate the encryption key, and also revoke all refresh tokens at Google (`POST https://oauth2.googleapis.com/revoke`) and ask clients to reconnect.
- `hash_pepper` and `comment_author_pepper` are versioned (`kid` prefix inside the stored hash). The comment pepper rotates every 30 days on purpose, so author hashes cannot be linked across months.

### 4.10 Config layering
`EffectiveConfig` = component defaults in `Settings` → `workspaces.settings` → `clients.settings` (validated `ClientSettings`, versioned) → `runs.params`. The merged result is stored in `runs.config_snapshot` so any recommendation can be reproduced and explained ("min_reference_videos was 3"). Component plans add their own `XxxSettings` sub-model under `Settings`. They register defaults; they never read env directly.

## 5. Algorithms & logic

### 5.1 Sharing cached public data within YouTube policy (design-doc question 2)

**Answer:** we keep one shared copy of public (API-key, Non-Authorized) data per channel, video and query, with a single storage clock per row. Every tenant reads from it. Tenant-specific interpretation lives in separate tenant-scoped rows. This is compliant because:

1. **The obligations bind the API Client, not each of our customers.** GrowthHacker is one API Client with exactly one API project (DP §III.D.1.c). Data from `channels.list`/`videos.list`/`search.list` with an API key is Non-Authorized Data ("API Data accessible by an API Client without User Credentials", DP §IV). That data "may be stored temporarily... not longer than 30 calendar days", after which it must be refreshed or deleted (DP §III.E.4.d). The 30-day clock belongs to the stored datum, so storing it once and reading it N times does not multiply obligations. It reduces both stored data and API calls, which is what the assignment asks for ("cache public channel and video data once and reuse it across clients").
2. **Only Non-Authorized Data enters the shared cache.** Anything fetched with a user's OAuth credentials (YouTube Analytics reports; any `mine=true` data) is Authorized Data. It lives only in tenant tables tied to the `oauth_tokens` row. It is never written to `yt_*` tables, never used to enrich other tenants, and is visible only to the authorizing user and approved agents (DP §III.E.3.b). Public data about the client's own channel is still fetched with the API key and shared like any other channel.
3. **Statistics are capped at 30 days.** DP §III.E.4.b: "must not store statistics retrieved as Non-Authorized Data for more than 30 days". So:
   - Latest stats on `yt_videos`/`yt_channels` stay alive only by being refreshed (re-fetched).
   - Snapshot time series (`yt_video_snapshots`) cannot be refreshed, because a past value cannot be re-fetched. They are deleted by dropping daily partitions once older than `max_age - safety_margin` (default 28 days).
   - Stats-derived values (views at age, outlier ratios, baselines) carry `stats_observed_at` = oldest input observation, and expire 30 days from it.
   - If we are accepted for the **Derived Metrics and Data Storage** amendment (DP Section L; [derived-metrics policy](https://developers.google.com/youtube/terms/derived-metrics-policy): statistics and derived metrics may be stored up to 36 months for audited "Analytics & Reporting" use cases), `retention.stats_storage_mode = "derived_metrics_approved"` switches only the stats-series rules to 36 months. Content (titles, descriptions, comment text) stays on 30 days in both modes, as that policy states.
4. **Consistency and display.** DP §III.E.4.e requires reasonable efforts to keep stored data consistent with current data, and §III.E.4.f allows historical data "presented accurately in context of time". Every stat we serve carries `as_of` (its fetched/captured time). Metadata changes (title, description, thumbnail) are picked up on refresh and invalidate derived rows through `content_hash`. Videos that turn private or are deleted are blanked at once (§5.5).
5. **Derived metrics stay within "simple maths".** Views at age, ratios, medians and averages of API data fall under the [Developer Policies guide's](https://developers.google.com/youtube/terms/developer-policies-guide) "acceptable metrics" (addition, subtraction, averages, multiplication, division of YouTube API data only). They are never merged with website or news data into one number (shown side by side). Computed values are labelled as computed by GrowthHacker, not YouTube (DP §III.E.4.h disclosure).
6. **No tenant leakage through the shared cache.** Shared tables hold only API data and client-agnostic deterministic derivations (length band, title traits, thumbnail traits, views at age, channel baselines). Anything that depends on a client's keywords, buyer questions, timezone or competitor set (topic matches, publish-day traits, trait lift, recommendations) is tenant-scoped. `yt_search_cache.params` stores query text, which may come from one tenant's keywords. It is never exposed through any API, holds no tenant id, and quota attribution lives only in `yt_quota_ledger`.
7. **Who referenced what decides refresh vs delete.** Refresh is only spent on data some active tenant still needs (`v_tracked_channels`, unexpired recommendations, open runs). Everything else is deleted at the deadline. That keeps storage minimal (DP §II.4 "Don't store user data indefinitely").
8. **Required surface features:** link to YouTube ToS (DP §III.A.1); privacy policy linking the Google Privacy Policy (§III.A.2.c), explaining revocation via the Google security settings page (§III.A.2.h), and disclosing that cached public YouTube data is shared across customers to provide the service. In-app "Disconnect YouTube" revokes programmatically (§III.D.2).

### 5.2 Data classes and retention rules

Every table is registered with exactly one `DataClass` and a rule. `T = retention.max_age_days` (30); `A = T − safety_margin_days` (28), the "act-by" age.

| DataClass | Tables | Clock | Action at age ≥ A |
|---|---|---|---|
| yt_public_content | yt_channels, yt_videos, yt_channel_resolutions | `metadata_fetched_at`, `stats_fetched_at` (LEAST) | refresh if referenced, else delete |
| yt_public_content (delete-only) | yt_comments, yt_search_cache, embedding_cache | `fetched_at` / `created_at` | delete (comments TTL default 28d; search cache uses its own `expires_at` ≤ fetched_at+30d) |
| yt_public_stats_series | yt_video_snapshots | partition upper bound | drop partition (standard mode); 36-month rule in approved mode |
| yt_public_derived | yt_video_metrics, yt_channel_baselines, yt_video_traits, yt_thumbnail_traits | `stats_observed_at` / `source_verified_at` | recompute if referenced (cheap, no quota), else delete |
| tenant_derived_from_api | topic_matches, analysis_artifacts, recommendations, refresh_suggestions, news_signals.youtube_demand, evaluation_labels.context | `api_data_observed_at` | regenerate in the next refresh run; if not regenerated by A → redact (§5.4) |
| yt_authorized_analytics | own_analytics_reports, oauth_tokens | `authorization_verified_at` | re-verify authorization every ≤ 30d (DP §III.E.4.b); on failure/revocation purge per §5.6 |
| tenant_owned | workspaces, clients, business_profiles, keywords, buyer_questions, competitors, crawled_pages, runs, recommendation_feedback, news_opportunities, evaluation_label_sets, evaluation_reports | business TTLs (crawled_pages 180d, run_events 30d) | delete per TTL / tenant deletion |
| external_non_youtube | news_items, news_query_cache | `fetched_at` | delete at 90d / `expires_at` |
| ops_ledger | yt_quota_ledger, provider_usage_ledger, audit_log | `occurred_at` | delete at 400d; tenant ids nulled on tenant purge |
| auth_secret | users, user_sessions, api_keys, deletion_requests | expiry columns | delete expired sessions daily; revoked keys kept 90d for audit |

### 5.3 Retention sweeper (arq cron `retention_sweep`, hourly at :17, unique)
Each step is idempotent, batched and resumable, and records a `RetentionReport`.

1. **Partitions:** `ensure_partitions(yt_video_snapshots, days_ahead=7)`, then `drop_partitions(older_than=A)`. Detach with `DETACH PARTITION … CONCURRENTLY`, then `DROP TABLE`. Dropping is far cheaper than bulk `DELETE` and avoids VACUUM load ([PG 18 partitioning](https://www.postgresql.org/docs/18/ddl-partitioning.html)). A partition `pYYYYMMDD` is dropped when its upper bound is ≤ `now − A`.
2. **Reference set:** compute `referenced_channels` = `v_tracked_channels` for clients with `status in (active, awaiting_confirmation, onboarding)` and `last_activity_at > now − 60d`, ∪ channels of videos in unexpired published recommendations, ∪ channels pinned by queued/running runs (`runs.pinned_channel_ids`). Compute `referenced_videos` = videos of referenced channels with `published_at > now − (max_window 90d + baseline_horizon_days 180d)` ∪ client own-channel videos (all of them; the refresh engine needs them) ∪ videos in unexpired recommendations/refresh suggestions.
3. **Due rows:** keyset-scan `yt_videos WHERE LEAST(metadata_fetched_at, stats_fetched_at) < now − A + lookahead(6h)`, ordered by the clock, `LIMIT refresh_batch_size*20`. Same for `yt_channels`.
4. **Refresh or delete:**
   - Referenced rows are grouped into `videos.list` batches of 50 ids and `channels.list` batches of 50, then enqueued on the `ingest` queue as `refresh_cache_batch` with `_job_id = f"refresh:videos:{sha1(ids)}"` (arq rejects duplicate job ids while queued/running/result-kept; [arq docs](https://arq-docs.helpmanual.io/)), purpose `retention_refresh`, drawing on the quota **compliance reserve** (10% of daily units; owned by the 02 quota manager).
   - Unreferenced rows are deleted directly in batches of 1000. FK cascades remove snapshots, comments, traits and metrics.
   - **Fail closed:** any row whose age is ≥ `T − 1d` and still unrefreshed (quota exhausted, API error, job lost) is deleted, not kept. Deleting is always compliant; keeping is not.
5. **Shared derived:** delete `yt_video_metrics`, `yt_channel_baselines`, `yt_thumbnail_traits`, `yt_video_traits` rows past A whose source video was not refreshed. For refreshed videos, the next analysis run recomputes them. Thumbnail traits are "refreshed" when the video refresh returns the same thumbnail URL and a conditional GET (`If-None-Match`) on the image returns 304. Then `source_verified_at` is bumped; otherwise traits are recomputed.
6. **Delete-only classes:** `yt_comments` (fetched_at < now − comments_ttl), `yt_search_cache` (expires_at < now), `yt_channel_resolutions` (expires_at < now), `embedding_cache` (created_at < now − A), `news_query_cache`, `news_items` (> 90d).
7. **Tenant derived (§5.4):** redact/delete rows with `expires_at < now`.
8. **Ops:** expire sessions; delete run_events of runs finished > 30d ago; ledgers > 400d.
9. **Compliance audit query** (also exported as a metric): `max(now − clock)` per registered table. If any value > T − 1d → `retention.violation` error log and a pager alert. CI runs the same query against fixture data under a `FrozenClock`.

A separate cron, `retention_watchdog` (daily 03:00 UTC), runs step 9 only and alerts if the hourly sweep has not completed in 6h.

### 5.4 Derived artifacts that reference purged data
Rules:
- Every tenant artifact row that embeds API-derived content or numbers stores `referenced_video_ids text[]`, `referenced_channel_ids text[]` (GIN indexed), `api_data_observed_at` (oldest input) and `expires_at = api_data_observed_at + T`.
- **Normal path:** the daily refresh run regenerates artifacts from refreshed cache before they expire. Only the latest run's artifacts are "current"; older runs' artifacts are superseded and deleted at expiry.
- **Expiry path (client paused, run failed):** at `expires_at` the redactor for that table runs.
  - `recommendations`: keep `id, type, lens, summary, buyer_question_id, window_days, generated_at, status, feedback`. Set to NULL: `reference_videos, why_it_works, fit_to_client, expected_range_*, confidence_*, evidence, details` (API-derived parts). Then `status='expired'`, `redacted_at=now`. The API returns these as "expired; re-run to refresh evidence". Summary text is GrowthHacker-authored, but the writer (10) is barred from putting competitor titles or stat numbers in `summary`. That keeps redaction sound.
  - `analysis_artifacts`, `topic_matches`, `refresh_suggestions` (contains the original description), `news_signals.youtube_demand`: delete the row (or NULL the column for news_signals).
  - `evaluation_labels`: keep `video_id, label, labeler, labelled_at`; set `context` (the stats shown to the labeller) to NULL. `evaluation_reports` keep aggregate numbers only (precision, recall, lift rates), never per-video stats.
- **Early invalidation:** when a video becomes unavailable or is deleted from cache, the same transaction (or sweeper batch) looks up dependants with a GIN query `referenced_video_ids && ARRAY[...]`. No separate purge-event table is needed. Affected published recommendations get `stale_reference_count` incremented. If remaining references < `min_reference_videos`, the recommendation is flagged `needs_regeneration` and hidden by the API until the next run. This keeps the quality bar ("≥3 reference videos") true at read time, not only at generation time.
- **Read-time join:** the API never trusts embedded snapshots for display of live numbers. When rendering reference videos, it left-joins `yt_videos` and marks missing rows as `available=false` instead of showing stale embedded data.

### 5.5 Deleted/private videos and changed channels
- `videos.list` returns only the items it can see. Requested ids missing from the response are treated as unavailable: deleted, private, or rejected.
- **Handling:** set `availability='unavailable'` and `unavailable_at=now`; NULL `title, description, tags, thumbnail_url` and stats; delete its snapshots, comments, traits, metrics and topic matches; keep a tombstone row (id, channel_id, published_at, unavailable_at) for ≤ T days so ingest doesn't re-add it from a stale playlist page and artifacts can show "no longer available". The tombstone is deleted at `unavailable_at + A`.
- A tombstoned video that reappears in a later uploads page (made public again) gets a normal upsert that clears `unavailable_at`.
- Channels missing from `channels.list` follow the same pattern (`availability='unavailable'`). Tenants that reference the channel (own or competitor) get a client alert, and that competitor's status becomes `needs_attention`.
- **Handles:** `channel_id` (`UC` + 22 chars) is the only identity key. `handle` and `custom_url` are mutable attributes, and handles can be released and claimed by another channel. The `yt_channel_resolutions` cache (handle → id) expires after ≤ 7 days (configurable, max 30). When the user enters a handle that now resolves to a different id than a stored competitor or own channel, we do not auto-switch. We flag `needs_attention` with both ids and ask the user. `channels.list` accepts `forHandle` with or without `@` and costs 1 unit ([channels.list](https://developers.google.com/youtube/v3/docs/channels/list)).
- **Ownership verification for own channel** (the Apollo `@Apollo247` trap) is owned by 03/04. The foundation stores the evidence in `clients.own_channel_verification` and allows `own_channel_status='verified'` only when the evidence list is non-empty.

### 5.6 OAuth lifecycle and revocation deletion
- **Connect:** OAuth web flow with `access_type=offline`, `prompt=consent` and the needed scopes (`yt-analytics.readonly`, `youtube.readonly`). Verify that `channels.list(mine=true)` equals `clients.own_channel_id`; otherwise reject and store nothing. Store the encrypted refresh token and record `approved_viewer_user_ids`.
- **Verification every ≤ 25 days** (DP §III.E.4.b requires ≤ 30): cron `verify_oauth_tokens` (daily) refreshes the access token and calls `channels.list(mine=true)`. On success it sets `authorization_verified_at`. On `invalid_grant` (user revoked, 6 months unused, password change, 100-token limit, Testing-mode 7-day expiry — [Google OAuth token expiration](https://developers.google.com/identity/protocols/oauth2)) it sets `status='invalid'`, `revocation_source='invalid_grant'`, `purge_due_at=now` (Google allows 30 days; we purge at once).
- **In-app disconnect:** call `https://oauth2.googleapis.com/revoke`, set `status='revoked'`, `purge_due_at=now`, enqueue `purge_authorized_data(token_id)` (target ≤ 1h; policy limit 7 days, DP §III.D.2). Purge deletes `own_analytics_reports` (FK cascade from `oauth_tokens`), Authorized-derived artifacts (`analysis_artifacts.kind='own_performance_report'` with `uses_authorized_data=true`), Authorized parts of recommendations (`fit_to_client` sentences derived from analytics → redacted via the `uses_authorized_data` flag), and the Redis access token. A `deletion_requests` row tracks the SLA and writes a report.
- **Google-side revocation:** detected by the next refresh attempt (≤ 24h with the daily verifier) or instantly via Cross-Account Protection (RISC) `token-revoked`/`tokens-revoked` events if `risc_enabled` ([RISC](https://developers.google.com/identity/protocols/risc)). Events are de-duplicated by `jti`. The handling is the same as in-app.
- **User/client deletion request:** `deletion_requests` with `due_at = requested_at + 7d` (DP §III.E.4.g). The job hard-deletes tenant rows. Shared cache rows are not "user data" of the tenant and are left to normal retention, except Authorized data, which is always tenant-scoped.

### 5.7 Runs, concurrency and partial failure (storage side; orchestration is 11)
- **One active run per client per run family:** partial unique index `runs(client_id, run_family) WHERE status IN ('queued','running')`. `run_family` is `onboarding | analysis` (daily refresh and analysis share the `analysis` family; backtests are their own family and may run concurrently).
- **Trigger semantics:** `POST /runs` with an `Idempotency-Key` header → `runs.idempotency_key` unique per workspace. A duplicate returns the existing run. If an active run exists with identical `params_hash`, return it (200). If params differ (e.g. window 7 → 30), mark the old run `superseding` (cooperative cancel: the runner checks `runs.cancel_requested_at` at stage boundaries; arq `allow_abort_jobs=True` for hard abort), wait for it to stop, then insert the new run.
- **Redis lock** `gh:lock:client:{id}:run` (TTL 120s, renewed by the runner heartbeat). It guards the short window before the DB row exists. The DB index is the source of truth.
- **Shared cache single-flight:** when two clients share competitor `UCx` and both runs need it fresh, `LockManager.single_flight("yt:channel:UCx:sync", ttl=300)` makes the first caller sync while the second waits on the lease (poll 250ms, max 60s). Then it re-reads `uploads_synced_at`. If that is within the freshness TTL (default 6h), the second skips the API call. The arq `_job_id` for channel sync jobs is `sync:channel:{id}:{yyyymmddhh}`, which deduplicates cron fan-out.
- **Stage checkpointing:** `run_stages(run_id, stage, attempt)` rows. Stage outputs are committed as `analysis_artifacts`. A retried run resumes at the first stage not `succeeded`, unless an upstream input changed (`input_hash` mismatch).
- **Stage criticality** (declared in the stage registry): critical stages (ingest of own channel and competitors, analytics, recommend, finalize) fail the run. Optional stages (news radar, refresh engine, comment signals, creator ranker) mark the run `partial` with a warning event, and their recommendation types are omitted.
- **Quota exhausted mid-run:** the stage records `blocked_on='quota'`, and the run goes to `partial` with `resume_after = next quota reset` (midnight America/Los_Angeles, [quota docs](https://developers.google.com/youtube/v3/determine_quota_cost)). The cache is still used for everything already present.
- **Purge mid-run:** runs pin the channel ids they work on (`runs.pinned_channel_ids`, set at ingest). The sweeper treats pinned channels as referenced (refresh, don't delete). If a row still disappears (fail-closed deletion, unavailable video), the reading stage treats it as missing, emits a `reference_dropped` warning, and the finalize stage re-validates that every reference video exists and passes gates before publishing.

### 5.8 Time and timezone handling
- All instants are stored as `timestamptz` and handled in Python as aware UTC datetimes. The DB session timezone is UTC. A lint rule bans naive `datetime.now()`/`utcnow()` (ruff `DTZ` rules).
- **Dates with meaning:** `yt_quota_ledger.quota_day` is a date in `America/Los_Angeles`, because YouTube quota resets at midnight Pacific. Evaluation `frozen_at` is an instant.
- **Client timezone** (`clients.timezone`, IANA, validated with `zoneinfo`; default inferred from profile locations by 03, e.g. `Asia/Kolkata` for Apollo). It is used for publish weekday/hour traits ("publish day" is the client audience's local day — computed at analysis time from `published_at`, never stored in shared tables), for "today" boundaries in UI, and for scheduling the daily refresh at local 05:00 (`clients.next_refresh_at` stored in UTC).
- **Windows** (7/30/90 days) are instant ranges `[generated_at − window, generated_at]` in UTC. They are not calendar days, so DST cannot change the window length.
- **Video age:** `age_hours = floor((captured_at − published_at)/1h)` is stored on snapshots, so "views at day 7" is timezone independent.

### 5.9 Large channels (4k+ videos)
- Ingest (02) pages the uploads playlist newest-first and stops at `backfill_horizon` (default: max window 90d + baseline horizon 180d, or 50 most recent videos, whichever covers more). For a channel like Apollo (~4.3k videos) that means a handful of `playlistItems.list` pages, not 87. A full backfill is done only for the client's own channel (refresh engine): about 87 + 87 = ~174 units once, then incremental.
- `yt_channels.backfill_cursor/backfill_horizon/backfill_complete` make backfill resumable across runs and quota days.
- Upserts are chunked to 500 rows. Snapshot inserts use `INSERT … ON CONFLICT (video_id, slot_at) DO NOTHING` (idempotent re-delivery). For bulk backfills, use `COPY` into a temp table, then merge.
- Queries over a channel's videos use `(channel_id, published_at DESC)`.

### 5.10 JSONB schema evolution
- Each JSONB column that holds a domain model has a sibling `*_schema_version smallint NOT NULL`. On read, the repository calls `artifact_schema_registry.upcast(kind, version, data)`, which chains `vN → vN+1` pure functions up to current. On write it always writes the current version.
- A lazy migration rewrites rows on next write. An optional arq job `backfill_jsonb(kind)` rewrites in batches when a SQL query needs the new shape.
- Rules: add fields with defaults (non-breaking, no version bump needed if optional); renames and splits need a version bump plus an upcaster; never remove a field that SQL indexes or filters use. JSONB keys used in SQL (rare) get an expression index declared in the migration.
- Business profiles are additionally versioned as rows (§6), so a profile edit is a new version and never a mutation. Downstream rows record `profile_version_id`.

### 5.11 Idempotency rules (summary)
| Write | Key | Conflict action |
|---|---|---|
| yt_channels / yt_videos | natural id | update where `excluded.*_fetched_at >= existing` |
| yt_video_snapshots | (video_id, slot_at) | do nothing |
| yt_comments | comment_id | update text/likes where newer |
| yt_search_cache | params_hash | update result, fetched_at |
| topic_matches | (client_id, video_id) | update if `ref_set_hash`/`video_content_hash`/`matcher_version` differ |
| analysis_artifacts | (run_id, kind, scope_key) | update payload |
| recommendations | (run_id, dedupe_key) | do nothing |
| runs | (workspace_id, idempotency_key) | return existing |
| news_items | url_hash | update last_seen_at |
| run_events | (run_id, seq) | do nothing |
| ledger rows | none (append-only); retried jobs pass `request_id` → unique (request_id) | do nothing |

### 5.12 PII in comments
- Stored: redacted text, like count, timestamps, `author_hash` (HMAC with a monthly-rotating pepper), `is_channel_owner_reply`. **Not stored:** author display name, avatar URL, author channel URL or raw channel id.
- Redaction at ingest, before the DB and before any LLM call: emails, phone numbers (E.164 and Indian 10-digit patterns), URLs, @mentions → `[email]`, `[phone]`, `[url]`, `[mention]`. Text is truncated to 1500 chars.
- Health niches (Apollo): comments may disclose conditions. CommentSignals (06) are aggregate counts only (questions matching tracked buyer questions, intent words). They are never attributed to a person and never used to infer protected attributes. The derived-metrics policy names health status as a forbidden inference.
- Delete-only retention (28 days), no refresh. Comments are never shown verbatim in recommendation cards. At most a paraphrased aggregate is shown, by 10.

## 6. Data model

**Conventions**
- PostgreSQL ≥ 18 (local Homebrew 18.4 verified; `uuidv7()` built in and tested locally).
- Surrogate keys: `id uuid PRIMARY KEY DEFAULT uuidv7()`. These are time-ordered, so index locality is good. External API ids are prefixed in the API layer only (`rec_`, `run_`, `cl_`, `ws_` + uuid hex).
- YouTube entities keyed by natural ids: `channel_id text` (`^UC[0-9A-Za-z_-]{22}$`), `video_id text` (`^[0-9A-Za-z_-]{11}$`), `comment_id text`.
- **Tenant tables never FK into `yt_*`.** They reference YouTube entities by natural id, so cache purges never cascade into tenant config. Dangling references are expected and handled (§5.4).
- Enumerations: `text` + `CHECK (col IN (...))` with Python `StrEnum`. No PG enum types, because adding values needs `ALTER TYPE` and our CHECKs can change in a normal migration.
- `created_at timestamptz NOT NULL DEFAULT now()`. `updated_at` is set explicitly by the app (no triggers).
- Constraint naming convention in `MetaData(naming_convention=…)` (`pk_%(table_name)s`, `fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s`, `uq_…`, `ix_…`, `ck_…`) so Alembic diffs are deterministic.
- Extensions: `citext`, `pg_trgm` (title search, optional), `pg_stat_statements`. **pgvector is not used** (not available in local Homebrew; vectors are small per client, so similarity runs in numpy, see 05). This can be revisited.
- Notation below: `→ table` = FK; `cascade` = ON DELETE CASCADE; `ws` = `workspace_id uuid NOT NULL → workspaces cascade`; `cl` = `client_id uuid NOT NULL` + composite FK `(workspace_id, client_id) → clients(workspace_id, id) cascade`.

### 6.1 Shared public YouTube cache (no tenant columns)

```sql
yt_channels (
  channel_id            text PK CHECK (channel_id ~ '^UC[0-9A-Za-z_-]{22}$'),
  handle                citext NULL,            -- without '@'; mutable, not unique (handles get reassigned)
  custom_url            text NULL,
  title                 text NULL,              -- NULL when unavailable (blanked)
  description           text NULL,
  country               text NULL,              -- snippet.country (self-declared, API data)
  default_language      text NULL,
  published_at          timestamptz NULL,
  thumbnail_url         text NULL,
  uploads_playlist_id   text NULL,
  subscriber_count      bigint NULL,
  hidden_subscriber_count boolean NOT NULL DEFAULT false,
  view_count            bigint NULL,
  video_count           integer NULL,
  branding_keywords     text NULL,              -- brandingSettings.channel.keywords
  made_for_kids         boolean NULL,
  availability          text NOT NULL DEFAULT 'available' CHECK (availability IN ('available','unavailable')),
  unavailable_at        timestamptz NULL,
  etag                  text NULL,
  metadata_fetched_at   timestamptz NOT NULL,   -- retention clock
  stats_fetched_at      timestamptz NOT NULL,   -- retention clock
  uploads_synced_at     timestamptz NULL,       -- last incremental playlist sync
  uploads_synced_through timestamptz NULL,      -- newest published_at seen
  backfill_cursor       text NULL,              -- playlistItems pageToken for resumable backfill
  backfill_horizon      timestamptz NULL,       -- oldest published_at ingested so far
  backfill_complete     boolean NOT NULL DEFAULT false,
  extra                 jsonb NOT NULL DEFAULT '{}',  extra_schema_version smallint NOT NULL DEFAULT 1,
  first_seen_at         timestamptz NOT NULL DEFAULT now()
)
INDEX ix_yt_channels_handle (handle)
INDEX ix_yt_channels_retention (LEAST(metadata_fetched_at, stats_fetched_at))
INDEX ix_yt_channels_uploads_synced_at (uploads_synced_at)

yt_channel_resolutions (                            -- handle/url → id cache; negative results cached too
  input_kind       text CHECK (input_kind IN ('handle','username','custom_url','channel_url','video_url')),
  input_normalized text,
  channel_id       text NULL,                       -- NULL = not found
  resolved_via     text NOT NULL,                   -- 'channels.list:forHandle' | 'channels.list:id' | 'videos.list' | ...
  resolved_at      timestamptz NOT NULL,
  expires_at       timestamptz NOT NULL CHECK (expires_at <= resolved_at + interval '30 days'),
  PRIMARY KEY (input_kind, input_normalized)
)
INDEX (expires_at)

yt_videos (
  video_id              text PK CHECK (video_id ~ '^[0-9A-Za-z_-]{11}$'),
  channel_id            text NOT NULL → yt_channels cascade,
  published_at          timestamptz NOT NULL,
  title                 text NULL,
  description           text NULL,              -- ≤ 5000 chars per YouTube
  tags                  text[] NULL,
  category_id           text NULL,              -- YouTube's own categoryId (stored, not used for topic grouping)
  default_language      text NULL,  default_audio_language text NULL,
  duration_seconds      integer NULL,           -- NULL for upcoming/live
  live_broadcast_content text NULL CHECK (live_broadcast_content IN ('none','live','upcoming')),
  live_actual_start_at  timestamptz NULL,
  definition            text NULL,  caption boolean NULL,  licensed_content boolean NULL,
  made_for_kids         boolean NULL,
  privacy_status        text NULL,              -- 'public' expected; 'unlisted' possible
  has_paid_product_placement boolean NULL,      -- paidProductPlacementDetails (disclosed paid promotion, used by 07 creator ranker)
  contains_synthetic_media boolean NULL,
  thumbnail_url         text NULL,  thumbnail_size text NULL,  -- maxres|standard|high|...
  view_count            bigint NULL,  like_count bigint NULL,  comment_count bigint NULL,   -- NULL = hidden/disabled/absent
  comments_disabled     boolean NULL,  comments_fetched_at timestamptz NULL,
  availability          text NOT NULL DEFAULT 'available' CHECK (availability IN ('available','unavailable')),
  unavailable_at        timestamptz NULL,
  content_hash          bytea NULL,             -- sha256(title || 0x00 || description || 0x00 || tags) → invalidates derived rows
  etag                  text NULL,
  ingest_source         text NOT NULL CHECK (ingest_source IN ('uploads','search','chart','manual','recommendation_ref')),
  next_snapshot_at      timestamptz NULL,       -- set by 02's adaptive cadence
  metadata_fetched_at   timestamptz NOT NULL,
  stats_fetched_at      timestamptz NOT NULL,
  first_seen_at         timestamptz NOT NULL DEFAULT now()
)
INDEX ix_yt_videos_channel_published (channel_id, published_at DESC)
INDEX ix_yt_videos_published (published_at DESC)
INDEX ix_yt_videos_retention (LEAST(metadata_fetched_at, stats_fetched_at))
INDEX ix_yt_videos_next_snapshot (next_snapshot_at) WHERE availability = 'available'
INDEX ix_yt_videos_unavailable (unavailable_at) WHERE availability = 'unavailable'
-- optional: GIN (title gin_trgm_ops) if 05 wants DB-side lexical prefiltering

yt_video_snapshots (                                -- PARTITION BY RANGE (slot_at), daily partitions yt_video_snapshots_pYYYYMMDD
  video_id       text NOT NULL → yt_videos cascade,
  slot_at        timestamptz NOT NULL,              -- captured_at truncated to snapshot slot (default 1h)
  captured_at    timestamptz NOT NULL,
  age_hours      integer NOT NULL,                  -- floor((captured_at - published_at)/1h), ≥ 0
  view_count     bigint NOT NULL,
  like_count     bigint NULL,
  comment_count  bigint NULL,
  source         text NOT NULL CHECK (source IN ('ingest','scheduled','refresh','backtest_freeze')),
  PRIMARY KEY (video_id, slot_at)                   -- includes the partition key, as PG requires
) PARTITION BY RANGE (slot_at)
INDEX (video_id, age_hours)                          -- partitioned index (created per partition)
-- no DEFAULT partition: an out-of-range insert must fail loudly; the cron keeps 7 days ahead; the writer calls ensure_partitions and retries once

yt_comments (
  comment_id         text PK,
  video_id           text NOT NULL → yt_videos cascade,
  thread_id          text NOT NULL,
  parent_comment_id  text NULL,
  is_reply           boolean NOT NULL,
  text_redacted      text NOT NULL,                 -- PII-redacted, ≤ 1500 chars
  like_count         integer NULL,
  reply_count        integer NULL,
  published_at       timestamptz NOT NULL,
  source_updated_at  timestamptz NULL,
  author_hash        bytea NULL,                    -- HMAC(comment_author_pepper[kid], authorChannelId); kid-prefixed
  is_channel_owner_reply boolean NOT NULL DEFAULT false,
  fetched_at         timestamptz NOT NULL           -- delete-only clock
)
INDEX (video_id, published_at DESC)
INDEX (fetched_at)

yt_search_cache (                                   -- search.list and chart (videos.list chart=mostPopular) results
  id             uuid PK DEFAULT uuidv7(),
  endpoint       text NOT NULL CHECK (endpoint IN ('search.list','videos.list:chart','playlistItems.list')),
  params_hash    bytea NOT NULL UNIQUE,             -- sha256(canonical JSON incl. endpoint; publishedAfter rounded to day)
  params         jsonb NOT NULL,                    -- q, type, order, publishedAfter/Before, regionCode, relevanceLanguage, videoDuration, maxResults, pageToken
  result_kind    text NOT NULL CHECK (result_kind IN ('video','channel','playlist')),
  result_ids     text[] NOT NULL,                   -- ordered as returned
  total_results  integer NULL,
  next_page_token text NULL,
  quota_units    integer NOT NULL,
  fetched_at     timestamptz NOT NULL,
  expires_at     timestamptz NOT NULL CHECK (expires_at <= fetched_at + interval '30 days'),
  hit_count      integer NOT NULL DEFAULT 0,
  last_hit_at    timestamptz NULL
)
INDEX (expires_at)

yt_quota_ledger (                                   -- ops data (contains no API content)
  id           bigint GENERATED ALWAYS AS IDENTITY PK,
  request_id   uuid NOT NULL UNIQUE,                -- idempotent ledger writes on job retry
  occurred_at  timestamptz NOT NULL,
  quota_day    date NOT NULL,                       -- America/Los_Angeles calendar day
  bucket       text NOT NULL CHECK (bucket IN ('default','search')),
  endpoint     text NOT NULL,
  units        integer NOT NULL,
  purpose      text NOT NULL CHECK (purpose IN ('ingest','snapshot','retention_refresh','discovery','resolution','comments','oauth_analytics','news_demand','evaluation')),
  http_status  smallint NULL,  error_reason text NULL,
  workspace_id uuid NULL, client_id uuid NULL, run_id uuid NULL,   -- no FKs; nulled on tenant purge
  cache_key    bytea NULL
)
INDEX (quota_day, bucket)
INDEX (client_id, quota_day)
INDEX (run_id)
BRIN (occurred_at)
```

**Shared client-agnostic derived tables** (logic owned by 06; class `yt_public_derived`):

```sql
yt_video_traits (
  video_id text PK → yt_videos cascade,
  content_hash bytea NOT NULL,  extractor_version text NOT NULL,
  length_band text NOT NULL,                        -- e.g. short|lt4m|4to10m|10to20m|gt20m (bands configurable in 06)
  is_short_candidate boolean NOT NULL,
  title_char_len smallint, title_word_count smallint,
  title_has_question boolean, title_has_number boolean, title_is_howto boolean,
  title_has_year boolean, title_year smallint NULL,
  extra jsonb NOT NULL DEFAULT '{}', extra_schema_version smallint NOT NULL DEFAULT 1,
  computed_at timestamptz NOT NULL
)                                                   -- publish weekday/hour NOT here: it depends on client timezone

yt_thumbnail_traits (
  video_id text PK → yt_videos cascade,
  thumbnail_url text NOT NULL,  image_sha256 bytea NOT NULL,  image_etag text NULL,
  width smallint, height smallint,
  text_present boolean NOT NULL, word_count smallint NOT NULL, text_area_ratio real NULL,
  ocr_text text NULL,                               -- ≤ 200 chars; content-derived → same 30-day class
  contrast real NOT NULL, brightness real NOT NULL, colorfulness real NULL,
  extractor_version text NOT NULL,
  computed_at timestamptz NOT NULL,
  source_verified_at timestamptz NOT NULL           -- retention clock (bumped on 304 re-validation)
)                                                   -- image bytes are never stored; no face detection columns, by policy

yt_video_metrics (
  video_id text PK → yt_videos cascade,
  metrics_version text NOT NULL,
  views_at_age jsonb NOT NULL,                      -- {"1":{"views":..,"method":"observed|interpolated|estimated","observed_at":..},"7":{...},...}
  views_day7 bigint NULL,  views_day7_method text NULL,   -- denormalized: contract default age
  velocity_views_per_day real NULL,
  engagement_rate real NULL,                        -- (likes+comments)/views, NULL if hidden
  channel_baseline_day7 real NULL,
  outlier_ratio real NULL,  is_outlier boolean NULL,
  stats_observed_at timestamptz NOT NULL,           -- oldest stat used → retention clock
  computed_at timestamptz NOT NULL
)
INDEX (is_outlier) WHERE is_outlier
INDEX (stats_observed_at)

yt_channel_baselines (
  channel_id text → yt_channels cascade,
  age_days smallint,
  method text NOT NULL,                             -- median|trimmed_mean (06)
  value real NOT NULL, sample_size integer NOT NULL,
  sample_window_start timestamptz, sample_window_end timestamptz,
  stats_observed_at timestamptz NOT NULL, computed_at timestamptz NOT NULL,
  PRIMARY KEY (channel_id, age_days)
)
```

### 6.2 Shared non-YouTube caches

```sql
news_items (                                        -- NewsItem; external_non_youtube; 90d
  id uuid PK DEFAULT uuidv7(),
  url text NOT NULL, url_hash bytea NOT NULL UNIQUE, -- canonicalized (scheme/host lowercased, tracking params stripped)
  source_domain citext NOT NULL,
  title text NOT NULL, snippet text NULL,           -- ≤ 1000 chars
  content_excerpt text NULL,                        -- ≤ 4000 chars; excerpts only (copyright)
  language text NULL, published_at timestamptz NULL,
  provider text NOT NULL,  provider_score real NULL,
  content_hash bytea NOT NULL,
  first_seen_at timestamptz NOT NULL, last_seen_at timestamptz NOT NULL, fetched_at timestamptz NOT NULL
)
INDEX (published_at DESC)  INDEX (source_domain)  INDEX (fetched_at)

news_query_cache (
  params_hash bytea PK, provider text NOT NULL, params jsonb NOT NULL,   -- query, topic, time_range, include/exclude_domains
  news_item_ids uuid[] NOT NULL, cost_credits real NULL,
  fetched_at timestamptz NOT NULL, expires_at timestamptz NOT NULL       -- default 6h
)

embedding_cache (                                   -- vectors of YouTube-derived text only (titles, descriptions, news titles)
  model text, text_sha256 bytea,
  dims smallint NOT NULL, vector bytea NOT NULL,    -- float32 little-endian
  source_kind text NOT NULL CHECK (source_kind IN ('video_title','video_text','news_title','comment')),
  created_at timestamptz NOT NULL, last_used_at timestamptz NOT NULL,
  PRIMARY KEY (model, text_sha256)
)
INDEX (created_at)
-- tenant text (keywords, buyer questions) embeddings live on the tenant rows, never here
```

### 6.3 Tenancy, auth, compliance

```sql
workspaces (
  id uuid PK, name text NOT NULL, slug citext NOT NULL UNIQUE,
  status text NOT NULL CHECK (status IN ('active','suspended','deleting')),
  plan text NOT NULL DEFAULT 'standard',
  settings jsonb NOT NULL DEFAULT '{}', settings_schema_version smallint NOT NULL DEFAULT 1,
  created_at, updated_at, deleted_at timestamptz NULL, purge_after timestamptz NULL
)

users (
  id uuid PK, email citext NOT NULL UNIQUE, display_name text NULL,
  password_hash text NULL,                          -- argon2id; NULL for SSO-only
  email_verified_at timestamptz NULL, last_login_at timestamptz NULL, disabled_at timestamptz NULL,
  created_at, updated_at
)

workspace_members (
  workspace_id uuid → workspaces cascade, user_id uuid → users cascade,
  role text NOT NULL CHECK (role IN ('owner','admin','analyst','viewer')),
  created_at, PRIMARY KEY (workspace_id, user_id)
)
INDEX (user_id)

user_sessions (
  id uuid PK, user_id uuid NOT NULL → users cascade,
  active_workspace_id uuid NULL → workspaces ON DELETE SET NULL,
  token_hash bytea NOT NULL UNIQUE,
  created_at, last_seen_at timestamptz NOT NULL, expires_at timestamptz NOT NULL, idle_expires_at timestamptz NOT NULL,
  revoked_at timestamptz NULL, user_agent text NULL, ip_prefix inet NULL     -- /24 or /48 only
)
INDEX (user_id)  INDEX (expires_at)

api_keys (
  id uuid PK, ws,
  name text NOT NULL, key_prefix text NOT NULL UNIQUE, secret_hash bytea NOT NULL,
  scopes text[] NOT NULL, created_by_user_id uuid NULL → users ON DELETE SET NULL,
  created_at, last_used_at timestamptz NULL, expires_at timestamptz NULL, revoked_at timestamptz NULL
)

audit_log (
  id bigint IDENTITY PK, workspace_id uuid NULL, actor_kind text NOT NULL, actor_id uuid NULL,
  action text NOT NULL,                             -- e.g. oauth.connect, oauth.revoke, client.delete, key.rotate, retention.purge
  target_kind text NULL, target_id text NULL,
  data jsonb NOT NULL DEFAULT '{}',                 -- never secrets, never API content
  ip_prefix inet NULL, at timestamptz NOT NULL DEFAULT now()
)
INDEX (workspace_id, at DESC)  BRIN (at)

deletion_requests (
  id uuid PK, workspace_id uuid NULL, client_id uuid NULL,     -- no FK: must survive the deletion it performs
  scope text NOT NULL CHECK (scope IN ('oauth_revocation','client','workspace','user','api_data_request')),
  oauth_token_id uuid NULL, requested_by text NOT NULL,
  requested_at timestamptz NOT NULL, due_at timestamptz NOT NULL,   -- +7d (in-app/user request) or +30d (Google-side)
  started_at timestamptz NULL, completed_at timestamptz NULL,
  status text NOT NULL CHECK (status IN ('pending','running','done','failed')),
  report jsonb NOT NULL DEFAULT '{}'                -- rows deleted per table
)
INDEX (status, due_at)
```

### 6.4 Clients, profiles, keywords, questions, competitors, crawl

```sql
clients (
  id uuid PK, ws,
  UNIQUE (workspace_id, id),                        -- target of composite FKs
  name text NOT NULL, slug citext NOT NULL, UNIQUE (workspace_id, slug),
  website_url text NULL, website_domain citext NULL,
  own_channel_id text NULL,
  own_channel_status text NOT NULL DEFAULT 'unresolved'
     CHECK (own_channel_status IN ('unresolved','proposed','verified','rejected','needs_attention')),
  own_channel_verification jsonb NOT NULL DEFAULT '[]',   -- [{kind:'site_links_channel'|'channel_links_site'|'user_confirmed'|'oauth_mine', url, observed_at}]
  timezone text NOT NULL DEFAULT 'UTC',             -- IANA, validated in app
  region_code char(2) NULL, language text NULL,
  status text NOT NULL CHECK (status IN ('onboarding','awaiting_confirmation','active','paused','archived','deleting')),
  settings jsonb NOT NULL DEFAULT '{}', settings_schema_version smallint NOT NULL DEFAULT 1,   -- ClientSettings
  next_refresh_at timestamptz NULL, last_activity_at timestamptz NULL,
  created_at, updated_at, archived_at timestamptz NULL, deleted_at timestamptz NULL,
  CHECK (website_url IS NOT NULL OR own_channel_id IS NOT NULL)
)
UNIQUE (workspace_id, own_channel_id) WHERE own_channel_id IS NOT NULL AND deleted_at IS NULL
INDEX (next_refresh_at) WHERE status = 'active'

business_profiles (                                 -- BusinessProfile, immutable versions
  id uuid PK, ws, cl,
  version integer NOT NULL, UNIQUE (client_id, version),
  status text NOT NULL CHECK (status IN ('draft','active','superseded','discarded')),
  source text NOT NULL CHECK (source IN ('crawl','user_edit','merge','import')),
  schema_version smallint NOT NULL,
  profile jsonb NOT NULL,                           -- offerings[{key,name,...}], value_propositions[{key,..}], locations[], conversion_actions[{key,..}],
                                                    -- freshness_profile{...}, niche_summary, brand_terms[] (shape owned by 03; stable `key` slugs required)
  parent_id uuid NULL → business_profiles, diff jsonb NULL,   -- RFC 6902 patch vs parent
  content_hash bytea NOT NULL,
  created_by_user_id uuid NULL, created_in_run_id uuid NULL,
  created_at, activated_at timestamptz NULL
)
UNIQUE (client_id) WHERE status = 'active'

keywords (                                          -- niche keywords (TopicRef kind=keyword)
  id uuid PK, ws, cl,
  text text NOT NULL, normalized_text text NOT NULL,
  kind text NOT NULL CHECK (kind IN ('niche','offering','brand','location','competitor_brand','negative')),
  source text NOT NULL CHECK (source IN ('crawl','user','llm_suggested','own_search_terms','spotlit')),
  status text NOT NULL CHECK (status IN ('proposed','confirmed','rejected','retired')),
  weight real NOT NULL DEFAULT 1.0, language text NULL, offering_key text NULL,
  embedding bytea NULL, embedding_model text NULL,
  introduced_in_profile_id uuid NULL → business_profiles ON DELETE SET NULL,
  uses_authorized_data boolean NOT NULL DEFAULT false,   -- true when sourced from own-channel search terms (purged on revocation)
  created_at, updated_at, retired_at timestamptz NULL
)
UNIQUE (client_id, normalized_text) WHERE retired_at IS NULL
INDEX (client_id, status)

buyer_questions (                                   -- BuyerQuestion (TopicRef kind=buyer_question)
  id uuid PK, ws, cl,
  text text NOT NULL, normalized_text text NOT NULL,
  intent_stage text NOT NULL CHECK (intent_stage IN ('awareness','consideration','decision')),
  source text NOT NULL CHECK (source IN ('crawl','user','llm_suggested','spotlit_ai_tracking','comments')),
  status text NOT NULL CHECK (status IN ('proposed','confirmed','rejected','retired')),
  weight real NOT NULL DEFAULT 1.0, language text NULL,
  offering_key text NULL, conversion_action_key text NULL,
  external_ref text NULL,                           -- Spotlit tracking id
  ai_cited_video_ids text[] NOT NULL DEFAULT '{}', ai_citations_observed_at timestamptz NULL,
  embedding bytea NULL, embedding_model text NULL,
  introduced_in_profile_id uuid NULL,
  created_at, updated_at, retired_at timestamptz NULL
)
UNIQUE (client_id, normalized_text) WHERE retired_at IS NULL
-- TopicRef view: v_topic_refs(client_id, ref_kind, ref_id, text, weight, status) = union of confirmed keywords (non-negative) + confirmed buyer_questions

competitors (                                       -- confirmed set = CompetitorCandidate accepted by user (3–10 enforced in service)
  id uuid PK, ws, cl,
  name text NOT NULL, website_url text NULL, website_domain citext NULL,
  channel_id text NULL, channel_verification jsonb NOT NULL DEFAULT '[]',
  status text NOT NULL CHECK (status IN ('proposed','confirmed','rejected','archived','needs_attention')),
  source text NOT NULL CHECK (source IN ('discovered','user')),
  discovery_method text NULL,                       -- web_search|yt_search|llm|user
  evidence jsonb NOT NULL DEFAULT '[]', evidence_schema_version smallint NOT NULL DEFAULT 1,
  score real NULL, rank smallint NULL,
  discovered_in_run_id uuid NULL, decided_by_user_id uuid NULL, decided_at timestamptz NULL, rejection_reason text NULL,
  created_at, updated_at
)
UNIQUE (client_id, channel_id) WHERE channel_id IS NOT NULL AND status <> 'archived'
UNIQUE (client_id, website_domain) WHERE website_domain IS NOT NULL AND status <> 'archived'
INDEX (channel_id) WHERE status = 'confirmed'       -- reverse lookup for retention reference set

-- v_tracked_channels(workspace_id, client_id, channel_id, role) =
--   clients.own_channel_id (status in proposed|verified) as 'own'  ∪  competitors.channel_id (status confirmed) as 'competitor'

crawled_pages (
  id uuid PK, ws, cl,
  subject_kind text NOT NULL CHECK (subject_kind IN ('client','competitor')),
  competitor_id uuid NULL → competitors cascade,
  url text NOT NULL, url_hash bytea NOT NULL, canonical_url text NULL,
  http_status smallint NULL, content_type text NULL,
  fetch_method text NOT NULL CHECK (fetch_method IN ('http','playwright','tavily_extract','tavily_crawl')),
  robots_allowed boolean NOT NULL,
  page_kind text NULL,                              -- home|service|product|pricing|about|location|blog|faq|contact|other
  title text NULL, meta_description text NULL, headings jsonb NULL,
  text_content text NULL,                           -- ≤ 100k chars
  text_hash bytea NULL, language text NULL,
  outbound_links jsonb NULL,                        -- incl. youtube.com/@handle links (own-channel verification evidence)
  structured_data jsonb NULL,                       -- schema.org blocks
  crawl_run_id uuid NULL, error text NULL,
  fetched_at timestamptz NOT NULL, created_at
)
UNIQUE NULLS NOT DISTINCT (client_id, competitor_id, url_hash)
INDEX (client_id, subject_kind, page_kind)
INDEX (fetched_at)
```

### 6.5 OAuth and Authorized data

```sql
oauth_tokens (
  id uuid PK, ws, cl,
  provider text NOT NULL CHECK (provider = 'google'),
  google_subject text NOT NULL, channel_id text NOT NULL,         -- must equal clients.own_channel_id at connect
  scopes text[] NOT NULL,
  key_id text NOT NULL, encrypted_payload bytea NOT NULL,         -- MultiFernet token of {refresh_token, binding}
  status text NOT NULL CHECK (status IN ('active','invalid','revoked','purged')),
  approved_viewer_user_ids uuid[] NOT NULL,                       -- DP §III.E.3.b agents approved by the authorizing user
  granted_by_user_id uuid NOT NULL, granted_at timestamptz NOT NULL,
  last_refreshed_at timestamptz NULL, authorization_verified_at timestamptz NOT NULL,
  failure_count smallint NOT NULL DEFAULT 0, last_error text NULL,
  revoked_at timestamptz NULL,
  revocation_source text NULL CHECK (revocation_source IN ('in_app','google_risc','invalid_grant','client_deleted')),
  purge_due_at timestamptz NULL,
  created_at, updated_at
)
UNIQUE (client_id, provider) WHERE status = 'active'
INDEX (authorization_verified_at) WHERE status = 'active'
INDEX (key_id)

own_analytics_reports (                             -- OwnAnalytics (02); yt_authorized_analytics
  id uuid PK, ws, cl,
  oauth_token_id uuid NOT NULL → oauth_tokens cascade,
  channel_id text NOT NULL,
  report_kind text NOT NULL CHECK (report_kind IN ('channel_daily','video_daily','traffic_sources','search_terms','subscribers','retention')),
  video_id text NULL,
  start_date date NOT NULL, end_date date NOT NULL,                -- channel's reporting days as returned by the API
  dimensions text[] NOT NULL, dimensions_hash bytea NOT NULL,
  column_headers jsonb NOT NULL, rows jsonb NOT NULL,
  fetched_at timestamptz NOT NULL
)
UNIQUE (client_id, report_kind, COALESCE(video_id,''), start_date, end_date, dimensions_hash)
```

### 6.6 Runs and events

```sql
runs (
  id uuid PK, ws, cl,
  kind text NOT NULL CHECK (kind IN ('onboarding','analysis','daily_refresh','backtest')),
  run_family text NOT NULL CHECK (run_family IN ('onboarding','analysis','backtest')),
  status text NOT NULL CHECK (status IN ('queued','running','awaiting_confirmation','succeeded','partial','failed','cancelled','superseded')),
  trigger text NOT NULL CHECK (trigger IN ('user','api','cron','system')),
  params jsonb NOT NULL, params_hash bytea NOT NULL,                -- window_days, as_of (backtest T), stage overrides
  config_snapshot jsonb NOT NULL,                                   -- EffectiveConfig
  profile_version_id uuid NULL → business_profiles ON DELETE SET NULL,
  idempotency_key text NULL, UNIQUE (workspace_id, idempotency_key),
  arq_job_id text NULL, attempt smallint NOT NULL DEFAULT 1,
  current_stage text NULL, progress real NOT NULL DEFAULT 0,
  pinned_channel_ids text[] NOT NULL DEFAULT '{}',
  blocked_on text NULL, resume_after timestamptz NULL,
  cancel_requested_at timestamptz NULL, superseded_by_run_id uuid NULL,
  error_code text NULL, error_message text NULL,
  quota_units_used integer NOT NULL DEFAULT 0, llm_cost_usd numeric(10,4) NOT NULL DEFAULT 0,
  created_by_user_id uuid NULL,
  queued_at timestamptz NOT NULL, started_at timestamptz NULL, finished_at timestamptz NULL, heartbeat_at timestamptz NULL
)
UNIQUE (client_id, run_family) WHERE status IN ('queued','running')
INDEX (client_id, queued_at DESC)
INDEX (status, heartbeat_at) WHERE status = 'running'               -- stale-run reaper

run_stages (
  run_id uuid → runs cascade, stage text, attempt smallint,
  workspace_id uuid NOT NULL,
  status text NOT NULL CHECK (status IN ('pending','running','succeeded','skipped','failed','blocked')),
  critical boolean NOT NULL, input_hash bytea NULL,
  started_at, finished_at, duration_ms integer NULL,
  quota_units integer NOT NULL DEFAULT 0, llm_input_tokens integer NOT NULL DEFAULT 0, llm_output_tokens integer NOT NULL DEFAULT 0,
  error_code text NULL, error_detail text NULL,
  PRIMARY KEY (run_id, stage, attempt)
)

run_events (                                        -- RunEvent (SSE source of truth; 30d)
  id bigint IDENTITY PK, run_id uuid NOT NULL → runs cascade, workspace_id uuid NOT NULL,
  seq integer NOT NULL, UNIQUE (run_id, seq),        -- SSE Last-Event-ID = seq
  ts timestamptz NOT NULL, stage text NULL,
  level text NOT NULL CHECK (level IN ('debug','info','warn','error')),
  event_type text NOT NULL,                         -- stage_started|progress|warning|reference_dropped|quota_blocked|...
  message text NOT NULL,                            -- no API content (ids only)
  progress real NULL, data jsonb NOT NULL DEFAULT '{}'
)
```

### 6.7 Analysis artifacts, topic matches, recommendations, refresh, news, feedback, evaluation

```sql
topic_matches (                                     -- TopicMatch cache per client × video (05)
  workspace_id uuid NOT NULL, client_id uuid NOT NULL, video_id text NOT NULL,
  ref_set_hash bytea NOT NULL, video_content_hash bytea NOT NULL, matcher_version text NOT NULL,
  matches jsonb NOT NULL,                           -- [{ref_kind, ref_id, score, method: lexical|embedding|llm, matched_terms[], review_flag, llm_verdict}]
  matches_schema_version smallint NOT NULL,
  best_score real NOT NULL, has_match boolean NOT NULL, needs_review boolean NOT NULL,
  computed_in_run_id uuid NULL,
  computed_at timestamptz NOT NULL,
  api_data_observed_at timestamptz NOT NULL, expires_at timestamptz NOT NULL,
  PRIMARY KEY (client_id, video_id),
  FOREIGN KEY (workspace_id, client_id) → clients cascade
)
INDEX (client_id) WHERE has_match
INDEX (expires_at)

analysis_artifacts (                                -- VideoMetrics views, TraitLift, ChannelScorecard, OwnPerformanceReport, TrendingReport,
  id uuid PK, ws, cl,                               --   CommentSignals, gap analysis, creator ranking inputs ... (kinds registered by owners)
  run_id uuid NOT NULL → runs cascade,
  kind text NOT NULL, scope_key text NOT NULL,      -- e.g. 'window=30;set=competitors;lens=views'
  schema_version smallint NOT NULL, payload jsonb NOT NULL,
  referenced_video_ids text[] NOT NULL DEFAULT '{}', referenced_channel_ids text[] NOT NULL DEFAULT '{}',
  uses_authorized_data boolean NOT NULL DEFAULT false,
  api_data_observed_at timestamptz NULL, expires_at timestamptz NULL,
  is_current boolean NOT NULL DEFAULT true,          -- flipped false when a newer run supersedes (kind, scope_key)
  created_at,
  UNIQUE (run_id, kind, scope_key)
)
INDEX (client_id, kind, scope_key) WHERE is_current
GIN (referenced_video_ids)
INDEX (expires_at)

recommendations (                                   -- Recommendation (output contract) + evidence
  id uuid PK, ws, cl,
  run_id uuid NOT NULL → runs cascade,
  type text NOT NULL CHECK (type IN ('video_idea','title_pattern','thumbnail_pattern','format','creator','news_opportunity','description_refresh')),
  status text NOT NULL CHECK (status IN ('draft','gated_out','published','superseded','withdrawn','needs_regeneration','expired')),
  dedupe_key bytea NOT NULL, UNIQUE (run_id, dedupe_key),          -- hash(type, topic refs, pattern) for cross-run continuity
  lens text[] NOT NULL,
  summary text NOT NULL,
  buyer_question_id uuid NULL → buyer_questions ON DELETE SET NULL, buyer_question_text text NULL,
  why_it_works jsonb NULL,                          -- [{text, evidence_refs[]}]
  reference_videos jsonb NULL,                      -- [{video_id, channel, published_at, views_day7, outlier_ratio}] (contract shape)
  fit_to_client text NULL,
  expected_range_low real NULL, expected_range_high real NULL, expected_range_label text NULL,
  confidence_label text NULL CHECK (confidence_label IN ('High','Medium','Low')),
  confidence_outliers smallint NULL, confidence_channels smallint NULL,
  window_days smallint NOT NULL CHECK (window_days IN (7,30,90)),
  details jsonb NULL, details_schema_version smallint NOT NULL DEFAULT 1,   -- type-specific (creator channel_id, news_opportunity_id, refresh_suggestion_id, pattern spec)
  evidence jsonb NULL, evidence_schema_version smallint NOT NULL DEFAULT 1, -- RecommendationDraft (07) input to the writer
  gate_results jsonb NOT NULL DEFAULT '[]',         -- [{gate, passed, detail}]
  score real NULL, rank smallint NULL,
  writer_model text NULL, writer_prompt_version text NULL,
  referenced_video_ids text[] NOT NULL DEFAULT '{}', referenced_channel_ids text[] NOT NULL DEFAULT '{}',
  stale_reference_count smallint NOT NULL DEFAULT 0,
  uses_authorized_data boolean NOT NULL DEFAULT false,
  generated_at timestamptz NOT NULL,
  api_data_observed_at timestamptz NOT NULL, expires_at timestamptz NOT NULL,
  redacted_at timestamptz NULL, superseded_by_id uuid NULL
)
INDEX (client_id, status, generated_at DESC)
INDEX (client_id, dedupe_key)
GIN (referenced_video_ids)
INDEX (expires_at) WHERE redacted_at IS NULL

recommendation_feedback (                           -- user feedback + human review rubric (12)
  id uuid PK, ws, cl,
  recommendation_id uuid NULL → recommendations ON DELETE SET NULL,
  recommendation_type text NOT NULL, recommendation_dedupe_key bytea NOT NULL,   -- survives redaction/deletion
  user_id uuid NULL → users ON DELETE SET NULL,
  kind text NOT NULL CHECK (kind IN ('thumbs_up','thumbs_down','dismiss','saved','made_it','not_relevant','review','comment')),
  rating smallint NULL CHECK (rating BETWEEN 1 AND 5),
  rubric jsonb NULL,                                -- {specific, evidenced, relevant, actionable, not_obvious} each 1–5
  comment text NULL,
  produced_video_id text NULL,                      -- client says "I made this" → outcome tracking
  created_at
)
INDEX (client_id, created_at DESC)  INDEX (recommendation_id)

refresh_suggestions (                               -- DescriptionRefresh (09)
  id uuid PK, ws, cl, run_id uuid NOT NULL → runs cascade,
  video_id text NOT NULL,                           -- client's own video
  decision text NOT NULL CHECK (decision IN ('refresh','skip')), decision_reasons jsonb NOT NULL,
  status text NOT NULL CHECK (status IN ('proposed','accepted','rejected','applied_detected','stale','expired')),
  original_description_hash bytea NOT NULL,         -- detects description edited since → stale
  insertions jsonb NOT NULL,                        -- [{anchor, position, text, keyword_id?, news_signal_id?, reason, evidence_refs[]}]
  insertions_schema_version smallint NOT NULL,
  keyword_ids uuid[] NOT NULL DEFAULT '{}', news_signal_ids uuid[] NOT NULL DEFAULT '{}',
  recommendation_id uuid NULL → recommendations ON DELETE SET NULL,
  score real NULL, generated_at timestamptz NOT NULL,
  applied_detected_at timestamptz NULL,
  api_data_observed_at timestamptz NOT NULL, expires_at timestamptz NOT NULL
)
UNIQUE (run_id, video_id)
INDEX (client_id, status)

news_signals (                                      -- NewsSignal (08), tenant relevance of a shared NewsItem
  id uuid PK, ws, cl, run_id uuid NULL → runs ON DELETE SET NULL,
  news_item_id uuid NULL → news_items ON DELETE SET NULL,
  news_url text NOT NULL, news_title text NOT NULL, news_published_at timestamptz NULL,   -- copied so the signal survives news_items TTL
  relevance_score real NOT NULL,
  matched_keyword_ids uuid[] NOT NULL DEFAULT '{}', matched_offering_keys text[] NOT NULL DEFAULT '{}',
  freshness_class text NULL,
  youtube_demand jsonb NULL,                        -- API-derived (search cache counts/ids) → nulled at youtube_demand_expires_at
  youtube_demand_expires_at timestamptz NULL,
  status text NOT NULL CHECK (status IN ('new','used','dismissed','expired')),
  detected_at timestamptz NOT NULL,
  UNIQUE (client_id, news_item_id)
)
INDEX (client_id, detected_at DESC)

news_opportunities (                                -- 08 intermediate: signal → angle tied to offering; published as recommendation type news_opportunity
  id uuid PK, ws, cl,
  news_signal_id uuid NOT NULL → news_signals cascade,
  recommendation_id uuid NULL → recommendations ON DELETE SET NULL,
  offering_key text NULL, value_prop_key text NULL,
  angle text NOT NULL, payload jsonb NOT NULL, payload_schema_version smallint NOT NULL,
  status text NOT NULL CHECK (status IN ('candidate','gated_out','published','dismissed','expired')),
  created_at, expires_at timestamptz NOT NULL       -- news half-life (08 config)
)

evaluation_label_sets (
  id uuid PK, workspace_id uuid NULL, client_id uuid NULL,        -- NULL = internal/global set
  kind text NOT NULL CHECK (kind IN ('outlier','human_review')),
  name text NOT NULL, frozen_at timestamptz NOT NULL,
  status text NOT NULL CHECK (status IN ('open','closed')),
  created_by_user_id uuid NULL, created_at
)

evaluation_labels (                                 -- 100 hand-labelled videos
  id uuid PK, label_set_id uuid NOT NULL → evaluation_label_sets cascade,
  video_id text NOT NULL, channel_id text NOT NULL,
  label text NOT NULL CHECK (label IN ('outlier','not_outlier','unsure')),
  labeler_user_id uuid NULL, labelled_at timestamptz NOT NULL,
  context jsonb NULL,                               -- stats shown to labeller (API-derived) → nulled at context_expires_at
  context_expires_at timestamptz NULL,
  UNIQUE (label_set_id, video_id, labeler_user_id)
)

evaluation_reports (
  id uuid PK, workspace_id uuid NULL, client_id uuid NULL, run_id uuid NULL,
  kind text NOT NULL CHECK (kind IN ('backtest','outlier_precision','human_review','ops')),
  params jsonb NOT NULL,                            -- T, window, baselines compared, label_set_id
  metrics jsonb NOT NULL,                           -- aggregates only (precision, recall, lift, p50 latency, units/client)
  created_at
)

provider_usage_ledger (                             -- LLM, Tavily, embeddings (ops report, cost per client)
  id bigint IDENTITY PK, request_id uuid NOT NULL UNIQUE,
  occurred_at timestamptz NOT NULL,
  provider text NOT NULL, model text NULL, tier text NULL, operation text NOT NULL,
  input_tokens integer NULL, output_tokens integer NULL, cache_read_tokens integer NULL, cache_write_tokens integer NULL,
  credits real NULL, cost_usd numeric(10,6) NULL, latency_ms integer NULL, status text NOT NULL,
  workspace_id uuid NULL, client_id uuid NULL, run_id uuid NULL, stage text NULL
)
INDEX (client_id, occurred_at)  INDEX (run_id)  BRIN (occurred_at)
```

### 6.8 JSONB vs columns, the rule we applied
- **Columns:** anything filtered, joined, sorted, indexed, uniqueness-checked or subject to retention (ids, statuses, clocks, counts, hashes, `referenced_*_ids`).
- **JSONB:** whole-document reads whose shape evolves (business profile, evidence, gate results, insertions, analysis payloads, raw analytics rows), always paired with `*_schema_version`.
- **Hybrid:** where a JSONB doc has a hot field, denormalize it (e.g. `views_day7` next to `views_at_age`).

### 6.9 Partitioning and growth
- `yt_video_snapshots`: daily RANGE partitions. In standard mode at most ~29 live partitions plus 7 pre-created. In approved mode (36 months), switch to monthly partitions through a migration that attaches new monthly partitions going forward.
- Candidates for later monthly partitioning if they pass ~50M rows: `run_events`, `yt_quota_ledger`, `provider_usage_ledger`. Plain tables with batched deletes are fine for v1.
- Alembic `include_object` skips child partitions `yt_video_snapshots_p%`, so autogenerate doesn't try to drop them.

### 6.10 Migration strategy (Alembic)
- Single linear history. Async `env.py` using `connection.run_sync(do_run_migrations)`. `compare_type=True`. Naming convention imported from `app.core.db`.
- Autogenerate is a draft only. Partitioned tables, partial/expression indexes, CHECKs and `NULLS NOT DISTINCT` are written by hand with `op.execute` or `postgresql_where`. Autogenerate does not reliably detect these ([Alembic autogenerate limits](https://alembic.sqlalchemy.org/en/latest/autogenerate.html#what-does-autogenerate-detect-and-what-does-it-not-detect)).
- `env.py` takes `pg_advisory_xact_lock(hashtext('gh_alembic'))` so concurrent deploys serialize.
- Migrations run as a one-off release step (`alembic upgrade head`; a compose `migrate` service; a prod release job or init container), never on API/worker startup.
- Expand/contract for zero downtime: (1) add nullable column or new table; (2) deploy code writing both; (3) backfill via arq job in batches; (4) add NOT NULL/constraint with `NOT VALID` then `VALIDATE CONSTRAINT`; (5) drop old after a release. `CREATE INDEX CONCURRENTLY` in an autocommit block for large tables.
- CI: `upgrade head` on empty DB → `alembic check` (no pending autogenerate diff) → `downgrade -1` → `upgrade head`.
- Seed: `python -m app.cli bootstrap --workspace … --owner-email …` creates a workspace, owner and first API key (printed once).

## 7. External APIs & libraries

| Item | Fact used | Source | Verified |
|---|---|---|---|
| Non-Authorized Data storage ≤ 30 days, then refresh/delete | DP §III.E.4.d | https://developers.google.com/youtube/terms/developer-policies | yes (fetched 2026-11-20) |
| Non-Authorized statistics must not be stored > 30 days; Authorized analytics/statistics may be stored while authorized, re-verify every 30 days | DP §III.E.4.b | same | yes |
| Other Authorized Data ≤ 30 days | DP §III.E.4.c | same | yes |
| Consistency with current data; historical data shown in time context | DP §III.E.4.e–f | same | yes |
| User deletion request → delete within 7 days; honour account deletion | DP §III.E.4.g | same | yes |
| No replacing API data with independent calculations / derived metrics; disclose non-YouTube metrics | DP §III.E.4.h | same | yes |
| Revocation in-app → delete within 7 days; via Google security page → within 30 days; periodic token validity checks; easy revoke UI | DP §III.D.2 | same | yes |
| Authorized Data only shown to authorizing user and agents they approve | DP §III.E.3.b | same | yes |
| Aggregation limited to same content owner; never to gain insight into YouTube's business | DP §III.E.2 | same | yes (see §13 Q3) |
| Exactly one API project per API client; quota extension via compliance audit | DP §III.D.1.c, §III.D.3 | same | yes |
| ToS link; privacy policy linking Google Privacy Policy and Google security settings revocation | DP §III.A.1, §III.A.2 | same | yes |
| Security controls, industry-standard transport encryption | DP §III.E.5 | same | yes |
| Derived metrics amendment: statistics and derived metrics storable up to 36 months for audited Analytics & Reporting use cases; content data stays on 30 days; no inference of protected attributes such as health status | DP Section L | https://developers.google.com/youtube/terms/derived-metrics-policy | yes; effective date of application route (June 1, 2026) is from the developer policies guide |
| Acceptable metrics = YouTube API data with add/sub/avg/mul/div only; no merging with other data; no ranking/gamifying channels; no custom channel scores | Policies guide | https://developers.google.com/youtube/terms/developer-policies-guide | yes |
| Quota: 100 search.list calls + 10,000 units/day other endpoints; invalid requests cost ≥ 1 unit | Getting started | https://developers.google.com/youtube/v3/getting-started | yes |
| Quota resets at midnight Pacific Time | Quota cost page | https://developers.google.com/youtube/v3/determine_quota_cost | yes (via search result summary of the official page) |
| channels.list: `id` / `forHandle` (with or without `@`) / `forUsername` / `mine`; 1 unit; one filter only | channels.list | https://developers.google.com/youtube/v3/docs/channels/list | yes |
| videos resource: `paidProductPlacementDetails.hasPaidProductPlacement`, `status.containsSyntheticMedia`, `contentDetails.duration` (ISO 8601), thumbnails incl. maxres/standard; missing ids → unavailable | videos resource | https://developers.google.com/youtube/v3/docs/videos | fields yes; "missing ids are omitted" is standard behaviour, to be confirmed by a 02 live smoke test |
| Refresh token invalidation causes (revoked, 6 months unused, 100 token limit, Testing mode 7 days, time-based access, admin restrictions) | Google OAuth 2.0 | https://developers.google.com/identity/protocols/oauth2 | yes |
| Token revocation endpoint `https://oauth2.googleapis.com/revoke` | OAuth web server flow | https://developers.google.com/identity/protocols/oauth2/web-server#tokenrevoke | from docs memory; not re-fetched |
| RISC: token-revoked/tokens-revoked events, receiver registration, JWT verification, `jti` dedupe | Cross-Account Protection | https://developers.google.com/identity/protocols/risc | yes |
| MultiFernet: encrypt with first key, decrypt trying each, `rotate()` keeps timestamp; AES-128-CBC + HMAC-SHA256 | cryptography | https://cryptography.io/en/latest/fernet/ | yes |
| Partitioning: PK must include partition key; DETACH CONCURRENTLY; DROP faster than DELETE; few-thousand partitions OK with pruning | PostgreSQL 18 | https://www.postgresql.org/docs/18/ddl-partitioning.html | yes |
| `uuidv7()` built-in in PG 18 | PostgreSQL 18 | https://www.postgresql.org/docs/18/functions-uuid.html | yes (executed locally on 18.4) |
| RLS with `current_setting` | PostgreSQL 18 | https://www.postgresql.org/docs/18/ddl-rowsecurity.html | not re-fetched (stable feature) |
| asyncpg dialect: prepared stmt cache 100/conn; with PgBouncer use `NullPool`, `prepared_statement_name_func`, DISCARD | SQLAlchemy 2.0 | https://docs.sqlalchemy.org/en/20/dialects/postgresql.html | yes |
| `on_conflict_do_update(index_elements, set_, where)`; `set_` ignores Python-side onupdate | SQLAlchemy 2.0 | same | yes |
| AsyncSession not safe across concurrent tasks | SQLAlchemy asyncio | https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html | not re-fetched (well known) |
| arq: `_job_id` uniqueness until finished + result cleared; `keep_result` default 3600s; `max_jobs` 10; `job_timeout` 300s; `max_tries` 5; cron `unique=True` runs once across workers; `allow_abort_jobs`; `queue_name`; health check key | arq | https://arq-docs.helpmanual.io/ | yes |
| pydantic-settings: `env_nested_delimiter`, `env_prefix`, `secrets_dir`, dotenv + env precedence over secrets | pydantic-settings | https://pydantic.dev/docs/validation/latest/concepts/pydantic_settings/ | yes |
| Redis single-instance lock pattern (`SET NX PX` + token compare release) | Redis | https://redis.io/docs/latest/develop/clients/patterns/distributed-locks/ | not re-fetched |

**Libraries.** Already in `pyproject.toml`: fastapi, sqlalchemy[asyncio], asyncpg, alembic, arq, redis, pydantic-settings, cryptography, structlog, tenacity, httpx, google-auth-oauthlib. **To add:** `argon2-cffi` (password hashing). No DI framework, no pgvector, no pg_partman (partition management is about 60 lines in `retention/partitions.py`).

**Local environment (verified):** Homebrew PostgreSQL 18.4 (`max_connections=100`, `shared_buffers=128MB`, databases `growthhacker` and `growthhacker_test` and role `growth` exist; extensions citext, pg_trgm, pgcrypto, pg_stat_statements, btree_gist available; **no pgvector**). Redis 8.10.3 (`maxmemory-policy noeviction` ✓, `appendonly no`, fine locally). **Docker daemon not running**, so compose is for parity and prod only for now.

## 8. Edge cases & failure modes

| # | Case | Detection | Handling | User-facing message |
|---|---|---|---|---|
| 1 | Two runs for the same client triggered at once | partial unique index violation on `runs` / Redis lock held | return existing run if same `params_hash`; otherwise supersede (cooperative cancel) and queue new | "An analysis is already running for Apollo Hospitals — showing its progress." |
| 2 | Daily cron refresh collides with a user-triggered run | same index | cron skips (logs `skipped_active_run`); user run wins | none |
| 3 | Two clients share a competitor channel and refresh at once | `single_flight("yt:channel:{id}:sync")` | second waiter re-reads freshness after lease; skips API call if fresh | none |
| 4 | Older API response overwrites newer cache row (slow worker) | upsert `WHERE excluded.fetched_at >= existing` | write is a no-op | none |
| 5 | Same snapshot slot written twice (job retry) | PK `(video_id, slot_at)` | `DO NOTHING` | none |
| 6 | Snapshot insert lands in a missing partition | `no partition of relation found` error | writer calls `ensure_partitions` and retries once; alert if it recurs | none |
| 7 | Partition drop blocked by a long query lock | `lock_timeout` 2s | retry next hour (well inside safety margin); alert if partition age > T−1d | none |
| 8 | Data purged mid-run | missing rows on read; finalize re-validation | reference dropped and gates re-run; run `partial` only if a critical stage fails | "1 reference video became unavailable during analysis and was excluded." |
| 9 | Refresh impossible before 30 days (quota exhausted, API outage) | sweeper sees age ≥ T−1d | delete row (fail closed); dependent artifacts flagged | "Some competitor data expired and will be re-fetched on the next refresh." |
| 10 | Video deleted/made private | id missing from `videos.list` | blank content, delete stats/derived rows, tombstone ≤ T days | reference card shows "Video no longer available" |
| 11 | Video made public again | appears in uploads page | upsert clears tombstone | none |
| 12 | Channel terminated/unavailable | id missing from `channels.list` | mark unavailable; competitor `needs_attention` | "Competitor channel X is no longer available on YouTube. Replace it?" |
| 13 | Handle now resolves to a different channel | resolution id ≠ stored id | no auto-switch; `needs_attention` with both ids | "@handle now points to a different channel. Confirm which to track." |
| 14 | User enters a lookalike handle (`@Apollo247`) | ownership verification fails (03/04) | `own_channel_status='proposed'` until evidence or user confirmation | "We couldn't confirm this channel belongs to apollohospitals.com." |
| 15 | Same own channel added twice in one workspace | unique `(workspace_id, own_channel_id)` | 409 with link to existing client | "This channel is already set up as client Apollo Hospitals." |
| 16 | Large channel (4k+ videos) | `video_count` > threshold | horizon-limited backfill; resumable cursor; own channel full backfill once | "Imported the last 90 days + baseline history (4,312 videos on channel)." |
| 17 | Backfill interrupted by quota | `blocked_on='quota'` | cursor saved; resume after Pacific midnight | "Import paused until YouTube quota resets (~in 6h)." |
| 18 | Hidden subscriber count / hidden likes / disabled comments | NULL fields | store NULL (never 0); metrics treat as missing | "Likes hidden by creator" |
| 19 | Live/upcoming videos (no duration, unstable views) | `live_broadcast_content` | excluded from same-age metrics until `none` | none |
| 20 | Snapshots start only when tracking begins | first snapshot age > 7d | 06 uses interpolation/estimation with method flag; foundation stores `age_hours` + method | "Day-7 views estimated (tracking began at day 12)." |
| 21 | 90-day window refs older than 30-day stats retention | no snapshot at day 7 retained | `views_day7_method='estimated'` (06) or approved storage mode | "Same-age views estimated for older videos." |
| 22 | Client timezone missing/invalid | zoneinfo error | default `UTC` + warn; profile inference (03) proposes value | "Set your timezone for accurate publish-day insights." |
| 23 | DST transitions | n/a (UTC instants) | windows are instant ranges; weekday computed per video in client tz | none |
| 24 | Quota day boundary vs UTC day | ledger `quota_day` in LA tz | counters keyed by LA date | none |
| 25 | JSONB written by an older code version during rolling deploy | `schema_version` < current | upcaster on read; writes current | none |
| 26 | JSONB from a *newer* version read by an older pod | `schema_version` > known | reject read for that row with typed error; retry after deploy | "Temporarily unavailable, retrying." |
| 27 | Profile edited while a run uses the old version | `runs.profile_version_id` fixed | run continues on its version; next run uses new | "Results based on profile v3; v4 applies from next run." |
| 28 | Keyword renamed → topic matches stale | `ref_set_hash` changes | matches recomputed on next run; old recommendation keeps snapshot text | none |
| 29 | Partial stage failure (news radar down) | non-critical stage failed | run `partial`; other recs published | "News opportunities unavailable this run (source error)." |
| 30 | Critical stage failure | stage `failed` | run `failed`; previous published recs stay visible (marked from earlier run) | "Refresh failed; showing results from Nov 18." |
| 31 | Worker crash mid-stage | `heartbeat_at` stale > 5 min | reaper sets stage failed → arq retry resumes from checkpoint | none |
| 32 | arq job re-executed (pessimistic execution) | n/a | all writes idempotent (§5.11); ledger `request_id` unique | none |
| 33 | OAuth revoked in-app | user action | Google revoke + purge ≤ 1h (limit 7d) | "YouTube disconnected. Your private analytics were deleted." |
| 34 | OAuth revoked on Google side | `invalid_grant` / RISC event | purge immediately (limit 30d) | "YouTube access was removed in your Google account. Reconnect to see private analytics." |
| 35 | OAuth connected for a channel ≠ client's own channel | `mine=true` id mismatch | reject, revoke token, store nothing | "That Google account manages a different channel." |
| 36 | Refresh token silently invalidated (100-token limit, Testing mode 7 days) | `invalid_grant` | same as #34; prod OAuth app must be in "In production" status | as #34 |
| 37 | Authorized data viewed by non-approved member | viewer not in `approved_viewer_user_ids` | 403 on own-analytics endpoints; public data still visible | "Ask the channel owner to share private analytics with you." |
| 38 | Encryption key rotation in progress | rows with old `key_id` | MultiFernet decrypts both; rotate job converges | none |
| 39 | Encryption key lost/misconfigured | `InvalidToken` on decrypt | mark token `invalid`; ask reconnect; alert | "Please reconnect YouTube." |
| 40 | Ciphertext swapped between rows | binding mismatch | treat as invalid + security alert | none |
| 41 | Cross-tenant id guessing | tenant repo returns None | 404 | "Not found." |
| 42 | API key leaked | user revokes / anomaly | `revoked_at`; cached principal evicted ≤ 5 min (Redis TTL) — immediate via explicit cache delete | "Key revoked." |
| 43 | Comment contains phone/email/health details | redaction regexes | stored redacted; aggregate signals only | none |
| 44 | Comment author identity | n/a | only peppered HMAC; pepper rotates monthly | none |
| 45 | Search cache key reveals tenant keywords | n/a | never exposed through API; no tenant id stored | none |
| 46 | Tenant deletion | `deletion_requests` | cascade delete tenant rows; null tenant ids in ledgers; shared cache untouched (normal retention) | "Client deleted. All client data is removed within 7 days." |
| 47 | Backups contain API data beyond 30 days | backup policy | PITR/backup retention ≤ 14 days (§10) | none |
| 48 | Redis loss (queue + locks) | connection errors / empty queue | runs reaper re-enqueues `queued` runs from DB; locks are advisory (DB indexes are truth); quota counters rebuilt from ledger for the current day | none |
| 49 | Postgres connection exhaustion | pool timeout | bounded pools per process (§10); 503 with retry-after on API | "Busy, retrying." |
| 50 | Long migration locks hot table | `lock_timeout` in migrations | expand/contract; concurrent index builds | none |
| 51 | Clock skew between workers | snapshot `captured_at` from API response time (server `Date` header) | app trusts DB `now()` for retention decisions; NTP on hosts | none |
| 52 | Retention sweeper not running | watchdog | page on-call; compliance audit metric | none |
| 53 | Recommendation's reference falls below min after purge | GIN lookup on purge | `needs_regeneration`, hidden until next run | "This idea is being refreshed with newer evidence." |
| 54 | Client paused > 28 days | artifacts expire | recommendations redacted; cache for its competitors deleted if no other tenant references them | "Results expired. Resume to regenerate." |
| 55 | Competitor without a YouTube channel | `channel_id NULL` | stays as website-only competitor (03 side-by-side) | "No YouTube channel found for X." |
| 56 | More than 10 competitors confirmed | service check | reject 11th | "Track up to 10 competitors." |
| 57 | Description changed by client after refresh suggestion | `original_description_hash` mismatch on refresh | suggestion `stale` (or `applied_detected` if insertions present) | "Description changed since this suggestion." |
| 58 | News item TTL expires while signal is live | `news_item_id` SET NULL | signal keeps url/title copy | none |
| 59 | Unicode/RTL titles and handles | n/a | `text`/`citext`, NFC-normalized `normalized_text` | none |
| 60 | Extremely long description/comment | length check | truncation at documented limits | none |
| 61 | Evaluation backtest at T older than stats retention | `as_of < now − A` | rejected in standard mode (only T within retained range) | "Backtests need T within the last ~3 weeks unless extended storage is approved." |

## 9. Policy & guardrail check

| Guardrail / policy | How the foundation complies |
|---|---|
| Official APIs only (assignment; DP §III.E.6, §III.I.14) | **Exception (2026-11-21):** a yt-dlp fallback serves public data when the Data API fails, on by default behind `youtube.fallback.enabled`; scraped rows carry `data_source='yt_dlp'` / snapshot `source='yt_dlp'` so they can be found and purged. Otherwise only YouTube Data/Analytics API responses. Website crawl data is in separate `crawled_pages`, never in `yt_*`. |
| Never merge YouTube with other sources into one metric | Schema separation: website/news data live in their own tables; no column combines them. `analysis_artifacts` kinds declare a single source class. |
| Refresh or delete within 30 days | Registry-enforced retention per table, fail-closed deletion, partition drops, watchdog plus CI invariant (every table classified; oldest-age audit). |
| Non-Authorized statistics ≤ 30 days | Snapshots dropped at 28 days; stats-derived artifacts expire from `stats_observed_at`. |
| Authorized data handling | Tenant-scoped only; encrypted tokens; re-verification ≤ 25 days; deletion on revocation (≤ 1h target, 7/30-day limits); approved-viewer list. |
| Aggregation limits (DP §III.E.2) | No Authorized data aggregated across content owners. Public cross-channel views are per-channel numbers plus simple totals/averages (see §13 Q3). |
| No revenue/monetisation estimates | No columns for revenue, CPM, earnings. |
| No audience demographics inference | No demographic columns; comment authors hashed; signals aggregate. |
| No content-category inference | `category_id` stored as API data but not used for grouping; topic grouping is tenant-scoped against the client's own `keywords`/`buyer_questions` (05). |
| Thumbnails simple traits, no faces | `yt_thumbnail_traits` has text/word count/contrast/brightness/colorfulness only; image bytes never stored. |
| Derived metrics disclosure (DP §III.E.4.h) | Every computed metric carries a `method` / computed flag; the API labels them "computed by GrowthHacker from YouTube data". |
| LLM can't add videos/numbers | `recommendations.reference_videos` and numbers come from `evidence` (07); writer output validated by 10. The foundation stores both so audits can diff them. |
| Privacy policy, ToS link, revoke UI (DP §III.A, §III.D.2) | Tracked as tasks for 11/13; the foundation provides the revoke flow and deletion requests. |
| Single API project (DP §III.D.1.c) | One `youtube.api_key` setting; no key pool. |
| Data security (DP §III.E.5) | TLS everywhere (DB `sslmode=require` in prod), encryption at rest (managed PG + Fernet for tokens), least-privilege DB roles, audit log. |

## 10. Performance, cost & scaling

**Quota from the foundation's own jobs** (the rest is 02):
- Retention refresh: `ceil(N_videos_referenced / 28 / 50)` + `ceil(N_channels / 28 / 50)` units/day. For 200k referenced videos and 5k channels: 143 + 4 ≈ **150 units/day** (1.5% of daily quota). In practice most rows are refreshed earlier by snapshot or ingest calls, so this is an upper bound.
- OAuth verification: 1 unit per connected client per day (`channels.list mine=true`).
- Compliance reserve: 10% of daily units held back for refresh-before-expiry.

**Postgres sizing** (standard mode, 30-day rolling):

| Table | Assumption (500 clients, ~3k tracked channels) | Rows | Size (incl. idx) |
|---|---|---|---|
| yt_videos | ~150 videos/channel within horizon | 450k | ~1.5 GB (descriptions dominate) |
| yt_video_snapshots | adaptive cadence avg 1.5/day for ~200k active videos | 6–9M | ~1.2 GB |
| yt_comments | top 20k videos × 100 | 2M | ~0.8 GB |
| topic_matches | 500 clients × ~8k videos | 4M | ~2 GB (JSONB) |
| recommendations + artifacts | 500 × daily runs, current + 30d | ~1M | ~2 GB |
| Total | | | ~10 GB hot |

A managed PG 18 with 2 vCPU / 8 GB RAM / 50 GB storage covers this (shared_buffers ~2GB). Scale up to 4 vCPU / 16 GB at ~2k clients. Approved storage mode (36 months of stats) multiplies snapshots by ~36×, which means monthly partitions and maybe a separate tablespace or BRIN on `slot_at`.

**Connection budget:**
- Local: `max_connections=100`. API 2 processes × (5+5) = 20; `worker-default` 1 × (5+5) = 10; `worker-ingest` 1 × (8+4) = 12; `worker-cpu` 1 × (2+2) = 4; `worker-cron` 1 × (3+2) = 5. Total ≈ 51 + psql/alembic headroom.
- Prod: per-process pool `pool_size=5, max_overflow=5`. Above ~8 processes put PgBouncer (transaction mode) or the provider's pooler in front. In that case set `use_external_pooler=True` → `NullPool` + `prepared_statement_name_func` (uuid names) per SQLAlchemy docs, with PgBouncer `server_reset_query=DISCARD ALL`.

**Latency budget for < 2 min per client refresh** (foundation overhead only): auth < 5 ms (Redis-cached principal); run creation < 50 ms; artifact and recommendation writes batched (≤ 1 s per run); topic match cache reads one indexed scan per client (< 200 ms for 10k rows). The rest belongs to 02/05/06/07/10.

**Deployment topology:**

```text
            ┌──────────── Next.js (13) ────────────┐
            │                                       │ HTTPS (cookie session) / API keys
        ┌───▼────────────────────────────┐
        │ api × N (uvicorn, stateless)   │──SSE── reads run_events (PG) + XREAD gh:run:{id}:events (Redis stream)
        └───┬───────────────┬────────────┘
            │enqueue         │SQL
   ┌────────▼───────┐   ┌────▼──────────────┐
   │ Redis          │   │ PostgreSQL 18     │
   │ db0 queues     │   │ (managed in prod) │
   │ locks, quota,  │   └────▲──────────────┘
   │ rate limits,   │        │
   │ streams, cache │        │
   └──▲──────────▲──┘        │
      │          │           │
 worker-default  worker-ingest  worker-cpu   worker-cron (1 replica)
 (pipeline       (YouTube/      (OCR, embed- (cron_jobs only: snapshots fan-out,
  stages, LLM)    Tavily I/O)    dings)       daily refresh, retention, oauth verify, partitions)
```

- **API:** stateless, horizontally scaled behind a load balancer. SSE connections are long-lived, so set `--timeout-keep-alive` and LB idle timeout ≥ 120s. Replay comes from `run_events` (PG), live updates from Redis Streams. Pub/sub drops messages on reconnect; streams give replay with `Last-Event-ID`.
- **Workers:** separate arq queues per workload (`gh:queue:default`, `gh:queue:ingest`, `gh:queue:cpu`) so CPU-heavy OCR does not starve I/O jobs and vice versa. Each scales on queue depth. arq cron jobs are `unique=True`, but they run in a dedicated `worker-cron` process so schedules live in one place.
- **Redis roles** (key prefix `gh:`):
  - arq queues and results;
  - locks `gh:lock:*`;
  - quota counters `gh:quota:{bucket}:{la_date}` (Lua `INCRBY` with cap check; rebuilt from ledger on loss);
  - token-bucket rate limiters `gh:rl:{provider}`;
  - run event streams `gh:run:{id}:events` (`MAXLEN ~ 1000`, TTL 24h);
  - principal cache `gh:auth:*` (TTL 5m);
  - OAuth access tokens (encrypted, TTL).
  - Prod settings: `maxmemory-policy noeviction` (queues/locks must not be evicted) and `appendonly yes` + `appendfsync everysec`. If cache volume grows, move cache keys to a second Redis with `allkeys-lru`.
- **Backups:** managed PITR with retention ≤ 14 days, so restored copies never hold API data far beyond policy. After a restore, run the retention sweeper before serving traffic.

**docker compose (local parity / small prod):**
- Services: `postgres` (image `postgres:18`, volume, healthcheck `pg_isready`), `redis` (image `redis:8`, `--appendonly yes`), `migrate` (one-off: `alembic upgrade head`, `depends_on: postgres healthy`), `api` (`uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2`), `worker-default`, `worker-ingest`, `worker-cpu` (`arq app.workers.settings.<Queue>WorkerSettings`), `worker-cron`, later `frontend`.
- Healthchecks: API `GET /healthz` (liveness) and `/readyz` (PG + Redis ping); workers `arq --check`.
- Env from `.env`; secrets via compose `secrets:` mapped to `/run/secrets`.
- **Local today (no Docker daemon):** Homebrew services. `brew services start postgresql@18 redis`; `DATABASE_URL=postgresql+asyncpg://growth:growth@localhost:5432/growthhacker`; `make dev` runs `uvicorn --reload` plus `arq` worker processes (or `honcho`/a Procfile). Tests use `growthhacker_test`.

**Prod outline:** containers on a managed platform (ECS/Fargate, Cloud Run for API plus GKE/ECS for workers, or Fly/Render), managed PG 18 (RDS/Cloud SQL/Neon; must support PG ≥ 18 for `uuidv7()`), managed Redis (ElastiCache/Memorystore), secret manager, egress static IP (YouTube key IP restriction), structlog JSON to the log platform, metrics (Prometheus/OTel) for queue depth, quota units, retention oldest-age, run durations. Zero-downtime deploy: migrate job → rolling API → drain workers (arq handles SIGTERM, jobs are idempotent).

**LLM/Tavily cost:** none incurred by the foundation. `provider_usage_ledger` attributes cost per client/run for the ops report (12).

## 11. Testing strategy

- **Unit:** settings parsing (nested env, secrets_dir, SecretStr redaction); `SecretCipher` (round trip, binding mismatch, rotation with 2 keys, `rotate()` keeps timestamp); API key and session hashing; registry duplicate detection; JSONB upcaster chains; timezone helpers (DST edges for `Asia/Kolkata` (none), `America/New_York`; LA quota day at 23:59/00:01 PT); comment PII redaction table tests (emails, Indian/E.164 phones, URLs, mentions).
- **Repository/integration (real PG 18, `growthhacker_test`):** each test in a transaction rolled back (SAVEPOINT pattern), schema created by `alembic upgrade head` once per session.
  - Upsert monotonicity (older fetched_at loses).
  - Snapshot PK idempotency; partition create/drop; missing-partition retry.
  - **Tenant isolation contract test:** for every `TenantRepository` subclass discovered via registry, create rows in workspace A and assert every public method called with scope B returns nothing or raises NotFound.
  - Composite FK rejects cross-workspace client ids.
- **Retention (FrozenClock):** seed every registered table with rows at ages 0, 27, 28, 29, 31 days. Run the sweeper with a fake YouTube gateway that succeeds, fails, or exhausts quota. Assert: (a) no row older than T−1d remains in any class; (b) referenced rows refreshed, unreferenced deleted; (c) recommendations redacted with the right fields nulled; (d) GIN-based invalidation sets `needs_regeneration`. Also a property test over random reference graphs.
- **Schema invariants (CI):** every ORM table has a `retention_registry` entry; every tenant table has `workspace_id` + scoped repository; every JSONB domain column has a `*_schema_version` sibling; no FK from tenant tables into `yt_*`; `alembic check` clean.
- **Concurrency:** two coroutines `single_flight` on the same key → one gateway call; two `create_run` calls → one active run; sweeper deleting while a run reads pinned channels → pinned rows survive.
- **OAuth:** fake Google endpoints via `respx` covering connect with wrong channel, `invalid_grant` → purge, RISC event JWT verification (fixture keys) and `jti` dedupe, deletion SLA report.
- **Migrations:** upgrade/downgrade round trip; expand/contract dry run on a seeded DB.
- **Live smoke (manual, minimal quota):** `channels.list?id=UC0InVdvqNyNzKBl1-TL348A` (1 unit) upserts and refreshes the Apollo channel row; `videos.list` with one real id plus one bogus id (1 unit) confirms that missing ids are omitted (feeds the unavailable logic).
- **Load:** synthetic 5M snapshot rows → partition drop time, `views at age` query p95 < 20 ms per video batch of 500.

## 12. Implementation task breakdown

| # | Task | Size | Depends on |
|---|---|---|---|
| F1 | `core/config.py` Settings tree + `.env.example` update + tests | S | — |
| F2 | `core/clock.py`, `core/logging.py` (structlog + redaction) | S | F1 |
| F3 | `core/db.py`: engine factory (pool vs NullPool), naming convention, session factory, UnitOfWork | M | F1 |
| F4 | Alembic async env (advisory lock, include_object, naming) + CI check | S | F3 |
| F5 | Migration 0001: extensions, tenancy/auth tables, audit_log, deletion_requests | M | F4 |
| F6 | Migration 0002: shared YouTube cache incl. partitioned snapshots + partition helper | L | F4 |
| F7 | Migration 0003: clients, profiles, keywords, buyer_questions, competitors, crawled_pages, oauth/own analytics | M | F5 |
| F8 | Migration 0004: runs, run_stages, run_events, topic_matches, analysis_artifacts, recommendations, feedback, refresh, news, evaluation, ledgers, views | L | F7 |
| F9 | `core/registry.py` + artifact schema registry/upcasters | S | — |
| F10 | Repositories: shared (cache, ledger) with monotonic upserts | M | F6, F9 |
| F11 | Tenant repository base + scoped repos + isolation contract test | M | F7, F8 |
| F12 | `core/security.py`: SecretCipher (MultiFernet + binding), token/API key hashing, argon2 | M | F1 |
| F13 | Auth: sessions, API keys, Principal, `require_role`, FastAPI deps, bootstrap CLI | M | F11, F12 (API wiring with 11) |
| F14 | `core/redis.py`: LockManager, single_flight, rate limiter, quota counter primitives (Lua) | M | F1 (02 consumes) |
| F15 | `core/container.py` composition root + FastAPI lifespan + arq on_startup | S | F3, F12, F14 |
| F16 | Retention: rule registry, sweeper, partitions, redactors, watchdog, compliance metric | L | F10, F11, 02 gateway (refresh batch job) |
| F17 | OAuth lifecycle: connect verification storage, daily verifier, revoke + purge, RISC receiver (flagged) | M | F12, F16, 02 (analytics fetch) |
| F18 | Key rotation job + runbook | S | F12 |
| F19 | Run storage semantics: idempotency, supersede, stale-run reaper, pinning | M | F8 (11 builds runner on top) |
| F20 | docker compose + Procfile/Makefile for Homebrew dev + healthz/readyz | S | F15 |
| F21 | Optional RLS migration + `SET LOCAL` in UoW (feature flag) | M | F11 |
| F22 | Live smoke script (2 units) + load test for snapshots | S | F10, F16 |

Critical path: F1 → F3 → F4 → F6/F7/F8 → F10/F11 → F15 → F16. Components 02 and 11 can start on F10/F14/F15 interfaces once F6 lands.

## 13. Open questions / proposed changes to shared context

1. **Apply for the Derived Metrics and Data Storage amendment (DP Section L).** Without it, non-authorized statistics (snapshots and anything derived from them, like views-at-day-7) cannot be kept past 30 days. That means: (a) same-age comparisons for 90-day windows must use estimated values for videos older than ~5 weeks; (b) backtests at frozen T can only use T within the last ~3 weeks; (c) outlier labelling context expires. **Proposal:** add to shared context §2 that "stats storage beyond 30 days requires Section L approval; until then all stats-derived values expire 30 days after observation," and add an ops task to submit the audit (Analytics & Reporting use case). The schema supports both modes through `retention.stats_storage_mode`.
2. **Ranking channels and channel scores.** The [developer policies guide](https://developers.google.com/youtube/terms/developer-policies-guide) lists as not allowed "Gamify channel performance by ranking or tracking views between different channels" and "Calculate and assign custom 'scores' to channels based on independently calculated averages or ratios". This affects `ChannelScorecard` (06) and the creator ranker (07). **Proposal:** present competitors and creators as evidence lists (per-video outliers, cadence, relevance to client keywords) sorted by a transparent single criterion, not as a composite channel score or leaderboard. Revisit if Section L is approved (it allows leaderboards and custom scores with disclosure).
3. **Cross-channel aggregation (DP §III.E.2).** Literally read, the section limits aggregation to channels under the same content owner. The guide lists "totals across a group of videos or channels" as acceptable. Niche-level trait lift and niche baselines aggregate public data across many owners. **Proposal:** flag for review in the design doc; keep per-channel numbers visible beside any niche aggregate; never aggregate Authorized data across clients.
4. **Authorized Data visibility in agency workspaces (DP §III.E.3.b).** Add to shared context that own-channel analytics are visible only to the authorizing user plus members they explicitly approve at connect time (`approved_viewer_user_ids`), not to the whole workspace.
5. **New shared ports:** add `Clock`, `LockManager`, `SecretCipher`, `UnitOfWork` to the §4 ports list, and add `TenantScope`, `DataClass`, `ArtifactEnvelope`, `EffectiveConfig` to the domain vocabulary.
6. **Retention wording:** change "retention cron purges/refreshes API data older than 30 days" to "acts at 28 days (configurable safety margin), fails closed (delete if refresh impossible), and covers derived artifacts via `api_data_observed_at`/`expires_at`."
7. **Comments:** shared context should state comments are delete-only (28 days), PII-redacted at ingest, with no author names stored.
8. **Video/channel ids in long-lived records** (feedback `produced_video_id`, evaluation labels): the policies do not exempt ids from the 30-day rule. Proposal: keep bare ids (no content or stats) as references, since they are needed to refresh or re-fetch. Flag for review; if rejected, those columns move to the 30-day redaction class.
9. **PostgreSQL ≥ 18 required in all environments** (`uuidv7()` server default, `NULLS NOT DISTINCT`, which needs ≥ 15). Fallback if a managed provider lacks 18: generate UUIDv7 in Python (adds a dependency) and keep the schema otherwise unchanged.
10. **New dependency `argon2-cffi`** for password sessions. Alternatively, start with API keys + Google Sign-In only and defer passwords. Owner decision needed.
11. **pgvector not used** (not available in local Homebrew PG). Similarity runs in numpy per client (05 should confirm scale assumptions: ≤ 50 topic refs × ≤ 20k videos).
12. **Run families:** daily refresh and user analysis share one concurrency slot per client. A user-triggered run with different params supersedes a running daily refresh. 11 should confirm.
13. **Backups:** PITR retention ≤ 14 days to avoid keeping API data in backups beyond policy. Needs owner agreement on the recovery window trade-off.
