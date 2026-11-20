import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np
from numpy.typing import NDArray

from app.domain.news import NewsItem, NewsStory
from app.services.news.normalize import effective_published_at
from app.services.news.text import token_set_ratio
from app.services.news.tiers import TIER_RANK, best_tier
from app.services.news.versions import NON_ENTITY_WORDS, version_tokens, versions_conflict

STORY_NAMESPACE = uuid.UUID("0c3f5d0e-2a8b-4f61-8d0e-5b7a9c1e2f34")
EXTERNAL_ID_KEYS = ("doi", "pmid", "arxiv_id", "don_id", "hf_repo", "fda_application")
STALE_AFTER = timedelta(days=30)
MIN_SHARED_ENTITY_TOKENS = 2


def story_id_for(item: NewsItem) -> uuid.UUID:
    return uuid.uuid5(STORY_NAMESPACE, item.url_hash)


def entity_tokens(title: str) -> frozenset[str]:
    words = [word for word in title.replace("-", " ").split() if any(char.isupper() for char in word)]
    return frozenset(
        token
        for token in (w.strip(".,:;!?'\"()").casefold() for w in words)
        if token and token not in NON_ENTITY_WORDS
    )


def item_time(item: NewsItem) -> datetime:
    return effective_published_at(item) or item.fetched_at


@dataclass
class _Cluster:
    story_id: uuid.UUID
    members: list[NewsItem] = field(default_factory=list)
    versions: set[str] = field(default_factory=set)
    entities: set[str] = field(default_factory=set)
    vectors: list[NDArray[np.float32]] = field(default_factory=list)

    def add(self, item: NewsItem, vector: NDArray[np.float32] | None, entity_terms: Sequence[str]) -> None:
        self.members.append(item)
        self.versions |= version_tokens(item.title, entity_terms)
        self.entities |= entity_tokens(item.title)
        if vector is not None:
            self.vectors.append(vector)

    def centroid(self) -> NDArray[np.float32] | None:
        if not self.vectors:
            return None
        mean = np.mean(np.stack(self.vectors), axis=0)
        norm = float(np.linalg.norm(mean))
        return mean / norm if norm > 0 else None


@dataclass(frozen=True)
class ClusterResult:
    stories: list[NewsStory]
    assignments: dict[uuid.UUID, uuid.UUID]
    centroids: dict[uuid.UUID, NDArray[np.float32]]


