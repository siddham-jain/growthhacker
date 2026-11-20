import html

from app.models.evaluation import REVIEW_CRITERIA

# anchored 1-3-5 descriptions (12 §5.3); 2 and 4 sit between the anchors
RUBRIC: dict[str, tuple[str, str, str, str]] = {
    "specific": (
        "Specific",
        'generic advice ("make engaging videos")',
        "topic named, but format or angle vague",
        "names topic, angle, format/length and title/thumbnail direction",
    ),
    "evidenced": (
        "Evidenced",
        "references missing, stale or unrelated",
        "references valid but weakly connected to the claim",
        "3 or more recent, relevant references whose numbers clearly support the claim",
    ),
    "relevant": (
        "Relevant to the client",
        "does not fit this business or its buyers",
        "fits the niche, not this client's offerings",
        "ties directly to a tracked buyer question or offering",
    ),
    "actionable": (
        "Actionable",
        "cannot brief a creator from it",
        "needs significant extra thinking",
        "could brief a creator today",
    ),
    "not_obvious": (
        "Not obvious",
        "something the client already does or anyone would say",
        "somewhat new",
        "a non-obvious, evidenced gap or angle",
    ),
}
if tuple(RUBRIC) != REVIEW_CRITERIA:
    raise RuntimeError("the rubric must cover exactly the review_rating criteria")

INSTRUCTIONS = (
    "Read the client sheet first. Rate every card on the five criteria from 1 to 5 using the anchors, say "
    "whether you would ship it, and add a comment if something is wrong. Rate on your own; do not compare "
    "notes with the other rater until both packs are exported."
)

# criterion -> the gate or writer knobs a low score points at (proposals only; a human approves)
KNOBS: dict[str, str] = {
    "evidenced": "raise min_reference_videos / min_ref_outlier_ratio or the minimum confidence label; "
    "require reference topic match score above the acceptance threshold",
    "specific": "writer schema must fill format, length band and title direction; gate cards whose summary "
    "lacks a format or length token",
    "relevant": "raise 05's topic acceptance threshold for evidence; require buyer_question_id for the "
    "conversion lens",
    "actionable": "require a non-empty spec for format plus a title or thumbnail pattern",
    "not_obvious": "novelty gate: reject when the client's last 90 days hold 2+ matching videos or the spec "
    "equals the copy-top spec",
}


def rubric_markdown() -> str:
    rows = [f"| {label} | {one} | {three} | {five} |" for label, one, three, five in RUBRIC.values()]
    lines = [
        "# Human review rubric",
        "",
        "Generated from `app/evaluation/review/rubric.py`; the review pack embeds the same text.",
        "",
        INSTRUCTIONS,
        "",
        "| Criterion | 1 | 3 | 5 |",
        "|---|---|---|---|",
        *rows,
        "",
        'Also collected: "would you ship this card?" (yes/no) and an optional comment.',
        "",
        "## Pass rule",
        "",
        "- Overall mean (mean over items of the mean over raters and criteria) at least 4.0, and every "
        "criterion mean at least 3.5. The lower 95% bootstrap bound and the share of would-ship (target 70%) "
        "are reported too.",
        "- Six blinded decoy cards (naive copy-the-most-viewed-video ideas) are mixed in. If real cards beat "
        "them by less than 0.3, the ratings are flagged as not discriminating.",
        "- Agreement: Krippendorff's alpha (ordinal) per criterion; below 0.667 triggers a calibration "
        "discussion.",
        "",
        "## What low scores change (proposals only)",
        "",
        *[f"- **{RUBRIC[criterion][0]}**: {knob}." for criterion, knob in KNOBS.items()],
    ]
    return "\n".join(lines) + "\n"


def rubric_html() -> str:
    rows = "".join(
        f"<tr><td><b>{html.escape(label)}</b></td><td>{html.escape(one)}</td><td>{html.escape(three)}</td>"
        f"<td>{html.escape(five)}</td></tr>"
        for label, one, three, five in RUBRIC.values()
    )
    return (
        f"<p>{html.escape(INSTRUCTIONS)}</p><table><tr><th>criterion</th><th>1</th><th>3</th><th>5</th></tr>"
        f"{rows}</table>"
    )
