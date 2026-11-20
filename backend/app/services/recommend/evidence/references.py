from collections.abc import Collection

from app.domain.recommendations import (
    MAX_DISPLAYED_REFERENCES,
    RecommendationDraft,
    ReferenceVideo,
    VideoIdeaSubject,
)


class ReferenceSelector:
    """picks the 3..5 displayed references from the ranked pool: outliers first, spread across channels."""

    def __init__(self, display: int = MAX_DISPLAYED_REFERENCES) -> None:
        self.display = display

    def select(self, draft: RecommendationDraft, exclude: Collection[str] = ()) -> list[ReferenceVideo]:
        pool = [ref for ref in draft.reference_pool if ref.video_id not in exclude]
        preferred = [ref for ref in pool if self._preferred(draft, ref)]
        rest = [ref for ref in pool if not self._preferred(draft, ref)]
        # spreading never trades an outlier for a weaker video; a dominant channel lowers confidence instead
        groups = [
            [ref for ref in preferred if ref.is_outlier],
            [ref for ref in preferred if not ref.is_outlier],
            rest,
        ]
        chosen: list[ReferenceVideo] = []
        channels: set[str] = set()
        for group in groups:
            for spread in (True, False):
                for ref in group:
                    if len(chosen) == self.display:
                        break
                    if ref in chosen or (spread and ref.channel_id in channels):
                        continue
                    chosen.append(ref)
                    channels.add(ref.channel_id)
        order = {ref.video_id: position for position, ref in enumerate(draft.reference_pool)}
        return sorted(chosen, key=lambda ref: order[ref.video_id])

    @staticmethod
    def _preferred(draft: RecommendationDraft, ref: ReferenceVideo) -> bool:
        subject = draft.subject
        if isinstance(subject, VideoIdeaSubject) and subject.format_key is not None:
            return ref.format_key == subject.format_key
        return True
