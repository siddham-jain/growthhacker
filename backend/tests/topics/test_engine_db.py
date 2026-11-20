from datetime import timedelta

import pytest
from sqlalchemy import func, select, update

from app.adapters.embeddings.router import EmbedderRouter
from app.core.clock import FrozenClock
from app.domain.common import DirectnessLevel
from app.models import topics as orm
from app.models.youtube_cache import YtVideo
from app.services.topics.engine import TopicEngine
from app.services.topics.review.queue import TopicMatchOverride
from tests.conftest import make_settings
from tests.topics.fixtures import CHANNEL, NOW, FakeLLM, HashEmbedder, judge_by_label
from tests.topics.golden import apollo

pytestmark = pytest.mark.db


def make_engine(uow_factory, llm=None) -> TopicEngine:
    embedder = HashEmbedder()
    router = EmbedderRouter({"hash": embedder}, {"Latn": ["hash"], "Deva": ["hash"]}, "hash")
    llm = llm or FakeLLM(judge=judge_by_label(apollo.ADJUDICATION_RULES), proposals=apollo.PROPOSALS)
    return TopicEngine(make_settings(), FrozenClock(NOW), uow_factory, router=router, llm=llm)


async def count(session, model, **filters) -> int:
    statement = select(func.count()).select_from(model)
    for column, value in filters.items():
        statement = statement.where(getattr(model, column) == value)
    return (await session.execute(statement)).scalar_one()


async def test_build_match_reuse_and_read_back(session, scope, uow_factory, video_ids) -> None:
    engine = make_engine(uow_factory)
    build = await engine.build_universe(
        scope, apollo.PROFILE.model_copy(update={"client_id": scope.client_id}), apollo.QUESTIONS
    )
    assert build.diff.added and not build.diff.removed
    report = await engine.match(scope, video_ids, own_channel_id=CHANNEL, window_days=30)
    assert report.videos_considered == 5 and report.videos_reused == 0
    # the hash test embedder is not multilingual, so the devanagari-only title is left to aliases the user confirms
    assert report.videos_matched == 3
    assert report.unmatched_by_reason == {"below_threshold": 2}
    assert report.llm_calls == 1
    assert await count(session, orm.VideoPreparedText) == 5
    assert await count(session, orm.ChannelBoilerplate, channel_id=CHANNEL) == 1
    assert await count(session, orm.TextEmbedding) > 0
    assert await count(session, orm.RefEmbedding, client_id=scope.client_id) == len(build.universe.refs)
    assert await count(session, orm.LlmAdjudication, client_id=scope.client_id) >= 1

    again = await engine.match(scope, video_ids)
    # a video without any candidate leaves no rows, so it is re-scored (cheaply, from cached vectors)
    assert again.videos_reused == 4 and again.llm_calls == 0

    assignments = {item.video_id: item for item in await engine.assignments(scope, video_ids)}
    assert assignments[video_ids[0]].primary_topic_id is not None
    assert assignments[video_ids[4]].unmatched_reason in {"below_threshold", "adjudicated_none"}
    rows = await engine.answer_directness(scope, video_ids)
    assert any(row.level is DirectnessLevel.direct for row in rows)
    matches = await engine.matches(scope, [video_ids[0]])
    stored = (
        (await session.execute(select(orm.TopicMatch).where(orm.TopicMatch.video_id == video_ids[0])))
        .scalars()
        .first()
    )
    assert stored.expires_at == NOW + timedelta(days=28)
    assert stored.trace["combine"]["C"] >= 0
    assert all(match.explanation for match in matches)


async def test_adjudications_are_cached_across_rematches(session, scope, uow_factory, video_ids) -> None:
    engine = make_engine(uow_factory)
    build = await engine.build_universe(
        scope, apollo.PROFILE.model_copy(update={"client_id": scope.client_id}), apollo.QUESTIONS
    )
    await engine.match(scope, video_ids)
    llm = FakeLLM(judge=judge_by_label(apollo.ADJUDICATION_RULES))
    rerun = make_engine(uow_factory, llm)
    universe = build.universe.model_copy(update={"universe_hash": "f" * 64})
    report = await rerun.match(scope, video_ids, universe=universe, non_topical_terms=["Apollo"])
    assert report.videos_reused == 0
    assert report.adjudication_cache_hits >= 1 and llm.calls == []


