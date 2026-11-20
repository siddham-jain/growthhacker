from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import select

from app.core.tenancy import TenantScope
from app.domain.profile import BusinessProfile, BuyerQuestion, ReviewStatus
from app.domain.runs import ACTIVE_RUN_STATUSES
from app.models.runs import PipelineRun, RunArtifact
from app.pipeline.artifacts import ArtifactExpiry, PgArtifactStore, RunArtifactReader
from app.pipeline.contracts import RunConfigSnapshot
from app.ports.artifacts import ArtifactKey, ArtifactReader
from app.services.analytics.inputs import AnalysisRequest
from app.services.website_intel.persistence import WebsiteIntelStore


class NoActiveRun(LookupError):
    pass


class ProfileMissing(LookupError):
    pass


@dataclass(frozen=True, slots=True)
class ActiveRun:
    run_id: UUID
    scope: TenantScope
    snapshot: RunConfigSnapshot
    as_of: datetime
    artifacts: ArtifactReader


def artifact_store(container: Any, scope: TenantScope) -> PgArtifactStore:
    settings = container.settings
    return PgArtifactStore(
        container.session_factory,
        scope,
        container.clock,
        ArtifactExpiry(
            api_data_days=settings.policy.retention_days, other_days=settings.retention.run_artifacts_ttl_days
        ),
    )


async def active_run(container: Any, client_id: UUID) -> ActiveRun:
    """the client's one running run (a partial unique index allows only one); its artifacts are this run's inputs."""
    async with container.session_factory() as session:
        run = (
            await session.execute(
                select(PipelineRun).where(
                    PipelineRun.client_id == client_id,
                    PipelineRun.status.in_([status.value for status in ACTIVE_RUN_STATUSES]),
                )
            )
        ).scalar_one_or_none()
    if run is None:
        raise NoActiveRun(f"client {client_id} has no active run")
    scope = TenantScope(workspace_id=run.workspace_id, client_id=run.client_id)
    return ActiveRun(
        run_id=run.id,
        scope=scope,
        snapshot=RunConfigSnapshot.model_validate(run.config_snapshot),
        as_of=run.as_of,
        artifacts=RunArtifactReader(artifact_store(container, scope), run.id),
    )


async def latest_artifact[T: BaseModel](
    container: Any, scope: TenantScope, key: ArtifactKey[T], *, exclude_run: UUID | None = None
) -> T | None:
    """newest stored artifact of this kind for the client, across runs (cross-run reuse)."""
    statement = (
        select(RunArtifact.run_id)
        .where(
            RunArtifact.workspace_id == scope.workspace_id,
            RunArtifact.client_id == scope.client_id,
            RunArtifact.key == key.name,
            RunArtifact.schema_version == key.schema_version,
            RunArtifact.expires_at > container.clock.now(),
        )
        .order_by(RunArtifact.created_at.desc())
        .limit(2)
    )
    async with container.session_factory() as session:
        run_ids = [run_id for run_id in (await session.execute(statement)).scalars() if run_id != exclude_run]
    if not run_ids:
        return None
    return await artifact_store(container, scope).get(run_ids[0], key)


async def profile_and_questions(
    container: Any, scope: TenantScope
) -> tuple[BusinessProfile | None, list[BuyerQuestion]]:
    async with container.uow(read_only=True) as uow:
        store = WebsiteIntelStore(uow.session, scope)
        profile = await store.latest_profile()
        questions = await store.buyer_questions()
    return profile, [question for question in questions if question.status is ReviewStatus.confirmed]


def analysis_request_of(
    snapshot: RunConfigSnapshot, as_of: datetime, creator_channel_ids: Sequence[str] = ()
) -> AnalysisRequest:
    return AnalysisRequest(
        workspace_id=snapshot.workspace_id,
        client_id=snapshot.client_id,
        as_of=as_of,
        own_channel_id=snapshot.own_channel_id,
        competitor_channel_ids=snapshot.competitor_channel_ids,
        creator_channel_ids=list(creator_channel_ids),
        topic_refs=snapshot.topic_refs,
        timezone=snapshot.market.timezone,
        country=snapshot.market.country,
        language=snapshot.market.language,
        oauth_connected=snapshot.oauth_connected,
    )
