import uuid
from collections.abc import Sequence
from datetime import date, datetime
from typing import Literal, Protocol

from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert

from app.core.db import SessionFactory, UnitOfWork
from app.core.tenancy import TenantScope
from app.domain.refresh import DescriptionRefresh, RefreshEligibility, RefreshImpact, RefreshStatus
from app.models.refresh import DescriptionRefresh as DescriptionRefreshRow
from app.models.refresh import LinkCheckCache
from app.models.refresh import RefreshEligibility as RefreshEligibilityRow
from app.models.refresh import RefreshImpact as RefreshImpactRow
from app.repositories.shared.base import SharedRepository
from app.repositories.tenant.base import TenantMismatchError, TenantRepository
from app.services.refresh.inputs import PastImpact, StoredRefresh
from app.services.refresh.lifecycle import StatusChange
from app.services.refresh.links.health import CachedLink
from app.services.refresh.links.status import LinkStatus, LinkStatusClass
from app.services.refresh.triggers.base import KEYWORD_TRIGGER_KINDS

APPLIED = (RefreshStatus.applied_claimed.value, RefreshStatus.applied_detected.value)


def stored_refresh(row: DescriptionRefreshRow) -> StoredRefresh:
    refresh = DescriptionRefresh(
        id=str(row.id),
        client_id=row.client_id,
        video_id=row.video_id,
        run_id=row.run_id,
        triggers=row.triggers,
        operations=row.operations,
        advisories=row.advisories,
        suggested_tags=row.suggested_tags,
        keywords_added=row.keywords_added,
        inserted_bytes=row.inserted_bytes,
        resulting_bytes=row.resulting_bytes,
        expected_effect=row.expected_effect,
        confidence=row.confidence,
        priority=row.priority,
        expires_at=row.expires_at,
        status=RefreshStatus(row.status),
        description_hash_at_generation=row.description_hash_at_generation,
        review_required=row.review_required,
        generated_at=row.generated_at,
    )
    source: Literal["user", "detected"] | None = (
        "detected" if row.applied_source == "detected" else "user" if row.applied_source else None
    )
    return StoredRefresh(
        refresh=refresh, applied_at=row.applied_at, applied_source=source, updated_at=row.updated_at
    )


class DescriptionRefreshRepository(TenantRepository[DescriptionRefreshRow]):
    model = DescriptionRefreshRow

    async def save(self, refresh: DescriptionRefresh) -> None:
        # the id is derived from the run, so a resumed attempt rewrites its own still-open row
        if self.scope.client_id is not None and refresh.client_id != self.scope.client_id:
            raise TenantMismatchError("refresh belongs to another client")
        dumped = refresh.model_dump(mode="json")
        values = {
            "id": uuid.UUID(refresh.id),
            "workspace_id": self.scope.workspace_id,
            "client_id": refresh.client_id,
            "video_id": refresh.video_id,
            "run_id": refresh.run_id,
            "triggers": dumped["triggers"],
            "operations": dumped["operations"],
            "advisories": dumped["advisories"],
            "suggested_tags": refresh.suggested_tags,
            "keywords_added": refresh.keywords_added,
            "dedupe_keys": sorted({trigger.dedupe_key for trigger in refresh.triggers}),
            "inserted_bytes": refresh.inserted_bytes,
            "resulting_bytes": refresh.resulting_bytes,
            "expected_effect": refresh.expected_effect,
            "confidence": dumped["confidence"],
            "priority": refresh.priority,
            "expires_at": refresh.expires_at,
            "status": refresh.status.value,
            "description_hash_at_generation": refresh.description_hash_at_generation,
            "review_required": refresh.review_required,
            "generated_at": refresh.generated_at,
        }
        statement = insert(DescriptionRefreshRow).values(**values)
        fixed = ("id", "workspace_id", "client_id", "video_id", "run_id")
        await self.session.execute(
            statement.on_conflict_do_update(
                index_elements=["id"],
                set_={key: statement.excluded[key] for key in values if key not in fixed},
                where=(DescriptionRefreshRow.workspace_id == self.scope.workspace_id)
                & (DescriptionRefreshRow.client_id == refresh.client_id)
                & (DescriptionRefreshRow.status == RefreshStatus.proposed.value),
            )
        )

    async def history(self, since: datetime) -> list[StoredRefresh]:
        # unredacted rows only: redaction at 28 days removes the evidence a suggestion needs
        statement = self._scoped(
            select(DescriptionRefreshRow).where(
                DescriptionRefreshRow.redacted_at.is_(None),
                or_(DescriptionRefreshRow.generated_at >= since, DescriptionRefreshRow.status.in_(APPLIED)),
            )
        ).order_by(DescriptionRefreshRow.generated_at)
        rows = (await self.session.execute(statement)).scalars().all()
        return [stored_refresh(row) for row in rows]

    async def apply_change(self, change: StatusChange) -> bool:
        values: dict[str, object] = {"status": change.to_status.value}
        if change.applied_at is not None:
            values.update(applied_at=change.applied_at, applied_source=change.applied_source)
        return await self._transition(uuid.UUID(change.refresh_id), [change.from_status.value], values)

    async def mark_applied(self, refresh_id: str, applied_at: date) -> bool:
        values = {
            "status": RefreshStatus.applied_claimed.value,
            "applied_at": applied_at,
            "applied_source": "user",
        }
        return await self._transition(uuid.UUID(refresh_id), [RefreshStatus.proposed.value], values)

    async def dismiss(self, refresh_id: str, reason: str | None) -> bool:
        values = {"status": RefreshStatus.dismissed.value, "dismissed_reason": reason}
        return await self._transition(uuid.UUID(refresh_id), [RefreshStatus.proposed.value], values)

    async def _transition(self, refresh_id: uuid.UUID, from_statuses: list[str], values: dict) -> bool:
        statement = (
            update(DescriptionRefreshRow)
            .where(
                DescriptionRefreshRow.id == refresh_id,
                DescriptionRefreshRow.workspace_id == self.scope.workspace_id,
                DescriptionRefreshRow.status.in_(from_statuses),
            )
            .values(**values)
        )
        if self.scope.client_id is not None:
            statement = statement.where(DescriptionRefreshRow.client_id == self.scope.client_id)
        result = await self.session.execute(statement)
        return result.rowcount == 1


