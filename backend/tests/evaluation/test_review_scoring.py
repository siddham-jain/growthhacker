import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import ReviewSettings
from app.evaluation.packs.exports import PackCsvError, write_pack_csv
from app.evaluation.packs.participants import participant_id
from app.evaluation.review.gate_tuning import propose_gate_changes
from app.evaluation.review.importer import import_ratings
from app.evaluation.review.models import (
    ClientCards,
    ClientSheet,
    Rating,
    ReviewCard,
    ReviewItemRecord,
    SourceCard,
    UniverseVideo,
)
from app.evaluation.review.pack import REVIEW_SCHEMA, render_review_pack
from app.evaluation.review.render import render_review_summary
from app.evaluation.review.rubric import rubric_markdown
from app.evaluation.review.sampler import ReviewSampleDesigner, type_quotas
from app.evaluation.review.scorer import summarise_review
from app.evaluation.review.source import card_from_recommendation, gate_features
from app.models.evaluation import REVIEW_CRITERIA
from tests.contracts.factories import make_recommendation

NOW = datetime(2026, 11, 20, 12, tzinfo=UTC)
TYPES = ("video_idea", "title_pattern", "thumbnail_pattern", "format", "creator")
SETTINGS = ReviewSettings(bootstrap_resamples=200)
R1, R2 = participant_id("mira"), participant_id("marketer")


def card(n: int, client: str = "Apollo") -> ReviewCard:
    return ReviewCard(
        client_name=client,
        type="video_idea",
        summary=f"Card {n}",
        lens=("views",),
        why_it_works=("because",),
        reference_videos=(),
        fit_to_client="fits",
        confidence="Medium · 4 outliers across 2 channels",
    )


def source(n: int, kind: str, lens: tuple[str, ...] = ("views",), label: str = "Medium") -> SourceCard:
    return SourceCard(
        rec_id=f"rec_{n:012d}",
        type=kind,
        lens=lens,
        confidence_label=label,
        card=card(n),
        gate_features={"confidence.outliers": float(n % 7)},
    )


def client_cards(index: int, per_type: dict[str, int]) -> ClientCards:
    client_id = uuid.UUID(int=index + 1)
    cards, n = [], index * 100
    for kind, count in per_type.items():
        for k in range(count):
            cards.append(
                source(n, kind, ("conversion",) if k % 2 else ("views",), ("Low", "Medium", "High")[k % 3])
            )
            n += 1
    return ClientCards(
        client_id=client_id,
        workspace_id=uuid.UUID(int=99),
        run_id=uuid.UUID(int=500 + index),
        as_of=NOW,
        window_days=30,
        sheet=ClientSheet(
            client_id=client_id, name=f"Client {index}", window_days=30, buyer_questions=("q?",)
        ),
        cards=tuple(cards),
        universe=tuple(
            UniverseVideo(
                video_id=f"top{index}{k:07d}",
                channel_id="UC" + "b" * 22,
                channel_title="Rival",
                title=f"Top video {index}-{k}",
                published_at=NOW - timedelta(days=3),
                views=90_000 - k,
            )
            for k in range(3)
        ),
        oldest_api_at=NOW - timedelta(days=1),
    )


def test_card_projection_keeps_visible_fields_only() -> None:
    rec = make_recommendation()
    projected = card_from_recommendation(rec, "Apollo")
    assert projected.summary == rec.summary
    assert projected.confidence == "High · 6 outliers across 3 channels"
    assert projected.reference_videos[0].views_label.startswith("18,400 views at day 7")
    dumped = projected.model_dump_json()
    assert rec.id not in dumped and "stable_key" not in dumped
    features = gate_features(rec, {"gate_results": [{"gate": "min_refs", "features": {"n": 3, "ok": True}}]})
    assert features["min_refs.n"] == 3.0 and "min_refs.ok" not in features
    assert features["expected_range.low"] == 1.2


def test_type_quotas_floor_and_proportional_rest() -> None:
    quotas = type_quotas(Counter({"video_idea": 40, "format": 2, "creator": 10}), 30, 3)
    assert quotas["format"] == 2
    assert quotas["creator"] >= 3
    assert sum(quotas.values()) == 30


def test_review_design_strata_decoys_and_blind_pack() -> None:
    clients = [
        client_cards(i, {"video_idea": 6, "title_pattern": 2, "format": 2, "creator": 2}) for i in range(5)
    ]
    design = ReviewSampleDesigner(SETTINGS).design(clients, seed=4, retention_days=28)
    real = [item for item in design.items if not item.is_decoy]
    decoys = [item for item in design.items if item.is_decoy]
    assert len(real) == 30 and len(decoys) == 6
    by_type = Counter(item.source.type for item in real)
    assert all(by_type[kind] >= 3 for kind in ("title_pattern", "format", "creator"))
    per_client = Counter(item.client_id for item in real)
    assert max(per_client.values()) - min(per_client.values()) <= 1
    assert {lens for item in real for lens in item.source.lens} == {"views", "conversion"}
    assert {item.source.confidence_label for item in real} == {"Low", "Medium", "High"}
    assert all(item.source.rec_id is None for item in decoys)
    assert all(item.expires_at == NOW + timedelta(days=27) for item in design.items)
    page = render_review_pack(design, participant="mira", seed=4, batch_size=25, now=NOW)
    assert "rec_000000000000" not in page
    assert "confidence.outliers" not in page
    assert "Make a video like" in page
    assert "Tracked buyer questions" in page
    assert "could brief a creator today" in page


