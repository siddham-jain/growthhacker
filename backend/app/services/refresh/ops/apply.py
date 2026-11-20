from collections.abc import Sequence

from app.domain.refresh import InsertionKind, InsertionOp


class AnchorNotFound(ValueError):
    pass


def newline_of(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def _at_line_boundary(text: str, offset: int) -> bool:
    return offset == 0 or offset == len(text) or text[offset - 1] == "\n"


def resolve_offset(description: str, op: InsertionOp) -> int:
    """where an op lands in this description; re-anchors by excerpt when the text moved."""
    hint = op.anchor.char_offset_hint
    excerpt = op.anchor.anchor_excerpt
    if op.kind is InsertionKind.link_fix:
        old = op.old_text or ""
        if hint is not None and description.startswith(old, hint):
            return hint
        located = description.find(excerpt + old)
        if excerpt and located >= 0 and description.count(excerpt + old) == 1:
            return located + len(excerpt)
        if description.count(old) == 1:
            return description.find(old)
        raise AnchorNotFound(f"cannot find the link to fix: {old}")
    hint_fits = hint is not None and hint <= len(description) and description[:hint].endswith(excerpt)
    if hint is not None and hint_fits and _at_line_boundary(description, hint):
        return hint
    if not excerpt:
        return 0
    if description.count(excerpt) == 1:
        offset = description.find(excerpt) + len(excerpt)
        if _at_line_boundary(description, offset):
            return offset
    raise AnchorNotFound("the description changed around the insertion point")


def _insert_line(description: str, offset: int, line: str, newline: str) -> str:
    if not description:
        return line
    if offset > 0 and description[offset - 1] != "\n":
        # the last line had no newline, so the new line starts with one
        return description[:offset] + newline + line + description[offset:]
    return description[:offset] + line + newline + description[offset:]


def apply_ops(description: str, ops: Sequence[InsertionOp]) -> str:
    """pure: inserts whole lines at line boundaries and swaps exact broken-url spans; nothing else changes."""
    newline = newline_of(description)
    # applied back to front so earlier offsets stay valid; at a shared offset the url swap goes first
    placed = sorted(
        (
            (resolve_offset(description, op), op.kind is InsertionKind.link_fix, index, op)
            for index, op in enumerate(ops)
        ),
        key=lambda item: item[:3],
        reverse=True,
    )
    result = description
    for offset, _, _, op in placed:
        if op.kind is InsertionKind.link_fix:
            old = op.old_text or ""
            result = result[:offset] + op.text + result[offset + len(old) :]
        else:
            result = _insert_line(result, offset, op.text, newline)
    return result


def _with_link_fixes(before: str, ops: Sequence[InsertionOp]) -> tuple[str, list[str]]:
    # resolves each url span exactly as apply_ops does, so verify and apply never disagree on offsets
    fixes = []
    problems = []
    for op in ops:
        if op.kind is not InsertionKind.link_fix:
            continue
        try:
            fixes.append((resolve_offset(before, op), op))
        except AnchorNotFound:
            problems.append("a link fix replaces text that is not in the description")
    fixed = before
    for offset, op in sorted(fixes, key=lambda item: item[0], reverse=True):
        fixed = fixed[:offset] + op.text + fixed[offset + len(op.old_text or "") :]
    return fixed, problems


def verify_additive(before: str, after: str, ops: Sequence[InsertionOp]) -> list[str]:
    """the original must survive as an ordered subsequence of lines, with only the proposed lines added."""
    newline = newline_of(before) if before else newline_of(after)
    fixed, problems = _with_link_fixes(before, ops)
    original = fixed.split(newline) if fixed else []
    cursor = 0
    added = []
    for line in after.split(newline) if after else []:
        if cursor < len(original) and line == original[cursor]:
            cursor += 1
        else:
            added.append(line)
    if cursor != len(original):
        problems.append("original text was changed or removed")
    if sorted(added) != sorted(op.text for op in ops if op.kind is InsertionKind.insert):
        problems.append("text other than the proposed lines was added")
    return problems
