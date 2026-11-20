from collections.abc import Mapping

from rapidfuzz import fuzz

from app.domain.topics import PreparedVideoText
from app.services.topics.text.normalize import normalize

MIN_QUOTE_CHARS = 3


def adjudication_fields(video: PreparedVideoText, description_chars: int, max_tags: int) -> dict[str, str]:
    """exactly the text the model is shown; quotes are verified against this and nothing else."""
    fields = video.fields
    return {
        "title": fields.title,
        "chapters": "\n".join(fields.chapters),
        "description": fields.description_head[:description_chars],
        "tags": ", ".join(fields.tags[:max_tags]),
        "hashtags": ", ".join(fields.hashtags),
    }


def verify_quote(
    quote: str, fields: Mapping[str, str], claimed: str | None, min_partial: float
) -> str | None:
    """the field the quote really comes from, or none when it cannot be found verbatim."""
    needle = normalize(quote)
    if len(needle) < MIN_QUOTE_CHARS:
        return None
    order = [claimed] if claimed in fields else []
    order += [name for name in fields if name != claimed]
    for name in order:
        haystack = normalize(fields[name])
        if needle in haystack:
            return name
    for name in order:
        haystack = normalize(fields[name])
        # partial_ratio aligns the shorter string, so a quote longer than the field never verifies
        if len(needle) <= len(haystack) and fuzz.partial_ratio(needle, haystack) >= min_partial:
            return name
    return None
