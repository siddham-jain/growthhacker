import hashlib
import json
import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Literal

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel

from app.core.config import TopicEngineSettings
from app.domain.profile import (
    BrandTerm,
    BusinessIdentity,
    BusinessModel,
    BusinessProfile,
    BuyerQuestion,
    BuyerQuestionIntent,
    BuyerQuestionSourceKind,
    ClaimMeta,
    ClaimOrigin,
    ClaimStatus,
    FreshnessProfile,
    FreshnessSensitivity,
    FunnelStage,
    LocationSummary,
    NicheKeyword,
    Offering,
    ProfileQuality,
    RefreshPolicy,
    ReviewStatus,
)
from app.ports.llm import LLMError, LLMErrorKind, LLMResult, TaskId, TokenUsage
from app.services.topics.lexicon import load_lexicons
from app.services.topics.text.boilerplate import BoilerplateProfile
from app.services.topics.text.normalize import normalize
from app.services.topics.text.prepare import VideoText, VideoTextPreparer

NOW = datetime(2026, 11, 20, 12, tzinfo=UTC)
APOLLO_CLIENT = uuid.UUID("00000000-0000-4000-8000-00000000a001")
RUNABLE_CLIENT = uuid.UUID("00000000-0000-4000-8000-00000000b001")
CHANNEL = "UC" + "a" * 22


def meta(status: ClaimStatus = ClaimStatus.confirmed) -> ClaimMeta:
    return ClaimMeta(
        origin=ClaimOrigin.user, status=status, confidence=1.0, first_seen_version=1, updated_at=NOW
    )


def offering(
    offering_id: str,
    name: str,
    *,
    aliases: Sequence[str] = (),
    definition: str | None = None,
    priority: int = 2,
    status: ClaimStatus = ClaimStatus.confirmed,
) -> Offering:
    return Offering(
        id=offering_id,
        name=name,
        kind="procedure",
        definition=definition,
        aliases=list(aliases),
        priority=priority,
        meta=meta(status),
    )


def keyword(
    keyword_id: str,
    term: str,
    *,
    language: str = "en",
    offering_ids: Sequence[str] = (),
    status: ClaimStatus = ClaimStatus.confirmed,
) -> NicheKeyword:
    return NicheKeyword(
        id=keyword_id,
        term=term,
        language=language,
        kind="procedure",
        tier="seed",
        source="user",
        weight=0.8,
        offering_ids=list(offering_ids),
        meta=meta(status),
    )


def question(
    client_id: uuid.UUID,
    text: str,
    *,
    stage: FunnelStage = FunnelStage.consideration,
    offering_ids: Sequence[str] = (),
    status: ReviewStatus = ReviewStatus.confirmed,
    language: str = "en",
    priority: int = 2,
) -> BuyerQuestion:
    return BuyerQuestion(
        id=uuid.uuid5(client_id, text),
        client_id=client_id,
        text=text,
        normalized_text=normalize(text),
        language=language,
        funnel_stage=stage,
        intent=BuyerQuestionIntent.other,
        offering_ids=list(offering_ids),
        source=BuyerQuestionSourceKind.user,
        status=status,
        priority=priority,
        meta=meta(),
    )


