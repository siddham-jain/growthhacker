import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

from app.domain.recommendations import CollaborationEvidence
from app.domain.youtube import ChannelRecord, VideoRecord
from app.services.recommend.context import CompetitorInfo

MatchedOn = Literal["handle", "channel_url", "custom_url", "website_domain", "brand_name"]


@dataclass(frozen=True, slots=True)
class Token:
    text: str
    matched_on: MatchedOn
    pattern: re.Pattern[str]


def _token(text: str, matched_on: MatchedOn) -> Token:
    escaped = re.escape(text.lower())
    if matched_on == "handle":
        pattern = rf"(?<![\w@/]){escaped}(?![\w.-])"
    else:
        pattern = rf"(?<![\w]){escaped}(?![\w-])"
    return Token(text=text, matched_on=matched_on, pattern=re.compile(pattern))


def _handle(handle: str | None) -> str | None:
    if not handle:
        return None
    return handle if handle.startswith("@") else f"@{handle}"


def channel_tokens(channel_id: str, handle: str | None) -> list[Token]:
    tokens = [_token(f"youtube.com/channel/{channel_id}", "channel_url")]
    normalised = _handle(handle)
    if normalised:
        tokens.append(_token(normalised, "handle"))
        tokens.append(_token(f"youtube.com/{normalised}", "custom_url"))
    return tokens


def competitor_tokens(competitor: CompetitorInfo) -> tuple[list[Token], list[Token]]:
    # strong tokens identify the competitor alone; brand names are ambiguous ("apollo" tyres) and need context
    strong = channel_tokens(competitor.channel_id, competitor.handle)
    if competitor.domain:
        strong.append(_token(competitor.domain.removeprefix("www."), "website_domain"))
    brands = [_token(alias, "brand_name") for alias in competitor.aliases if len(alias) >= 3]
    return strong, brands


class CollaborationDetector:
    """labels public collaboration evidence from cached titles and descriptions; nothing is inferred."""

    def __init__(self, sponsor_phrases: Sequence[str], niche_keywords: Iterable[str]) -> None:
        self.sponsor_phrases = [phrase.lower() for phrase in sponsor_phrases]
        self.niche_keywords = sorted({keyword.lower() for keyword in niche_keywords if keyword.strip()})

    def detect(
        self,
        creator: ChannelRecord,
        creator_uploads: Sequence[VideoRecord],
        competitors: Sequence[CompetitorInfo],
        competitor_uploads: Sequence[VideoRecord],
    ) -> list[CollaborationEvidence]:
        found: list[CollaborationEvidence] = []
        creator_side = channel_tokens(creator.channel_id, creator.handle)
        by_id = {competitor.channel_id: competitor for competitor in competitors}
        for video in competitor_uploads:
            competitor = by_id.get(video.channel_id)
            hit = self._first_hit(_text(video), creator_side) if competitor else None
            if competitor and hit:
                found.append(self._evidence("mentioned_by_competitor", competitor, video, hit))
        for video in creator_uploads:
            text = _text(video)
            for competitor in competitors:
                strong, brands = competitor_tokens(competitor)
                hit = self._first_hit(text, strong)
                if hit is None and self._has_niche_keyword(text):
                    hit = self._first_hit(text, brands)
                if hit is None:
                    continue
                found.append(self._evidence(self._kind(video, text, hit), competitor, video, hit))
        return sorted(
            found,
            key=lambda item: (item.published_at, item.video_id, item.competitor_channel_id),
            reverse=True,
        )

    def _kind(self, video: VideoRecord, text: str, hit: Token) -> str:
        if video.has_paid_product_placement:
            return "disclosed_paid_promotion"
        for line in text.splitlines():
            if hit.pattern.search(line) and any(phrase in line for phrase in self.sponsor_phrases):
                return "sponsor_language"
        return "mentions_competitor"

    def _has_niche_keyword(self, text: str) -> bool:
        return any(keyword in text for keyword in self.niche_keywords)

    @staticmethod
    def _first_hit(text: str, tokens: Sequence[Token]) -> Token | None:
        return next((token for token in tokens if token.pattern.search(text)), None)

    @staticmethod
    def _evidence(
        kind: str, competitor: CompetitorInfo, video: VideoRecord, hit: Token
    ) -> CollaborationEvidence:
        return CollaborationEvidence(
            kind=kind,
            competitor_channel_id=competitor.channel_id,
            competitor_name=competitor.name,
            video_id=video.video_id,
            published_at=video.published_at,
            matched_on=hit.matched_on,
            matched_token=hit.text,
        )


def _text(video: VideoRecord) -> str:
    return f"{video.title}\n{video.description}".lower()


def collaboration_label(item: CollaborationEvidence) -> str:
    day = item.published_at.date().isoformat()
    if item.kind == "disclosed_paid_promotion":
        return f"Disclosed paid promotion on {day} in a video that mentions {item.competitor_name}"
    if item.kind == "sponsor_language":
        return (
            f"Mentions {item.competitor_name} with sponsor wording on {day} (not YouTube's disclosure flag)"
        )
    if item.kind == "mentioned_by_competitor":
        return f"Mentioned by {item.competitor_name} on {day}"
    return f"Mentions {item.competitor_name} on {day}"
