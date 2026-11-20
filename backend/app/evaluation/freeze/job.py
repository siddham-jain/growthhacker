import hashlib
import json
import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.evaluation.freeze.bundle import (
    BundleStore,
    FreezeBundle,
    FreezeManifest,
    late_rows,
    oldest_api_datum,
)
from app.evaluation.freeze.store import FreezeRepository
from app.models.competitors import CompetitorSet
from app.models.runs import ACTIVE_RUN_STATUSES, PipelineRun
from app.models.tenancy import Client
from app.pipeline.contracts import RunConfigSnapshot
from app.services.analytics.inputs import AnalysisRequest, AnalyticsInputs
from app.services.analytics.settings import AnalyticsEngineSettings


class FreezeRefused(RuntimeError):
    pass


def analysis_request(snapshot: RunConfigSnapshot, as_of: datetime) -> AnalysisRequest:
    return AnalysisRequest(
        workspace_id=snapshot.workspace_id,
        client_id=snapshot.client_id,
        as_of=as_of,
        own_channel_id=snapshot.own_channel_id,
        competitor_channel_ids=snapshot.competitor_channel_ids,
        topic_refs=snapshot.topic_refs,
        timezone=snapshot.market.timezone,
        country=snapshot.market.country,
        language=snapshot.market.language,
        oauth_connected=snapshot.oauth_connected,
    )


def gate_config_version(settings: Settings) -> str:
    payload = json.dumps(settings.recommend.model_dump(mode="json"), sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def record_counts(inputs: AnalyticsInputs) -> dict[str, int]:
    return {
        "channels": len(inputs.channels),
        "videos": len(inputs.videos),
        "snapshots": sum(len(items) for items in inputs.snapshots.values()),
        "comments": sum(len(items) for items in inputs.comments.values()),
        "topic_matches": len(inputs.topic_matches),
        "thumbnails": len(inputs.thumbnails),
        "topic_refs": len(inputs.topic_refs),
    }


class FreezeJob:
    """dumps what the analysis reads at T into a bundle plus an eval_freeze row; needs no recommender."""

    def __init__(self, container: Any) -> None:
        self.session_factory = container.session_factory
        self.settings: Settings = container.settings
        self.config_source = container.adapter("run_config_source")
        self.input_source = container.adapter("analytics_inputs")
        self.store = BundleStore.for_settings(self.settings)

    async def run(self, client_id: uuid.UUID, as_of: datetime | None = None) -> FreezeManifest:
        async with self.session_factory() as session:
            client = await session.get(Client, client_id)
            if client is None:
                raise FreezeRefused(f"client {client_id} does not exist")
            now = await _database_now(session)
            as_of = as_of or now
            if as_of > now:
                raise FreezeRefused("a freeze cannot describe the future")
            await _refuse_while_running(session, client_id)
            snapshot = await self.config_source.snapshot(
                session, client, self.settings.evaluation.backtest.window_days
            )
            competitor_version = await _competitor_set_version(session, client)
        inputs = (await self.input_source.load(analysis_request(snapshot, as_of))).visible_at(as_of)
        late = late_rows(inputs, as_of)
        oldest = oldest_api_datum(inputs, now)
        manifest = FreezeManifest(
            freeze_id=uuid.uuid4(),
            workspace_id=client.workspace_id,
            client_id=client.id,
            as_of=as_of,
            created_at=now,
            channel_ids=inputs.channel_ids(*{c.role for c in inputs.channels}),
            own_channel_id=inputs.own_channel_id,
            competitor_set_version=competitor_version,
            topic_ref_version=snapshot.topic_set_version,
            business_profile_version=snapshot.profile_version or 0,
            gate_config_version=gate_config_version(self.settings),
            analytics_config_hash=AnalyticsEngineSettings.from_settings(self.settings).settings_hash(),
            record_counts=record_counts(inputs),
            oldest_api_fetched_at=oldest,
            # the copy made at T is deleted 28 days after T; older rows inside it are pruned on their own clock
            expires_at=as_of + timedelta(days=self.settings.policy.retention_days),
            pit_level="approximate" if late else "strict",
            late_tables=sorted(late),
        )
        directory = self.store.write(FreezeBundle(manifest=manifest, snapshot=snapshot, inputs=inputs))
        async with self.session_factory() as session, session.begin():
            await FreezeRepository(session).add(manifest, str(directory))
        return manifest


async def _database_now(session: AsyncSession) -> datetime:
    # the db clock, so worker clock drift cannot move T (edge case 68)
    return (await session.execute(select(func.now()))).scalar_one()


async def _refuse_while_running(session: AsyncSession, client_id: uuid.UUID) -> None:
    # a freeze taken mid-ingest would mix two states of the same channel (edge case 67)
    statement = select(func.count()).where(
        PipelineRun.client_id == client_id, PipelineRun.status.in_(ACTIVE_RUN_STATUSES)
    )
    if (await session.execute(statement)).scalar_one():
        raise FreezeRefused("a run is active for this client; freeze again once it finishes")


async def _competitor_set_version(session: AsyncSession, client: Client) -> int:
    statement = select(func.max(CompetitorSet.version)).where(
        CompetitorSet.workspace_id == client.workspace_id, CompetitorSet.client_id == client.id
    )
    return (await session.execute(statement)).scalar_one_or_none() or 0
