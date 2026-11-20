from collections import Counter
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any

from app.adapters.youtube.errors import YouTubeApiError
from app.core.registry import artifact_schema_registry, stage_registry
from app.core.tenancy import TenantScope
from app.domain.common import ChannelRole, DomainModel
from app.domain.runs import RunKind, RunNotice, RunNoticeCode
from app.domain.topics import TopicKind
from app.domain.youtube import SearchQuery
from app.pipeline.contracts import RunConfigSnapshot, RunContext, StageBudget, StagePolicy, StageResult
from app.pipeline.stages.youtube import charge_usage, data_layer, open_run_budget, youtube_context
from app.ports.artifacts import ArtifactKey
from app.ports.quota import QuotaExhausted
from app.services.competitors.repository import CompetitorRepository

# one cached query per analysis run; daily refreshes reuse the shared cache (72 h ttl) for free
NICHE_QUERIES = {RunKind.analysis: 1, RunKind.daily_refresh: 1}
SEARCH_LOOKBACK_DAYS = 30
# single short words ("ENT") are too ambiguous for a youtube search
MIN_SINGLE_WORD_QUERY = 3
# search-hit channels baselined per run so creator lists have uploads to judge (one uploads page each)
CREATOR_BASELINES = 10


class NicheChannel(DomainModel):
    channel_id: str
    role: ChannelRole
    status: str
    videos: int = 0


class NicheDiscovery(DomainModel):
    channels: list[NicheChannel] = []
    queries: list[str] = []
    # channel id -> videos it placed in the cached niche searches
    search_hits: dict[str, int] = {}
    youtube_units: int = 0


def creator_channel_ids(discovery: NicheDiscovery | None) -> list[str]:
    if discovery is None:
        return []
    return [channel.channel_id for channel in discovery.channels if channel.role is ChannelRole.creator]


NICHE_DISCOVERY: ArtifactKey[NicheDiscovery] = ArtifactKey(
    name="niche_discovery", model=NicheDiscovery, schema_version=1, contains_api_data=True
)
artifact_schema_registry.register(NICHE_DISCOVERY.name, NICHE_DISCOVERY.schema_version, NicheDiscovery)


def niche_query(snapshot: RunConfigSnapshot, as_of: datetime) -> str | None:
    """one of the client's offerings per day, rotating, so creator discovery covers every offering over time."""
    entities = [ref for ref in snapshot.topic_refs if ref.kind is not TopicKind.buyer_question]
    searchable = [
        ref for ref in entities if len(ref.label.split()) > 1 or len(ref.label) > MIN_SINGLE_WORD_QUERY
    ]
    offerings = [ref for ref in searchable if ref.kind is TopicKind.offering]
    pool = sorted(offerings or searchable, key=lambda ref: ref.id)
    if not pool:
        return None
    return pool[as_of.toordinal() % len(pool)].label


