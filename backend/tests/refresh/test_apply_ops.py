import hashlib

import pytest

from app.domain.refresh import ClaimLevel, InsertionAnchor, InsertionKind, InsertionOp, InsertionPosition
from app.services.refresh.ops.apply import AnchorNotFound, apply_ops, verify_additive
from app.services.refresh.ops.planner import ANCHOR_CHARS, line_index_for
from app.services.refresh.structure import parse_description
from app.services.refresh.text import KeywordPresence


def op_at(description: str, line_index: int, text: str, kind: InsertionKind = InsertionKind.insert, old=None):
    structure = parse_description(description)
    offset = min(structure.line_offset(line_index), len(description))
    return InsertionOp(
        op_id=f"op_{line_index}_{len(text)}",
        kind=kind,
        position=InsertionPosition.after_first_paragraph,
        anchor=InsertionAnchor(
            strategy="line_boundary",
            anchor_excerpt=description[max(0, offset - ANCHOR_CHARS) : offset],
            anchor_hash=hashlib.sha256(b"").hexdigest(),
            char_offset_hint=offset,
        ),
        old_text=old,
        text=text,
        text_bytes=len(text.encode()),
        claim_level=ClaimLevel.l0_navigational,
        phrased_by="template",
    )


def link_fix(description: str, old: str, new: str) -> InsertionOp:
    start = description.index(old)
    return InsertionOp(
        op_id="op_fix",
        kind=InsertionKind.link_fix,
        position=InsertionPosition.before_links_block,
        anchor=InsertionAnchor(
            strategy="url_span",
            anchor_excerpt=description[max(0, start - ANCHOR_CHARS) : start],
            anchor_hash="h",
            char_offset_hint=start,
        ),
        old_text=old,
        text=new,
        text_bytes=len(new.encode()),
        claim_level=ClaimLevel.l0_navigational,
        phrased_by="template",
    )


DESCRIPTION = "First line.\nStill first paragraph.\n\nSecond paragraph 🙂\n----------\nFooter"


@pytest.mark.parametrize(
    ("position", "expected_index"),
    [
        (InsertionPosition.top_line, 0),
        (InsertionPosition.after_first_paragraph, 2),
        (InsertionPosition.before_links_block, 4),
        (InsertionPosition.before_hashtag_line, 6),
        (InsertionPosition.append, 6),
    ],
)
def test_positions_resolve_to_line_boundaries(position: InsertionPosition, expected_index: int) -> None:
    assert line_index_for(position, parse_description(DESCRIPTION)) == expected_index


def test_insert_keeps_every_original_byte() -> None:
    ops = [
        op_at(DESCRIPTION, 2, "New line A"),
        op_at(DESCRIPTION, 0, "Top line"),
        op_at(DESCRIPTION, 6, "End"),
    ]
    after = apply_ops(DESCRIPTION, ops)
    assert after == (
        "Top line\nFirst line.\nStill first paragraph.\nNew line A\n\nSecond paragraph 🙂\n----------\nFooter\nEnd"
    )
    assert verify_additive(DESCRIPTION, after, ops) == []


def test_crlf_and_trailing_newline_are_preserved() -> None:
    description = "One\r\nTwo\r\n"
    ops = [op_at(description, 1, "Inserted"), op_at(description, 2, "Last")]
    after = apply_ops(description, ops)
    assert after == "One\r\nInserted\r\nTwo\r\nLast\r\n"
    assert verify_additive(description, after, ops) == []


def test_empty_description_gets_plain_lines() -> None:
    ops = [op_at("", 0, "A"), op_at("", 0, "B")]
    assert apply_ops("", ops) == "A\nB"


def test_link_fix_swaps_only_the_broken_span() -> None:
    bad = "https://www.facebook.com/https://www.facebook.com/TheApolloHospitals/"
    good = "https://www.facebook.com/TheApolloHospitals/"
    description = f"Body\n\nFacebook: {bad}\nX: https://x.com/a"
    ops = [link_fix(description, bad, good), op_at(description, 2, "Book: https://example.com/book")]
    after = apply_ops(description, ops)
    assert after == f"Body\n\nBook: https://example.com/book\nFacebook: {good}\nX: https://x.com/a"
    assert verify_additive(description, after, ops) == []


def test_reanchors_when_text_above_changed() -> None:
    op = op_at(DESCRIPTION, 2, "New line")
    edited = "A brand new opening.\n" + DESCRIPTION
    after = apply_ops(edited, [op])
    assert after.splitlines()[3] == "New line"


def test_missing_anchor_raises() -> None:
    op = op_at(DESCRIPTION, 2, "New line")
    with pytest.raises(AnchorNotFound):
        apply_ops("Completely different text", [op])


def test_rewrites_and_removals_are_detected() -> None:
    op = op_at(DESCRIPTION, 0, "Top line")
    rewritten = "Top line\nFirst line, now with keywords.\nStill first paragraph.\n\nSecond paragraph 🙂"
    assert "original text was changed or removed" in verify_additive(DESCRIPTION, rewritten, [op])
    padded = apply_ops(DESCRIPTION, [op]) + "\nsneaky extra"
    assert "text other than the proposed lines was added" in verify_additive(DESCRIPTION, padded, [op])


def test_multi_line_inserts_are_rejected_by_the_contract() -> None:
    with pytest.raises(ValueError, match="single line"):
        op_at(DESCRIPTION, 0, "line one\nline two")


hypothesis = pytest.importorskip("hypothesis")
strategies = pytest.importorskip("hypothesis.strategies")

LINE = strategies.text(
    alphabet=strategies.characters(blacklist_characters="\r\n", blacklist_categories=("Cs",)), max_size=40
)


@hypothesis.settings(max_examples=150, deadline=None)
@hypothesis.given(
    lines=strategies.lists(LINE, max_size=8),
    crlf=strategies.booleans(),
    picks=strategies.lists(strategies.integers(min_value=0, max_value=8), min_size=1, max_size=3),
)
def test_apply_ops_is_always_additive(lines: list[str], crlf: bool, picks: list[int]) -> None:
    description = ("\r\n" if crlf else "\n").join(lines)
    structure = parse_description(description)
    ops = []
    for index, pick in enumerate(picks):
        line_index = min(pick, len(structure.lines))
        ops.append(op_at(description, line_index, f"Claude Fable 5.2 note {index}"))
    try:
        after = apply_ops(description, ops)
    except AnchorNotFound:
        return
    assert verify_additive(description, after, ops) == []
    assert KeywordPresence(after).contains("Claude Fable 5.2")
    assert len(after.encode()) == len(description.encode()) + sum(
        len(op.text.encode()) + len(structure.newline.encode()) for op in ops
    ) - (len(structure.newline.encode()) if not description and ops else 0)
