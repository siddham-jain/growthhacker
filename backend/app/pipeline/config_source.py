from typing import Any, Protocol

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import Clock
from app.core.registry import adapter_registry
from app.domain.profile import FreshnessProfile
from app.domain.topics import TopicRef
from app.models.competitors import CompetitorSet
from app.models.tenancy import Client, OAuthToken
from app.models.topics import TopicRef as TopicRefRow
from app.models.topics import TopicUniverse
from app.models.website import BusinessProfile
from app.pipeline.client_settings import read_settings
from app.pipeline.contracts import Market, RunConfigSnapshot
from app.pipeline.fingerprint import sha256_hex

_HASHED_FIELDS = (
    "window_days",
    "own_channel_id",
    "website_url",
    "business_name",
    "channel_ref",
    "competitor_channel_ids",
    "topic_refs",
    "topic_set_version",
    "profile_version",
    "oauth_connected",
    "market",
    "feature_flags",
)


class ConfigInvalid(ValueError):
    code = "config_invalid"


class RunConfigSource(Protocol):
    async def snapshot(
        self, session: AsyncSession, client: Client, window_days: int | None
    ) -> RunConfigSnapshot: ...


def config_hash(snapshot_fields: dict[str, Any]) -> str:
    return sha256_hex({name: snapshot_fields.get(name) for name in _HASHED_FIELDS})


class TableRunConfigSource:
    """builds the frozen run inputs from the owners' tables (01 clients, 03 profiles, 04 sets, 05 refs)."""

    def __init__(self, clock: Clock, feature_flags: dict[str, bool]) -> None:
        self._clock = clock
        self._feature_flags = feature_flags

    async def snapshot(
        self, session: AsyncSession, client: Client, window_days: int | None
    ) -> RunConfigSnapshot:
        settings = read_settings(client)
        if client.country is None:
            raise ConfigInvalid("client market country is not set")
        profile = await self._latest_profile(session, client)
        topic_set_version, refs = await self._topic_refs(session, client)
        fields: dict[str, Any] = {
            "workspace_id": client.workspace_id,
            "client_id": client.id,
            "config_version": settings.config_version,
            "window_days": window_days or settings.window_days,
            "own_channel_id": client.own_channel_id if client.own_channel_status == "verified" else None,
            "website_url": client.website_url,
            "business_name": client.name,
            "channel_ref": getattr(settings, "channel_ref", None) or client.own_channel_id,
            "competitor_channel_ids": await self._competitors(session, client),
            "topic_refs": [ref.model_dump(mode="json") for ref in refs],
            "topic_set_version": topic_set_version,
            "freshness_profile": self._freshness(profile),
            "profile_version": None if profile is None else profile.version,
            "oauth_connected": await self._oauth_connected(session, client),
            "market": Market(
                country=client.country, language=client.language or "en", timezone=client.timezone
            ).model_dump(mode="json"),
            "feature_flags": dict(self._feature_flags),
        }
        return RunConfigSnapshot.model_validate({**fields, "config_hash": config_hash(fields)})

    async def _latest_profile(self, session: AsyncSession, client: Client) -> BusinessProfile | None:
        statement = (
            select(BusinessProfile)
            .where(
                BusinessProfile.workspace_id == client.workspace_id,
                BusinessProfile.client_id == client.id,
                BusinessProfile.status.in_(("confirmed", "awaiting_confirmation")),
            )
            .order_by(BusinessProfile.version.desc())
            .limit(1)
        )
        return (await session.execute(statement)).scalar_one_or_none()

    @staticmethod
    def _freshness(profile: BusinessProfile | None) -> dict[str, Any] | None:
        if profile is None or not isinstance(profile.profile, dict) or "freshness" not in profile.profile:
            return None
        try:
            return FreshnessProfile.model_validate(profile.profile["freshness"]).model_dump(mode="json")
        except ValidationError:
            return None

    async def _competitors(self, session: AsyncSession, client: Client) -> list[str]:
        statement = (
            select(CompetitorSet.competitor_channel_ids)
            .where(CompetitorSet.workspace_id == client.workspace_id, CompetitorSet.client_id == client.id)
            .order_by(CompetitorSet.version.desc())
            .limit(1)
        )
        ids = (await session.execute(statement)).scalar_one_or_none()
        return sorted(ids or [])

    async def _topic_refs(self, session: AsyncSession, client: Client) -> tuple[int, list[TopicRef]]:
        version = (
            await session.execute(
                select(func.max(TopicUniverse.profile_version)).where(
                    TopicUniverse.workspace_id == client.workspace_id, TopicUniverse.client_id == client.id
                )
            )
        ).scalar_one_or_none()
        if version is None:
            return 0, []
        now = self._clock.now()
        rows = (
            (
                await session.execute(
                    select(TopicRefRow)
                    .where(
                        TopicRefRow.workspace_id == client.workspace_id,
                        TopicRefRow.client_id == client.id,
                        TopicRefRow.profile_version == version,
                        TopicRefRow.status == "active",
                        (TopicRefRow.expires_at.is_(None)) | (TopicRefRow.expires_at > now),
                    )
                    .order_by(TopicRefRow.id)
                )
            )
            .scalars()
            .all()
        )
        refs = [
            TopicRef(
                id=row.id,
                client_id=row.client_id,
                profile_version=row.profile_version,
                kind=row.kind,
                source_item_id=row.source_item_id,
                label=row.label,
                definition=row.definition,
                negative_contexts=list(row.negative_contexts),
                parent_ids=list(row.parent_ids),
                offering_ids=list(row.offering_ids),
                funnel_stage=row.funnel_stage,
                facets=list(row.facets),
                core_entity_terms=list(row.core_entity_terms),
                locality_terms=list(row.locality_terms),
                weight=row.weight,
                status=row.status,
                expires_at=row.expires_at,
                fingerprint=row.fingerprint,
            )
            for row in rows
        ]
        return version, refs

    async def _oauth_connected(self, session: AsyncSession, client: Client) -> bool:
        statement = select(func.count()).where(
            OAuthToken.workspace_id == client.workspace_id,
            OAuthToken.client_id == client.id,
            OAuthToken.status == "active",
        )
        return bool((await session.execute(statement)).scalar_one())


@adapter_registry.register("run_config_source", "tables")
def build_table_run_config_source(container: Any) -> TableRunConfigSource:
    return TableRunConfigSource(container.clock, container.settings.features.model_dump())