class RefreshEligibilityRepository(TenantRepository[RefreshEligibilityRow]):
    model = RefreshEligibilityRow

    def save(
        self, eligibility: RefreshEligibility, last_keyword_eval_at: datetime | None
    ) -> RefreshEligibilityRow:
        return self.add(
            RefreshEligibilityRow(
                client_id=eligibility.client_id,
                run_id=eligibility.run_id,
                decision=eligibility.decision,
                cadence_days=eligibility.cadence_days,
                keyword_refresh_need=eligibility.keyword_refresh_need,
                components=eligibility.components,
                evergreen_video_count=eligibility.evergreen_video_count,
                eligible_video_count=eligibility.eligible_video_count,
                velocity_basis=eligibility.velocity_basis,
                reasons=eligibility.reasons,
                computed_at=eligibility.computed_at,
                last_keyword_eval_at=last_keyword_eval_at,
            )
        )

    async def last_keyword_eval_at(self) -> datetime | None:
        statement = self._scoped(
            select(RefreshEligibilityRow.last_keyword_eval_at).where(
                RefreshEligibilityRow.last_keyword_eval_at.is_not(None)
            )
        ).order_by(RefreshEligibilityRow.computed_at.desc())
        return (await self.session.execute(statement.limit(1))).scalar_one_or_none()


class RefreshImpactRepository(TenantRepository[RefreshImpactRow]):
    model = RefreshImpactRow

    async def upsert(self, impact: RefreshImpact) -> None:
        if self.scope.client_id is None:
            raise ValueError("refresh impact needs a client-bound scope")
        values = {
            **impact.model_dump(exclude={"refresh_id"}),
            "refresh_id": uuid.UUID(impact.refresh_id),
            "workspace_id": self.scope.workspace_id,
            "client_id": self.scope.client_id,
        }
        statement = insert(RefreshImpactRow).values(**values)
        updatable = {
            key: statement.excluded[key] for key in values if key not in ("refresh_id", "workspace_id")
        }
        await self.session.execute(
            statement.on_conflict_do_update(index_elements=["refresh_id"], set_=updatable)
        )

    async def past_impacts(self) -> list[PastImpact]:
        statement = self._scoped(select(RefreshImpactRow, DescriptionRefreshRow.triggers)).join(
            DescriptionRefreshRow, DescriptionRefreshRow.id == RefreshImpactRow.refresh_id
        )
        impacts = []
        for row, triggers in (await self.session.execute(statement)).all():
            kinds = [trigger["kind"] for trigger in triggers if trigger["kind"] in KEYWORD_TRIGGER_KINDS]
            kind = kinds[0] if kinds else (triggers[0]["kind"] if triggers else "unknown")
            impact = RefreshImpact(
                refresh_id=str(row.refresh_id),
                applied_at=row.applied_at,
                applied_source=row.applied_source,
                pre_window_days=row.pre_window_days,
                post_window_days=row.post_window_days,
                pre_velocity=row.pre_velocity,
                post_velocity=row.post_velocity,
                ratio=row.ratio,
                control_ratio=row.control_ratio,
                adjusted_ratio=row.adjusted_ratio,
                owner_search_views_pre=row.owner_search_views_pre,
                owner_search_views_post=row.owner_search_views_post,
                verdict=row.verdict,
                caveats=row.caveats,
                computed_at=row.computed_at,
            )
            impacts.append(PastImpact(trigger_kind=kind, impact=impact))
        return impacts


