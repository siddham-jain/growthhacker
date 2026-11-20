import uuid
from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.api import ids
from app.api.errors import (
    AppError,
    ChannelUnresolvable,
    CompetitorsOutOfRange,
    ConfigInvalidError,
    FieldError,
    NotFound,
    ValidationFailed,
)
from app.api.pagination import Page, PageRequest
from app.api.schemas.clients import ChannelRef
from app.api.schemas.setup import (
    BusinessFactsView,
    ChannelRelevanceView,
    CompetitorAddIn,
    CompetitorChannelView,
    CompetitorEvidence,
    CompetitorRejectIn,
    CompetitorRolePatch,
    CompetitorView,
)
from app.api.services import COMPETITORS, api_service_registry
from app.api.services_impl.common import ServiceBase, channel_refs
from app.api.services_impl.profile import ProfileEdits, ProfileMissing
from app.core.container import Container
from app.core.tenancy import TenantScope
from app.domain.competitors import (
    BusinessRelevance,
    CandidateRole,
    CandidateStatus,
    CompetitorCandidate,
    YouTubeRelevance,
)
from app.domain.youtube import OwnershipLabel
from app.models.competitors import CompetitorCandidate as CandidateRow
from app.models.tenancy import Client
from app.pipeline.stages.competitors import discovery_runner
from app.services.competitors.edits import (
    CONFIRMED_STATUSES,
    OWN_GROUP_RELATIONS,
    CandidateNotFound,
    CompetitorEditService,
)
from app.services.competitors.model import UserCompetitorInput
from app.services.competitors.repository import ROLE_TO_DB, CompetitorRepository
from app.services.competitors.runner import DiscoveryRequest
from app.services.competitors.select.validation import SetValidationError

ROLE_ORDER = {role: index for index, role in enumerate(CandidateRole)}
STATUS_ORDER = {status: index for index, status in enumerate(CandidateStatus)}
# a selection the user is still building: the decision is saved, the frozen set waits until it is valid
IN_PROGRESS_CODES = frozenset({"competitors_out_of_range", "too_few_active"})


def set_error(error: SetValidationError) -> AppError:
    if error.code == "competitors_out_of_range":
        return CompetitorsOutOfRange(error.message, extra=error.detail or None)
    return ValidationFailed(
        error.message,
        errors=[FieldError(loc=["body", "competitor"], msg=error.message, type=error.code)],
        extra={"code": error.code, **error.detail},
    )


def relevance_view(relevance: YouTubeRelevance) -> ChannelRelevanceView:
    matching = getattr(relevance, "matching_uploads", None)
    checked = getattr(relevance, "uploads_checked", None)
    label = f"matching uploads: {matching} of last {checked}" if matching is not None and checked else None
    return ChannelRelevanceView(
        matching_uploads=matching,
        uploads_checked=checked,
        matching_uploads_label=label,
        matched_topic_refs=relevance.matched_topic_refs,
        size_band=relevance.size_band,
        subscriber_band=relevance.subscriber_band,
    )


def business_view(business: BusinessRelevance) -> BusinessFactsView:
    return BusinessFactsView(
        tier=business.tier,
        matched_offerings=business.matched_offerings,
        matched_offerings_count=len(business.matched_offerings),
        geo_basis=business.geo_basis,
    )


def candidate_view(
    candidate: CompetitorCandidate, refs: dict[str, ChannelRef], tracked: set[str]
) -> CompetitorView:
    channels = [
        CompetitorChannelView(
            channel=refs[match.channel_id].model_copy(
                update={
                    "title": match.title,
                    "handle": match.handle,
                    "subscriber_count": match.youtube.subscriber_count,
                    "verified": match.ownership.label is OwnershipLabel.verified,
                }
            ),
            is_primary=match.is_primary,
            tracked=match.channel_id in tracked,
            ownership=match.ownership,
            evidence=match.youtube,
            relevance=relevance_view(match.youtube_relevance),
            preview_video_ids=list(match.youtube.recent_upload_ids[:3]),
            flags=list(match.flags),
        )
        for match in candidate.channels
    ]
    primary = next((channel for channel in channels if channel.is_primary), None)
    evidence = [
        CompetitorEvidence(
            kind=source.kind,
            url=source.ref if source.ref.startswith("http") else None,
            text=source.snippet or source.ref,
        )
        for source in candidate.sources
    ]
    if primary is not None:
        evidence += [
            CompetitorEvidence(kind=f"ownership:{signal.name}", url=signal.source_url, text=signal.detail)
            for signal in primary.ownership.signals
        ]
    kinds = list(dict.fromkeys(source.kind for source in candidate.sources))
    return CompetitorView(
        id=ids.encode(ids.COMPETITOR, candidate.id),
        status=candidate.status,
        role=candidate.role,
        relation=candidate.relation,
        name=candidate.brand_name,
        official_domain=candidate.official_domain,
        confidence=candidate.confidence,
        channel=primary.channel if primary else None,
        channels=channels,
        sources=kinds,
        evidence=evidence,
        evidence_summary=candidate.evidence_summary,
        business=business_view(candidate.business),
        flags=candidate.flags,
        reject_reason=candidate.rejection_reason,
        added_by="user" if "user" in kinds or candidate.status is CandidateStatus.user_added else "system",
        decided_by=candidate.decided_by,
    )


