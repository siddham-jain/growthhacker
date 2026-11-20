from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

import regex
import tldextract
from pydantic import BaseModel

from app.core.config import YouTubeOwnershipSettings
from app.core.registry import Registry
from app.domain.youtube import ChannelRecord, OwnershipLabel, OwnershipSignal, OwnershipVerdict
from app.services.youtube.input_parser import InputParser
from app.services.youtube.store import YouTubeStore

LINK_SIGNALS = frozenset({"site_links_channel", "jsonld_sameas", "embed_majority", "channel_links_site"})
DOMAIN = regex.compile(r"(?i)\b(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}\b")
UNOFFICIAL = regex.compile(r"(?i)\b(fan ?(page|channel|made)|unofficial|not affiliated|re-?upload(s|ed)?)\b")
_EXTRACT = tldextract.TLDExtract(suffix_list_urls=())
_PARSER = InputParser()


def registrable_domain(value: str) -> str | None:
    extracted = _EXTRACT(value.strip().lower())
    return extracted.top_domain_under_public_suffix or None


def domains_in(text: str) -> set[str]:
    return {domain for match in DOMAIN.findall(text or "") if (domain := registrable_domain(match))}


class BusinessHint(BaseModel):
    """what is known about the business; site-side evidence comes from the website crawl (03)."""

    name: str | None = None
    website_domain: str | None = None
    country: str | None = None
    site_channel_links: list[str] = []
    site_sameas_links: list[str] = []
    embed_channel_ids: list[str] = []
    user_asserted_channel_id: str | None = None
    oauth_channel_id: str | None = None


@dataclass(frozen=True)
class OwnershipEvidence:
    channel: ChannelRecord
    hint: BusinessHint
    recent_descriptions: Sequence[str]


class OwnershipSignalProvider(Protocol):
    def signals(
        self, evidence: OwnershipEvidence, weights: YouTubeOwnershipSettings
    ) -> list[OwnershipSignal]: ...


ownership_signal_registry: Registry[OwnershipSignalProvider] = Registry("youtube_ownership_signals")


def link_points_to(link: str, channel: ChannelRecord) -> bool | None:
    """true if a site link names this channel, false if it names another channel, none if it is not a channel link."""
    ref = _PARSER.parse(link)
    if ref.kind == "channel_id":
        return ref.value == channel.channel_id
    if ref.kind in ("handle", "custom_url", "username"):
        handle = (channel.handle or "").lstrip("@").casefold()
        return bool(handle) and ref.value.casefold() == handle
    return None


@ownership_signal_registry.register("site_links_channel")
class SiteLinksChannel:
    # anchor and json-ld sameAs links are not additive; the stronger one counts
    def signals(
        self, evidence: OwnershipEvidence, weights: YouTubeOwnershipSettings
    ) -> list[OwnershipSignal]:
        hint, channel = evidence.hint, evidence.channel
        found = []
        for name, links in (
            ("site_links_channel", hint.site_channel_links),
            ("jsonld_sameas", hint.site_sameas_links),
        ):
            hits = [link for link in links if link_points_to(link, channel)]
            if hits:
                found.append(
                    OwnershipSignal(
                        name=name,
                        weight=weights.site_links_channel,
                        detail=f"official site links to {hits[0]}",
                        source_url=hits[0],
                    )
                )
        if found:
            return found[:1]
        others = [
            link
            for link in [*hint.site_channel_links, *hint.site_sameas_links]
            if link_points_to(link, channel) is False
        ]
        if others:
            return [
                OwnershipSignal(
                    name="site_links_other_channel",
                    weight=weights.site_links_other_channel,
                    detail=f"official site links to a different channel: {others[0]}",
                    source_url=others[0],
                )
            ]
        return []


@ownership_signal_registry.register("embed_majority")
class EmbedMajority:
    def signals(
        self, evidence: OwnershipEvidence, weights: YouTubeOwnershipSettings
    ) -> list[OwnershipSignal]:
        embeds = evidence.hint.embed_channel_ids
        own = sum(1 for channel_id in embeds if channel_id == evidence.channel.channel_id)
        if len(embeds) >= 2 and own / len(embeds) >= 0.5:
            return [
                OwnershipSignal(
                    name="embed_majority",
                    weight=weights.embed_majority,
                    detail=f"{own} of {len(embeds)} videos embedded on the site are from this channel",
                )
            ]
        return []


@ownership_signal_registry.register("channel_links_site")
class ChannelLinksSite:
    def signals(
        self, evidence: OwnershipEvidence, weights: YouTubeOwnershipSettings
    ) -> list[OwnershipSignal]:
        target = registrable_domain(evidence.hint.website_domain or "")
        if target and target in domains_in(evidence.channel.description):
            return [
                OwnershipSignal(
                    name="channel_links_site",
                    weight=weights.channel_links_site,
                    detail=f"channel description links to {target}",
                )
            ]
        return []


