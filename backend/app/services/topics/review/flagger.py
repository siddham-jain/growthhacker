from collections.abc import Sequence
from typing import Any

from app.domain.topics import TopicMatch

IMPACT_DEFAULT = 1.0
IMPACT_OUTLIER = 2.0
IMPACT_CITED = 3.0


def uncertainty(combined_score: float) -> float:
    return max(0.0, 1.0 - abs(combined_score - 0.5) * 2)


class ReviewFlagger:
    """turns flagged matches into review items; priority is impact x uncertainty."""

    def items(self, matches: Sequence[TopicMatch]) -> list[dict[str, Any]]:
        items = []
        for match in matches:
            if not match.review_reasons:
                continue
            items.append(
                {
                    "video_id": match.video_id,
                    "topic_ref_id": match.topic_ref_id,
                    "reasons": [reason.value for reason in match.review_reasons],
                    "priority": round(IMPACT_DEFAULT * max(uncertainty(match.combined_score), 0.05), 4),
                    "status": "open",
                }
            )
        return items
