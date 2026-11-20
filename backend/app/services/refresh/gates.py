from typing import Any, ClassVar

from app.core.registry import quality_gate_registry
from app.domain.recommendations import GateAction, GateResult, RecommendationDraft, RecType, RefreshSubject
from app.domain.refresh import MAX_INSERTED_CHARS, MAX_OPERATIONS, InsertionKind
from app.services.refresh.links.urls import URL, host_of
from app.services.refresh.text import normalise

APPLIES_TO = frozenset({RecType.description_refresh})
MAX_NEW_KEYWORDS = 3
MAX_NEW_HASHTAGS = 3


def _subject(draft: RecommendationDraft) -> RefreshSubject | None:
    return draft.subject if isinstance(draft.subject, RefreshSubject) else None


@quality_gate_registry.register("AdditiveDiffOnly")
class AdditiveDiffOnly:
    """a refresh only adds whole lines; the single exception swaps a broken link for its repaired form."""

    name: ClassVar[str] = "AdditiveDiffOnly"
    phase: ClassVar[str] = "pre_writer"
    applies_to: ClassVar[frozenset[RecType]] = APPLIES_TO
    order: ClassVar[int] = 15

    def check(self, draft: RecommendationDraft, ctx: Any = None, written: Any = None) -> GateResult:
        subject = _subject(draft)
        if subject is None:
            return GateResult(
                gate=self.name, action=GateAction.suppress, reason_code="REFRESH_SUBJECT_MISSING"
            )
        for op in subject.operations:
            if op.kind is InsertionKind.insert:
                continue
            old, new = op.old_text or "", op.text
            same_site = host_of(old) == host_of(new)
            if not (URL.fullmatch(old) and URL.fullmatch(new) and same_site):
                return GateResult(
                    gate=self.name,
                    action=GateAction.suppress,
                    reason_code="REFRESH_NOT_ADDITIVE",
                    features={"op_id": op.op_id},
                )
        return GateResult(gate=self.name, action=GateAction.pass_)


@quality_gate_registry.register("MinimalEdit")
class MinimalEdit:
    """at most three small changes, three new keywords and three hashtags per video."""

    name: ClassVar[str] = "MinimalEdit"
    phase: ClassVar[str] = "pre_writer"
    applies_to: ClassVar[frozenset[RecType]] = APPLIES_TO
    order: ClassVar[int] = 16

    def check(self, draft: RecommendationDraft, ctx: Any = None, written: Any = None) -> GateResult:
        subject = _subject(draft)
        if subject is None:
            return GateResult(
                gate=self.name, action=GateAction.suppress, reason_code="REFRESH_SUBJECT_MISSING"
            )
        ops = subject.operations
        inserted = sum(len(op.text) for op in ops if op.kind is InsertionKind.insert)
        keywords = {normalise(keyword) for op in ops for keyword in op.keywords_added}
        hashtags = {tag.casefold() for op in ops for tag in op.hashtags_added}
        features = {
            "ops": len(ops),
            "inserted_chars": inserted,
            "keywords": len(keywords),
            "hashtags": len(hashtags),
        }
        too_big = (
            len(ops) > MAX_OPERATIONS
            or inserted > MAX_INSERTED_CHARS
            or len(keywords) > MAX_NEW_KEYWORDS
            or len(hashtags) > MAX_NEW_HASHTAGS
        )
        if too_big:
            return GateResult(
                gate=self.name,
                action=GateAction.suppress,
                reason_code="REFRESH_NOT_MINIMAL",
                features=features,
            )
        return GateResult(gate=self.name, action=GateAction.pass_, features=features)
