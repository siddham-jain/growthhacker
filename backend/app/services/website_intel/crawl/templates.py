import gzip
import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from itertools import combinations
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlsplit

from app.services.website_intel.models import TemplateSummary, UrlCandidate

GAZETTEER_PATH = Path(__file__).parent / "data" / "cities15000_names.txt.gz"
VARIABLE_MIN_CARDINALITY = 8
VARIABLE_MIN_JACCARD = 0.5
LOCATION_MIN_VALUES = 3
LOCATION_MIN_SHARE = 0.8
JACCARD_SAMPLE = 30
SUMMARY_VALUE_SAMPLE = 20
CITY, VAR, SLUG = "{city}", "{var}", "{slug}"
VARIABLE_TOKENS = (CITY, VAR, SLUG)


def slugify(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")


@lru_cache(maxsize=1)
def _default_names() -> frozenset[str]:
    with gzip.open(GAZETTEER_PATH, "rt", encoding="utf-8") as handle:
        return frozenset(line.strip() for line in handle if line.strip() and not line.startswith("#"))


@dataclass(frozen=True)
class Gazetteer:
    names: frozenset[str]

    @classmethod
    def load_default(cls) -> "Gazetteer":
        return cls(_default_names())

    def contains(self, slug: str) -> bool:
        return slugify(slug) in self.names

    def with_extra(self, names: Iterable[str]) -> "Gazetteer":
        return Gazetteer(self.names | {slugify(name) for name in names if slugify(name)})


@dataclass
class TemplateClustering:
    template_of: dict[str, str]
    summaries: list[TemplateSummary]
    location_of: dict[str, str] = field(default_factory=dict)

    def is_templated(self, url: str) -> bool:
        template = self.template_of.get(url)
        return template is not None and any(token in template for token in VARIABLE_TOKENS)


@dataclass
class _Row:
    url: str
    segments: list[str]
    query_keys: str
    resolved: list[str] = field(default_factory=list)
    location: str | None = None


def _mean_jaccard(suffix_sets: list[set[str]]) -> float:
    if len(suffix_sets) < 2:
        return 0.0
    step = max(1, len(suffix_sets) // JACCARD_SAMPLE)
    sample = suffix_sets[::step][:JACCARD_SAMPLE]
    scores = [len(a & b) / len(a | b) for a, b in combinations(sample, 2) if a | b]
    return sum(scores) / len(scores) if scores else 0.0


class TemplateClusterer:
    """collapses city x service url grids into path templates (plan 03 §5.5)."""

    def __init__(self, gazetteer: Gazetteer | None = None) -> None:
        self.gazetteer = gazetteer if gazetteer is not None else Gazetteer.load_default()

    def cluster(self, urls: Sequence[str]) -> TemplateClustering:
        rows = [self._row(url) for url in dict.fromkeys(urls)]
        max_depth = max((len(row.segments) for row in rows), default=0)
        for position in range(max_depth):
            groups: dict[tuple[int, str], list[_Row]] = defaultdict(list)
            for row in rows:
                if position < len(row.segments):
                    groups[(len(row.segments), "/".join(row.resolved))].append(row)
            for (depth, _), group in groups.items():
                self._resolve_position(group, position, is_leaf=position == depth - 1)
        template_of: dict[str, str] = {}
        location_of: dict[str, str] = {}
        members: dict[str, list[_Row]] = defaultdict(list)
        for row in rows:
            template = "/" + "/".join(row.resolved) + (f"?{row.query_keys}" if row.query_keys else "")
            template_of[row.url] = template
            members[template].append(row)
            if row.location:
                location_of[row.url] = row.location
        summaries = [self._summary(template, group) for template, group in members.items()]
        summaries.sort(key=lambda summary: (-summary.count, summary.template_key))
        return TemplateClustering(template_of=template_of, summaries=summaries, location_of=location_of)

    @staticmethod
    def _row(url: str) -> _Row:
        parts = urlsplit(url)
        segments = [unquote(segment).lower() for segment in parts.path.split("/") if segment]
        query_keys = ",".join(sorted({key for key, _ in parse_qsl(parts.query, keep_blank_values=True)}))
        return _Row(url=url, segments=segments, query_keys=query_keys)

    def _resolve_position(self, group: list[_Row], position: int, *, is_leaf: bool) -> None:
        values = sorted({row.segments[position] for row in group})
        cities = {value for value in values if self.gazetteer.contains(value)}
        if len(cities) >= LOCATION_MIN_VALUES and len(cities) / len(values) >= LOCATION_MIN_SHARE:
            cities = set(values)
        if len(cities) >= LOCATION_MIN_VALUES:
            for row in group:
                value = row.segments[position]
                if value in cities:
                    row.resolved.append(CITY)
                    row.location = row.location or value
                else:
                    row.resolved.append(value)
            return
        variable = False
        if len(values) >= VARIABLE_MIN_CARDINALITY:
            if is_leaf:
                variable = True
            else:
                suffixes: dict[str, set[str]] = defaultdict(set)
                for row in group:
                    suffixes[row.segments[position]].add("/".join(row.segments[position + 1 :]))
                variable = _mean_jaccard([suffixes[value] for value in values]) >= VARIABLE_MIN_JACCARD
        token = SLUG if is_leaf else VAR
        for row in group:
            row.resolved.append(token if variable else row.segments[position])

    @staticmethod
    def _summary(template: str, group: list[_Row]) -> TemplateSummary:
        variable_values: list[str] = []
        for row in group:
            for resolved, raw in zip(row.resolved, row.segments, strict=True):
                if resolved in VARIABLE_TOKENS and raw not in variable_values:
                    variable_values.append(raw)
            if len(variable_values) >= SUMMARY_VALUE_SAMPLE:
                break
        locations = sorted({row.location for row in group if row.location})
        return TemplateSummary(
            template_key=template,
            count=len(group),
            variable_values=variable_values[:SUMMARY_VALUE_SAMPLE],
            location_values=locations,
        )


def apply_saturation_caps(
    candidates: Sequence[UrlCandidate],
    *,
    per_template_cap: int,
    clustering: TemplateClustering,
    max_location_values: int = 4,
) -> list[UrlCandidate]:
    """keeps candidate order; templated pages capped per template, per location value and per location set."""
    kept: list[UrlCandidate] = []
    per_template: dict[str, int] = defaultdict(int)
    per_template_location: set[tuple[str, str]] = set()
    chosen_locations: list[str] = []
    for candidate in candidates:
        template = clustering.template_of.get(candidate.url) or candidate.template_key
        if template is None or not any(token in template for token in VARIABLE_TOKENS):
            kept.append(candidate)
            continue
        if per_template[template] >= per_template_cap:
            continue
        location = clustering.location_of.get(candidate.url)
        if location is not None:
            if (template, location) in per_template_location:
                continue
            if location not in chosen_locations:
                if len(chosen_locations) >= max_location_values:
                    continue
                chosen_locations.append(location)
            per_template_location.add((template, location))
        per_template[template] += 1
        kept.append(candidate)
    return kept
