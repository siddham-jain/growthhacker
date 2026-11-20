import re
from dataclasses import dataclass
from typing import Protocol

from app.core.config import OwnershipWeights
from app.domain.youtube import OwnershipSignal
from app.services.competitors.identity.normalize import (
    compact,
    compact_ratio,
    normalize_name,
    token_set_ratio,
)
from app.services.competitors.model import CandidateEntity, ChannelLead
from app.services.competitors.registries import ownership_signal_registry

UNOFFICIAL = re.compile(
    r"\b(fan|fans|fanpage|unofficial|not affiliated|reupload|re-upload|parody|tribute|news about)\b",
    re.IGNORECASE,
)
HANDLE_NOISE = ("official", "videos", "video", "channel", "tv", "india", "hq")


@dataclass(frozen=True)
class SignalContext:
    weights: OwnershipWeights
    brand_countries: frozenset[str]
    uploads_link_share: float


class OwnershipSignalRule(Protocol):
    name: str

    def evaluate(
        self, lead: ChannelLead, entity: CandidateEntity, ctx: SignalContext
    ) -> OwnershipSignal | None: ...


def _evidence(lead: ChannelLead, name: str):
    return next((item for item in lead.evidence if item.name == name), None)


@ownership_signal_registry.register("site_link")
class SiteLinkRule:
    # an anchor and a json-ld sameAs are the same fact, so the larger weight counts once
    name = "site_links_channel"

    def evaluate(
        self, lead: ChannelLead, entity: CandidateEntity, ctx: SignalContext
    ) -> OwnershipSignal | None:
        options = [
            (item, name, weight)
            for name, weight in (
                ("site_links_channel", ctx.weights.site_links_channel),
                ("jsonld_sameas", ctx.weights.jsonld_sameas),
            )
            if (item := _evidence(lead, name)) is not None
        ]
        if not options:
            return None
        item, name, weight = max(options, key=lambda option: option[2])
        return OwnershipSignal(name=name, weight=weight, detail=item.detail, source_url=item.source_url)


@ownership_signal_registry.register("embed_majority")
class EmbedMajorityRule:
    name = "embed_majority"

    def evaluate(
        self, lead: ChannelLead, entity: CandidateEntity, ctx: SignalContext
    ) -> OwnershipSignal | None:
        majority = _evidence(lead, "embed_majority")
        if majority is None:
            return None
        return OwnershipSignal(
            name=self.name,
            weight=ctx.weights.embed_majority,
            detail=majority.detail,
            source_url=majority.source_url,
        )


@ownership_signal_registry.register("channel_links_site")
class ChannelLinksSiteRule:
    name = "channel_links_site"

    def evaluate(
        self, lead: ChannelLead, entity: CandidateEntity, ctx: SignalContext
    ) -> OwnershipSignal | None:
        domain = entity.official_domain
        # an llm-guessed domain still counts here: a real channel linking it independently corroborates both
        if lead.record is None or not domain:
            return None
        if domain.casefold() not in lead.record.description.casefold():
            return None
        return OwnershipSignal(
            name=self.name,
            weight=ctx.weights.channel_links_site,
            detail=f"channel description mentions {domain}",
        )


@ownership_signal_registry.register("uploads_link_site")
class UploadsLinkSiteRule:
    name = "uploads_link_site"

    def evaluate(
        self, lead: ChannelLead, entity: CandidateEntity, ctx: SignalContext
    ) -> OwnershipSignal | None:
        domain = entity.official_domain
        if not lead.uploads or not domain:
            return None
        linking = sum(domain.casefold() in video.description.casefold() for video in lead.uploads)
        if linking / len(lead.uploads) < ctx.uploads_link_share:
            return None
        return OwnershipSignal(
            name=self.name,
            weight=ctx.weights.uploads_link_site,
            detail=f"'{domain}' in {linking}/{len(lead.uploads)} recent upload descriptions",
        )


@ownership_signal_registry.register("uploads_brand_reference")
class UploadsBrandReferenceRule:
    # a brand that tags or names itself across its own uploads (e.g. #fortishealthcare) when no domain is linked
    name = "uploads_brand_reference"
    min_token_chars = 6

    def evaluate(
        self, lead: ChannelLead, entity: CandidateEntity, ctx: SignalContext
    ) -> OwnershipSignal | None:
        token = compact(entity.brand_name)
        if not lead.uploads or len(token) < self.min_token_chars:
            return None
        domain = (entity.official_domain or "").casefold()
        texts = [compact(video.description) for video in lead.uploads]
        if (
            domain
            and sum(domain in video.description.casefold() for video in lead.uploads) / len(lead.uploads)
            >= ctx.uploads_link_share
        ):
            return None
        naming = sum(token in text for text in texts)
        if naming / len(lead.uploads) < ctx.uploads_link_share:
            return None
        return OwnershipSignal(
            name=self.name,
            weight=ctx.weights.uploads_brand_reference,
            detail=f"'{token}' appears in {naming}/{len(lead.uploads)} recent upload descriptions",
        )