@stage_registry.register("niche_discovery")
class NicheDiscoveryStage:
    """niche and benchmark uploads (light depth) plus one shared, cached niche search for creators and trending."""

    name = "niche_discovery"
    version = 1
    depends_on: tuple[str, ...] = ("snapshot_config",)
    optional_deps: tuple[str, ...] = ()
    produces = NICHE_DISCOVERY
    policy = StagePolicy(
        timeout_s=300,
        soft_timeout_s=180,
        max_attempts=2,
        critical=False,
        budget=StageBudget(youtube_units=120, search_calls=1),
    )
    weight = 8.0

    def enabled(self, snapshot: RunConfigSnapshot) -> bool:
        return True

    def fingerprint_inputs(self, ctx: RunContext[Any]) -> Mapping[str, Any]:
        return {
            "competitors": sorted(ctx.snapshot.competitor_channel_ids),
            "query": niche_query(ctx.snapshot, ctx.as_of),
            "pt_day": ctx.as_of.date().isoformat(),
        }

    async def run(self, ctx: RunContext[Any]) -> StageResult[NicheDiscovery]:
        scope = TenantScope(workspace_id=ctx.workspace_id, client_id=ctx.client_id)
        async with ctx.deps.uow(read_only=True) as uow:
            competitor_set = await CompetitorRepository(uow.session, scope).active_set()
        channels: list[tuple[str, ChannelRole]] = []
        if competitor_set is not None:
            # benchmarks are read at niche depth too; only competitors get the full lookback
            channels = [(cid, ChannelRole.niche) for cid in competitor_set.niche_channel_ids]
            channels += [(cid, ChannelRole.niche) for cid in competitor_set.benchmark_channel_ids]
        layer = data_layer(ctx)
        await open_run_budget(ctx, layer)
        before = await layer.quota.run_usage(ctx.run_id)
        request = youtube_context(ctx)
        mode = "incremental" if ctx.kind is RunKind.daily_refresh else "full"
        found: list[NicheChannel] = []
        notices: list[RunNotice] = []
        for channel_id, role in channels:
            await ctx.checkpoint()
            result = await layer.ingest.ingest_channel(channel_id, role, request, mode=mode)
            found.append(
                NicheChannel(
                    channel_id=channel_id, role=role, status=result.status, videos=len(result.videos)
                )
            )
            if result.status == "quota_limited":
                notices.append(
                    RunNotice(
                        code=RunNoticeCode.quota_limited,
                        stage=self.name,
                        message="Some niche channels were not refreshed today because of the daily data limit.",
                    )
                )
                break
        artifact = NicheDiscovery(channels=found)
        query = niche_query(ctx.snapshot, ctx.as_of)
        if query and NICHE_QUERIES.get(ctx.kind, 0) and not notices:
            artifact = await self._search(ctx, layer, request, query, artifact, notices)
            tracked = {channel_id for channel_id, _ in channels} | set(ctx.snapshot.competitor_channel_ids)
            artifact = await self._baseline_creators(ctx, layer, request, artifact, tracked, notices)
        units = await charge_usage(ctx, layer, before)
        artifact = artifact.model_copy(update={"youtube_units": units})
        await ctx.reporter.step(
            "niche.done", f"Checked {len(found)} niche channels and {len(artifact.queries)} niche searches"
        )
        return StageResult(
            status="degraded" if notices else "succeeded",
            artifact=artifact,
            counts={"channels": len(found), "search_channels": len(artifact.search_hits)},
            notices=tuple(notices),
        )

    @staticmethod
    async def _baseline_creators(
        ctx, layer, request, artifact: NicheDiscovery, tracked: set[str], notices
    ) -> NicheDiscovery:
        hits = artifact.search_hits
        ranked = sorted((cid for cid in hits if cid not in tracked), key=lambda cid: (-hits[cid], cid))
        results = await layer.ingest.ensure_baseline(ranked[:CREATOR_BASELINES], request)
        found = [
            NicheChannel(
                channel_id=result.channel_id,
                role=ChannelRole.creator,
                status=result.status,
                videos=len(result.videos),
            )
            for result in results
            if result.status != "quota_limited"
        ]
        if any(result.status == "quota_limited" for result in results):
            notices.append(
                RunNotice(
                    code=RunNoticeCode.quota_limited,
                    stage="niche_discovery",
                    message="Some creator channels were not read today because of the daily data limit.",
                )
            )
        return artifact.model_copy(update={"channels": [*artifact.channels, *found]})

    @staticmethod
    async def _search(ctx, layer, request, query: str, artifact: NicheDiscovery, notices) -> NicheDiscovery:
        snapshot = ctx.snapshot
        search = SearchQuery(
            q=query,
            type="video",
            published_after=(ctx.as_of - timedelta(days=SEARCH_LOOKBACK_DAYS)).date(),
            region_code=snapshot.market.country,
            relevance_language=snapshot.market.language.split("-")[0],
        )
        try:
            page = await layer.gateway.search(search, None, request)
        except (QuotaExhausted, YouTubeApiError):
            notices.append(
                RunNotice(
                    code=RunNoticeCode.quota_limited,
                    stage="niche_discovery",
                    message="Niche search was skipped today; creators come from tracked channels only.",
                )
            )
            return artifact
        hits = Counter(hit.channel_id for hit in page.items if hit.kind == "video")
        own = {snapshot.own_channel_id} if snapshot.own_channel_id else set()
        return artifact.model_copy(
            update={
                "queries": [query],
                "search_hits": {cid: count for cid, count in sorted(hits.items()) if cid not in own},
            }
        )
