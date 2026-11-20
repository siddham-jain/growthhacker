import pytest

from app.services.topics.repositories import TopicUniverseRepository
from app.services.topics.universe.builder import TopicUniverseBuilder
from tests.topics.golden import apollo
from tests.topics.test_engine_db import make_engine

pytestmark = pytest.mark.db


def statuses(universe) -> dict[tuple[str, str], str]:
    return {(ref.id, alias.norm_key): alias.status for ref in universe.refs for alias in ref.aliases}


def reviewable(universe, origin: str) -> list[tuple[str, str]]:
    return [
        (ref.id, alias.norm_key)
        for ref in universe.refs
        for alias in ref.aliases
        if alias.origin == origin and alias.status == "proposed" and alias.kind != "canonical"
    ]


async def test_a_user_confirmed_rule_alias_is_kept_as_a_decision(session, scope, uow_factory) -> None:
    engine = make_engine(uow_factory)
    profile = apollo.PROFILE.model_copy(update={"client_id": scope.client_id})
    first = await engine.build_universe(scope, profile, apollo.QUESTIONS)
    ref = first.universe.refs[0]
    variant = ref.aliases[0].model_copy(
        update={
            "text": "test variant",
            "norm_key": "test variant",
            "kind": "morph_variant",
            "origin": "rule",
            "status": "proposed",
            "weight": 0.6,
        }
    )
    universe = first.universe.model_copy(
        update={
            "refs": [ref.model_copy(update={"aliases": [*ref.aliases, variant]}), *first.universe.refs[1:]]
        }
    )
    async with uow_factory() as uow:
        await TopicUniverseRepository(uow.session, scope).save(universe, [], {})
        await uow.commit()
    await engine.decide_alias(scope, ref.id, "test variant", "confirmed")
    async with uow_factory() as uow:
        decisions = await TopicUniverseRepository(uow.session, scope).alias_decisions()
    assert decisions[(ref.id, "test variant")] == "confirmed"


async def test_a_decision_made_while_the_universe_rebuilds_is_kept(scope, uow_factory, monkeypatch) -> None:
    engine = make_engine(uow_factory)
    profile = apollo.PROFILE.model_copy(update={"client_id": scope.client_id})
    first = await engine.build_universe(scope, profile, apollo.QUESTIONS)
    ref_id, key = reviewable(first.universe, "llm_proposed")[0]
    build = TopicUniverseBuilder.build

    async def slow_build(self, inputs, **kwargs):
        built = await build(self, inputs, **kwargs)
        # the reviewer rejects the alias after the rebuild read its decisions
        await engine.decide_alias(scope, ref_id, key, "rejected")
        return built

    monkeypatch.setattr(TopicUniverseBuilder, "build", slow_build)
    rebuilt = await engine.build_universe(scope, profile, apollo.QUESTIONS)
    assert statuses(rebuilt.universe)[(ref_id, key)] == "rejected"
    loaded, _ = await engine.load_universe(scope)
    assert statuses(loaded)[(ref_id, key)] == "rejected"
    assert loaded.universe_hash == rebuilt.universe.universe_hash
