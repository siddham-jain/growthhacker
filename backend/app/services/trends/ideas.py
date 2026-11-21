import hashlib
import re
from collections.abc import Sequence
from difflib import SequenceMatcher
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.trends import KeywordTrend, TrendIdea
from app.services.news.text import normalize

# a working title this close to a reference or own title is a copy, not an idea
COPY_SIMILARITY = 0.8
DIGIT = re.compile(r"\d")


class DraftIdea(BaseModel):
    model_config = ConfigDict(extra="forbid")

    keyword: str
    title: str = Field(max_length=120)
    angle: str = Field(max_length=400)
    fit_to_client: str = Field(max_length=300)
    format: Literal["short", "long"]
    reference_video_ids: list[str] = Field(min_length=1, max_length=5)
    buyer_question: str | None = None


class DraftIdeas(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ideas: list[DraftIdea] = Field(max_length=30)


class DraftWebKeyword(BaseModel):
    model_config = ConfigDict(extra="forbid")

    keyword: str = Field(max_length=80)
    topic_ref_id: str
    reason: str = Field(max_length=240)
    source_urls: list[str] = Field(max_length=3)


class DraftWebKeywords(BaseModel):
    model_config = ConfigDict(extra="forbid")

    keywords: list[DraftWebKeyword] = Field(max_length=20)


def idea_id(keyword: str, title: str) -> str:
    return "ti_" + hashlib.sha1(f"{normalize(keyword)}|{normalize(title)}".encode()).hexdigest()[:12]


def _similar(a: str, b: str) -> bool:
    return SequenceMatcher(None, a, b).ratio() >= COPY_SIMILARITY


def _compact(value: float) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M".replace(".0M", "M")
    if value >= 1_000:
        return f"{value / 1_000:.1f}k".replace(".0k", "k")
    return f"{value:.0f}"


def evidence_lines(trend: KeywordTrend) -> list[str]:
    """plain facts from the measured keyword; the only numbers an idea card shows."""
    lines = [
        f"{trend.recent_videos} videos on “{trend.candidate.keyword}” in the last {trend.window_days} days "
        f"from {trend.channels} channels"
    ]
    if trend.leaders_views_per_day is not None and trend.top_views_per_day is not None:
        lines.append(
            f"The fastest of them typically get {_compact(trend.leaders_views_per_day)} views a day; "
            f"the top one {_compact(trend.top_views_per_day)}"
        )
    if trend.competitor_videos:
        lines.append(f"{trend.competitor_videos} of them come from your competitors")
    if trend.momentum is not None and trend.momentum >= 1.2:
        lines.append(f"Views a day are {trend.momentum:.1f}x what we saw at the last check")
    return lines


def _states_numbers(draft: DraftIdea, keyword: str) -> bool:
    # the llm may not add numbers; digits that are part of the keyword itself ("2026") are fine
    allowed = set(DIGIT.findall(keyword))
    text = f"{draft.angle} {draft.fit_to_client}"
    return any(digit not in allowed for digit in DIGIT.findall(text))


def accept_ideas(
    drafts: Sequence[DraftIdea],
    trends: Sequence[KeywordTrend],
    *,
    buyer_questions: Sequence[str],
    own_titles: Sequence[str],
    min_references: int,
    per_keyword: int,
) -> list[TrendIdea]:
    """keeps only ideas whose keyword was checked and whose cited videos are that keyword's evidence."""
    by_keyword = {normalize(t.candidate.keyword): t for t in trends if t.videos}
    questions = {normalize(q): q for q in buyer_questions}
    own = [normalize(title) for title in own_titles]
    accepted: list[TrendIdea] = []
    counts: dict[str, int] = {}
    seen: list[str] = []
    for draft in drafts:
        trend = by_keyword.get(normalize(draft.keyword))
        if trend is None or _states_numbers(draft, trend.candidate.keyword):
            continue
        evidence = {v.video_id: v for v in trend.videos}
        refs = [vid for vid in dict.fromkeys(draft.reference_video_ids) if vid in evidence]
        if not refs or len(refs) < min(min_references, len(evidence)):
            continue
        title = normalize(draft.title)
        key = normalize(trend.candidate.keyword)
        if (
            any(_similar(title, normalize(video.title)) for video in evidence.values())
            or any(_similar(title, old) for old in own)
            or any(_similar(title, earlier) for earlier in seen)
            or counts.get(key, 0) >= per_keyword
        ):
            continue
        seen.append(title)
        counts[key] = counts.get(key, 0) + 1
        accepted.append(
            TrendIdea(
                id=idea_id(trend.candidate.keyword, draft.title),
                keyword=trend.candidate.keyword,
                topic_ref_id=trend.candidate.topic_ref_id,
                title=draft.title.strip(),
                angle=draft.angle.strip(),
                fit_to_client=draft.fit_to_client.strip(),
                format=draft.format,
                why=evidence_lines(trend),
                reference_video_ids=refs,
                buyer_question=questions.get(normalize(draft.buyer_question))
                if draft.buyer_question
                else None,
            )
        )
    return accepted
