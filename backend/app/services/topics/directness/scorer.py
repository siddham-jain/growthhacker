from collections.abc import Sequence
from dataclasses import dataclass, field
from uuid import UUID

from rapidfuzz import fuzz

from app.domain.common import DirectnessLevel
from app.domain.topics import (
    AnswerDirectness,
    DirectnessComponents,
    Facet,
    MatchEvidence,
    PreparedVideoText,
    TopicRef,
)
from app.services.topics.lexicon import Lexicons, PhraseSet
from app.services.topics.text.normalize import normalize
from app.services.topics.text.tokenize import Token, tokenize

WEIGHTS = {
    "question_alignment": 0.35,
    "facet_coverage": 0.25,
    "entity_coverage": 0.20,
    "specificity": 0.10,
    "intent_cues": 0.10,
}
FIELD_WEIGHTS = {
    "title": 1.0,
    "chapters": 0.8,
    "description_head": 0.6,
    "tags": 0.4,
    "hashtags": 0.4,
    "description_rest": 0.3,
}
HEADLINE = ("title", "chapters", "description_head")
LEVELS: tuple[tuple[float, DirectnessLevel], ...] = (
    (0.70, DirectnessLevel.direct),
    (0.45, DirectnessLevel.partial),
    (0.20, DirectnessLevel.mention),
)
BOUNDARIES = tuple(threshold for threshold, _ in LEVELS)
QUESTION_BONUS = 0.1
PROMO_PENALTY, LIST_PENALTY, TAGS_PENALTY = 0.20, 0.15, 0.10
LIST_MENTION_REFS = 4
SHORT_CAP = 0.69
LEVEL_PHRASE = {
    DirectnessLevel.direct: "Directly answers",
    DirectnessLevel.partial: "Partly answers",
    DirectnessLevel.mention: "Mentions the topic of",
    DirectnessLevel.none: "Does not answer",
}
FIELD_NAME = {
    "title": "title",
    "chapters": "chapters",
    "description_head": "description",
    "tags": "tags",
    "hashtags": "hashtags",
    "description_rest": "description",
}


def level_for(score: float) -> DirectnessLevel:
    for threshold, level in LEVELS:
        if score >= threshold:
            return level
    return DirectnessLevel.none


@dataclass
class DirectnessInput:
    client_id: UUID
    universe_hash: str
    video: PreparedVideoText
    question: TopicRef
    parent_evidence: Sequence[MatchEvidence] = ()
    question_evidence: Sequence[MatchEvidence] = ()
    title_semantic: float = 0.0
    refs_matched_in_title: int = 0
    is_short: bool = False
    locations: Sequence[str] = field(default_factory=list)