class StoryClusterer:
    """exact ids -> fuzzy title -> embedding, never merging items whose version tokens disagree."""

    def __init__(
        self,
        *,
        fuzzy_min_ratio: float,
        fuzzy_window: timedelta,
        embedding_min_cosine: float,
        developing_window: timedelta,
        entity_terms: Sequence[str] = (),
    ) -> None:
        self._fuzzy_min_ratio = fuzzy_min_ratio
        self._fuzzy_window = fuzzy_window
        self._embedding_min_cosine = embedding_min_cosine
        self._developing_window = developing_window
        self._entity_terms = list(entity_terms)

    def _exact_match(self, item: NewsItem, by_key: Mapping[str, _Cluster]) -> _Cluster | None:
        for key in EXTERNAL_ID_KEYS:
            value = item.external_ids.get(key)
            if value and f"{key}:{value}" in by_key:
                return by_key[f"{key}:{value}"]
        return by_key.get(f"url:{item.url_hash}")

    def _fuzzy_match(self, item: NewsItem, clusters: Sequence[_Cluster]) -> _Cluster | None:
        best: tuple[float, _Cluster] | None = None
        for cluster in clusters:
            for member in cluster.members:
                if abs(item_time(item) - item_time(member)) > self._fuzzy_window:
                    continue
                ratio = token_set_ratio(item.title, member.title)
                if ratio >= self._fuzzy_min_ratio and (best is None or ratio > best[0]):
                    best = (ratio, cluster)
        return best[1] if best else None

    def _versioned_entity_match(self, item: NewsItem, clusters: Sequence[_Cluster]) -> _Cluster | None:
        # a shared versioned name ("claude fable 5.2") inside the window is the same event
        versions = version_tokens(item.title, self._entity_terms)
        if not versions:
            return None
        names = entity_tokens(item.title)
        for cluster in clusters:
            if not versions & cluster.versions or len(names & cluster.entities) < MIN_SHARED_ENTITY_TOKENS:
                continue
            if any(abs(item_time(item) - item_time(m)) <= self._fuzzy_window for m in cluster.members):
                return cluster
        return None

    def _embedding_match(
        self, item: NewsItem, vector: NDArray[np.float32] | None, clusters: Sequence[_Cluster]
    ) -> _Cluster | None:
        if vector is None:
            return None
        item_entities = entity_tokens(item.title)
        best: tuple[float, _Cluster] | None = None
        for cluster in clusters:
            centroid = cluster.centroid()
            if centroid is None or not (item_entities & cluster.entities):
                continue
            cosine = float(np.dot(vector, centroid))
            if cosine >= self._embedding_min_cosine and (best is None or cosine > best[0]):
                best = (cosine, cluster)
        return best[1] if best else None

    def cluster(
        self,
        items: Sequence[NewsItem],
        now: datetime,
        vectors: Mapping[uuid.UUID, NDArray[np.float32]] | None = None,
    ) -> ClusterResult:
        vectors = vectors or {}
        clusters: dict[uuid.UUID, _Cluster] = {}
        by_key: dict[str, _Cluster] = {}
        ordered = sorted(items, key=lambda item: (item.story_id is None, item_time(item), item.url_hash))
        for item in ordered:
            vector = vectors.get(item.id)
            item_versions = version_tokens(item.title, self._entity_terms)
            target = clusters.get(item.story_id) if item.story_id else None
            if target is None:
                target = self._exact_match(item, by_key)
            if target is None:
                compatible = [
                    c
                    for c in clusters.values()
                    if not versions_conflict(item_versions, frozenset(c.versions))
                ]
                target = (
                    self._fuzzy_match(item, compatible)
                    or self._versioned_entity_match(item, compatible)
                    or self._embedding_match(item, vector, compatible)
                )
            if target is None:
                story_id = item.story_id or story_id_for(item)
                target = clusters.setdefault(story_id, _Cluster(story_id))
            target.add(item, vector, self._entity_terms)
            by_key[f"url:{item.url_hash}"] = target
            for key in EXTERNAL_ID_KEYS:
                if item.external_ids.get(key):
                    by_key[f"{key}:{item.external_ids[key]}"] = target
        stories = [self._story(cluster, now) for cluster in clusters.values()]
        assignments = {
            member.id: cluster.story_id for cluster in clusters.values() for member in cluster.members
        }
        centroids = {cid: c for cid, cluster in clusters.items() if (c := cluster.centroid()) is not None}
        return ClusterResult(stories=stories, assignments=assignments, centroids=centroids)

    def _story(self, cluster: _Cluster, now: datetime) -> NewsStory:
        members = cluster.members
        dated = [effective_published_at(m) for m in members if m.published_precision != "none"]
        times = [t for t in dated if t is not None] or [m.fetched_at for m in members]
        representative = max(
            members,
            key=lambda m: (TIER_RANK[m.source_tier], "paywalled" not in m.flags, -item_time(m).timestamp()),
        )
        first, last = min(times), max(times)
        newest_seen = max(max(m.fetched_at for m in members), last)
        if first < now - STALE_AFTER:
            status = "stale"
        elif newest_seen >= now - self._developing_window:
            status = "developing"
        else:
            status = "stable"
        return NewsStory(
            id=cluster.story_id,
            first_published_at=first,
            last_published_at=last,
            item_ids=[m.id for m in members],
            outlet_count=len({m.domain for m in members}),
            best_tier=best_tier(m.source_tier for m in members),
            representative_item_id=representative.id,
            version_tokens=sorted(cluster.versions),
            status=status,
        )
