from uuid import UUID

from app.core.config import CompetitorSettings
from app.domain.competitors import CandidateRole, CompetitorRelation, OwnerKind
from app.services.competitors.model import CompetitorSetProposal
from app.services.competitors.select.assessment import AssessedCandidate

NICHE_RELATIONS = frozenset(
    {
        CompetitorRelation.content,
        CompetitorRelation.aggregator,
        CompetitorRelation.direct,
        CompetitorRelation.direct_public,
    }
)


class SetProposer:
    """fills the competitor set from verified or likely, active, direct rivals, then the other sets."""

    def __init__(self, settings: CompetitorSettings) -> None:
        self.settings = settings

    def propose(self, client_id: UUID, assessed: list[AssessedCandidate]) -> CompetitorSetProposal:
        settings = self.settings
        eligible = [item for item in assessed if not item.entity.is_excluded and item.owned and item.active]
        size = min(max(settings.proposal_size, settings.min_competitors), settings.max_competitors)

        open_role = [
            item for item in eligible if item.entity.intended_role in {None, CandidateRole.competitor_set}
        ]
        chosen_by_user = self._ordered(
            item for item in open_role if item.entity.intended_role is CandidateRole.competitor_set
        )
        commercial = self._ordered(
            item for item in open_role if item.entity.relation is CompetitorRelation.direct
        )
        public = self._ordered(
            item for item in open_role if item.entity.relation is CompetitorRelation.direct_public
        )
        pool = self._unique([*chosen_by_user, *commercial, *public])
        competitors = pool[:size]
        overflow = pool[size:]
        contains_content = False
        if len(competitors) < settings.min_competitors:
            fillers = self._ordered(
                item
                for item in open_role
                if item.entity.relation in {CompetitorRelation.aggregator, CompetitorRelation.content}
                and item not in competitors
            )
            needed = settings.min_competitors - len(competitors)
            competitors.extend(fillers[:needed])
            contains_content = bool(fillers[:needed])
        swapped = self._fairness_swap(competitors, overflow)

        taken = {id(item) for item in competitors}
        benchmarks = self._ordered(
            item
            for item in eligible
            if id(item) not in taken
            and (
                item.entity.relation is CompetitorRelation.benchmark
                or item.entity.intended_role is CandidateRole.benchmark_set
            )
        )[: settings.benchmark_max]
        taken.update(id(item) for item in benchmarks)
        creators = [
            item
            for item in eligible
            if id(item) not in taken
            and item.entity.owner_kind is OwnerKind.individual
            and item.entity.relation is CompetitorRelation.content
            and (item.entity.query_hits >= settings.niche_min_query_hits or item.entity.is_user)
        ][: settings.creator_seed_max]
        taken.update(id(item) for item in creators)
        niche = sorted(
            (
                item
                for item in eligible
                if id(item) not in taken
                and (
                    item.entity.relation in NICHE_RELATIONS
                    or item.entity.intended_role is CandidateRole.niche_set
                )
            ),
            key=lambda item: (item.entity.intended_role is not CandidateRole.niche_set, *item.youtube_key()),
        )[: settings.niche_max]

        for role, members in (
            (CandidateRole.competitor_set, competitors),
            (CandidateRole.benchmark_set, benchmarks),
            (CandidateRole.creator_seed, creators),
            (CandidateRole.niche_set, niche),
        ):
            for item in members:
                item.role = role
        return CompetitorSetProposal(
            client_id=client_id,
            competitor_channel_ids=[item.channel_id for item in competitors],
            niche_channel_ids=[item.channel_id for item in niche],
            benchmark_channel_ids=[item.channel_id for item in benchmarks],
            creator_seed_channel_ids=[item.channel_id for item in creators],
            contains_content_competitors=contains_content,
            fairness_swap=swapped,
            ready=len(competitors) >= settings.min_competitors,
        )

    @staticmethod
    def _ordered(items) -> list[AssessedCandidate]:
        return sorted(items, key=lambda item: item.rank_key())

    @staticmethod
    def _unique(items: list[AssessedCandidate]) -> list[AssessedCandidate]:
        seen: set[int] = set()
        unique = []
        for item in items:
            if id(item) not in seen:
                seen.add(id(item))
                unique.append(item)
        return unique

    @staticmethod
    def _fairness_swap(competitors: list[AssessedCandidate], overflow: list[AssessedCandidate]) -> bool:
        # small clients see at least one comparable rival when every pick dwarfs them
        if not competitors or any(item.size_band != "much_larger" for item in competitors):
            return False
        comparable = [
            item
            for item in overflow
            if item.size_band in {"peer", "larger"} and item.business.tier in {"high", "medium"}
        ]
        if not comparable:
            return False
        overflow.remove(comparable[0])
        overflow.append(competitors.pop())
        competitors.append(comparable[0])
        return True
