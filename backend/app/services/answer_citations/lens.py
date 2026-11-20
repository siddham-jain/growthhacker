from collections.abc import Collection, Iterable
from typing import Literal

Owner = Literal["own", "competitor", "other"]
QuestionStatus = Literal["cites_you", "competitor_only", "others_only", "no_youtube"]
OWNER_ORDER: dict[Owner, int] = {"own": 0, "competitor": 1, "other": 2}


def owner_of(channel_id: str, own_channel_id: str | None, competitor_ids: Collection[str]) -> Owner:
    if own_channel_id is not None and channel_id == own_channel_id:
        return "own"
    return "competitor" if channel_id in competitor_ids else "other"


def question_status(owners: Iterable[Owner]) -> QuestionStatus:
    """conversion-lens reading of one answer: you are cited, only a competitor is (a gap), or neither."""
    present = set(owners)
    if "own" in present:
        return "cites_you"
    if "competitor" in present:
        return "competitor_only"
    return "others_only" if present else "no_youtube"
