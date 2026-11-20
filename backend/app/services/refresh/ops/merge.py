from collections.abc import Sequence
from dataclasses import dataclass

from app.core.config import RefreshSettings
from app.domain.refresh import (
    DESCRIPTION_BYTE_LIMIT,
    MAX_INSERTED_CHARS,
    MAX_OPERATIONS,
    Advisory,
    InsertionKind,
    InsertionOp,
    InsertionPosition,
)
from app.domain.youtube import VideoRecord
from app.services.refresh.ops.apply import apply_ops, newline_of, verify_additive
from app.services.refresh.text import normalise
from app.services.refresh.triggers.base import Finding, TriggerKind

PROTECTED_KINDS = frozenset({TriggerKind.broken_link, TriggerKind.missing_cta})
POSITION_ORDER = {position: index for index, position in enumerate(InsertionPosition)}


@dataclass(frozen=True, slots=True)
class Candidate:
    finding: Finding
    priority: float
    op: InsertionOp | None


@dataclass(frozen=True, slots=True)
class MergeResult:
    operations: tuple[InsertionOp, ...]
    advisories: tuple[Advisory, ...]
    findings: tuple[Finding, ...]
    dropped: tuple[tuple[Finding, str], ...]
    inserted_bytes: int
    resulting_bytes: int
    preview_after: str


def entity_family(entity: str) -> str:
    # "claude fable 5.1" and "claude fable 5.2" are one family; the newest release wins
    return " ".join(token for token in normalise(entity).split() if not any(char.isdigit() for char in token))


class RefreshMerger:
    """one refresh per video: newest entity wins, caps and the byte budget drop the lowest priority first."""

    def __init__(self, settings: RefreshSettings) -> None:
        self.settings = settings

    def merge(self, video: VideoRecord, candidates: Sequence[Candidate]) -> MergeResult:
        dropped: list[tuple[Finding, str]] = []
        kept = self._newest_news(candidates, dropped)
        kept = self._single_top_line(kept, dropped)
        description = video.description
        newline_bytes = len(newline_of(description).encode("utf-8"))
        base_bytes = len(description.encode("utf-8"))
        budget = DESCRIPTION_BYTE_LIMIT - self.settings.byte_margin
        at_limit = base_bytes >= self.settings.near_limit_bytes
        ordered = sorted(
            ((item, item.op) for item in kept if item.op is not None),
            key=lambda pair: (pair[0].finding.kind not in PROTECTED_KINDS, -pair[0].priority),
        )
        selected: list[tuple[Candidate, InsertionOp]] = []
        used_bytes = chars = 0
        keywords: set[str] = set()
        hashtags: set[str] = set()
        for item, op in ordered:
            reason = self._rejection(op, len(selected), chars, keywords, hashtags, at_limit)
            delta = (
                op.text_bytes + newline_bytes
                if op.kind is InsertionKind.insert
                else op.text_bytes - len((op.old_text or "").encode("utf-8"))
            )
            if reason is None and base_bytes + used_bytes + delta > budget:
                reason = "would exceed YouTube's 5000-byte description limit"
            if reason:
                dropped.append((item.finding, reason))
                continue
            selected.append((item, op))
            used_bytes += delta
            if op.kind is InsertionKind.insert:
                chars += len(op.text)
            keywords |= {normalise(keyword) for keyword in op.keywords_added}
            hashtags |= {tag.casefold() for tag in op.hashtags_added}
        selected.sort(key=lambda pair: (POSITION_ORDER[pair[1].position], -pair[0].priority))
        operations = tuple(op for _, op in selected)
        preview = apply_ops(description, operations)
        problems = verify_additive(description, preview, operations)
        if problems:
            raise ValueError(f"refresh for {video.video_id} is not additive: {'; '.join(problems)}")
        findings = [item.finding for item, _ in selected] + [item.finding for item in kept if item.op is None]
        resulting = len(preview.encode("utf-8"))
        return MergeResult(
            operations=operations,
            advisories=self._advisories(video, kept, operations, at_limit),
            findings=tuple(findings),
            dropped=tuple(dropped),
            inserted_bytes=max(0, resulting - base_bytes),
            resulting_bytes=resulting,
            preview_after=preview,
        )

    def _rejection(
        self,
        op: InsertionOp,
        selected: int,
        chars: int,
        keywords: set[str],
        hashtags: set[str],
        at_limit: bool,
    ) -> str | None:
        settings = self.settings
        if at_limit:
            return "description is already at the byte limit"
        if selected >= MAX_OPERATIONS:
            return f"at most {MAX_OPERATIONS} changes per video"
        if op.kind is InsertionKind.insert and chars + len(op.text) > MAX_INSERTED_CHARS:
            return f"at most {MAX_INSERTED_CHARS} added characters per video"
        new_keywords = {normalise(keyword) for keyword in op.keywords_added} - keywords
        if len(keywords) + len(new_keywords) > settings.max_new_keywords:
            return f"at most {settings.max_new_keywords} new keywords per video"
        new_tags = {tag.casefold() for tag in op.hashtags_added} - hashtags
        if len(hashtags) + len(new_tags) > settings.max_new_hashtags:
            return f"at most {settings.max_new_hashtags} new hashtags per video"
        return None

    @staticmethod
    def _newest_news(candidates: Sequence[Candidate], dropped: list[tuple[Finding, str]]) -> list[Candidate]:
        newest: dict[str, Candidate] = {}
        for item in candidates:
            if item.finding.kind is not TriggerKind.news_entity:
                continue
            family = entity_family(item.finding.params["entity"])
            current = newest.get(family)
            if (
                current is None
                or item.finding.params["first_seen_at"] > current.finding.params["first_seen_at"]
            ):
                newest[family] = item
        kept = []
        for item in candidates:
            is_news = item.finding.kind is TriggerKind.news_entity
            if is_news and newest[entity_family(item.finding.params["entity"])] is not item:
                dropped.append((item.finding, "a newer release of the same product is suggested instead"))
                continue
            kept.append(item)
        return kept

    @staticmethod
    def _single_top_line(
        candidates: Sequence[Candidate], dropped: list[tuple[Finding, str]]
    ) -> list[Candidate]:
        top = [
            item
            for item in candidates
            if item.op is not None and item.op.position is InsertionPosition.top_line
        ]
        if len(top) < 2:
            return list(candidates)
        winner = max(top, key=lambda item: item.priority)
        kept = []
        for item in candidates:
            if item in top and item is not winner:
                dropped.append((item.finding, "only one new top line per video"))
                continue
            kept.append(item)
        return kept

    def _advisories(
        self,
        video: VideoRecord,
        candidates: Sequence[Candidate],
        operations: Sequence[InsertionOp],
        at_limit: bool,
    ) -> tuple[Advisory, ...]:
        advisories: list[Advisory] = []
        for item in candidates:
            advisories += item.finding.advisories
        if at_limit and any(item.op is not None for item in candidates):
            advisories.append(
                Advisory(
                    kind="manual_review",
                    message="This description is at YouTube's 5000-byte limit. Remove something before adding this line.",
                    risk="medium",
                )
            )
        translations = [lang for lang in video.localizations if lang != video.default_language]
        if operations and translations:
            advisories.append(
                Advisory(
                    kind="localizations_not_updated",
                    message=(
                        f"This video has translated descriptions in {len(translations)} languages. Viewers in "
                        "those languages won't see this line unless you update them in Studio."
                    ),
                    risk="low",
                )
            )
        unique: dict[tuple[str, str], Advisory] = {}
        for advisory in advisories:
            unique.setdefault((advisory.kind, advisory.message), advisory)
        return tuple(unique.values())
