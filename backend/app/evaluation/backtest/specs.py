import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from app.domain.recommendations import (
    ASSIGNMENT_REC_TYPES,
    LensAssignment,
    MatchSpec,
    Recommendation,
    RecommendationDraft,
    RecType,
    VideoIdeaSubject,
)
from app.evaluation.backtest.models import ArmSpec
from app.services.recommend.annotate import match_spec
from app.services.recommend.service import RecommendOutput, WindowOutput

# news and refresh cards cannot be replayed as of T (plan 12 §5.1.9)
BACKTEST_TYPES: frozenset[RecType] = ASSIGNMENT_REC_TYPES


@dataclass
class SystemSpecs:
    specs: list[ArmSpec] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def spec_signature(spec: MatchSpec) -> str:
    # identity of what a spec demands, ignoring where it came from (edge case 23)
    payload = spec.model_dump(
        mode="json", exclude={"spec_id", "source", "source_ref", "lens", "buyer_question_id"}
    )
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


def displayed_cards(window: WindowOutput) -> list[Recommendation]:
    """cards in the order 07 shows them; creators follow the creator list's grouped order."""
    cards = list(window.set.recommendations)
    by_id = {card.id: card for card in cards}
    creator_order = [
        entry.recommendation_id for entries in window.creator_list.groups.values() for entry in entries
    ]
    creators = [by_id[rec_id] for rec_id in creator_order if rec_id in by_id]
    creators += [c for c in cards if c.type is RecType.creator and c not in creators]
    return [c for c in cards if c.type is not RecType.creator] + creators


def topic_ids(card: Recommendation) -> list[str]:
    subject = card.extras.subject
    if isinstance(subject, VideoIdeaSubject):
        return [subject.topic_id]
    return sorted({topic for ref in card.reference_videos for topic in ref.matched_topic_ids})


def draft_from_card(card: Recommendation, output: RecommendOutput) -> RecommendationDraft | None:
    subject = card.extras.subject
    if subject is None:
        return None
    lenses = card.extras.lens_reasons or [LensAssignment(lens=lens, reasons=[]) for lens in card.lens]
    return RecommendationDraft(
        draft_id=card.extras.stable_key,
        stable_key=card.extras.stable_key,
        client_id=output.client_id,
        type=card.type,
        window_days=card.window_days,
        as_of=output.as_of,
        produced_by="backtest_replay",
        subject=subject,
        topic_ids=topic_ids(card),
        buyer_question_id=card.extras.buyer_question_id,
        lenses=lenses,
    )


def system_specs(
    output: RecommendOutput,
    k_per_type: Mapping[str, int],
    universe: Iterable[str],
    stored_drafts: Mapping[str, RecommendationDraft] | None = None,
) -> SystemSpecs:
    """top-k cards per type exactly as the client would have seen them at T, turned into match specs.

    07's persisted drafts carry the exact match_spec; without them the spec is rebuilt from the card with
    07's own match_spec(), where pattern topics come from the shown references (noted)."""
    result = SystemSpecs()
    window = output.window(output.selected_window)
    if window is None:
        result.notes.append("no recommendations for the selected window at T")
        return result
    allowed = set(universe)
    stored = stored_drafts or {}
    taken: dict[RecType, int] = {}
    seen: set[str] = set()
    rebuilt = 0
    for card in displayed_cards(window):
        if card.type not in BACKTEST_TYPES or taken.get(card.type, 0) >= k_per_type.get(card.type.value, 0):
            continue
        draft = stored.get(card.extras.stable_key)
        spec = draft.match_spec if draft is not None else None
        if spec is None:
            rebuilt_draft = draft_from_card(card, output)
            if rebuilt_draft is None:
                result.notes.append(f"{card.id}: card has no subject; skipped")
                continue
            spec = match_spec(rebuilt_draft)
            rebuilt += 1
        if spec.channel_id is not None and spec.channel_id not in allowed:
            # a creator found after T is selection leakage (edge case 8)
            result.notes.append(f"{card.id}: creator outside the frozen universe; skipped")
            continue
        signature = spec_signature(spec)
        if signature in seen:
            continue
        seen.add(signature)
        taken[card.type] = taken.get(card.type, 0) + 1
        expected = card.extras.expected_range
        result.specs.append(
            ArmSpec(
                arm="system",
                spec=spec.model_copy(update={"source": "system", "source_ref": card.id}),
                confidence=card.confidence.label.value,
                expected_low=expected.low if expected else None,
                expected_high=expected.high if expected else None,
            )
        )
    if rebuilt:
        result.notes.append(f"{rebuilt} specs rebuilt from cards; pattern topics taken from shown references")
    return result
