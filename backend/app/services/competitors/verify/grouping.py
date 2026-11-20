from app.domain.profile import BusinessProfile
from app.domain.youtube import OwnershipLabel
from app.services.competitors.classify.geo import LANGUAGE_NAMES, REGION_GAZETTEER
from app.services.competitors.classify.owner_kind import HONORIFIC
from app.services.competitors.identity.normalize import normalize_text
from app.services.competitors.model import CandidateEntity, ChannelLead

GOOD_LABELS = frozenset({OwnershipLabel.verified, OwnershipLabel.likely})
DUPLICATE_TITLE_SHARE = 0.6


def _contains(text: str, phrases: set[str]) -> str | None:
    padded = f" {text} "
    return next((phrase for phrase in sorted(phrases) if f" {phrase} " in padded), None)


class MultiChannelGrouper:
    """labels each official channel of a brand and picks the primary by matching uploads, a raw count."""

    def __init__(self, profile: BusinessProfile) -> None:
        locations = profile.locations
        self.client_places = {
            normalize_text(place) for place in [*locations.cities, *locations.regions] if place
        }
        self.gazetteer = set(REGION_GAZETTEER) | self.client_places
        self.single_region = (
            {normalize_text(region) for region in locations.regions} if len(locations.regions) == 1 else set()
        )
        self.specialties = {
            normalize_text(name)
            for offering in profile.offerings
            for name in [offering.name, *offering.aliases]
            if name
        }

    def group(self, entity: CandidateEntity, matching_uploads: dict[str, int]) -> ChannelLead | None:
        good = [
            lead for lead in entity.leads.values() if lead.label in GOOD_LABELS and lead.record is not None
        ]
        if len(good) >= 2:
            self._assign_variants(good)
            self._collapse_duplicates(good)
        eligible = [lead for lead in good if lead.variant != "person" and "duplicate" not in lead.flags]
        if eligible:
            return max(eligible, key=lambda lead: self._primary_key(lead, matching_uploads))
        uncertain = [lead for lead in entity.leads.values() if lead.label is OwnershipLabel.uncertain]
        return max(uncertain, key=lambda lead: lead.score, default=None)

    def _assign_variants(self, leads: list[ChannelLead]) -> None:
        largest = max(leads, key=lambda lead: lead.record.subscriber_count or 0)
        for lead in leads:
            text = normalize_text(f"{lead.record.title} {lead.record.description[:300]}")
            title = lead.record.title
            if HONORIFIC.match(title):
                lead.variant = "person"
                lead.flags.add("affiliated_person")
            elif place := _contains(normalize_text(title), self.gazetteer):
                lead.variant = "regional"
                lead.flags.add(f"region:{place}")
            elif _contains(text, set(LANGUAGE_NAMES)):
                lead.variant = "language"
            elif _contains(normalize_text(title), self.specialties):
                lead.variant = "specialty"
            else:
                lead.variant = "main" if lead is largest else "unknown"

    @staticmethod
    def _collapse_duplicates(leads: list[ChannelLead]) -> None:
        ordered = sorted(leads, key=lambda lead: -(lead.record.subscriber_count or 0))
        for index, keeper in enumerate(ordered):
            if "duplicate" in keeper.flags:
                continue
            keeper_titles = {normalize_text(video.title) for video in keeper.uploads or []}
            for other in ordered[index + 1 :]:
                titles = [normalize_text(video.title) for video in other.uploads or []]
                if (
                    keeper_titles
                    and titles
                    and sum(title in keeper_titles for title in titles) / len(titles) >= DUPLICATE_TITLE_SHARE
                ):
                    other.flags.update({"duplicate", f"duplicate_of:{keeper.channel_id}"})

    def _primary_key(self, lead: ChannelLead, matching_uploads: dict[str, int]) -> tuple:
        preferred_region = any(f"region:{region}" in lead.flags for region in self.single_region)
        return (
            preferred_region,
            matching_uploads.get(lead.channel_id, 0),
            lead.variant == "main",
            lead.record.subscriber_count or 0,
        )
