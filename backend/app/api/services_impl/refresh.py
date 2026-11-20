import difflib
from datetime import UTC, datetime, time
from uuid import UUID

from sqlalchemy import select

from app.api import ids
from app.api.errors import AppError, FieldError, NotFound, ValidationFailed
from app.api.pagination import Page, PageRequest
from app.api.schemas.results import (
    HygieneData,
    RefreshAppliedIn,
    RefreshSuggestionView,
    RefreshVideo,
)
from app.api.services import REFRESH, api_service_registry
from app.api.services_impl.common import ServiceBase, video_url
from app.core.container import Container
from app.core.tenancy import TenantScope
from app.domain.confidence import ConfidenceBasis
from app.domain.refresh import DescriptionRefresh, RefreshStatus
from app.models.refresh import DescriptionRefresh as RefreshRow
from app.models.youtube_cache import YtVideo
from app.pipeline.stages.refresh import REFRESH_ARTIFACT
from app.services.refresh.lifecycle import detect_applied
from app.services.refresh.ops.apply import AnchorNotFound, apply_ops
from app.services.refresh.store import DescriptionRefreshRepository, stored_refresh

VIEW_STATUS = {
    RefreshStatus.proposed: "open",
    RefreshStatus.applied_claimed: "applied",
    RefreshStatus.applied_detected: "applied",
    RefreshStatus.dismissed: "dismissed",
}
STATUS_FILTER = {
    "open": (RefreshStatus.proposed,),
    "applied": (RefreshStatus.applied_claimed, RefreshStatus.applied_detected),
    "dismissed": (RefreshStatus.dismissed,),
}
MAX_DIFF_CHARS = 20_000


class RefreshNotOpen(AppError):
    status = 409
    code = "refresh_not_open"
    title = "This suggestion was already applied, dismissed or withdrawn"


def basis_of(refresh: DescriptionRefresh) -> str:
    if refresh.confidence.basis is ConfidenceBasis.rule_based:
        return "rule_based"
    return "news" if any(trigger.kind == "news_entity" for trigger in refresh.triggers) else "keyword"


def preview(description: str | None, current_hash: str | None, refresh: DescriptionRefresh) -> str | None:
    """unified diff of the additive change, or None when the description moved on since generation."""
    if description is None or current_hash != refresh.description_hash_at_generation:
        return None
    try:
        after = apply_ops(description, refresh.operations)
    except AnchorNotFound:
        return None
    lines = difflib.unified_diff(
        description.splitlines(), after.splitlines(), "published", "suggested", lineterm=""
    )
    return "\n".join(lines)[:MAX_DIFF_CHARS]