def profile(
    client_id: uuid.UUID,
    *,
    name: str,
    offerings: Sequence[Offering] = (),
    keywords: Sequence[NicheKeyword] = (),
    brand_terms: Sequence[str] = (),
    languages: Sequence[str] = ("en", "hi"),
    cities: Sequence[str] = (),
    domain_kind: Literal["healthcare", "finance", "legal", "none"] = "none",
    description: str | None = None,
    version: int = 1,
) -> BusinessProfile:
    return BusinessProfile(
        profile_id=uuid.uuid5(client_id, f"profile-{version}"),
        client_id=client_id,
        version=version,
        status="confirmed",
        generated_at=NOW,
        identity=BusinessIdentity(
            name=name,
            name_meta=meta(),
            one_liner=f"{name} profile",
            one_liner_meta=meta(),
            description=description,
            industry="healthcare" if domain_kind == "healthcare" else "software",
            industry_meta=meta(),
            niche="test",
            business_model=BusinessModel.multi_location_service,
            geo_scope="national",
            primary_language="en",
            languages=list(languages),
            website=None,
        ),
        offerings=list(offerings),
        niche_keywords=list(keywords),
        brand_terms=[BrandTerm(term=term, kind="company", meta=meta()) for term in brand_terms],
        locations=LocationSummary(count=len(cities), cities=list(cities)),
        freshness=FreshnessProfile(
            sensitivity=FreshnessSensitivity.low,
            sensitivity_score=0.2,
            score_components={},
            rationale="test",
            domain_kind=domain_kind,
            refresh_policy=RefreshPolicy(
                news_scan_interval_hours=24,
                refresh_check_interval_days=7,
                description_refresh_eligible=True,
                min_signal_strength=0.5,
            ),
            meta=meta(),
        ),
        quality=ProfileQuality(
            claims_total=1,
            claims_verified=1,
            claims_dropped_unverified=0,
            injection_flags=0,
            thin_site=False,
            label="adequate",
            completeness=0.8,
        ),
    )


def video_id(label: str) -> str:
    return hashlib.sha1(label.encode()).hexdigest()[:11].replace("a", "A")


def video_text(
    title: str,
    description: str = "",
    tags: Sequence[str] = (),
    *,
    channel_id: str = CHANNEL,
    key: str | None = None,
    is_short: bool = False,
) -> VideoText:
    identifier = video_id(key or title)
    return VideoText(
        video_id=identifier,
        channel_id=channel_id,
        title=title,
        description=description,
        tags=list(tags),
        content_hash=hashlib.sha256(f"{title}|{description}|{tags}".encode()).hexdigest(),
        is_short=is_short,
    )


def prepare(
    video: VideoText, settings: TopicEngineSettings | None = None, profile: BoilerplateProfile | None = None
):
    preparer = VideoTextPreparer(settings or TopicEngineSettings(), load_lexicons(), {"Latn", "Deva"})
    return preparer.prepare(video, profile or BoilerplateProfile.unprofiled(video.channel_id))


class HashEmbedder:
    """deterministic bag of words and character trigrams; similar spellings land close together."""

    model_id = "hash-test@1"
    dim = 256

    def __init__(self) -> None:
        self.calls = 0

    def embed(self, texts: Sequence[str], role: str) -> NDArray[np.float32]:
        self.calls += 1
        rows = []
        for text in texts:
            vector = np.zeros(self.dim, dtype=np.float32)
            normalized = normalize(text)
            features = normalized.split() + [
                normalized[i : i + 3] for i in range(max(0, len(normalized) - 2))
            ]
            for feature in features:
                digest = hashlib.md5(feature.encode()).digest()
                vector[digest[0] % self.dim] += 1.0 if digest[1] % 2 else -1.0
            norm = np.linalg.norm(vector)
            rows.append(vector / norm if norm else vector)
        return np.vstack(rows).astype(np.float32)


class FrozenEmbedder:
    """replays vectors recorded from the real model; unknown text fails loudly."""

    def __init__(self, vectors: Mapping[str, NDArray[np.float32]], model_id: str, dim: int) -> None:
        self._vectors = vectors
        self.model_id = model_id
        self.dim = dim

    def embed(self, texts: Sequence[str], role: str) -> NDArray[np.float32]:
        missing = [text for text in texts if hashlib.sha256(text.encode()).hexdigest() not in self._vectors]
        if missing:
            raise KeyError(f"no frozen vector for {missing[:2]}; rerun tests/topics/golden/freeze.py")
        return np.vstack([self._vectors[hashlib.sha256(text.encode()).hexdigest()] for text in texts])


Judge = Callable[[dict[str, Any], list[dict[str, Any]]], list[tuple[str, str, str]]]


