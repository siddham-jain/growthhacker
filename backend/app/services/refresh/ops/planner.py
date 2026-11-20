import hashlib

from app.domain.refresh import (
    ClaimLevel,
    InsertionAnchor,
    InsertionKind,
    InsertionOp,
    InsertionPosition,
)
from app.services.refresh.inputs import RefreshClientOptions
from app.services.refresh.ops.templates import TemplateLine
from app.services.refresh.structure import DescriptionStructure
from app.services.refresh.triggers.base import Finding, TriggerKind

ANCHOR_CHARS = 80
POSITION_BY_KIND: dict[TriggerKind, InsertionPosition] = {
    TriggerKind.above_fold_keyword: InsertionPosition.top_line,
    TriggerKind.news_entity: InsertionPosition.after_first_paragraph,
    TriggerKind.buyer_question_gap: InsertionPosition.after_first_paragraph,
    TriggerKind.stale_year: InsertionPosition.after_first_paragraph,
    TriggerKind.missing_cta: InsertionPosition.before_links_block,
    TriggerKind.missing_contact: InsertionPosition.before_links_block,
    TriggerKind.missing_hashtags: InsertionPosition.append,
}
POSITION_ORDER = list(InsertionPosition)


def _end_index(structure: DescriptionStructure) -> int:
    lines = structure.lines
    return len(lines) - 1 if len(lines) > 1 and lines[-1] == "" else len(lines)


def line_index_for(position: InsertionPosition, structure: DescriptionStructure) -> int:
    if structure.is_empty:
        return 0
    if position is InsertionPosition.top_line:
        return 0
    if position is InsertionPosition.after_first_paragraph:
        if structure.first_paragraph_end_line is None:
            return line_index_for(InsertionPosition.top_line, structure)
        return structure.first_paragraph_end_line + 1
    if position is InsertionPosition.before_links_block:
        if structure.links_block_start_line is not None:
            return structure.links_block_start_line
        return line_index_for(InsertionPosition.before_hashtag_line, structure)
    if position is InsertionPosition.before_hashtag_line and structure.hashtag_line is not None:
        return structure.hashtag_line
    return _end_index(structure)


class OperationPlanner:
    """places each line deterministically: keywords above the fold, links beside links, never past a disclaimer."""

    def __init__(self, above_fold_chars: int) -> None:
        self.above_fold_chars = above_fold_chars

    def place(
        self, finding: Finding, structure: DescriptionStructure, options: RefreshClientOptions
    ) -> tuple[InsertionPosition, int]:
        position = POSITION_BY_KIND[finding.kind]
        if finding.kind is TriggerKind.news_entity and options.news_on_top:
            position = InsertionPosition.top_line
        if structure.only_boilerplate and position is not InsertionPosition.append:
            position = InsertionPosition.top_line
        index = line_index_for(position, structure)
        if finding.keyword_trigger and self._offset(structure, index) > self.above_fold_chars:
            position, index = InsertionPosition.top_line, 0
        disclaimer = structure.disclaimer_line
        if disclaimer is not None and index > disclaimer and position is not InsertionPosition.append:
            index = disclaimer
        return position, index

    @staticmethod
    def _offset(structure: DescriptionStructure, index: int) -> int:
        return min(structure.line_offset(index), len(structure.text))

    def build(
        self,
        finding: Finding,
        line: TemplateLine,
        phrased_by: str,
        structure: DescriptionStructure,
        options: RefreshClientOptions,
    ) -> InsertionOp:
        if line.kind is InsertionKind.link_fix:
            start = int(finding.params["start"])
            anchor = self._anchor(structure, start, "url_span", self._line_at(structure, start))
            position = InsertionPosition.before_links_block
        else:
            position, index = self.place(finding, structure, options)
            offset = self._offset(structure, index)
            previous = structure.lines[index - 1] if index > 0 else ""
            anchor = self._anchor(structure, offset, "line_boundary", previous)
        op_id = hashlib.sha1(f"{finding.trigger_id}|{line.text}".encode()).hexdigest()[:12]
        return InsertionOp(
            op_id=f"op_{op_id}",
            kind=line.kind,
            position=position,
            anchor=anchor,
            old_text=line.old_text,
            text=line.text,
            text_bytes=len(line.text.encode("utf-8")),
            keywords_added=list(line.keywords_added),
            hashtags_added=list(line.hashtags_added),
            claim_level=finding.claim_level or ClaimLevel.l0_navigational,
            trigger_ids=[finding.trigger_id],
            phrased_by="llm" if phrased_by == "llm" else "template",
        )

    @staticmethod
    def _line_at(structure: DescriptionStructure, offset: int) -> str:
        return structure.lines[structure.text.count(structure.newline, 0, offset)]

    @staticmethod
    def _anchor(structure: DescriptionStructure, offset: int, strategy: str, line: str) -> InsertionAnchor:
        return InsertionAnchor(
            strategy=strategy,
            anchor_excerpt=structure.text[max(0, offset - ANCHOR_CHARS) : offset],
            anchor_hash=hashlib.sha256(line.encode("utf-8")).hexdigest(),
            char_offset_hint=offset,
        )