class RefreshReadService(ServiceBase):
    """09's stored suggestions; rule-based fixes form the separate channel hygiene list (owner o3)."""

    async def _views(self, rows: list[RefreshRow]) -> list[RefreshSuggestionView]:
        video_ids = sorted({row.video_id for row in rows})
        videos: dict[str, YtVideo] = {}
        if video_ids:
            async with self.session_factory() as session:
                result = await session.execute(select(YtVideo).where(YtVideo.video_id.in_(video_ids)))
                videos = {video.video_id: video for video in result.scalars()}
        views = []
        for row in rows:
            refresh = stored_refresh(row).refresh
            video = videos.get(row.video_id)
            diff = preview(
                video.description if video else None, video.description_hash if video else None, refresh
            )
            views.append(
                RefreshSuggestionView(
                    id=ids.encode(ids.REFRESH, row.id),
                    video=RefreshVideo(
                        video_id=row.video_id,
                        title=video.title if video else None,
                        published_at=video.published_at if video else None,
                        url=video_url(row.video_id),
                    ),
                    basis=basis_of(refresh),  # type: ignore[arg-type]
                    warranted_because=[
                        trigger.kind.replace("_", " ") + (f": {trigger.keyword}" if trigger.keyword else "")
                        for trigger in refresh.triggers
                    ],
                    operations=refresh.operations,
                    advisories=refresh.advisories,
                    inserted_bytes=refresh.inserted_bytes,
                    resulting_bytes=refresh.resulting_bytes,
                    expected_effect=refresh.expected_effect,
                    review_required=refresh.review_required,
                    original_description_hash=refresh.description_hash_at_generation,
                    preview_diff=diff or "",
                    description_changed=diff is None,
                    status=VIEW_STATUS[refresh.status],  # type: ignore[arg-type]
                    applied_at=datetime.combine(row.applied_at, time(), UTC) if row.applied_at else None,
                    expires_at=refresh.expires_at,
                )
            )
        return views

    async def _rows(
        self, scope: TenantScope, statuses: tuple[RefreshStatus, ...], video_id: str | None
    ) -> list[RefreshRow]:
        statement = select(RefreshRow).where(
            RefreshRow.workspace_id == scope.workspace_id,
            RefreshRow.client_id == scope.client_id,
            RefreshRow.redacted_at.is_(None),
            RefreshRow.status.in_([status.value for status in statuses]),
        )
        if video_id is not None:
            statement = statement.where(RefreshRow.video_id == video_id)
        statement = statement.order_by(
            RefreshRow.priority.desc(), RefreshRow.generated_at.desc(), RefreshRow.id
        )
        async with self.session_factory() as session:
            return list((await session.execute(statement)).scalars())

    async def _listed(
        self, scope: TenantScope, status: str | None, video_id: str | None
    ) -> list[RefreshSuggestionView]:
        statuses = STATUS_FILTER[status] if status else tuple(VIEW_STATUS)
        return await self._views(await self._rows(scope, statuses, video_id))

    async def list_suggestions(
        self, scope: TenantScope, run_id: UUID, status: str | None, video_id: str | None, page: PageRequest
    ) -> Page[RefreshSuggestionView]:
        views = [view for view in await self._listed(scope, status, video_id) if view.basis != "rule_based"]
        return self.paginate(
            views, page, {"client": str(scope.client_id), "status": status, "video": video_id}
        )

    async def hygiene(self, scope: TenantScope, run_id: UUID, page: PageRequest) -> HygieneData:
        output = await self.optional_artifact(scope, run_id, REFRESH_ARTIFACT)
        views = [view for view in await self._listed(scope, None, None) if view.basis == "rule_based"]
        chunk = self.paginate(views, page, {"client": str(scope.client_id), "surface": "hygiene"})
        return HygieneData(
            eligibility=output.eligibility if output else None,
            headline=output.headline if output else None,
            items=chunk.items,
            next_cursor=chunk.next_cursor,
            limit=chunk.limit,
        )

    async def _transition(self, scope: TenantScope, suggestion_id: str, change) -> RefreshSuggestionView:
        refresh_id = ids.decode(ids.REFRESH, suggestion_id)
        async with self.session_factory() as session, session.begin():
            repository = DescriptionRefreshRepository(session, scope)
            row = await repository.get(refresh_id)
            if row is None:
                raise NotFound("refresh suggestion not found")
            if not await change(repository, row):
                raise RefreshNotOpen(f"the suggestion is {row.status}")
            client_scope = scope.for_client(row.client_id)
        rows = await self._rows(client_scope, tuple(RefreshStatus), None)
        return (await self._views([row for row in rows if row.id == refresh_id]))[0]

    async def applied(
        self, scope: TenantScope, suggestion_id: str, body: RefreshAppliedIn
    ) -> RefreshSuggestionView:
        when = (body.applied_at or self.clock.now()).date()
        overlap = self.settings.refresh.applied_token_overlap

        async def change(repository: DescriptionRefreshRepository, row: RefreshRow) -> bool:
            if body.applied_text is not None and not detect_applied(
                stored_refresh(row).refresh, body.applied_text, overlap
            ):
                raise ValidationFailed(
                    "the description you pasted does not contain the suggested lines",
                    errors=[
                        FieldError(
                            loc=["body", "applied_text"], msg="suggested lines not found", type="invalid"
                        )
                    ],
                )
            return await repository.mark_applied(str(row.id), when)

        return await self._transition(scope, suggestion_id, change)

    async def dismiss(self, scope: TenantScope, suggestion_id: str) -> RefreshSuggestionView:
        async def change(repository: DescriptionRefreshRepository, row: RefreshRow) -> bool:
            return await repository.dismiss(str(row.id), "dismissed by user")

        return await self._transition(scope, suggestion_id, change)


@api_service_registry.register(REFRESH)
def build_refresh_service(container: Container) -> RefreshReadService:
    return RefreshReadService(container)