class AnswerDirectnessScorer:
    """how directly a video's metadata answers a tracked buyer question (plan 05 section 5.7)."""

    def __init__(self, lexicons: Lexicons) -> None:
        self._lexicons = lexicons

    def score(self, data: DirectnessInput) -> AnswerDirectness:
        fields = self._field_tokens(data.video)
        title_keys = [token.stem for token in fields.get("title", [])]
        question_form, overlap = self._question_alignment(data.question, data.video, title_keys)
        alignment = min(1.0, max(overlap, data.title_semantic) + (QUESTION_BONUS if question_form else 0.0))
        facet_fields = self._facet_fields(data.question.facets, fields)
        covered = [facet for facet, name in facet_fields.items() if name]
        if data.question.facets:
            facet_coverage = sum(
                FIELD_WEIGHTS[name] if name else 0.0 for name in facet_fields.values()
            ) / len(facet_fields)
        else:
            facet_coverage = alignment
        entity_field, entity_coverage = self._entity_coverage(data, fields)
        specificity = self._specificity(data, covered, fields)
        intent_field = self._best_field(self._lexicons.intent, fields, HEADLINE)
        intent = FIELD_WEIGHTS[intent_field] if intent_field else 0.0
        penalties, penalty_notes = self._penalties(data, fields)
        raw = (
            WEIGHTS["question_alignment"] * alignment
            + WEIGHTS["facet_coverage"] * facet_coverage
            + WEIGHTS["entity_coverage"] * entity_coverage
            + WEIGHTS["specificity"] * specificity
            + WEIGHTS["intent_cues"] * intent
            - penalties
        )
        score = min(1.0, max(0.0, raw))
        if data.is_short and not (question_form and alignment >= 0.8):
            score = min(score, SHORT_CAP)
        level = level_for(score)
        components = DirectnessComponents(
            question_alignment=round(alignment, 4),
            facet_coverage=round(facet_coverage, 4),
            entity_coverage=round(entity_coverage, 4),
            specificity=round(specificity, 4),
            intent_cues=round(intent, 4),
            penalties=round(penalties, 4),
        )
        notes = []
        if question_form:
            notes.append(f"title is phrased as a question (overlap {overlap:.2f})")
        elif overlap >= 0.5:
            notes.append(f"title wording overlaps the question ({overlap:.2f})")
        notes += [
            f"covers {facet.value.replace('_', ' ')} in {FIELD_NAME[name]}"
            for facet, name in facet_fields.items()
            if name
        ]
        if entity_field:
            notes.append(f"topic named in {FIELD_NAME[entity_field]}")
        if intent_field:
            notes.append("explainer framing")
        notes += penalty_notes
        explanation = (
            f"{LEVEL_PHRASE[level]} '{data.question.label}': {'; '.join(notes) or 'no answer cues'}. "
            "Based on title, description and tags only."
        )
        return AnswerDirectness(
            client_id=data.client_id,
            video_id=data.video.video_id,
            buyer_question_ref_id=data.question.id,
            universe_hash=data.universe_hash,
            prepared_hash=data.video.prepared_hash,
            score=round(score, 4),
            level=level,
            components=components,
            facets_covered=covered,
            facets_missing=[facet for facet in data.question.facets if facet not in covered],
            method="deterministic",
            explanation=explanation,
        )

    def _field_tokens(self, video: PreparedVideoText) -> dict[str, list[Token]]:
        prepared = video.fields
        texts = {
            "title": prepared.title,
            "chapters": "\n".join(prepared.chapters),
            "description_head": prepared.description_head,
            "tags": "\n".join(prepared.tags),
            "hashtags": "\n".join(prepared.hashtags),
            "description_rest": prepared.description_rest,
        }
        return {name: tokenize(text) for name, text in texts.items() if text}

    def _question_alignment(
        self, question: TopicRef, video: PreparedVideoText, title_keys: list[str]
    ) -> tuple[bool, float]:
        lexicons = self._lexicons
        question_tokens = tokenize(question.label)
        content = [
            token
            for token in question_tokens
            if token.stem not in lexicons.stopwords and token.stem not in lexicons.question_words
        ]
        title_tokens = tokenize(video.fields.title)
        overlap = 0.0
        if content and title_tokens:
            by_stem = fuzz.token_set_ratio(" ".join(t.stem for t in content), " ".join(title_keys)) / 100
            by_sound = (
                fuzz.token_set_ratio(
                    " ".join(t.phonetic or t.stem for t in content),
                    " ".join(t.phonetic or t.stem for t in title_tokens),
                )
                / 100
            )
            overlap = max(by_stem, by_sound)
        question_form = "?" in video.fields.title or bool(
            title_tokens and title_tokens[0].stem in lexicons.question_words
        )
        return question_form, overlap

    def _facet_fields(
        self, facets: Sequence[Facet], fields: dict[str, list[Token]]
    ) -> dict[Facet, str | None]:
        return {
            facet: self._best_field(self._lexicons.facets[facet], fields, tuple(FIELD_WEIGHTS))
            for facet in facets
        }

    def _best_field(
        self, cues: PhraseSet, fields: dict[str, list[Token]], names: Sequence[str]
    ) -> str | None:
        best: str | None = None
        for name in names:
            tokens = fields.get(name)
            stronger = best is None or FIELD_WEIGHTS[name] > FIELD_WEIGHTS[best]
            if tokens and stronger and cues.contains([token.stem for token in tokens]):
                best = name
        return best

    def _entity_coverage(
        self, data: DirectnessInput, fields: dict[str, list[Token]]
    ) -> tuple[str | None, float]:
        best_field, best = None, 0.0
        for evidence in [*data.parent_evidence, *data.question_evidence]:
            if evidence.method != "lexical" or evidence.field not in FIELD_WEIGHTS:
                continue
            value = FIELD_WEIGHTS[evidence.field] * evidence.match_quality
            if value > best:
                best_field, best = evidence.field, value
        if best_field is None and data.question.core_entity_terms:
            wanted = {token.stem for term in data.question.core_entity_terms for token in tokenize(term)}
            for name, tokens in fields.items():
                if wanted <= {token.stem for token in tokens} and FIELD_WEIGHTS[name] > best:
                    best_field, best = name, FIELD_WEIGHTS[name]
        return best_field, best

    def _specificity(
        self, data: DirectnessInput, covered: Sequence[Facet], fields: dict[str, list[Token]]
    ) -> float:
        facets = data.question.facets
        if not facets:
            return 0.0
        prepared = data.video.fields
        headline = normalize(" \n ".join([prepared.title, *prepared.chapters, prepared.description_head]))
        title = normalize(prepared.title)
        concrete = 0
        for facet in covered:
            if facet is Facet.location:
                places = [*data.question.locality_terms, *data.locations]
                concrete += any(normalize(place) in title for place in places if place)
                continue
            patterns = self._lexicons.specificity.get(facet, ())
            concrete += any(pattern.search(headline) for pattern in patterns)
        return concrete / len(facets)

    def _penalties(self, data: DirectnessInput, fields: dict[str, list[Token]]) -> tuple[float, list[str]]:
        penalties, notes = 0.0, []
        if self._best_field(self._lexicons.promo, fields, ("title", "description_head")):
            penalties += PROMO_PENALTY
            notes.append("promotional or event framing")
        lexical = [
            item for item in [*data.parent_evidence, *data.question_evidence] if item.method == "lexical"
        ]
        listed = data.refs_matched_in_title >= LIST_MENTION_REFS or any(
            item.detail.get("list_mention") for item in lexical
        )
        if listed:
            penalties += LIST_PENALTY
            notes.append("named in a list of topics")
        if lexical and all(item.field in {"tags", "hashtags"} for item in lexical):
            penalties += TAGS_PENALTY
            notes.append("named in tags only")
        return penalties, notes


def nearest_boundary(score: float, band: float) -> float | None:
    distances = [(abs(score - boundary), boundary) for boundary in BOUNDARIES]
    distance, boundary = min(distances)
    return boundary if distance <= band else None
