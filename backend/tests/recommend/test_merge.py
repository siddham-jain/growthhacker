from app.domain.recommendations import PatternSpec, RecType
from app.services.recommend.merge import CandidateMerger
from tests.recommend.factories import draft, reference
from tests.recommend.fixtures import news_drafts, question_id

MERGER = CandidateMerger()


def pool(*indices: int):
    return [reference(index, channel=f"ch{index}") for index in indices]


def codes(result):
    return sorted(code for _, code, _ in result.dropped)


def test_exact_keys_keep_the_draft_with_more_references(ctx) -> None:
    small, large = draft(pool(1, 2, 3), key="same"), draft(pool(1, 2, 3, 4), key="same")
    result = MERGER.merge([small, large], ctx)

    assert [len(item.reference_pool) for item in result.kept] == [4]
    assert codes(result) == ["duplicate_key"]


def test_overlapping_ideas_merge_and_keep_the_higher_intent_question(ctx) -> None:
    decision = draft(pool(1, 2, 3, 4), key="a", buyer_question_id=question_id("bq_knee"))
    awareness = draft(pool(1, 2, 3, 5), key="b", buyer_question_id=question_id("bq_heart"))
    result = MERGER.merge([awareness, decision], ctx)

    (survivor,) = result.kept
    assert survivor.buyer_question_id == question_id("bq_knee")
    assert survivor.related_buyer_question_ids == [question_id("bq_heart")]
    assert {ref.video_id for ref in survivor.reference_pool} == {ref.video_id for ref in pool(1, 2, 3, 4, 5)}
    assert codes(result) == ["merged_into"]


def test_distinct_evidence_is_not_merged(ctx) -> None:
    result = MERGER.merge([draft(pool(1, 2, 3), key="a"), draft(pool(4, 5, 6), key="b")], ctx)

    assert len(result.kept) == 2 and result.dropped == []


def pattern(traits: dict[str, str], lift: float, n_with: int, key: str, refs):
    item = draft(refs, RecType.title_pattern, key=key)
    subject = PatternSpec(
        kind="title",
        traits=traits,
        scope="niche",
        lift=lift,
        ci_low=1.2,
        ci_high=3.0,
        n_with=n_with,
        n_outliers_with=4,
        n_total=40,
        n_outliers_total=8,
    )
    return item.model_copy(update={"subject": subject})


def test_a_conjunction_covered_by_its_single_trait_is_dropped(ctx) -> None:
    single = pattern({"title.is_question": "true"}, 2.0, 12, "single", pool(1, 2, 3))
    pair = pattern({"title.is_question": "true", "title.has_year": "true"}, 2.1, 5, "pair", pool(7, 8, 9))
    result = MERGER.merge([single, pair], ctx)

    assert [item.stable_key for item in result.kept] == [single.stable_key]
    assert codes(result) == ["subsumed_pattern"]


def test_news_on_the_same_topic_and_evidence_replaces_the_idea(ctx) -> None:
    news = news_drafts(30)[0]
    idea = draft(list(news.reference_pool), key="idea", topic_ids=news.topic_ids)
    result = MERGER.merge([idea, news], ctx)

    assert [item.type for item in result.kept] == [RecType.news_opportunity]
    assert codes(result) == ["superseded_by_news"]


def test_event_driven_ideas_are_handed_to_news(ctx) -> None:
    result = MERGER.merge([draft(pool(1, 2, 3), key="event", flags=["event_driven"])], ctx)

    assert result.kept == [] and codes(result) == ["event_driven"]


def test_two_lifted_values_of_one_trait_are_mixed_evidence(ctx) -> None:
    short = pattern({"title.char_len_band": "≤30"}, 2.0, 12, "short", pool(1, 2, 3))
    long = pattern({"title.char_len_band": ">70"}, 2.0, 12, "long", pool(4, 5, 6))
    result = MERGER.merge([short, long], ctx)

    assert all("contradictory" in item.flags for item in result.kept)
