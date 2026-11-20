import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from app.core.config import NewsSettings
from app.domain.profile import OperatingLevel, WatchEntity
from app.ports.freshness import PlannedQuery
from app.services.news.context import NewsClientContext, raised
from app.services.news.packs import SourcePack
from app.services.news.text import contains_phrase, normalize

Origin = Literal[
    "client", "entity", "topic", "seasonal", "offering", "competitor", "domain", "feed", "research"
]
ORIGIN_WEIGHT: dict[Origin, float] = {
    "client": 1.0,
    "topic": 0.95,
    "entity": 0.9,
    "seasonal": 0.9,
    "domain": 0.85,
    "offering": 0.7,
    "competitor": 0.6,
    "feed": 1.0,
    "research": 0.8,
}
CREDIT_SOURCES = frozenset({"tavily_news", "tavily_domains"})
MAX_ALIASES = 2
MAX_COMPETITOR_QUERIES = 5
MAX_PUBMED_TERMS = 3
MAX_ARXIV_ENTITIES = 2
REGION_NAMES = {
    "IN": "India",
    "US": "United States",
    "GB": "United Kingdom",
    "AU": "Australia",
    "CA": "Canada",
}
DEFAULT_EVENT_TERMS = ["launch", "update", "announces"]


def query_hash(source_key: str, normalized_query: str, params: dict[str, Any]) -> str:
    canonical = json.dumps(params, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{source_key}|{normalized_query}|{canonical}".encode()).hexdigest()


def _quoted(term: str) -> str:
    term = normalize(term).replace('"', "")
    return f'"{term}"' if " " in term or not term.isalnum() else term


def _or(terms: Sequence[str]) -> str:
    unique = sorted({_quoted(term) for term in terms if term.strip()})
    return " OR ".join(unique)


def _group(terms: Sequence[str]) -> str:
    joined = _or(terms)
    return f"({joined})" if " OR " in joined else joined


@dataclass(frozen=True)
class PlannedSubscription:
    query: PlannedQuery
    origin: Origin
    weight: float
    importance: int

    @property
    def costs_credits(self) -> bool:
        return self.query.source_key in CREDIT_SOURCES


@dataclass(frozen=True)
class QueryPlan:
    subscriptions: list[PlannedSubscription]
    dropped: list[PlannedSubscription]


class QueryPlanner:
    """FreshnessProfile + BusinessProfile + competitors + packs -> shared, hashable queries."""

    def __init__(self, settings: NewsSettings) -> None:
        self._settings = settings

    def _make(
        self,
        source_key: str,
        text: str,
        params: dict[str, Any],
        cadence: int,
        origin: Origin,
        importance: int = 2,
    ) -> PlannedSubscription:
        # boolean OR stays upper case so pubmed and gdelt still parse it
        normalized = normalize(text).replace(" or ", " OR ")
        query = PlannedQuery(
            source_key=source_key,
            normalized_query=normalized,
            params=params,
            query_hash=query_hash(source_key, normalized, params),
            cadence_minutes=cadence,
        )
        return PlannedSubscription(
            query=query, origin=origin, weight=ORIGIN_WEIGHT[origin], importance=importance
        )

    def _news(
        self,
        text: str,
        subject: Sequence[str],
        params: dict[str, Any],
        cadence: int,
        origin: Origin,
        importance: int = 2,
    ) -> PlannedSubscription:
        # gdelt cannot parse tavily-style queries; the subject group is the fallback when tavily is down
        fallback = {**params, "fallback_query": _group(subject)}
        return self._make("tavily_news", text, fallback, cadence, origin, importance)

    def _news_params(self, ctx: NewsClientContext, time_range: str) -> dict[str, Any]:
        languages = ctx.languages
        params: dict[str, Any] = {"time_range": time_range, "language": languages[0]}
        if len(languages) == 1:
            params["filter_by_language"] = True
        return params

    def _excluded(self, ctx: NewsClientContext, text: str) -> bool:
        return any(contains_phrase(text, topic) for topic in ctx.freshness.excluded_topics)

    def _event_terms(self, entity: WatchEntity, packs: Sequence[SourcePack]) -> list[str]:
        for pack in packs:
            if entity.kind in pack.event_terms:
                return pack.event_terms[entity.kind]
        return DEFAULT_EVENT_TERMS

    def _entity_queries(self, ctx, packs, params, cadence) -> list[PlannedSubscription]:
        planned = []
        entities = sorted(ctx.freshness.watch_entities, key=lambda e: (-e.importance, normalize(e.name)))
        for entity in entities:
            if self._excluded(ctx, entity.name):
                continue
            subject = [entity.name, *entity.aliases[:MAX_ALIASES]]
            text = f"{_or(subject)} {_or(self._event_terms(entity, packs))}"
            planned.append(self._news(text, subject, params, cadence, "entity", entity.importance))
        return planned

    def _topic_queries(self, ctx, params, cadence) -> list[PlannedSubscription]:
        # the profile's own watch topics are written as searches, e.g. a lab's new model release
        planned = []
        for topic in ctx.freshness.watch_topics:
            if self._excluded(ctx, topic.query):
                continue
            topic_params = {**params, "language": topic.language} if topic.language else params
            planned.append(self._news(topic.query, [topic.query], topic_params, cadence, "topic", 3))
        return planned

    def _offering_queries(self, ctx, params, cadence, year: int) -> list[PlannedSubscription]:
        planned = []
        offerings = sorted(ctx.profile.offerings, key=lambda o: (not o.is_primary, -o.priority, o.name))
        keywords = sorted(ctx.profile.niche_keywords, key=lambda k: -k.weight)
        for offering in offerings:
            linked = [k.term for k in keywords if offering.id in k.offering_ids][:2] or [offering.name]
            if self._excluded(ctx, " ".join(linked)):
                continue
            text = f"{_or(linked)} new {year}"
            planned.append(self._news(text, linked, params, cadence, "offering", offering.priority))
        return planned

    def _competitor_queries(self, ctx, packs, params, cadence) -> list[PlannedSubscription]:
        terms = next((p.event_terms["competitor"] for p in packs if "competitor" in p.event_terms), None)
        terms = terms or ["launch", "raises", "acquires", "outage", "pricing"]
        return [
            self._news(f"{_quoted(c.brand_name)} {_or(terms)}", [c.brand_name], params, cadence, "competitor")
            for c in ctx.confirmed_competitors[:MAX_COMPETITOR_QUERIES]
        ]

    def _seasonal_queries(self, ctx, params, cadence, now: datetime) -> list[PlannedSubscription]:
        month = now.astimezone(ctx.timezone).month
        region = REGION_NAMES.get(ctx.regions[0], ctx.regions[0]) if ctx.regions else ""
        planned = []
        for window in ctx.freshness.seasonality:
            if month not in window.months or not window.query_terms:
                continue
            text = f"{_or(window.query_terms)} {region}".strip()
            for language in ctx.languages:
                language_params = {**params, "language": language}
                planned.append(self._news(text, window.query_terms, language_params, cadence, "seasonal", 3))
        return planned

    def _pack_queries(self, ctx, packs, envelope, time_range: str) -> list[PlannedSubscription]:
        planned: list[PlannedSubscription] = []
        feed_cadence = envelope.feed_cadence_minutes
        search_cadence = envelope.search_cadence_minutes
        for pack in packs:
            for group in pack.domain_groups:
                params = {"time_range": time_range, "include_domains": sorted(group.domains)}
                planned.append(self._make("tavily_domains", group.query, params, search_cadence, "domain", 3))
            if not feed_cadence:
                continue
            for feed in pack.feeds:
                params = {"url": feed.url, "tier": feed.tier.value}
                planned.append(self._make("rss", feed.name, params, feed_cadence, "feed", 3))
            if pack.pubmed:
                names = [o.name for o in ctx.profile.offerings][:MAX_PUBMED_TERMS]
                if names:
                    planned.append(self._make("pubmed", _or(names), {}, feed_cadence, "research"))
            if pack.arxiv_categories:
                entities = sorted(ctx.freshness.watch_entities, key=lambda e: -e.importance)[
                    :MAX_ARXIV_ENTITIES
                ]
                for entity in entities:
                    params = {"categories": sorted(pack.arxiv_categories)}
                    planned.append(self._make("arxiv", entity.name, params, feed_cadence, "research"))
            if pack.hf_orgs:
                params = {"orgs": sorted(pack.hf_orgs)}
                planned.append(self._make("hf_trending", "trending models", params, feed_cadence, "research"))
            if pack.who_outbreaks:
                planned.append(self._make("who_don", "disease outbreak news", {}, feed_cadence, "research"))
            if pack.openfda_approvals:
                planned.append(self._make("openfda", "drug approvals", {}, feed_cadence, "research"))
        return planned

    def plan(self, ctx: NewsClientContext, packs: Sequence[SourcePack], now: datetime) -> QueryPlan:
        envelope = ctx.envelope(self._settings)
        params = self._news_params(ctx, envelope.tavily_time_range)
        cadence = envelope.search_cadence_minutes
        seasonal_envelope = ctx.envelope(self._settings, raised(ctx.level))
        seasonal_params = self._news_params(ctx, seasonal_envelope.tavily_time_range)
        client_cadence = max(
            cadence, self._settings.envelopes[OperatingLevel.low.value].search_cadence_minutes
        )
        candidates = [
            self._news(
                _quoted(ctx.profile.identity.name),
                [ctx.profile.identity.name],
                params,
                client_cadence,
                "client",
                3,
            ),
            *self._topic_queries(ctx, params, cadence),
            *self._entity_queries(ctx, packs, params, cadence),
            *self._seasonal_queries(ctx, seasonal_params, seasonal_envelope.search_cadence_minutes, now),
            *self._offering_queries(ctx, params, cadence, now.year),
            *self._competitor_queries(ctx, packs, params, cadence),
            *self._pack_queries(ctx, packs, envelope, envelope.tavily_time_range),
        ]
        unique: dict[str, PlannedSubscription] = {}
        for candidate in candidates:
            unique.setdefault(candidate.query.query_hash, candidate)
        paid = sorted(
            (s for s in unique.values() if s.costs_credits), key=lambda s: (-s.importance, -s.weight)
        )
        free = [s for s in unique.values() if not s.costs_credits]
        limit = envelope.max_queries_per_client
        return QueryPlan(subscriptions=paid[:limit] + free, dropped=paid[limit:])
