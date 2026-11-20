from app.core.config import CompetitorSettings
from app.domain.youtube import OwnershipLabel, OwnershipVerdict
from app.services.competitors.identity.normalize import normalize_name, token_set_ratio
from app.services.competitors.model import LINK_SIGNALS, CandidateEntity, ChannelLead
from app.services.competitors.registries import ownership_signal_registry
from app.services.competitors.verify.signals import SignalContext

DUPLICATE_TITLE_RATIO = 95


class OwnershipVerifier:
    """deterministic score from registered signals; only hard link evidence can reach verified."""

    def __init__(self, settings: CompetitorSettings) -> None:
        self.weights = settings.ownership
        self.uploads_link_share = settings.uploads_link_share
        self.rules = [rule() for rule in ownership_signal_registry.all().values()]

    def verify_entity(self, entity: CandidateEntity) -> None:
        context = SignalContext(
            weights=self.weights,
            brand_countries=frozenset(geo.country_iso2 for geo in entity.geography),
            uploads_link_share=self.uploads_link_share,
        )
        for lead in entity.leads.values():
            lead.verdict = self.verdict(lead, entity, context)
        self._penalise_late_duplicates(entity, context)
        if "domain_unverified" in entity.flags and any(
            signal.name == "channel_links_site"
            for lead in entity.leads.values()
            for signal in lead.verdict.signals
        ):
            entity.flags.discard("domain_unverified")
            entity.flags.add("domain_corroborated_by_channel")

    def verdict(self, lead: ChannelLead, entity: CandidateEntity, context: SignalContext) -> OwnershipVerdict:
        signals = [
            signal for rule in self.rules if (signal := rule.evaluate(lead, entity, context)) is not None
        ]
        score = min(max(sum(signal.weight for signal in signals), 0.0), 1.0)
        names = {signal.name for signal in signals}
        has_link = bool(names & LINK_SIGNALS)
        weights = self.weights
        if score >= weights.verified_min and has_link:
            label = OwnershipLabel.verified
        elif score >= weights.likely_min:
            label = OwnershipLabel.likely
        elif score >= weights.uncertain_min:
            label = OwnershipLabel.uncertain
        else:
            label = OwnershipLabel.rejected
        method = "link" if has_link else ("user" if "user_asserted" in names else None)
        return OwnershipVerdict(label=label, method=method, score=round(score, 4), signals=signals)

    def _penalise_late_duplicates(self, entity: CandidateEntity, context: SignalContext) -> None:
        verified = [
            lead for lead in entity.leads.values() if lead.label is OwnershipLabel.verified and lead.record
        ]
        for original in verified:
            title = normalize_name(original.record.title)
            for lead in entity.leads.values():
                if lead is original or lead.record is None or "late_duplicate" in lead.flags:
                    continue
                newer = lead.record.published_at > original.record.published_at
                if (
                    newer
                    and token_set_ratio(title, normalize_name(lead.record.title)) >= DUPLICATE_TITLE_RATIO
                ):
                    lead.flags.add("late_duplicate")
                    lead.verdict = self.verdict(lead, entity, context)
