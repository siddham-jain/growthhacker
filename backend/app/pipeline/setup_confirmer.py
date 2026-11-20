from typing import Any

from sqlalchemy import update

from app.core.registry import adapter_registry
from app.core.tenancy import TenantScope
from app.domain.profile import BusinessProfile, ClaimStatus
from app.models.website import BusinessProfile as ProfileRow
from app.models.website import BuyerQuestion as QuestionRow
from app.models.website import Keyword as KeywordRow
from app.pipeline.inputs.common import ProfileMissing, latest_artifact, profile_and_questions
from app.pipeline.setup import SetupInvalid
from app.pipeline.stages.competitors import COMPETITOR_DISCOVERY_ARTIFACT
from app.services.competitors.edits import CONFIRMED_STATUSES, CompetitorEditService
from app.services.competitors.repository import CompetitorRepository
from app.services.competitors.select.validation import SetValidationError
from app.services.topics.engine import TopicEngine
from app.services.website_intel.persistence import WebsiteIntelStore

# third-party claims (web mentions, search snippets, archives) wait for a person even in demo mode
ACCEPTED_CLAIMS = {ClaimStatus.proposed.value}


def accept_claims(value: Any) -> Any:
    """every proposed claim in the profile tree becomes confirmed; claims that need confirmation, rejected
    and stale claims stay as they are."""
    if isinstance(value, list):
        return [accept_claims(item) for item in value]
    if not isinstance(value, dict):
        return value
    accepted = {key: accept_claims(item) for key, item in value.items()}
    meta = accepted.get("meta")
    if isinstance(meta, dict) and meta.get("status") in ACCEPTED_CLAIMS and "origin" in meta:
        accepted["meta"] = {**meta, "status": ClaimStatus.confirmed.value}
    return accepted


class TableSetupConfirmer:
    """turns the reviewed (or, in demo mode, all suggested) onboarding output into the confirmed setup."""

    def __init__(self, container: Any) -> None:
        self._container = container

    async def accept_suggestions(self, scope: TenantScope) -> None:
        # demo auto-confirm: profile claims, keywords, questions and the proposed competitor set
        profile, _ = await profile_and_questions(self._container, scope)
        if profile is None:
            raise ProfileMissing("the client has no business profile to confirm")
        discovery = await latest_artifact(self._container, scope, COMPETITOR_DISCOVERY_ARTIFACT)
        proposal = discovery.proposal.competitor_channel_ids if discovery is not None else []
        confirmed = BusinessProfile.model_validate(
            {**accept_claims(profile.model_dump(mode="json")), "status": "confirmed"}
        )
        now = self._container.clock.now()
        async with self._container.uow() as uow:
            await self._save_profile(uow.session, scope, confirmed)
            for model in (KeywordRow, QuestionRow):
                await uow.session.execute(
                    update(model)
                    .where(
                        model.workspace_id == scope.workspace_id,
                        model.client_id == scope.client_id,
                        model.status == "proposed",
                    )
                    .values(status="confirmed")
                )
            repository = CompetitorRepository(uow.session, scope)
            try:
                await CompetitorEditService(repository, self._container.settings.competitors).auto_confirm(
                    proposal, now
                )
            except SetValidationError as error:
                raise SetupInvalid([f"{error.code}: {error.message}"]) from error
            await uow.commit()
        await self._build_universe(scope)

    async def prepare(self, scope: TenantScope) -> None:
        # manual confirm: freeze the user's competitor decisions and rebuild topic refs from confirmed items
        now = self._container.clock.now()
        async with self._container.uow() as uow:
            repository = CompetitorRepository(uow.session, scope)
            try:
                await CompetitorEditService(repository, self._container.settings.competitors).freeze(now)
            except SetValidationError as error:
                raise SetupInvalid([f"{error.code}: {error.message}"]) from error
            await uow.session.execute(
                update(ProfileRow)
                .where(
                    ProfileRow.workspace_id == scope.workspace_id,
                    ProfileRow.client_id == scope.client_id,
                    ProfileRow.status == "awaiting_confirmation",
                )
                .values(status="confirmed")
            )
            await uow.commit()
        await self._build_universe(scope)

    @staticmethod
    async def _save_profile(session, scope: TenantScope, profile: BusinessProfile) -> None:
        await session.execute(
            update(ProfileRow)
            .where(
                ProfileRow.workspace_id == scope.workspace_id,
                ProfileRow.client_id == scope.client_id,
                ProfileRow.id == profile.profile_id,
            )
            .values(profile=profile.model_dump(mode="json"), status="confirmed")
        )

    async def _build_universe(self, scope: TenantScope) -> None:
        async with self._container.uow(read_only=True) as uow:
            store = WebsiteIntelStore(uow.session, scope)
            profile = await store.latest_profile()
            questions = [q for q in await store.buyer_questions() if q.status.value == "confirmed"]
            candidates = await CompetitorRepository(uow.session, scope).load_candidates()
        if profile is None:
            raise ProfileMissing("the client has no business profile to confirm")
        names = [candidate.brand_name for candidate in candidates if candidate.status in CONFIRMED_STATUSES]
        engine = TopicEngine.from_container(self._container)
        await engine.build_universe(scope, profile, questions, competitor_names=names)


@adapter_registry.register("setup_confirmer", "tables")
def build_table_setup_confirmer(container: Any) -> TableSetupConfirmer:
    return TableSetupConfirmer(container)
