from typing import Protocol

from app.evaluation.review.models import ClientCards, ReviewCard, ReviewReference, SourceCard


class DecoyCardBuilder(Protocol):
    def build(self, client: ClientCards, count: int) -> list[SourceCard]: ...


class CopyTopDecoyBuilder:
    """the naive 'copy the most-viewed recent video' baseline as a card. wording is template-built here,
    not written by the card writer, so the decoys share the visible fields but not the writer's voice."""

    def build(self, client: ClientCards, count: int) -> list[SourceCard]:
        cards = []
        for video in client.universe[:count]:
            reference = ReviewReference(
                title=video.title,
                channel=video.channel_title,
                url=f"https://www.youtube.com/watch?v={video.video_id}",
                published_on=video.published_at.date(),
                views_label=f"{video.views:,} views so far",
                thumbnail_url=video.thumbnail_url,
            )
            card = ReviewCard(
                client_name=client.sheet.name,
                type="video_idea",
                summary=f'Make a video like "{video.title}"'[:200],
                lens=("views",),
                why_it_works=(
                    f'"{video.title}" by {video.channel_title} has {video.views:,} views, the most of any '
                    f"tracked upload in the last {client.window_days} days."[:300],
                ),
                reference_videos=(reference,),
                fit_to_client="It is the most-viewed recent video among the channels you track.",
                confidence="Low · 1 outliers across 1 channels",
            )
            cards.append(
                SourceCard(
                    rec_id=None,
                    type="video_idea",
                    lens=("views",),
                    confidence_label="Low",
                    card=card,
                )
            )
        return cards