class FakeLLM:
    """stands in for component 10; answers each task with a scripted function and records calls."""

    def __init__(
        self,
        *,
        judge: Judge | None = None,
        proposals: Mapping[str, list[dict[str, str]]] | None = None,
        negatives: Mapping[str, list[str]] | None = None,
        tiebreak: Callable[[dict[str, Any]], tuple[str, str]] | None = None,
        error: LLMErrorKind | None = None,
        omit_videos: Sequence[str] = (),
    ) -> None:
        self.judge = judge
        self.proposals = proposals or {}
        self.negatives = negatives or {}
        self.tiebreak = tiebreak
        self.error = error
        self.omit_videos = set(omit_videos)
        self.calls: list[dict[str, Any]] = []

    async def generate_structured(
        self, *, task, schema: type[BaseModel], system, input, context, tier=None, budget=None
    ) -> LLMResult:
        self.calls.append({"task": task, "input": input, "prompt": system.prompt_id})
        if self.error is not None:
            raise LLMError(self.error, "scripted failure")
        if task is TaskId.topic_adjudication:
            payload = self._adjudicate(input)
        elif task is TaskId.topic_alias_proposal:
            payload = self._propose(input)
        else:
            payload = self._tiebreak(input)
        value = schema.model_validate(payload)
        return LLMResult(
            value=value,
            raw_text=json.dumps(payload),
            task=task,
            prompt_id=system.prompt_id,
            prompt_version="v1+test",
            provider="fake",
            model="claude-haiku-4-5",
            route_index=0,
            attempts=1,
            repaired=False,
            usage=TokenUsage(input_tokens=1000, output_tokens=100),
            cost_usd=0.0015,
            latency_ms=5,
            stop_reason="end_turn",
            request_ids=["req_test"],
        )

    def _adjudicate(self, prompt_input) -> dict[str, Any]:
        candidates = json.loads(prompt_input.trusted[0].text)
        videos = json.loads(prompt_input.untrusted[0].text)
        decisions = []
        for video in videos:
            key = video["video_key"]
            if video["title"] in self.omit_videos:
                continue
            picks = self.judge(video, candidates[key]) if self.judge else []
            decisions.append(
                {
                    "video_key": key,
                    "matches": [
                        {"topic_id": topic_id, "evidence_quote": quote, "evidence_field": field}
                        for topic_id, quote, field in picks
                    ],
                    "none": not picks,
                }
            )
        return {"decisions": decisions}

    def _propose(self, prompt_input) -> dict[str, Any]:
        topics = json.loads(prompt_input.trusted[0].text)["topics"]
        return {
            "refs": [
                {
                    "topic_id": topic["topic_id"],
                    "aliases": self.proposals.get(topic["label"], []),
                    "negative_contexts": self.negatives.get(topic["label"], []),
                }
                for topic in topics
            ]
        }

    def _tiebreak(self, prompt_input) -> dict[str, Any]:
        videos = json.loads(prompt_input.untrusted[0].text)
        decisions = []
        for key, video in videos.items():
            if self.tiebreak is None:
                continue
            level, quote = self.tiebreak(video)
            decisions.append({"pair_key": key, "level": level, "evidence_quote": quote})
        return {"decisions": decisions}


def judge_by_label(rules: Mapping[str, Sequence[str]]) -> Judge:
    """rules: label -> phrases; a candidate matches when one phrase appears in the title."""

    def judge(video: dict[str, Any], candidates: list[dict[str, Any]]) -> list[tuple[str, str, str]]:
        picks = []
        for candidate in candidates:
            for phrase in rules.get(candidate["label"], ()):
                for field in ("title",):
                    if phrase.lower() in video[field].lower():
                        start = video[field].lower().index(phrase.lower())
                        picks.append(
                            (candidate["topic_id"], video[field][start : start + len(phrase)], field)
                        )
                        break
                else:
                    continue
                break
        return picks

    return judge