@ownership_signal_registry.register("uploads_link_site")
class UploadsLinkSite:
    def signals(
        self, evidence: OwnershipEvidence, weights: YouTubeOwnershipSettings
    ) -> list[OwnershipSignal]:
        target = registrable_domain(evidence.hint.website_domain or "")
        descriptions = list(evidence.recent_descriptions)[:50]
        if not target or not descriptions:
            return []
        linking = sum(1 for text in descriptions if target in domains_in(text))
        if linking / len(descriptions) >= weights.uploads_link_share:
            return [
                OwnershipSignal(
                    name="uploads_link_site",
                    weight=weights.uploads_link_site,
                    detail=f"{linking} of {len(descriptions)} recent uploads link to {target}",
                )
            ]
        return []


@ownership_signal_registry.register("user_asserted")
class UserAsserted:
    def signals(
        self, evidence: OwnershipEvidence, weights: YouTubeOwnershipSettings
    ) -> list[OwnershipSignal]:
        if evidence.hint.user_asserted_channel_id == evidence.channel.channel_id:
            return [
                OwnershipSignal(
                    name="user_asserted",
                    weight=weights.user_asserted,
                    detail="the user supplied this exact channel",
                )
            ]
        return []


@ownership_signal_registry.register("sanity")
class SanityPenalties:
    def signals(
        self, evidence: OwnershipEvidence, weights: YouTubeOwnershipSettings
    ) -> list[OwnershipSignal]:
        channel, hint = evidence.channel, evidence.hint
        found = []
        if channel.video_count < 5:
            found.append(
                OwnershipSignal(
                    name="few_videos",
                    weight=weights.few_videos_penalty,
                    detail=f"only {channel.video_count} public videos",
                )
            )
        if channel.country and hint.country and channel.country.upper() != hint.country.upper():
            found.append(
                OwnershipSignal(
                    name="country_mismatch",
                    weight=weights.country_mismatch_penalty,
                    detail=f"channel country {channel.country} differs from {hint.country}",
                )
            )
        if UNOFFICIAL.search(f"{channel.title}\n{channel.description}"):
            found.append(
                OwnershipSignal(
                    name="unofficial_wording",
                    weight=weights.unofficial_penalty,
                    detail="channel describes itself as unofficial, a fan page or re-uploads",
                )
            )
        return found


class OwnershipVerifier:
    """website <-> channel ownership from real links only; name similarity is never a signal."""

    def __init__(
        self,
        settings: YouTubeOwnershipSettings,
        store: YouTubeStore | None = None,
        providers: Registry[OwnershipSignalProvider] = ownership_signal_registry,
    ) -> None:
        self._settings = settings
        self._store = store
        self._providers = [
            provider() if isinstance(provider, type) else provider for provider in providers.all().values()
        ]

    async def _recent_descriptions(self, channel: ChannelRecord) -> list[str]:
        # only already-ingested uploads are read, so verification costs no quota
        if self._store is None:
            return []
        rows = await self._store.channel_videos(channel.channel_id)
        return [row.description or "" for row in rows[:50]]

    async def assess(self, channel: ChannelRecord, hint: BusinessHint) -> OwnershipVerdict:
        if hint.oauth_channel_id == channel.channel_id:
            signal = OwnershipSignal(name="oauth", weight=1.0, detail="owner signed in with this channel")
            return OwnershipVerdict(
                label=OwnershipLabel.verified, method="oauth", score=1.0, signals=[signal]
            )
        evidence = OwnershipEvidence(channel, hint, await self._recent_descriptions(channel))
        return self.verdict(
            [signal for p in self._providers for signal in p.signals(evidence, self._settings)]
        )

    def verdict(self, signals: list[OwnershipSignal]) -> OwnershipVerdict:
        weights = self._settings
        score = min(1.0, max(0.0, sum(signal.weight for signal in signals)))
        names = {signal.name for signal in signals}
        has_link = bool(names & LINK_SIGNALS)
        if score >= weights.verified_min and has_link:
            label, method = OwnershipLabel.verified, "link"
        elif score >= weights.likely_min:
            label, method = (
                OwnershipLabel.likely,
                "user" if "user_asserted" in names else ("link" if has_link else None),
            )
        elif score >= weights.uncertain_min:
            label, method = OwnershipLabel.uncertain, "user" if "user_asserted" in names else None
        else:
            label, method = OwnershipLabel.rejected, None
        return OwnershipVerdict(label=label, method=method, score=round(score, 4), signals=signals)