class LinkCheckCacheRepository(SharedRepository):
    """web data shared across clients (not youtube data), 7-day ttl via next_check_at and retention."""

    async def get_many(self, keys: Sequence[str]) -> dict[str, CachedLink]:
        if not keys:
            return {}
        statement = select(LinkCheckCache).where(LinkCheckCache.url_normalised.in_(list(keys)))
        rows = (await self.session.execute(statement)).scalars().all()
        return {
            row.url_normalised: CachedLink(
                status=LinkStatus(
                    url_normalised=row.url_normalised,
                    status_class=LinkStatusClass(row.status_class),
                    checked_at=row.checked_at,
                    http_status=row.http_status,
                    final_url=row.final_url,
                    error=row.error,
                ),
                next_check_at=row.next_check_at,
            )
            for row in rows
        }

    async def put_many(self, entries: Sequence[CachedLink]) -> None:
        if not entries:
            return
        rows = {
            entry.status.url_normalised: {
                "url_normalised": entry.status.url_normalised,
                "status_class": entry.status.status_class.value,
                "http_status": entry.status.http_status,
                "final_url": entry.status.final_url,
                "error": entry.status.error,
                "checked_at": entry.status.checked_at,
                "next_check_at": entry.next_check_at,
            }
            for entry in entries
        }
        statement = insert(LinkCheckCache).values(list(rows.values()))
        columns = ("status_class", "http_status", "final_url", "error", "checked_at", "next_check_at")
        await self.session.execute(
            statement.on_conflict_do_update(
                index_elements=["url_normalised"], set_={name: statement.excluded[name] for name in columns}
            )
        )


class RefreshStore(Protocol):
    async def load_history(
        self, since: datetime
    ) -> tuple[list[StoredRefresh], list[PastImpact], datetime | None]: ...

    async def save_run(
        self,
        refreshes: Sequence[DescriptionRefresh],
        eligibility: RefreshEligibility,
        changes: Sequence[StatusChange],
        impacts: Sequence[RefreshImpact],
        last_keyword_eval_at: datetime | None,
    ) -> None: ...


class SqlRefreshStore:
    def __init__(self, session_factory: SessionFactory, scope: TenantScope) -> None:
        if scope.client_id is None:
            raise ValueError("the refresh store needs a client-bound scope")
        self.session_factory = session_factory
        self.scope = scope

    async def load_history(
        self, since: datetime
    ) -> tuple[list[StoredRefresh], list[PastImpact], datetime | None]:
        async with UnitOfWork(self.session_factory, read_only=True) as uow:
            history = await uow.repository(DescriptionRefreshRepository, self.scope).history(since)
            impacts = await uow.repository(RefreshImpactRepository, self.scope).past_impacts()
            last_eval = await uow.repository(RefreshEligibilityRepository, self.scope).last_keyword_eval_at()
        return history, impacts, last_eval

    async def save_run(
        self,
        refreshes: Sequence[DescriptionRefresh],
        eligibility: RefreshEligibility,
        changes: Sequence[StatusChange],
        impacts: Sequence[RefreshImpact],
        last_keyword_eval_at: datetime | None,
    ) -> None:
        async with UnitOfWork(self.session_factory) as uow:
            refresh_repository = uow.repository(DescriptionRefreshRepository, self.scope)
            for change in changes:
                await refresh_repository.apply_change(change)
            for refresh in refreshes:
                await refresh_repository.save(refresh)
            uow.repository(RefreshEligibilityRepository, self.scope).save(eligibility, last_keyword_eval_at)
            impact_repository = uow.repository(RefreshImpactRepository, self.scope)
            for impact in impacts:
                await impact_repository.upsert(impact)
            await uow.commit()


class SqlLinkStatusCache:
    """link cache adapter that opens a short transaction per call, so probes never hold a session open."""

    def __init__(self, session_factory: SessionFactory) -> None:
        self.session_factory = session_factory

    async def get_many(self, keys: Sequence[str]) -> dict[str, CachedLink]:
        async with UnitOfWork(self.session_factory, read_only=True) as uow:
            return await uow.repository(LinkCheckCacheRepository).get_many(keys)

    async def put_many(self, entries: Sequence[CachedLink]) -> None:
        async with UnitOfWork(self.session_factory) as uow:
            await uow.repository(LinkCheckCacheRepository).put_many(entries)
            await uow.commit()
