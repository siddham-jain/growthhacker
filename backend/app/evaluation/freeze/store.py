from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.evaluation.freeze.bundle import FreezeManifest
from app.models.evaluation import EvalFreeze

# a backtest run and its freeze must describe the same instant; the api passes the freeze's as_of back
AS_OF_TOLERANCE = timedelta(seconds=1)


class FreezeNotFound(LookupError):
    pass


def manifest_row(manifest: FreezeManifest, bundle_uri: str) -> EvalFreeze:
    return EvalFreeze(
        freeze_id=manifest.freeze_id,
        workspace_id=manifest.workspace_id,
        client_id=manifest.client_id,
        as_of=manifest.as_of,
        channel_ids=list(manifest.channel_ids),
        competitor_set_version=manifest.competitor_set_version,
        topic_ref_version=manifest.topic_ref_version,
        business_profile_version=manifest.business_profile_version,
        gate_config_version=manifest.gate_config_version,
        analytics_config_hash=manifest.analytics_config_hash,
        record_counts=dict(manifest.record_counts),
        bundle_uri=bundle_uri,
        pit_level=manifest.pit_level,
        oldest_api_fetched_at=manifest.oldest_api_fetched_at,
        expires_at=manifest.expires_at,
        status="active",
        created_at=manifest.created_at,
    )


class FreezeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, manifest: FreezeManifest, bundle_uri: str) -> None:
        self.session.add(manifest_row(manifest, bundle_uri))
        await self.session.flush()

    async def get(self, freeze_id: UUID) -> EvalFreeze:
        row = await self.session.get(EvalFreeze, freeze_id)
        if row is None:
            raise FreezeNotFound(f"freeze {freeze_id} does not exist")
        return row

    async def at(self, client_id: UUID, as_of: datetime) -> EvalFreeze:
        statement = (
            select(EvalFreeze)
            .where(
                EvalFreeze.client_id == client_id,
                EvalFreeze.as_of >= as_of - AS_OF_TOLERANCE,
                EvalFreeze.as_of <= as_of + AS_OF_TOLERANCE,
            )
            .order_by(EvalFreeze.created_at.desc())
            .limit(1)
        )
        row = (await self.session.execute(statement)).scalar_one_or_none()
        if row is None:
            raise FreezeNotFound(
                f"no freeze for client {client_id} at {as_of.isoformat()}; run `python -m app.evaluation freeze create`"
            )
        return row

    async def listed(self, client_id: UUID | None = None) -> list[EvalFreeze]:
        statement = select(EvalFreeze).order_by(EvalFreeze.as_of.desc())
        if client_id is not None:
            statement = statement.where(EvalFreeze.client_id == client_id)
        return list((await self.session.execute(statement)).scalars())

    async def expired(self, now: datetime) -> list[EvalFreeze]:
        statement = select(EvalFreeze).where(EvalFreeze.expires_at <= now).order_by(EvalFreeze.expires_at)
        return list((await self.session.execute(statement)).scalars())

    async def mark_purged(self, freeze_id: UUID, now: datetime) -> None:
        await self.session.execute(
            update(EvalFreeze)
            .where(EvalFreeze.freeze_id == freeze_id, EvalFreeze.purged_at.is_(None))
            .values(status="purged", purged_at=now, bundle_uri=None, channel_ids=[])
        )