def ratings_for(items: list[ReviewItemRecord], score_of) -> list[Rating]:
    ratings = []
    for item in items:
        for rater, shift in ((R1, 0), (R2, -1)):
            base = score_of(item)
            values = {c: max(1, min(5, base + (shift if c == "not_obvious" else 0))) for c in REVIEW_CRITERIA}
            ratings.append(
                Rating(item_id=item.item_id, rater_id=rater, would_ship=base >= 4, rated_at=NOW, **values)
            )
    return ratings


def records(n_real: int, n_decoy: int) -> list[ReviewItemRecord]:
    return [
        ReviewItemRecord(
            item_id=uuid.UUID(int=k + 1),
            batch_id=uuid.UUID(int=1000),
            workspace_id=uuid.UUID(int=99),
            client_id=uuid.UUID(int=k % 2 + 1),
            is_decoy=k >= n_real,
            rec_id=None,
            type=TYPES[k % len(TYPES)],
            lens=("views",),
            confidence_label="Medium",
            card=card(k),
            gate_features={"confidence.outliers": float(k % 6), "noise": float((k * 7) % 5)},
        )
        for k in range(n_real + n_decoy)
    ]


def test_summary_pass_rule_decoy_gap_and_agreement() -> None:
    items = records(30, 6)
    ratings = ratings_for(items, lambda item: 2 if item.is_decoy else 5 if item.item_id.int % 3 else 4)
    summary = summarise_review(uuid.UUID(int=1000), items, ratings, SETTINGS, seed=1)
    assert summary.n_items == 30 and summary.n_decoys == 6 and summary.n_raters == 2
    assert summary.passed
    assert summary.decoys_informative is True
    assert summary.decoy_gap["overall"] > 2
    assert summary.by_criterion["specific"].alpha_ordinal == pytest.approx(1.0)
    assert summary.by_criterion["not_obvious"].weighted_kappa < 1.0
    assert summary.overall_ci[0] <= summary.overall_mean <= summary.overall_ci[1]
    assert set(summary.by_type) == set(TYPES)
    assert "PASS" in render_review_summary(summary)


def test_one_weak_criterion_fails_and_flat_decoys_are_flagged() -> None:
    items = records(10, 2)
    ratings = []
    for item in items:
        for rater in (R1, R2):
            values = {c: 5 for c in REVIEW_CRITERIA} | {"evidenced": 3}
            ratings.append(
                Rating(item_id=item.item_id, rater_id=rater, would_ship=False, rated_at=NOW, **values)
            )
    summary = summarise_review(uuid.UUID(int=1000), items, ratings, SETTINGS, seed=1)
    assert summary.overall_mean == pytest.approx(4.6)
    assert not summary.passed
    assert summary.decoys_informative is False
    assert any("decoy" in w for w in summary.warnings)
    assert any("would ship" in w for w in summary.warnings)


def test_gate_tuning_proposes_only_a_useful_cut() -> None:
    items = records(30, 0)
    ratings = ratings_for(items, lambda item: 5 if item.gate_features["confidence.outliers"] >= 2 else 2)
    proposals = propose_gate_changes(items, ratings, SETTINGS)
    by_feature = {p.feature: p for p in proposals}
    proposal = by_feature["confidence.outliers"]
    # the least restrictive cut that still averages 4.0 wins: dropping only the zero-outlier cards
    assert proposal.proposed_threshold == 1.0
    assert proposal.direction == "higher_is_better"
    assert proposal.retained_share == pytest.approx(25 / 30)
    assert proposal.retained_mean_rating >= 4.0
    assert proposal.config_diff == {"recommend": {"gate_features": {"confidence.outliers": {"min": 1.0}}}}
    assert "exploratory" in proposal.rationale


def test_rating_import_rejects_incomplete_rows() -> None:
    items = records(2, 0)
    known = {i.item_id for i in items}
    full = {
        "item_id": str(items[0].item_id),
        **{c: "4" for c in REVIEW_CRITERIA},
        "would_ship": "true",
        "comment": "",
        "seconds_spent": "40",
    }
    empty = {"item_id": str(items[1].item_id)}
    result = import_ratings(
        write_pack_csv([full, empty], REVIEW_SCHEMA), rater="Mira", known_item_ids=known, rated_at=NOW
    )
    assert result.skipped == 1 and result.ratings[0].specific == 4 and result.ratings[0].would_ship
    partial = {**full, "evidenced": ""}
    with pytest.raises(PackCsvError, match="incomplete"):
        import_ratings(
            write_pack_csv([partial], REVIEW_SCHEMA), rater="m", known_item_ids=known, rated_at=NOW
        )
    out_of_range = {**full, "specific": "6"}
    with pytest.raises(PackCsvError, match="1 to 5"):
        import_ratings(
            write_pack_csv([out_of_range], REVIEW_SCHEMA), rater="m", known_item_ids=known, rated_at=NOW
        )


def test_rubric_markdown_covers_every_criterion() -> None:
    text = rubric_markdown()
    for heading in ("Specific", "Evidenced", "Relevant to the client", "Actionable", "Not obvious"):
        assert heading in text
