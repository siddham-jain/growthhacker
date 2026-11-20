import json
import os
import random
import re
from datetime import timedelta
from pathlib import Path

import pytest

from app.core.config import RecommendSettings
from app.domain.recommendations import (
    ASSIGNMENT_REC_TYPES,
    RECOMMENDATION_ID_PATTERN,
    Recommendation,
    RecType,
    StrictRecommendation,
)
from app.services.recommend.inputs import ClientRecommendConfig, FeedbackState, PreviousCard
from app.services.recommend.service import RecommendationService
from app.services.recommend.thin_data import render_notice
from tests.recommend.fixtures import AS_OF, GENERATED_AT, RUN_ID, apollo_inputs

GOLDEN = Path(__file__).parent / "golden" / "apollo_30d_strict.json"
ASSIGNMENT_FIELDS = {
    "id",
    "type",
    "summary",
    "lens",
    "buyer_question",
    "why_it_works",
    "reference_videos",
    "fit_to_client",
    "expected_range_vs_baseline",
    "confidence",
    "window_days",
    "generated_at",
}
REFERENCE_FIELDS = {"video_id", "channel", "published_at", "views_day7", "outlier_ratio"}


async def run(inputs):
    return await RecommendationService(RecommendSettings()).generate(
        inputs, run_id=RUN_ID, generated_at=GENERATED_AT
    )


@pytest.fixture
async def apollo_run():
    return await run(apollo_inputs())


def cards(result, window: int = 30) -> list[Recommendation]:
    return result.output.window(window).set.recommendations


async def test_apollo_30_day_cards_meet_the_quality_bar(apollo_run) -> None:
    shown = cards(apollo_run)

    assert 5 <= len(shown) <= 10
    assert {card.type for card in shown} >= {RecType.video_idea, RecType.title_pattern, RecType.creator}
    for card in shown:
        assert len(card.reference_videos) >= 3
        assert card.lens and card.why_it_works and card.fit_to_client
        for ref in card.reference_videos:
            assert ref.url.startswith("https://www.youtube.com/watch?v=")
            assert ref.channel and ref.outlier_ratio is not None and ref.views_at_age.views is not None
            assert AS_OF - timedelta(days=30) <= ref.published_at <= AS_OF
        if card.confidence.label.value == "High":
            assert card.confidence.outliers >= 5 and card.confidence.channels >= 3
        expected = card.extras.expected_range
        if expected is not None and expected.basis == "insufficient":
            assert card.confidence.label.value == "Low"
        elif expected is not None and expected.basis != "not_applicable":
            assert card.expected_range_vs_baseline.endswith("(indicative)")


async def test_strict_contract_matches_the_assignment_shape_exactly(apollo_run) -> None:
    strict = [card.strict_contract().model_dump(mode="json") for card in cards(apollo_run)]

    for item in strict:
        assert set(item) == ASSIGNMENT_FIELDS
        assert set(item["confidence"]) == {"label", "outliers", "channels"}
        assert all(set(ref) == REFERENCE_FIELDS for ref in item["reference_videos"])
        assert re.match(RECOMMENDATION_ID_PATTERN, item["id"])
        assert item["type"] in {rec_type.value for rec_type in RecType}
        assert StrictRecommendation.model_validate(item).model_dump(mode="json") == item
    assert {item["type"] for item in strict} <= {rec_type.value for rec_type in ASSIGNMENT_REC_TYPES} | {
        "news_opportunity"
    }


async def test_strict_contract_golden_snapshot(apollo_run) -> None:
    strict = [card.strict_contract().model_dump(mode="json") for card in cards(apollo_run)]
    if os.environ.get("GH_TEST_UPDATE_GOLDEN") == "1" or not GOLDEN.exists():
        GOLDEN.write_text(json.dumps(strict, indent=2, ensure_ascii=False) + "\n")

    assert strict == json.loads(GOLDEN.read_text())


