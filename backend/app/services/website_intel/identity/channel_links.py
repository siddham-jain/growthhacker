from collections.abc import Sequence
from dataclasses import dataclass, field

from app.domain.youtube import ChannelRef
from app.services.website_intel.extract.structured import is_youtube_url, same_as_urls
from app.services.website_intel.models import ExtractedPage, LinkRegion
from app.services.youtube.input_parser import InputParser
from app.services.youtube.ownership import BusinessHint

WEIGHT_SAME_AS = 5
WEIGHT_CHROME_LINK = 4
WEIGHT_BODY_LINK = 2
WEIGHT_EMBED = 1
MAX_EMBED_POINTS = 3
CHANNEL_KINDS = frozenset({"channel_id", "handle", "username", "custom_url"})


@dataclass
class ChannelCandidate:
    ref: ChannelRef
    score: int = 0
    signals: list[str] = field(default_factory=list)
    urls: list[str] = field(default_factory=list)


@dataclass
class SiteChannelSignals:
    candidates: list[ChannelCandidate]
    anchor_links: list[str]
    same_as_links: list[str]
    embedded_video_ids: list[str]

    def hint(self, *, name: str | None, domain: str | None, country: str | None) -> BusinessHint:
        return BusinessHint(
            name=name,
            website_domain=domain,
            country=country,
            site_channel_links=self.anchor_links,
            site_sameas_links=self.same_as_links,
        )


class ChannelLinkCollector:
    """collects youtube references on the site and scores them per plan 03 §5.2."""

    def __init__(self, parser: InputParser | None = None) -> None:
        self._parser = parser or InputParser()

    def collect(self, pages: Sequence[ExtractedPage]) -> SiteChannelSignals:
        by_key: dict[str, ChannelCandidate] = {}
        anchors: list[str] = []
        same_as: list[str] = []
        videos: list[str] = []

        def credit(url: str, weight: int, signal: str) -> None:
            ref = self._parser.parse(url)
            if ref.kind == "video":
                if ref.value not in videos:
                    videos.append(ref.value)
                return
            if ref.kind not in CHANNEL_KINDS:
                return
            key = f"{ref.kind}:{ref.value.casefold()}"
            candidate = by_key.setdefault(key, ChannelCandidate(ref=ref))
            candidate.score += weight
            candidate.signals.append(signal)
            if url not in candidate.urls:
                candidate.urls.append(url)

        for page in pages:
            for url in same_as_urls(page.page.json_ld):
                if is_youtube_url(url):
                    same_as.append(url)
                    credit(url, WEIGHT_SAME_AS, "json_ld_same_as")
            for link in page.details.links:
                if not is_youtube_url(link.url):
                    continue
                chrome = link.region in {LinkRegion.header, LinkRegion.footer, LinkRegion.nav}
                anchors.append(link.url)
                credit(
                    link.url, WEIGHT_CHROME_LINK if chrome else WEIGHT_BODY_LINK, f"{link.region.value}_link"
                )
            for embed in page.details.youtube_embeds:
                credit(embed, WEIGHT_EMBED, "embed")
        ranked = sorted(by_key.values(), key=lambda candidate: -candidate.score)
        return SiteChannelSignals(
            candidates=ranked,
            anchor_links=list(dict.fromkeys(anchors)),
            same_as_links=list(dict.fromkeys(same_as)),
            embedded_video_ids=videos,
        )