async def test_alias_confirmation_invalidates_matches(session, scope, uow_factory, video_ids) -> None:
    engine = make_engine(uow_factory)
    build = await engine.build_universe(
        scope, apollo.PROFILE.model_copy(update={"client_id": scope.client_id}), apollo.QUESTIONS
    )
    await engine.match(scope, video_ids)
    knee = next(ref for ref in build.universe.refs if ref.label == "Knee Replacement")
    alias = next(alias for alias in knee.aliases if alias.text == "ghutne ka operation")
    updated = await engine.decide_alias(scope, knee.id, alias.norm_key, "confirmed")
    assert updated.universe_hash != build.universe.universe_hash
    report = await engine.match(scope, video_ids)
    assert report.videos_reused == 0
    hinglish = [
        match for match in await engine.matches(scope, [video_ids[2]]) if match.topic_ref_id == knee.id
    ][0]
    assert hinglish.decision == "accepted" and hinglish.method.value == "lexical"
    rejected = await engine.decide_alias(scope, knee.id, alias.norm_key, "rejected")
    loaded, _ = await engine.load_universe(scope)
    assert {a.text: a.status for ref in loaded.refs for a in ref.aliases if ref.id == knee.id}[
        "ghutne ka operation"
    ] == "rejected"
    assert rejected.universe_hash == loaded.universe_hash
    with pytest.raises(LookupError):
        await engine.decide_alias(scope, knee.id, "not an alias", "confirmed")


async def test_removed_refs_are_soft_deleted(session, scope, uow_factory, video_ids) -> None:
    engine = make_engine(uow_factory)
    profile = apollo.PROFILE.model_copy(update={"client_id": scope.client_id})
    first = await engine.build_universe(scope, profile, apollo.QUESTIONS)
    await engine.match(scope, video_ids)
    trimmed = profile.model_copy(
        update={
            "version": 2,
            "offerings": [item for item in profile.offerings if item.name != "Cardiac Bypass Surgery"],
        }
    )
    second = await engine.build_universe(scope, trimmed, apollo.QUESTIONS)
    bypass = next(ref.id for ref in first.universe.refs if ref.label == "Cardiac Bypass Surgery")
    assert bypass in second.diff.removed
    deleted = await session.execute(
        select(func.count())
        .select_from(orm.TopicMatch)
        .where(orm.TopicMatch.topic_ref_id == bypass, orm.TopicMatch.deleted_at.is_not(None))
    )
    assert deleted.scalar_one() >= 1