class CompetitorReadService(ServiceBase):
    """04's candidates with every decision stamped by the user; the frozen set follows each edit."""

    def __init__(self, container: Container) -> None:
        super().__init__(container)
        self.edits = ProfileEdits(container)

    async def list_competitors(
        self, scope: TenantScope, status: str | None, page: PageRequest
    ) -> Page[CompetitorView]:
        views = await self._views(scope)
        if status is not None:
            views = [view for view in views if view.status == status]
        return self.paginate(views, page, {"status": status, "client": str(scope.client_id)})

    async def add(self, scope: TenantScope, body: CompetitorAddIn) -> CompetitorView:
        client = await self.client(scope)
        try:
            async with self.session_factory() as session:
                profile = (await self.edits.load(session, scope)).effective
        except ProfileMissing:
            raise ConfigInvalidError(
                "finish onboarding first: competitors are matched against the business profile"
            ) from None
        result = await discovery_runner(self.container, None).run(
            DiscoveryRequest(
                scope=scope,
                trigger="manual",
                profile=profile,
                own_channel_ids=self._own_channels(client),
                user_inputs=[
                    UserCompetitorInput(raw=body.channel_ref, intended_role=CandidateRole(body.role))
                ],
                enabled_sources=frozenset({"user"}),
            )
        )
        added = next(
            (
                candidate
                for candidate in result.candidates
                if "user" in {source.kind for source in candidate.sources} and candidate.channels
            ),
            None,
        )
        if added is None:
            raise ChannelUnresolvable("no YouTube channel matched; paste the channel URL or its UC id")
        primary = next((channel for channel in added.channels if channel.is_primary), added.channels[0])
        role = CandidateRole(body.role)

        async def decide(service: CompetitorEditService, session: AsyncSession) -> uuid.UUID:
            row = await self._row_for_channel(service.repository, primary.channel_id)
            if row.relation in OWN_GROUP_RELATIONS:
                raise SetValidationError("own_channel", "That looks like your own channel.")
            if role is CandidateRole.competitor_set:
                await self._ensure_room(service, row.id)
            await service.set_role(row.id, role, self.clock.now())
            row.status, row.rejection_reason = CandidateStatus.user_added.value, None
            return row.id

        return await self._edit(scope, client, decide)

    async def confirm(self, scope: TenantScope, competitor_id: str) -> CompetitorView:
        candidate_id = ids.decode(ids.COMPETITOR, competitor_id)

        async def decide(service: CompetitorEditService, session: AsyncSession) -> uuid.UUID:
            await service.confirm(candidate_id, self.clock.now())
            return candidate_id

        return await self._edit(scope, await self.client(scope), decide)

    async def reject(
        self, scope: TenantScope, competitor_id: str, body: CompetitorRejectIn
    ) -> CompetitorView:
        candidate_id = ids.decode(ids.COMPETITOR, competitor_id)
        reason = ": ".join(part for part in (body.reason, body.note) if part) or None

        async def decide(service: CompetitorEditService, session: AsyncSession) -> uuid.UUID:
            await service.reject(candidate_id, reason, self.clock.now())
            return candidate_id

        return await self._edit(scope, await self.client(scope), decide)

    async def set_role(
        self, scope: TenantScope, competitor_id: str, body: CompetitorRolePatch
    ) -> CompetitorView:
        candidate_id = ids.decode(ids.COMPETITOR, competitor_id)
        role = CandidateRole(body.role)

        async def decide(service: CompetitorEditService, session: AsyncSession) -> uuid.UUID:
            if role is CandidateRole.competitor_set:
                await self._ensure_room(service, candidate_id)
            await service.set_role(candidate_id, role, self.clock.now())
            return candidate_id

        return await self._edit(scope, await self.client(scope), decide)

    async def delete(self, scope: TenantScope, competitor_id: str) -> None:
        # removal excludes the candidate from every set; rediscovery keeps the user's decision
        candidate_id = ids.decode(ids.COMPETITOR, competitor_id)

        async def decide(service: CompetitorEditService, session: AsyncSession) -> uuid.UUID:
            await service.set_role(candidate_id, CandidateRole.excluded, self.clock.now())
            return candidate_id

        await self._edit(scope, await self.client(scope), decide)

    async def _edit(
        self,
        scope: TenantScope,
        client: Client,
        decide: Callable[[CompetitorEditService, AsyncSession], Awaitable[uuid.UUID]],
    ) -> CompetitorView:
        now = self.clock.now()
        async with self.session_factory() as session, session.begin():
            service = CompetitorEditService(CompetitorRepository(session, scope), self.settings.competitors)
            try:
                candidate_id = await decide(service, session)
                await session.flush()
                await self._freeze(service, client, now)
            except CandidateNotFound:
                raise NotFound("competitor not found") from None
            except SetValidationError as error:
                raise set_error(error) from None
        view = next(
            (
                view
                for view in await self._views(scope)
                if view.id == ids.encode(ids.COMPETITOR, candidate_id)
            ),
            None,
        )
        if view is None:
            raise NotFound("competitor not found")
        return view

    async def _freeze(self, service: CompetitorEditService, client: Client, now) -> None:
        # each competitor was decided by the user, which acknowledges uncertain ownership
        try:
            await service.freeze(
                now, own_channel_ids=frozenset(self._own_channels(client)), acknowledge_uncertain=True
            )
        except SetValidationError as error:
            if error.code not in IN_PROGRESS_CODES:
                raise

    async def _ensure_room(self, service: CompetitorEditService, candidate_id: uuid.UUID) -> None:
        limit = self.settings.competitors.max_competitors
        confirmed = [
            row
            for row in await service.repository.candidates.all_rows()
            if row.id != candidate_id
            and row.role == ROLE_TO_DB[CandidateRole.competitor_set]
            and CandidateStatus(row.status) in CONFIRMED_STATUSES
        ]
        if len(confirmed) >= limit:
            raise SetValidationError(
                "competitors_out_of_range",
                f"You already track {limit} competitors; move one to the niche set first.",
                {"selected": len(confirmed) + 1},
            )

    @staticmethod
    async def _row_for_channel(repository: CompetitorRepository, channel_id: str) -> CandidateRow:
        rows = await repository.candidates.all_rows()
        matches = await repository.matches.for_candidates([row.id for row in rows]) if rows else []
        owner = next((match.candidate_id for match in matches if match.channel_id == channel_id), None)
        row = next((row for row in rows if row.id == owner), None)
        if row is None:
            raise ChannelUnresolvable("the channel was found but could not be stored as a competitor")
        return row

    async def _views(self, scope: TenantScope) -> list[CompetitorView]:
        async with self.session_factory() as session:
            repository = CompetitorRepository(session, scope)
            candidates = await repository.load_candidates()
            matches = (
                await repository.matches.for_candidates([c.id for c in candidates]) if candidates else []
            )
        tracked = {match.channel_id for match in matches if match.is_tracked}
        refs = await channel_refs(
            self.session_factory, [match.channel_id for c in candidates for match in c.channels]
        )
        ordered = sorted(
            candidates,
            key=lambda c: (ROLE_ORDER[c.role], STATUS_ORDER[c.status], c.brand_name.casefold()),
        )
        return [candidate_view(candidate, refs, tracked) for candidate in ordered]

    @staticmethod
    def _own_channels(client: Client) -> list[str]:
        return [client.own_channel_id] if client.own_channel_id else []


@api_service_registry.register(COMPETITORS)
def build_competitor_service(container: Container) -> CompetitorReadService:
    return CompetitorReadService(container)
