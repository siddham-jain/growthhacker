import json
import re
from dataclasses import dataclass, field
from datetime import date

from app.adapters.llm.untrusted import looks_like_injection, sanitize
from app.domain.card_copy import (
    MAX_PACK_FACTS,
    MAX_PACK_VIDEOS,
    EvidencePack,
    PackAlias,
    PackClient,
    PackConfidence,
    PackFact,
    PackTopic,
    PackVideo,
)
from app.domain.recommendations import (
    CreatorSubject,
    EvidenceNumber,
    EvidenceSource,
    NewsSubject,
    Reason,
    RecommendationDraft,
    ReferenceVideo,
    VideoIdeaSubject,
)
from app.ports.llm import PayloadClass
from app.services.llm.writer.numbers import AllowedNumber, compact_number, numbers_in, ratio_display
from app.services.llm.writer.schemas import WriterClientContext
from app.services.llm.writer.text_match import normalize

MAX_CLIENT_ALIASES = 20
RANGE_NUMBER = r"(\d+(?:\.\d+)?)\s*x"

PAYLOAD_BY_SOURCE: dict[EvidenceSource, PayloadClass] = {
    EvidenceSource.youtube_public: PayloadClass.public_youtube,
    EvidenceSource.youtube_own_analytics: PayloadClass.client_private_analytics,
    EvidenceSource.spotlit_ai_tracking: PayloadClass.client_private_analytics,
    EvidenceSource.website: PayloadClass.public_web,
    EvidenceSource.news: PayloadClass.news,
    EvidenceSource.client_config: PayloadClass.client_profile,
}


@dataclass(frozen=True)
class GroundingIndex:
    """everything the validators may accept, derived deterministically from one pack."""

    fact_numbers: dict[str, tuple[AllowedNumber, ...]]
    line_numbers: tuple[AllowedNumber, ...]
    all_numbers: tuple[AllowedNumber, ...]
    allowed_years: frozenset[int]
    allowed_dates: tuple[date, ...]
    masked_spans: tuple[str, ...]
    allowed_entities: tuple[str, ...]
    forbidden_entities: tuple[str, ...]
    client_domains: tuple[str, ...]
    forbidden_titles: tuple[str, ...]
    statements: dict[str, str]
    why_aliases: tuple[str, ...]
    fit_aliases: tuple[str, ...]
    language: str
    vertical: str
    has_own_analytics: bool
    reason_ids: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class BuiltPack:
    pack: EvidencePack
    grounding: GroundingIndex
    payload_classes: frozenset[PayloadClass]
    suspicious_videos: int = 0

    @property
    def fingerprint(self) -> str:
        return self.pack.model_dump_json()


def _allowed(number: EvidenceNumber) -> AllowedNumber:
    return AllowedNumber(value=number.value, unit=number.unit, display=number.display)


def _topic_label(draft: RecommendationDraft, client: WriterClientContext) -> str:
    subject = draft.subject
    if isinstance(subject, VideoIdeaSubject):
        return subject.topic_text
    for topic_id in draft.topic_ids:
        if topic_id in client.topic_labels:
            return client.topic_labels[topic_id]
    if isinstance(subject, NewsSubject):
        return subject.angle
    return ""


def _subject_entities(draft: RecommendationDraft) -> list[str]:
    subject = draft.subject
    if isinstance(subject, CreatorSubject):
        return [name for name in (subject.title, subject.handle) if name]
    if isinstance(subject, NewsSubject):
        return [subject.key_entity]
    return []


