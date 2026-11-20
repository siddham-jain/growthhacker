from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from app.core.registry import Registry
from app.domain.recommendations import EvidenceNumber, EvidenceSource, Reason

TEXT_PARAM_MAX_CHARS = 110
STATEMENT_MAX_CHARS = 160

PARAM_UNITS: dict[str, str] = {
    "k": "count",
    "n": "count",
    "count": "count",
    "covering": "count",
    "total": "count",
    "n_with": "videos",
    "n_total": "videos",
    "n_outliers_with": "videos",
    "n_outliers_total": "videos",
    "uploads": "videos",
    "creator_uploads": "videos",
    "competitor_uploads": "videos",
    "topics": "count",
    "competitor_citations": "count",
    "client_citations": "count",
    "window": "days",
    "gap_days": "days",
    "days": "days",
    "ratio": "ratio_x",
    "median_ratio": "ratio_x",
    "lift": "ratio_x",
    "value": "count",
    "median": "count",
}


@dataclass(frozen=True, slots=True)
class ReasonTemplate:
    code: str
    template: str
    priority: int


reason_catalogue: Registry[ReasonTemplate] = Registry("reason_catalogue")


def register_reason(code: str, template: str, priority: int) -> None:
    reason_catalogue.add(code, ReasonTemplate(code=code, template=template, priority=priority))


for _code, _template, _priority in (
    ("COMPETITORS_COVER_CLIENT_DOESNT", "{covering} of {total} competitors cover this; you haven't", 10),
    ("CLIENT_STALE_COVERAGE", "You last covered this on {date}", 12),
    ("CLIENT_UNDERPERFORMING", "Your {n} videos on this peaked at {ratio}x your baseline", 13),
    ("ANSWERS_BUYER_QUESTION", "{k} of {n} reference videos directly answer this buyer question", 20),
    ("BUYER_QUESTION_MATCH", "Answers your tracked buyer question: {question}", 21),
    ("KEYWORD_MATCH", "Matches your keyword: {keyword}", 22),
    (
        "AI_CITES_COMPETITOR",
        "AI answers to this question cite {competitor_citations} competitor videos and "
        "{client_citations} of yours (Spotlit AI tracking)",
        23,
    ),
    (
        "FORMAT_LIFT",
        "{trait_label}: {n_outliers_with} of {n_outliers_total} outliers vs {n_with} of {n_total} "
        "videos overall",
        30,
    ),
    (
        "TRAIT_LIFT",
        "{trait_label}: {n_outliers_with} of {n_outliers_total} outliers vs {n_with} of {n_total} "
        "videos overall",
        30,
    ),
    ("OUTLIER_SHARE", "{k} of {n} outliers in the last {window} days cover {topic}", 40),
    (
        "MEDIAN_RATIO",
        "Those outliers ran a median {median_ratio}x their own channel's normal ({n} videos)",
        41,
    ),
    ("TOPIC_SUPPORT", "{k} of {n} supporting outliers match your topics", 42),
    ("MIXED_EVIDENCE", "Evidence is mixed: holds for {scope_a}, not for {scope_b}", 45),
    ("SINGLE_CHANNEL", "Only one channel has done this so far ({n} videos), so treat it as a lead", 46),
    ("OWN_FORMAT_RESULT", "Your {label} run a median {ratio}x your baseline ({n} videos)", 50),
    ("OWN_TRAIT_RESULT", "Your videos with {label} run a median {ratio}x your baseline ({n} videos)", 50),
    (
        "OWN_SUBS_PER_1K",
        "Your similar videos gained {value} subscribers per 1k views vs {median} median "
        "(your YouTube Analytics)",
        51,
    ),
    ("CLIENT_TRAIT_USAGE", "{k} of your last {n} videos use {label}", 52),
    ("CLIENT_FORMAT_ABSENT", "None of your last {n} videos are {label}", 53),
    ("CLIENT_COVERED_DIFFERENT_FORMAT", "You covered this on {date} in a different format", 54),
    (
        "UNDERPERFORMING_TOPICS",
        "On {k} of your topics your other formats ran below {ratio}x your baseline",
        55,
    ),
    ("CREATOR_RELEVANCE", "{k} of {n} recent uploads match your topics ({topics} topics)", 20),
    ("CREATOR_ANSWERS_BQ", "{k} of their matching uploads directly answer your buyer questions", 21),
    (
        "CREATOR_PERFORMANCE",
        "Matching uploads ran a median {median_ratio}x their own normal ({n} videos)",
        40,
    ),
    ("CREATOR_CONSISTENCY", "{uploads} uploads in the last {days} days, a median {gap_days} days apart", 41),
    ("CREATOR_COLLAB", "{label}", 30),
    (
        "CREATOR_COLLAB_COVERAGE",
        "Checked {creator_uploads} recent uploads of this creator and "
        "{competitor_uploads} competitor uploads for collaborations",
        60,
    ),
    ("CREATOR_TOPIC_MATCH", "Covers your topics: {topics_text}", 22),
):
    register_reason(_code, _template, _priority)


def _display(value: int | float | str) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        return f"{value:.1f}" if abs(value - round(value, 1)) < 1e-9 else f"{value:.2f}"
    text = str(value)
    return text if len(text) <= TEXT_PARAM_MAX_CHARS else text[: TEXT_PARAM_MAX_CHARS - 1].rstrip() + "…"


def _numbers(params: dict[str, int | float | str]) -> list[EvidenceNumber]:
    numbers = []
    for name, value in params.items():
        if isinstance(value, bool) or not isinstance(value, int | float):
            continue
        numbers.append(
            EvidenceNumber(value=float(value), unit=PARAM_UNITS.get(name, "count"), display=_display(value))
        )
    return numbers


def make_reason(
    reason_id: str,
    code: str,
    params: dict[str, int | float | str | date],
    *,
    ref_video_ids: Iterable[str] = (),
    source: EvidenceSource = EvidenceSource.youtube_public,
) -> Reason:
    template = reason_catalogue.get(code)
    clean = {
        name: (value.isoformat() if isinstance(value, date) else value) for name, value in params.items()
    }
    statement = template.template.format(**{name: _display(value) for name, value in clean.items()})
    if len(statement) > STATEMENT_MAX_CHARS:
        statement = statement[: STATEMENT_MAX_CHARS - 1].rstrip() + "…"
    return Reason(
        reason_id=reason_id,
        code=code,
        params=clean,
        statement=statement,
        numbers=_numbers(clean),
        ref_video_ids=sorted(set(ref_video_ids)),
        source=source,
    )


def priority(reason: Reason) -> int:
    if reason.code in reason_catalogue:
        return reason_catalogue.get(reason.code).priority
    return 100


def select_reasons(reasons: list[Reason], limit: int, prefix: str) -> list[Reason]:
    # code picks the reasons in a fixed priority order and renumbers them r1.. / f1..
    chosen = sorted(reasons, key=lambda reason: (priority(reason), reason.code))[:limit]
    return [
        reason.model_copy(update={"reason_id": f"{prefix}{index}"}) for index, reason in enumerate(chosen, 1)
    ]