async def test_side_surfaces_stay_out_of_the_recommendation_list(apollo_run) -> None:
    window = apollo_run.output.window(30)
    listed = {card.id for card in window.set.recommendations}

    assert [signal.key_entity for signal in window.early_signals] == ["AI triage pilot"]
    assert window.early_signals[0].confidence.basis.value == "first_mover"
    assert len(window.channel_hygiene) == 1 and window.channel_hygiene[0].id not in listed
    assert [card.type for card in window.refresh_cards] == [RecType.description_refresh]
    assert window.refresh_cards[0].extras.refresh.resulting_bytes == 420
    assert not listed & {card.id for card in window.refresh_cards}
    assert [note.text for note in window.set.white_space] == [
        "Does Apollo offer cashless insurance for kidney transplant?"
    ]


async def test_creator_list_is_grouped_and_sorted_by_one_visible_metric(apollo_run) -> None:
    creator_list = apollo_run.output.window(30).creator_list
    entries = [entry for group in creator_list.groups.values() for entry in group]

    assert creator_list.sort == "matching_uploads"
    values = [entry.sort_value for entry in creator_list.groups["strong"]]
    assert values == sorted(values, reverse=True)
    assert all("score" not in entry.model_dump() for entry in entries)
    resorted = await run(apollo_inputs(config=ClientRecommendConfig(creator_sort="median_outlier_ratio")))
    assert resorted.output.window(30).creator_list.sort == "median_outlier_ratio"


async def test_thin_seven_day_window_says_so_with_projected_counts(apollo_run) -> None:
    week = apollo_run.output.window(7).set
    (thin,) = [notice for notice in week.notices if notice.kind == "thin_window"]

    assert thin.suggestion.action == "widen_window" and thin.suggestion.widen_to == 30
    assert thin.suggestion.projected.outliers > thin.found.outliers
    message = render_notice(thin)
    assert message.startswith("Not enough outliers in the last 7 days")
    assert "Widen to 30?" in message


async def test_a_client_without_a_channel_gets_peer_relative_ranges() -> None:
    inputs = apollo_inputs()
    analytics = [item.model_copy(update={"own_report": None}) for item in inputs.analytics]
    result = await run(inputs.model_copy(update={"own_channel_id": None, "analytics": analytics}))
    window = result.output.window(30).set

    assert {notice.kind for notice in window.notices} >= {"no_client_baseline"}
    ranges = [card.extras.expected_range for card in window.recommendations if card.extras.expected_range]
    assert all(item.basis in {"peer_only_no_baseline", "insufficient", "not_applicable"} for item in ranges)
    assert any(item.basis == "peer_only_no_baseline" for item in ranges)


def shuffled(inputs, seed: int):
    rng = random.Random(seed)
    lists = {}
    for name in (
        "videos",
        "topic_matches",
        "directness",
        "channels",
        "topic_refs",
        "buyer_questions",
        "news_drafts",
    ):
        items = list(getattr(inputs, name))
        rng.shuffle(items)
        lists[name] = items
    return inputs.model_copy(update=lists)


async def test_output_never_depends_on_input_order(apollo_run) -> None:
    again = await run(shuffled(apollo_inputs(), 11))

    assert again.output.model_dump(mode="json") == apollo_run.output.model_dump(mode="json")


async def test_ids_are_stable_across_daily_runs_and_cards_become_ongoing(apollo_run) -> None:
    first = {card.extras.stable_key: card for card in cards(apollo_run)}
    previous = {
        card.draft.stable_key: PreviousCard(
            stable_key=card.draft.stable_key,
            first_seen_at=card.meta.first_seen_at,
            unchanged_since=card.meta.unchanged_since,
            evidence_hash=card.meta.evidence_hash,
        )
        for card in apollo_run.cards
    }
    second = await run(apollo_inputs(previous_cards=previous))

    for card in cards(second):
        assert card.id == first[card.extras.stable_key].id
        assert card.extras.status == "ongoing"
        assert card.extras.first_seen_at == first[card.extras.stable_key].extras.first_seen_at


async def test_dismissed_cards_stay_hidden(apollo_run) -> None:
    target = cards(apollo_run)[0]
    result = await run(
        apollo_inputs(feedback=FeedbackState(hidden_keys={target.extras.stable_key: "dismissed"}))
    )

    assert target.id not in {card.id for card in cards(result)}
    assert any(item.reason_code == "hidden_by_feedback" for item in result.suppressed)