def name_similarity(entity: CandidateEntity, lead: ChannelLead) -> float:
    if lead.record is None:
        return 0.0
    handle = (lead.record.handle or "").lstrip("@").casefold()
    for noise in HANDLE_NOISE:
        handle = handle.removesuffix(noise)
    title = normalize_name(lead.record.title)
    best = 0.0
    for name in entity.names:
        best = max(best, token_set_ratio(normalize_name(name), title))
        if handle:
            best = max(best, compact_ratio(name, handle))
    return best


@ownership_signal_registry.register("name_similarity")
class NameSimilarityRule:
    name = "name_similarity"

    def evaluate(
        self, lead: ChannelLead, entity: CandidateEntity, ctx: SignalContext
    ) -> OwnershipSignal | None:
        similarity = name_similarity(entity, lead)
        if similarity >= 90:
            weight = ctx.weights.name_similarity_strong
        elif similarity >= 75:
            weight = ctx.weights.name_similarity_weak
        else:
            return None
        return OwnershipSignal(
            name=self.name,
            weight=weight,
            detail=f"channel name matches '{entity.brand_name}' ({similarity:.0f}/100)",
        )


@ownership_signal_registry.register("websearch_youtube_hit")
class WebSearchHitRule:
    name = "websearch_youtube_hit"

    def evaluate(
        self, lead: ChannelLead, entity: CandidateEntity, ctx: SignalContext
    ) -> OwnershipSignal | None:
        hits = [
            item for item in lead.evidence if item.name == "websearch_youtube_hit" and (item.rank or 99) <= 2
        ]
        if not hits:
            return None
        best = min(hits, key=lambda item: item.rank or 99)
        return OwnershipSignal(
            name=self.name,
            weight=ctx.weights.websearch_youtube_hit,
            detail=best.detail,
            source_url=best.source_url,
        )


@ownership_signal_registry.register("user_asserted")
class UserAssertedRule:
    name = "user_asserted"

    def evaluate(
        self, lead: ChannelLead, entity: CandidateEntity, ctx: SignalContext
    ) -> OwnershipSignal | None:
        asserted = _evidence(lead, "user_asserted")
        if asserted is None:
            return None
        return OwnershipSignal(name=self.name, weight=ctx.weights.user_asserted, detail=asserted.detail)


@ownership_signal_registry.register("channel_is_entity")
class ChannelIsEntityRule:
    # niche-search and user channels define the entity themselves; this never counts as link evidence
    name = "channel_is_entity"

    def evaluate(
        self, lead: ChannelLead, entity: CandidateEntity, ctx: SignalContext
    ) -> OwnershipSignal | None:
        if lead.channel_id not in entity.channel_hints or name_similarity(entity, lead) < 90:
            return None
        return OwnershipSignal(
            name=self.name,
            weight=ctx.weights.channel_is_entity,
            detail="this channel is how the candidate was found",
        )


@ownership_signal_registry.register("sanity_penalty")
class SanityPenaltyRule:
    name = "sanity_penalty"

    def evaluate(
        self, lead: ChannelLead, entity: CandidateEntity, ctx: SignalContext
    ) -> OwnershipSignal | None:
        record = lead.record
        if record is None:
            return None
        weights, penalty, reasons = ctx.weights, 0.0, []
        if record.video_count < 5:
            penalty += weights.penalty_few_videos
            reasons.append(f"only {record.video_count} videos")
        if record.country and ctx.brand_countries and record.country not in ctx.brand_countries:
            penalty += weights.penalty_foreign_country
            reasons.append(f"channel country {record.country}")
        text = f"{record.title} {record.description}"
        if UNOFFICIAL.search(text):
            penalty += weights.penalty_unofficial_tokens
            reasons.append("describes itself as unofficial or fan-made")
        if record.title.endswith("- Topic"):
            penalty += weights.penalty_unofficial_tokens
            reasons.append("auto-generated topic channel")
        if record.made_for_kids and not entity.is_user:
            penalty += weights.penalty_unofficial_tokens
            reasons.append("made for kids")
        if "late_duplicate" in lead.flags:
            penalty += weights.penalty_late_duplicate
            reasons.append("near-identical copy of the brand's verified channel")
        if not reasons:
            return None
        return OwnershipSignal(name=self.name, weight=round(penalty, 4), detail="; ".join(reasons))
