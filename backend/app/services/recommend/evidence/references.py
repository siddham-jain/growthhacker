from collections.abc import Collection

from app.domain.recommendations import (
    MAX_DISPLAYED_REFERENCES,
    RecommendationDraft,
    ReferenceVideo,
    VideoIdeaSubject,
)


class ReferenceSelector:
    """picks the 3..5 displayed references from the ranked pool, spreading them across channels."""

    def __init__(self, display: int = MAX_DISPLAYED_REFERENCES) -> None:
        self.display = display

    def select(self, draft: RecommendationDraft, exclude: Collection[str] = ()) -> list[ReferenceVideo]:
        pool = [ref for ref in draft.reference_pool if ref.video_id not in exclude]
        preferred = [ref for ref in pool if self._preferred(draft, ref)]
        rest = [ref for ref in pool if not self._preferred(draft, ref)]
        chosen: list[ReferenceVideo] = []
        channels: set[str] = set()
        for ref in preferred:
            if len(chosen) == self.display:
                break
            if ref.channel_id not in channels:
                chosen.append(ref)
                channels.add(ref.channel_id)
        for ref in preferred + rest:
            if len(chosen) == self.display:
                break
            if ref not in chosen:
                chosen.append(ref)
        order = {ref.video_id: position for position, ref in enumerate(draft.reference_pool)}
        return sorted(chosen, key=lambda ref: order[ref.video_id])

    @staticmethod
    def _preferred(draft: RecommendationDraft, ref: ReferenceVideo) -> bool:
        subject = draft.subject
        if isinstance(subject, VideoIdeaSubject) and subject.format_key is not None:
            return ref.format_key == subject.format_key
        return True
