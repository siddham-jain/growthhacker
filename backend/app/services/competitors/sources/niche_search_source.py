from collections import Counter, defaultdict
from datetime import timedelta

from app.domain.competitors import CandidateSource
from app.domain.topics import TopicKind
from app.domain.youtube import SearchQuery
from app.pipeline.contracts import BudgetExceeded
from app.ports.quota import QuotaExhausted
from app.services.competitors.context import DiscoveryContext
from app.services.competitors.model import RawCandidate
from app.services.competitors.registries import candidate_generator_registry
from app.services.competitors.sources.base import SourceDeps

MAX_COMBINED_WORDS = 3


def niche_queries(ctx: DiscoveryContext) -> list[str]:
    # buyer questions first, then keywords; short keywords are or-combined in pairs to save search calls
    limit = ctx.settings.niche_queries_for_discovery
    active = [ref for ref in ctx.topic_refs if ref.status == "active"]
    if active:
        ranked = sorted(active, key=lambda ref: (ref.kind is not TopicKind.buyer_question, -ref.weight))
        questions = [ref.label for ref in ranked if ref.kind is TopicKind.buyer_question]
        keywords = [ref.label for ref in ranked if ref.kind is not TopicKind.buyer_question]
    else:
        questions = list(ctx.buyer_questions)
        ranked_keywords = sorted(
            ctx.profile.niche_keywords, key=lambda keyword: (keyword.tier != "seed", -keyword.weight)
        )
        keywords = [keyword.term for keyword in ranked_keywords]
    short = [keyword for keyword in keywords if len(keyword.split()) <= MAX_COMBINED_WORDS]
    long = [keyword for keyword in keywords if len(keyword.split()) > MAX_COMBINED_WORDS]
    combined = ["|".join(short[index : index + 2]) for index in range(0, len(short), 2)]
    return list(dict.fromkeys([*questions, *combined, *long]))[:limit]


@candidate_generator_registry.register("niche_search")
class NicheSearchCandidateSource:
    kind = "niche_search"
    used_in_rediscovery = True

    def __init__(self, deps: SourceDeps) -> None:
        self.youtube = deps.youtube

    def enabled(self, ctx: DiscoveryContext) -> bool:
        return ctx.source_enabled(self.kind) and ctx.settings.niche_queries_for_discovery > 0

    async def generate(self, ctx: DiscoveryContext) -> list[RawCandidate]:
        settings = ctx.settings
        published_after = (ctx.now - timedelta(days=settings.niche_lookback_days)).date()
        queries_by_channel: dict[str, set[str]] = defaultdict(set)
        total_hits: Counter[str] = Counter()
        queries = niche_queries(ctx)
        for text in queries:
            query = SearchQuery(
                q=text,
                type="video",
                published_after=published_after,
                region_code=ctx.market,
                relevance_language=ctx.profile.identity.primary_language or None,
                max_results=50,
            )
            try:
                page = await self.youtube.search(query, "niche")
            except BudgetExceeded:
                ctx.warn(
                    "NICHE_SEARCH_PARTIAL", "Some YouTube niche searches were skipped to stay within budget."
                )
                break
            except QuotaExhausted:
                ctx.warn(
                    "NICHE_SEARCH_SKIPPED",
                    "YouTube search limit reached for today. Results may miss some channels; we'll retry tomorrow.",
                )
                break
            for hit in page.items:
                queries_by_channel[hit.channel_id].add(text)
                total_hits[hit.channel_id] += 1
        own = set(ctx.own_channel_ids)
        eligible = [
            channel_id
            for channel_id, hit_queries in queries_by_channel.items()
            if channel_id not in own
            and (
                len(hit_queries) >= settings.niche_min_query_hits
                or total_hits[channel_id] >= settings.niche_min_total_hits
            )
        ]
        eligible.sort(
            key=lambda channel_id: (-len(queries_by_channel[channel_id]), -total_hits[channel_id], channel_id)
        )
        eligible = eligible[: settings.source_caps.niche_search]
        try:
            records = await self.youtube.channels(eligible)
        except (BudgetExceeded, QuotaExhausted):
            return []
        candidates = []
        for channel_id in eligible:
            record = records.get(channel_id)
            if record is None:
                continue
            hit_queries = sorted(queries_by_channel[channel_id])
            candidates.append(
                RawCandidate(
                    name=record.title,
                    channel_id=channel_id,
                    channel_record=record,
                    relation_guess="content",
                    query_hits=len(hit_queries),
                    total_hits=total_hits[channel_id],
                    source=CandidateSource(
                        kind=self.kind,
                        ref=" ; ".join(hit_queries)[:400],
                        snippet=f"Appeared in {len(hit_queries)} of {len(queries)} YouTube searches for your topics",
                        retrieved_at=ctx.now,
                    ),
                )
            )
        return candidates
