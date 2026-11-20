from datetime import timedelta

import pytest

from app.domain.common import FormatSegment
from app.domain.recommendations import Lens, MatchSpec, RecType
from app.evaluation.backtest.arms import ArmContext, CopyTopArm, ObservedValues, RandomArm, generate_arms
from app.evaluation.backtest.matcher import UnmatchableSpecType, matches
from app.evaluation.backtest.models import ArmSpec, CorpusVideo
from app.evaluation.backtest.specs import spec_signature
from tests.evaluation.backtest_fixtures import T, spec


def video(vid: str, **overrides) -> CorpusVideo:
    values = {
        "video_id": vid,
        "channel_id": "UCaaaaaaaaaaaaaaaaaaaaaa",
        "published_at": T - timedelta(days=3),
        "segment": "long_form",
        "traits": {
            "title.is_question": "true",
            "title.char_len_band": "40-60",
            "thumbnail.text_present": "false",
            "segment": "long_form",
            "length_band": "4-8m",
        },
        "topic_ids": frozenset({"tr_a"}),
        "topic_scores": {"tr_a": 0.9},
        "views_at_t": 1000,
    }
    return CorpusVideo(**(values | overrides))


def test_every_constraint_must_hold_and_topics_are_any_of() -> None:
    target = video("v1")
    assert matches(spec("s", topics=["tr_b", "tr_a"], question=True, segment=FormatSegment.long_form), target)
    assert not matches(spec("s", topics=["tr_b"]), target)
    assert not matches(spec("s", question=False), target)
    assert not matches(spec("s", segment=FormatSegment.short_form), target)
    assert not matches(spec("s", channel_id="UCbbbbbbbbbbbbbbbbbbbbbb", rec_type=RecType.creator), target)
    banded = spec("s").model_copy(
        update={"length_band": "8-15m", "thumbnail_traits": {"text_present": False}}
    )
    assert not matches(banded, target)
    assert matches(banded.model_copy(update={"length_band": "4-8m"}), target)
    assert matches(spec("empty"), target)


def test_unregistered_spec_types_are_reported_not_guessed() -> None:
    with pytest.raises(UnmatchableSpecType):
        matches(spec("n", rec_type=RecType.news_opportunity), video("v1"))


def window() -> list[CorpusVideo]:
    return [
        video("own", views_at_t=99_999, is_own=True),
        video(
            "top",
            views_at_t=5_000,
            topic_ids=frozenset({"tr_b", "tr_c"}),
            topic_scores={"tr_b": 0.4, "tr_c": 0.8},
        ),
        video(
            "tie-late",
            views_at_t=3_000,
            published_at=T - timedelta(days=1),
            traits={"title.is_question": "false", "segment": "short_form", "length_band": "<1m"},
            segment="short_form",
        ),
        video("tie-early", views_at_t=3_000, published_at=T - timedelta(days=9)),
        video("no-views", views_at_t=None),
    ]


def system() -> list[ArmSpec]:
    return [
        ArmSpec(
            arm="system", spec=spec("a", topics=["tr_a"], question=True, segment=FormatSegment.long_form)
        ),
        ArmSpec(arm="system", spec=spec("b", topics=["tr_a"])),
        ArmSpec(
            arm="system",
            spec=spec(
                "c", channel_id="UCaaaaaaaaaaaaaaaaaaaaaa", rec_type=RecType.creator, lens=[Lens.conversion]
            ),
        ),
    ]


def context(replicates: int = 50, seed: int = 3) -> ArmContext:
    return ArmContext(
        system=system(),
        window=window(),
        topic_ref_ids=["tr_a", "tr_b", "tr_c", "tr_d"],
        universe=["UCaaaaaaaaaaaaaaaaaaaaaa", "UCbbbbbbbbbbbbbbbbbbbbbb"],
        replicates=replicates,
        seed=seed,
    )


def test_copy_top_ranks_raw_views_excludes_own_and_breaks_ties_by_age() -> None:
    specs = CopyTopArm().generate(context())
    by_id = {s.spec.spec_id: s.spec for s in specs}
    first = by_id["copy_top:video_idea:0"]
    assert first.source_ref == "top"
    # the same template as the system spec: best topic, its format, its question flag
    assert first.topic_ids == ["tr_c"] and first.format is FormatSegment.long_form
    assert first.title_traits == {"is_question": "true"}
    assert by_id["copy_top:video_idea:1"].source_ref == "tie-early"
    assert by_id["copy_top:creator:0"].channel_id == "UCaaaaaaaaaaaaaaaaaaaaaa"
    assert all(s.spec.source == "copy_top" for s in specs)
    assert len([s for s in specs if s.spec.rec_type is RecType.video_idea]) == 2


def test_random_specs_keep_type_and_specificity_and_are_feasible() -> None:
    specs = RandomArm().generate(context(replicates=40))
    assert len(specs) == 40 * 3
    observed = ObservedValues.of(window())
    templates = {i: s.spec for i, s in enumerate(system())}
    for item in specs:
        index = int(item.spec.spec_id.split(":")[2])
        template = templates[index]
        assert item.spec.rec_type is template.rec_type
        assert item.spec.specificity == template.specificity
        assert set(item.spec.topic_ids) <= {"tr_a", "tr_b", "tr_c", "tr_d"}
        if item.spec.format is not None:
            assert item.spec.format.value in observed.segments
        assert item.spec.lens == template.lens


def test_random_arm_is_seeded_and_drops_unobservable_constraints() -> None:
    first = [s.spec for s in RandomArm().generate(context(seed=11))]
    again = [s.spec for s in RandomArm().generate(context(seed=11))]
    other = [s.spec for s in RandomArm().generate(context(seed=12))]
    assert first == again and first != other
    narrow = ArmContext(
        system=[
            ArmSpec(
                arm="system",
                spec=spec("x", question=True).model_copy(update={"title_traits": {"has_year": True}}),
            )
        ],
        window=window(),
        topic_ref_ids=[],
        universe=[],
        replicates=1,
        seed=1,
    )
    [drawn] = RandomArm().generate(narrow)
    assert drawn.spec.title_traits == {} and drawn.spec.specificity == 0


def test_generate_arms_keeps_the_system_specs_first() -> None:
    specs = generate_arms(context(replicates=2))
    assert [s.arm for s in specs[:3]] == ["system"] * 3
    assert {s.arm for s in specs} == {"system", "copy_top", "random"}


def test_spec_signature_ignores_provenance() -> None:
    base = spec("a", topics=["tr_a"])
    assert spec_signature(base) == spec_signature(
        base.model_copy(update={"spec_id": "z", "source": "random"})
    )
    assert spec_signature(base) != spec_signature(spec("a", topics=["tr_b"]))
    assert isinstance(base, MatchSpec)
