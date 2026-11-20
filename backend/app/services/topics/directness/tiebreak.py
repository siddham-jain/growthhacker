import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from uuid import UUID

from pydantic import ConfigDict, create_model

from app.core.config import TopicEngineSettings
from app.domain.common import DirectnessLevel
from app.domain.topics import AnswerDirectness, PreparedVideoText, TopicRef
from app.ports.llm import LLMClient, LLMError, PromptInput, TaskId, TrustedBlock, UntrustedBlock
from app.services.topics.directness.scorer import LEVELS, level_for, nearest_boundary
from app.services.topics.llm import SpendGuard, call_structured, enum_of
from app.services.topics.matching.quotes import adjudication_fields, verify_quote
from app.services.topics.versions import TIEBREAK_PROMPT_VERSION

STEP = 0.01


@dataclass(frozen=True)
class TiebreakCase:
    directness: AnswerDirectness
    video: PreparedVideoText
    question: TopicRef
    boundary: float

    @property
    def options(self) -> tuple[DirectnessLevel, DirectnessLevel]:
        above = level_for(self.boundary)
        below = level_for(self.boundary - STEP)
        return below, above


def tiebreak_cases(
    rows: Sequence[AnswerDirectness],
    videos: Mapping[str, PreparedVideoText],
    questions: Mapping[str, TopicRef],
    band: float,
) -> list[TiebreakCase]:
    cases = []
    for row in rows:
        boundary = nearest_boundary(row.score, band)
        video, question = videos.get(row.video_id), questions.get(row.buyer_question_ref_id)
        if (
            boundary is not None
            and video is not None
            and question is not None
            and row.method == "deterministic"
        ):
            cases.append(TiebreakCase(row, video, question, boundary))
    return cases


class DirectnessTieBreaker:
    """borderline directness only; the model picks one of the two adjacent levels and quotes the text."""

    def __init__(self, llm: LLMClient, settings: TopicEngineSettings, guard: SpendGuard) -> None:
        self._llm = llm
        self._settings = settings
        self._guard = guard

    async def resolve(
        self, cases: Sequence[TiebreakCase], *, client_id: UUID, run_id: UUID | None
    ) -> list[AnswerDirectness]:
        if not cases:
            return []
        keys = {f"p{index + 1}": case for index, case in enumerate(cases)}
        schema = create_model(
            "DirectnessTiebreaks",
            __config__=ConfigDict(extra="forbid"),
            decisions=(
                list[
                    create_model(
                        "DirectnessTiebreak",
                        __config__=ConfigDict(extra="forbid"),
                        pair_key=(enum_of(list(keys)), ...),
                        level=(
                            enum_of([level.value for _, level in LEVELS] + [DirectnessLevel.none.value]),
                            ...,
                        ),
                        evidence_quote=(str, ...),
                    )
                ],
                ...,
            ),
        )
        settings = self._settings
        pairs = {
            key: {
                "question": case.question.label,
                "allowed_levels": [level.value for level in case.options],
                "video": adjudication_fields(
                    case.video, settings.adj_description_chars, settings.adj_max_tags
                ),
            }
            for key, case in keys.items()
        }
        questions = {key: value["question"] for key, value in pairs.items()}
        videos = {
            key: {"allowed_levels": value["allowed_levels"], **value["video"]} for key, value in pairs.items()
        }
        questions_text = json.dumps(questions, ensure_ascii=False, sort_keys=True)
        videos_text = json.dumps(videos, ensure_ascii=False, sort_keys=True)
        try:
            result = await call_structured(
                self._llm,
                task=TaskId.directness_tiebreak,
                schema=schema,
                prompt_id="directness_tiebreak",
                prompt_input=PromptInput(
                    trusted=[TrustedBlock(label="buyer_questions", text=questions_text)],
                    untrusted=[
                        UntrustedBlock(label="video_data", source="youtube_metadata", text=videos_text)
                    ],
                ),
                client_id=client_id,
                run_id=run_id,
                stage="recommend",
                guard=self._guard,
                estimated_usd=settings.adj_estimated_usd_per_batch,
                idempotency_key=hashlib.sha256(
                    f"{TIEBREAK_PROMPT_VERSION}|{questions_text}|{videos_text}".encode()
                ).hexdigest(),
            )
        except LLMError:
            return []
        resolved = []
        for decision in result.value.decisions:
            case = keys.get(decision.pair_key)
            if case is None:
                continue
            chosen = DirectnessLevel(decision.level)
            below, above = case.options
            shown = adjudication_fields(case.video, settings.adj_description_chars, settings.adj_max_tags)
            if chosen not in {below, above} or not verify_quote(
                decision.evidence_quote, shown, None, settings.adj_quote_min_partial
            ):
                continue
            score = case.boundary + STEP if chosen == above else case.boundary - STEP
            row = case.directness
            resolved.append(
                row.model_copy(
                    update={
                        "score": round(score, 4),
                        "level": chosen,
                        "method": "deterministic+llm_tiebreak",
                        "explanation": f"{row.explanation} Borderline; adjudicated: {chosen.value} because "
                        f"'{decision.evidence_quote}'.",
                    }
                )
            )
        return resolved