class EvidencePackBuilder:
    """draft -> aliased, capped, deterministic evidence pack plus the grounding index for validation."""

    def build(self, draft: RecommendationDraft, client: WriterClientContext) -> BuiltPack:
        if draft.confidence is None:
            raise ValueError(f"draft {draft.draft_id} has no confidence; only gated drafts reach the writer")
        facts, reason_ids = self._facts(draft)
        videos, suspicious = self._videos(draft)
        offerings = [
            PackAlias(alias=f"o{i}", text=text)
            for i, text in enumerate(client.offerings[:MAX_CLIENT_ALIASES], 1)
        ]
        value_props = [
            PackAlias(alias=f"p{i}", text=text)
            for i, text in enumerate(client.value_props[:MAX_CLIENT_ALIASES], 1)
        ]
        questions = sorted(client.buyer_questions.items(), key=lambda item: str(item[0]))[:MAX_CLIENT_ALIASES]
        question_aliases = {question_id: f"q{i}" for i, (question_id, _) in enumerate(questions, 1)}
        topic = PackTopic(
            label=_topic_label(draft, client),
            buyer_question_alias=question_aliases.get(draft.buyer_question_id)
            if draft.buyer_question_id
            else None,
        )
        entities = sorted({*draft.entities, *_subject_entities(draft)})
        forbidden_titles = sorted(
            {*draft.forbidden_titles, *client.recent_titles, *(video.title for video in videos)}
        )
        allowed_dates = sorted({*draft.allowed_dates, *(video.published_at for video in videos)})
        confidence = draft.confidence
        pack = EvidencePack(
            draft_id=draft.draft_id,
            rec_type=draft.type,
            lens=sorted({assignment.lens for assignment in draft.lenses}),
            window_days=draft.window_days,
            language=client.language,
            client=PackClient(
                name=client.name,
                offerings=offerings,
                value_props=value_props,
                buyer_questions=[
                    PackAlias(alias=question_aliases[qid], text=text) for qid, text in questions
                ],
                locations=client.locations,
                vertical=client.vertical,
            ),
            topic=topic,
            facts=facts,
            videos=videos,
            entities=entities,
            confidence=PackConfidence(
                label=confidence.label, outliers=confidence.outliers, channels=confidence.channels
            ),
            expected_range_vs_baseline=draft.expected_range.display if draft.expected_range else None,
            forbidden_titles=forbidden_titles,
            allowed_dates=allowed_dates,
        )
        payload = {PayloadClass.public_youtube, PayloadClass.client_profile}
        payload |= {PAYLOAD_BY_SOURCE[fact.reason.source] for fact in facts}
        return BuiltPack(
            pack=pack,
            grounding=self._grounding(pack, client, reason_ids),
            payload_classes=frozenset(payload),
            suspicious_videos=suspicious,
        )

    def _facts(self, draft: RecommendationDraft) -> tuple[list[PackFact], dict[str, str]]:
        # 07 orders reasons by priority; fit reasons may repeat a why reason, which then supports both
        ordered: dict[str, tuple[Reason, set[str]]] = {}
        for supports, reasons in (("why", draft.reasons), ("fit", draft.fit_reasons)):
            for reason in reasons:
                entry = ordered.setdefault(reason.reason_id, (reason, set()))
                entry[1].add(supports)
        facts = []
        reason_ids = {}
        for index, (reason, supports) in enumerate(list(ordered.values())[:MAX_PACK_FACTS], 1):
            alias = f"f{index}"
            reason_ids[alias] = reason.reason_id
            facts.append(
                PackFact.model_validate({"alias": alias, "supports": sorted(supports), "reason": reason})
            )
        return facts, reason_ids

    def _videos(self, draft: RecommendationDraft) -> tuple[list[PackVideo], int]:
        seen: set[str] = set()
        chosen: list[ReferenceVideo] = []
        suspicious = 0
        for video in [*draft.references, *draft.reference_pool]:
            if video.video_id in seen:
                continue
            seen.add(video.video_id)
            # flagged titles stay in metrics but never reach the writer
            if looks_like_injection(video.title) or looks_like_injection(video.channel):
                suspicious += 1
                continue
            chosen.append(video)
        return [
            PackVideo(
                alias=f"v{index}",
                video_id=video.video_id,
                channel_name=sanitize(video.channel, "youtube_channel"),
                title=sanitize(video.title, "youtube_title"),
                published_at=video.published_at.date(),
                views_at_age=video.views_at_age,
                views_day7=video.views_day7,
                outlier_ratio=video.outlier_ratio,
            )
            for index, video in enumerate(chosen[:MAX_PACK_VIDEOS], 1)
        ], suspicious

    def _grounding(
        self, pack: EvidencePack, client: WriterClientContext, reason_ids: dict[str, str]
    ) -> GroundingIndex:
        fact_numbers = {
            fact.alias: tuple(_allowed(number) for number in fact.reason.numbers) for fact in pack.facts
        }
        context: list[AllowedNumber] = [
            AllowedNumber(pack.window_days, "days", f"{pack.window_days} days"),
            AllowedNumber(pack.confidence.outliers, "count", str(pack.confidence.outliers)),
            AllowedNumber(pack.confidence.channels, "channels", str(pack.confidence.channels)),
        ]
        if pack.expected_range_vs_baseline:
            for raw in re.findall(RANGE_NUMBER, pack.expected_range_vs_baseline):
                context.append(AllowedNumber(float(raw), "ratio_x", f"{raw}x"))
        client_strings = [
            *(item.text for item in pack.client.offerings),
            *(item.text for item in pack.client.value_props),
            *(item.text for item in pack.client.buyer_questions),
            *pack.client.locations,
            pack.topic.label,
            pack.client.name,
            *pack.entities,
        ]
        for text in client_strings:
            context.extend(AllowedNumber(value, "count", f"{value:g}") for value in numbers_in(text))
        video_numbers: list[AllowedNumber] = []
        for video in pack.videos:
            age = video.views_at_age
            # comparison ages are context, so any line may name them
            context.append(AllowedNumber(age.age_days, "days", f"day {age.age_days}"))
            if age.views is not None:
                video_numbers.append(AllowedNumber(age.views, "views", compact_number(age.views)))
            if video.views_day7 is not None:
                video_numbers.append(
                    AllowedNumber(video.views_day7, "views", compact_number(video.views_day7))
                )
            if video.outlier_ratio is not None:
                video_numbers.append(
                    AllowedNumber(video.outlier_ratio, "ratio_x", ratio_display(video.outlier_ratio))
                )
        line_numbers = tuple(context)
        all_numbers = (
            *line_numbers,
            *video_numbers,
            *(n for numbers in fact_numbers.values() for n in numbers),
        )
        years = {item.year for item in pack.allowed_dates}
        years |= {int(n.value) for n in all_numbers if n.unit == "year"}
        years |= {
            int(value) for text in client_strings for value in numbers_in(text) if 1900 <= value <= 2100
        }
        allowed_entities = sorted(
            {
                pack.client.name,
                *client.locations,
                *pack.entities,
                *(item.text for item in pack.client.offerings),
            }
            | {video.channel_name for video in pack.videos}
        )
        allowed_norm = {normalize(name) for name in allowed_entities}
        forbidden = sorted(
            {
                name
                for name in client.known_channel_names
                if name.strip() and normalize(name) not in allowed_norm
            }
        )
        masked = [
            *allowed_entities,
            *(item.text for item in pack.client.buyer_questions),
            *(item.text for item in pack.client.value_props),
            pack.topic.label,
            *pack.forbidden_titles,
        ]
        if pack.expected_range_vs_baseline:
            masked.append(pack.expected_range_vs_baseline)
        return GroundingIndex(
            fact_numbers=fact_numbers,
            line_numbers=line_numbers,
            all_numbers=all_numbers,
            allowed_years=frozenset(years),
            allowed_dates=tuple(pack.allowed_dates),
            masked_spans=tuple(span for span in masked if span),
            allowed_entities=tuple(allowed_entities),
            forbidden_entities=tuple(forbidden),
            client_domains=tuple(domain.lower().removeprefix("www.") for domain in client.domains),
            forbidden_titles=tuple(pack.forbidden_titles),
            statements={fact.alias: fact.reason.statement for fact in pack.facts},
            why_aliases=tuple(fact.alias for fact in pack.facts if "why" in fact.supports),
            fit_aliases=tuple(fact.alias for fact in pack.facts if "fit" in fact.supports),
            language=pack.language,
            vertical=pack.client.vertical,
            has_own_analytics=any(
                fact.reason.source is EvidenceSource.youtube_own_analytics for fact in pack.facts
            ),
            reason_ids=reason_ids,
        )