async def test_overrides_win_until_the_video_changes(session, scope, uow_factory, video_ids) -> None:
    engine = make_engine(uow_factory)
    build = await engine.build_universe(
        scope, apollo.PROFILE.model_copy(update={"client_id": scope.client_id}), apollo.QUESTIONS
    )
    await engine.match(scope, video_ids)
    knee = next(ref for ref in build.universe.refs if ref.label == "Knee Replacement")
    ivf = next(ref for ref in build.universe.refs if ref.label == "IVF")
    with pytest.raises(ValueError):
        await engine.apply_override(
            scope,
            TopicMatchOverride(
                client_id=scope.client_id, video_id=video_ids[0], topic_ref_id=knee.id, verdict="reassign"
            ),
        )
    overridden = await engine.apply_override(
        scope,
        TopicMatchOverride(
            client_id=scope.client_id,
            video_id=video_ids[0],
            topic_ref_id=knee.id,
            verdict="reject",
            reason="wrong",
        ),
    )
    assert overridden.decision == "rejected" and overridden.method.value == "override"
    await engine.apply_override(
        scope,
        TopicMatchOverride(
            client_id=scope.client_id, video_id=video_ids[4], topic_ref_id=ivf.id, verdict="accept"
        ),
    )
    universe, terms = await engine.load_universe(scope)
    forced = universe.model_copy(update={"universe_hash": "e" * 64})
    await engine.match(scope, video_ids, universe=forced, non_topical_terms=terms)
    by_ref = {match.topic_ref_id: match for match in await engine.matches(scope, [video_ids[0]])}
    assert by_ref[knee.id].decision == "rejected" and by_ref[knee.id].method.value == "override"
    moon = {match.topic_ref_id: match for match in await engine.matches(scope, [video_ids[4]])}
    assert moon[ivf.id].decision == "accepted"
    await session.execute(
        update(YtVideo).where(YtVideo.video_id == video_ids[0]).values(content_hash="changed")
    )
    await session.commit()
    await engine.match(
        scope,
        video_ids,
        universe=forced.model_copy(update={"universe_hash": "d" * 64}),
        non_topical_terms=terms,
    )
    stale = (
        await session.execute(
            select(orm.TopicMatchOverride.stale).where(orm.TopicMatchOverride.video_id == video_ids[0])
        )
    ).scalar_one()
    assert stale is True
    by_ref = {match.topic_ref_id: match for match in await engine.matches(scope, [video_ids[0]])}
    assert by_ref[knee.id].method.value != "override"


async def test_review_queue_and_reference_sampling(session, scope, uow_factory, video_ids) -> None:
    engine = make_engine(uow_factory)
    await engine.build_universe(
        scope, apollo.PROFILE.model_copy(update={"client_id": scope.client_id}), apollo.QUESTIONS
    )
    await engine.match(scope, video_ids)
    queue = await engine.review_queue(scope)
    assert queue and all(item.reasons for item in queue)
    assert queue == sorted(queue, key=lambda item: -item.priority)
    before = await count(session, orm.TopicReviewItem, client_id=scope.client_id)
    touched = await engine.prioritise_review(scope, [video_ids[0]])
    assert touched >= 1
    sampled = (
        (
            await session.execute(
                select(orm.TopicReviewItem).where(
                    orm.TopicReviewItem.video_id == video_ids[0],
                    orm.TopicReviewItem.client_id == scope.client_id,
                )
            )
        )
        .scalars()
        .all()
    )
    assert all("reference_video_sample" in item.reasons for item in sampled)
    assert await count(session, orm.TopicReviewItem, client_id=scope.client_id) >= before


async def test_tiebreak_and_similar_refs(session, scope, uow_factory, video_ids) -> None:
    llm = FakeLLM(
        judge=judge_by_label(apollo.ADJUDICATION_RULES),
        proposals=apollo.PROPOSALS,
        tiebreak=lambda video: (video["allowed_levels"][1], video["title"][:12]),
    )
    engine = make_engine(uow_factory, llm)
    build = await engine.build_universe(
        scope, apollo.PROFILE.model_copy(update={"client_id": scope.client_id}), apollo.QUESTIONS
    )
    await engine.match(scope, video_ids)
    rows = await engine.answer_directness(scope, video_ids)
    row = rows[0]
    await session.execute(
        update(orm.AnswerDirectness)
        .where(
            orm.AnswerDirectness.video_id == row.video_id,
            orm.AnswerDirectness.bq_ref_id == row.buyer_question_ref_id,
        )
        .values(score=0.47)
    )
    await session.commit()
    resolved = await engine.request_tiebreak(scope, [(row.video_id, row.buyer_question_ref_id)])
    assert resolved[0].method == "deterministic+llm_tiebreak" and resolved[0].score == pytest.approx(0.46)
    knee = next(ref.id for ref in build.universe.refs if ref.label == "Knee Replacement")
    similar = await engine.similar_refs(scope, knee, k=3)
    assert len(similar) == 3 and knee not in {ref_id for ref_id, _ in similar}
