from collections.abc import Mapping
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from app.core.registry import artifact_schema_registry, stage_registry
from app.domain.common import DomainModel
from app.domain.profile import BusinessProfile, ClaimStatus
from app.models.website import BuyerQuestion as QuestionRow
from app.models.website import Keyword as KeywordRow
from app.pipeline.contracts import RunConfigSnapshot, RunContext, StagePolicy, StageResult
from app.pipeline.stages.website_intel import BUSINESS_PROFILE
from app.ports.artifacts import ArtifactKey
from app.services.website_intel.profile.claims import normalise_name

KEYWORD_STATUS = {ClaimStatus.confirmed: "confirmed", ClaimStatus.edited: "confirmed"}
SEED_PRIORITY = {"seed": 1, "expanded": 2}


class SeededTopics(DomainModel):
    keywords_proposed: int
    keywords_total: int
    buyer_questions_proposed: int


SEEDED_TOPICS: ArtifactKey[SeededTopics] = ArtifactKey(
    name="seed_topic_refs", model=SeededTopics, schema_version=1, contains_api_data=False
)
artifact_schema_registry.register(SEEDED_TOPICS.name, SEEDED_TOPICS.schema_version, SeededTopics)


def keyword_rows(profile: BusinessProfile, workspace_id: Any) -> list[dict[str, Any]]:
    rows: dict[tuple[str, str], dict[str, Any]] = {}
    for keyword in profile.niche_keywords:
        if keyword.meta.status in {ClaimStatus.rejected, ClaimStatus.stale}:
            continue
        normalized = normalise_name(keyword.term)
        if not normalized or (normalized, keyword.language) in rows:
            continue
        rows[(normalized, keyword.language)] = {
            "workspace_id": workspace_id,
            "client_id": profile.client_id,
            "text": keyword.term,
            "normalized_text": normalized,
            "language": keyword.language,
            "script": keyword.script,
            "kind": keyword.kind,
            "tier": keyword.tier,
            "source": keyword.source,
            "status": KEYWORD_STATUS.get(keyword.meta.status, "proposed"),
            "priority": SEED_PRIORITY[keyword.tier],
            "weight": keyword.weight,
            "offering_ids": keyword.offering_ids,
            "meta": {"keyword_id": keyword.id, "claim": keyword.meta.model_dump(mode="json")},
            "introduced_in_profile_id": profile.profile_id,
        }
    return list(rows.values())


@stage_registry.register("seed_topic_refs")
class SeedTopicRefsStage:
    """stores the profile's keywords as suggestions next to 03's buyer questions; the universe is built on confirm."""

    name = "seed_topic_refs"
    version = 1
    depends_on: tuple[str, ...] = ("build_profile",)
    optional_deps: tuple[str, ...] = ()
    produces = SEEDED_TOPICS
    policy = StagePolicy(timeout_s=60, max_attempts=2)
    weight = 5.0

    def enabled(self, snapshot: RunConfigSnapshot) -> bool:
        return True

    def fingerprint_inputs(self, ctx: RunContext[Any]) -> Mapping[str, Any]:
        return {"client_id": str(ctx.client_id)}

    async def run(self, ctx: RunContext[Any]) -> StageResult[SeededTopics]:
        profile = await ctx.artifacts.get(BUSINESS_PROFILE)
        rows = keyword_rows(profile, ctx.workspace_id)
        async with ctx.deps.session_factory() as session, session.begin():
            if rows:
                statement = insert(KeywordRow).values(rows)
                # user decisions survive re-crawls: only system fields are refreshed on conflict
                await session.execute(
                    statement.on_conflict_do_update(
                        index_elements=["client_id", "normalized_text", "language"],
                        set_={
                            "kind": statement.excluded.kind,
                            "tier": statement.excluded.tier,
                            "weight": statement.excluded.weight,
                            "offering_ids": statement.excluded.offering_ids,
                            "meta": statement.excluded.meta,
                            "updated_at": func.now(),
                        },
                    )
                )
            proposed = await _count(session, KeywordRow, ctx, "proposed")
            questions = await _count(session, QuestionRow, ctx, "proposed")
        seeded = SeededTopics(
            keywords_proposed=proposed, keywords_total=len(rows), buyer_questions_proposed=questions
        )
        await ctx.reporter.step(
            "topics.seeded", f"Suggested {proposed} keywords and {questions} buyer questions for your review"
        )
        return StageResult(status="succeeded", artifact=seeded, counts=seeded.model_dump())


async def _count(session, model, ctx: RunContext[Any], status: str) -> int:
    statement = select(func.count()).where(
        model.workspace_id == ctx.workspace_id, model.client_id == ctx.client_id, model.status == status
    )
    return int((await session.execute(statement)).scalar_one())