def pack_payload(pack: EvidencePack) -> tuple[str, str]:
    """(trusted evidence json, untrusted video json) rendered deterministically for the prompt."""
    facts = [
        {
            "alias": fact.alias,
            "supports": fact.supports,
            "source": fact.reason.source.value,
            "statement": fact.reason.statement,
            "numbers": [number.display for number in fact.reason.numbers],
        }
        for fact in pack.facts
    ]
    evidence = {
        "rec_type": pack.rec_type.value,
        "lens": [lens.value for lens in pack.lens],
        "window_days": pack.window_days,
        "language": pack.language,
        "topic": pack.topic.model_dump(mode="json"),
        "facts": facts,
        "entities": pack.entities,
        "confidence": pack.confidence.model_dump(mode="json"),
        "expected_range_vs_baseline": pack.expected_range_vs_baseline,
        "allowed_dates": [item.isoformat() for item in pack.allowed_dates],
        "video_count": len(pack.videos),
    }
    videos = [
        {
            "alias": video.alias,
            "channel": video.channel_name,
            "title": video.title,
            "published": video.published_at.isoformat(),
            "views_at_age": None
            if video.views_at_age.views is None
            else f"{compact_number(video.views_at_age.views)} at day {video.views_at_age.age_days}",
            "outlier_ratio": None if video.outlier_ratio is None else ratio_display(video.outlier_ratio),
        }
        for video in pack.videos
    ]
    return stable_json(evidence), stable_json(
        {"videos": videos, "titles_you_must_not_copy": pack.forbidden_titles}
    )


def stable_json(value: object) -> str:
    # byte-stable serialisation keeps prompt-cache prefixes and fixtures identical across runs
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def client_payload(pack: EvidencePack) -> str:
    return stable_json(pack.client.model_dump(mode="json"))
