import re
import unicodedata
from collections.abc import Callable, Iterable, Sequence
from urllib.parse import urlsplit

from rapidfuzz import fuzz

from app.domain.profile import ClaimMeta, ConversionAction, Location, Offering, ValueProposition
from app.services.website_intel.profile.evidence import numbers_in
from app.services.website_intel.profile.market import REGIONS

_DASHES = re.compile(r"[‐-―−⁃﹘﹣－]")
_SPACES = re.compile(r"[\s ​  　﻿]+")
_TOKEN = re.compile(r"[^\W_]+")
# british and american spellings compare equal; the shown label keeps the site's spelling
SPELLING = {
    "speciality": "specialty",
    "specialities": "specialties",
    "multispeciality": "multispecialty",
    "specialised": "specialized",
    "centre": "center",
    "centres": "centers",
    "paediatric": "pediatric",
    "paediatrics": "pediatrics",
    "orthopaedic": "orthopedic",
    "orthopaedics": "orthopedics",
    "gynaecology": "gynecology",
    "gynaecological": "gynecological",
    "gynaeoncology": "gyneoncology",
    "haematology": "hematology",
    "anaesthesia": "anesthesia",
    "anaesthesiology": "anesthesiology",
    "oesophageal": "esophageal",
    "programme": "program",
    "programmes": "programs",
    "tumour": "tumor",
    "tumours": "tumors",
    "labour": "labor",
    "counselling": "counseling",
    "organisation": "organization",
}
COMPANY_WORDS = frozenset(
    {
        "the",
        "group",
        "hospital",
        "hospitals",
        "clinic",
        "clinics",
        "health",
        "healthcare",
        "ltd",
        "limited",
        "inc",
    }
)
NAME_STOP = frozenset({"the", "and", "of", "for", "a", "an", "our", "at", "in", "on", "by", "with", "to"})
STATEMENT_WORDS = frozenset(
    {"located", "situated", "has", "have", "operations", "operates", "serves", "available", "offers"}
    | {"provides", "including", "includes", "equipped"}
)
# words that describe any facility of the business; a residual made only of these names no place in particular
FACILITY_DESCRIPTORS = frozenset(
    {"hospital", "super", "multi", "specialty", "multispecialty", "branch", "campus", "facility", "unit"}
    | {"location", "center", "available", "operation", "medical"}
)
LOCATION_FILLER = frozenset({"location", "address"})
STREET_WORDS = frozenset(
    {"road", "rd", "street", "st", "sector", "marg", "lane", "nagar", "block", "plot", "floor"}
)
FACILITY_KINDS = frozenset({"hospital", "clinic", "store", "office"})
KIND_RANK = {"hospital": 0, "clinic": 1, "office": 2, "store": 3, "branch": 4, "other": 5, "service_area": 6}
VALUE_PROP_MATCH = 90
MAX_ALIASES = 10
MIN_TYPO_WORD = 6
WORD_TYPO_MATCH = 88


def canonical_tokens(text: str) -> tuple[str, ...]:
    """nfkc, unicode dashes and spaces unified, casefolded, '&' as 'and', spelling variants folded."""
    text = _DASHES.sub("-", unicodedata.normalize("NFKC", text)).replace("&", " and ")
    return tuple(SPELLING.get(token, token) for token in _TOKEN.findall(_SPACES.sub(" ", text).casefold()))


def stem(token: str) -> str:
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def brand_tokens(names: Iterable[str]) -> frozenset[str]:
    """the leading distinctive word of each company name ('medanta', 'apollo'), stripped only for comparison."""
    found = set()
    for name in names:
        tokens = [token for token in canonical_tokens(name) if token not in COMPANY_WORDS]
        if tokens and len(tokens[0]) >= 4:
            found.add(tokens[0])
    return frozenset(found)


def comparison_key(text: str, brands: frozenset[str] = frozenset()) -> tuple[str, ...]:
    tokens = [stem(token) for token in canonical_tokens(text) if token not in NAME_STOP]
    stripped = [token for token in tokens if token not in brands]
    return tuple(sorted(stripped or tokens))


def same_name(a: tuple[str, ...], b: tuple[str, ...]) -> bool:
    """equal word lists, or ones whose differing words are misspellings of each other ("gastroscience"/
    "gastrosciences" yes; "liver"/"lung" and "men"/"women" no)."""
    if not a or not b:
        return False
    if a == b:
        return True
    only_a, only_b = sorted(set(a) - set(b)), sorted(set(b) - set(a))
    if len(a) != len(b) or len(only_a) != len(only_b) or numbers_in(" ".join(a)) != numbers_in(" ".join(b)):
        return False
    return all(
        min(len(left), len(right)) >= MIN_TYPO_WORD and fuzz.ratio(left, right) >= WORD_TYPO_MATCH
        for left, right in zip(only_a, only_b, strict=True)
    )


def merged_meta(metas: Sequence[ClaimMeta]) -> ClaimMeta:
    """the first meta with the union of every evidence quote, so each kept quote is still verbatim."""
    evidence, seen = [], set()
    for meta in metas:
        for item in meta.evidence:
            key = (item.source_url, item.quote)
            if key not in seen:
                seen.add(key)
                evidence.append(item)
    return metas[0].model_copy(
        update={
            "evidence": evidence,
            "basis_ids": list(dict.fromkeys(basis for meta in metas for basis in meta.basis_ids)),
            "confidence": max(meta.confidence for meta in metas),
            "locked": any(meta.locked for meta in metas),
        }
    )


def _groups[T](items: Sequence[T], same: Callable[[T, T], bool]) -> list[list[int]]:
    parent = list(range(len(items)))

    def root(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    for i in range(len(items)):
        for j in range(i):
            if root(i) != root(j) and same(items[j], items[i]):
                parent[root(i)] = root(j)
    groups: dict[int, list[int]] = {}
    for index in range(len(items)):
        groups.setdefault(root(index), []).append(index)
    return list(groups.values())


def _aliases(best: str, names: Iterable[str], existing: Iterable[str] = ()) -> list[str]:
    seen = {canonical_tokens(best)}
    result = []
    for name in [*existing, *names]:
        key = canonical_tokens(name)
        if key and key not in seen:
            seen.add(key)
            result.append(name)
    return result[:MAX_ALIASES]


def merge_offerings(
    items: Sequence[Offering], brands: frozenset[str]
) -> tuple[list[Offering], dict[str, str]]:
    """one offering per canonical name; returns the kept items and a dropped id -> kept id map."""
    keys = [comparison_key(item.name, brands) for item in items]
    groups = _groups(list(range(len(items))), lambda a, b: same_name(keys[a], keys[b]))
    kept, remap = [], {}
    for group in sorted(groups, key=min):
        members = [items[index] for index in group]
        best = min(members, key=lambda item: (not item.is_primary, item.url is None, item.kind == "other"))
        prices = {price.raw_text: price for member in members for price in member.price_signals}
        kept.append(
            best.model_copy(
                update={
                    "url": best.url or next((member.url for member in members if member.url), None),
                    "description": best.description
                    or next((member.description for member in members if member.description), None),
                    "is_primary": any(member.is_primary for member in members),
                    "priority": min(member.priority for member in members),
                    "price_signals": list(prices.values()),
                    "aliases": _aliases(best.name, (member.name for member in members), best.aliases),
                    "meta": merged_meta(
                        [best.meta, *(member.meta for member in members if member is not best)]
                    ),
                }
            )
        )
        remap.update({member.id: best.id for member in members if member.id != best.id})
    return kept, remap


def merge_value_props(items: Sequence[ValueProposition]) -> list[ValueProposition]:
    keys = [comparison_key(item.statement) for item in items]

    def same(a: int, b: int) -> bool:
        if numbers_in(items[a].statement) != numbers_in(items[b].statement):
            return False
        return (
            keys[a] == keys[b]
            or fuzz.token_sort_ratio(" ".join(keys[a]), " ".join(keys[b])) >= VALUE_PROP_MATCH
        )

    kept = []
    for group in sorted(_groups(list(range(len(items))), same), key=min):
        members = [items[index] for index in group]
        best = members[0]
        kept.append(
            best.model_copy(
                update={
                    "proof_points": list(dict.fromkeys(p for member in members for p in member.proof_points))[
                        :5
                    ],
                    "offering_ids": list(dict.fromkeys(o for member in members for o in member.offering_ids)),
                    "meta": merged_meta([member.meta for member in members]),
                }
            )
        )
    return kept


def contact_key(action: ConversionAction) -> tuple[str, str]:
    target = action.target.strip()
    if target.lower().startswith(("tel:", "mailto:")):
        return action.type, target.lower()
    parts = urlsplit(target if "://" in target else f"https://{target}")
    host = (parts.hostname or "").lower().removeprefix("www.")
    return action.type, f"{host}{parts.path.rstrip('/')}"


def merge_conversions(items: Sequence[ConversionAction]) -> list[ConversionAction]:
    """one action per type and target (scheme, www, query and trailing slash ignored), and one per type and
    label for web actions ("Book Appointment" twice reads as a duplicate); the first is the most prominent."""
    keys = [contact_key(item) for item in items]
    labels = [canonical_tokens(item.label) if item.medium == "web" else None for item in items]

    def same(a: int, b: int) -> bool:
        same_label = labels[a] is not None and labels[a] == labels[b] and items[a].type == items[b].type
        return keys[a] == keys[b] or same_label

    kept = []
    for group in sorted(_groups(list(range(len(items))), same), key=min):
        members = [items[index] for index in group]
        best = members[0]
        kept.append(
            best.model_copy(
                update={
                    "is_primary": any(member.is_primary for member in members),
                    "offering_ids": list(dict.fromkeys(o for member in members for o in member.offering_ids)),
                    "prominence": best.prominence.model_copy(
                        update={
                            "pages_seen": max(member.prominence.pages_seen for member in members),
                            "on_home": any(member.prominence.on_home for member in members),
                            "in_header": any(member.prominence.in_header for member in members),
                            "in_footer": any(member.prominence.in_footer for member in members),
                        }
                    ),
                    "meta": merged_meta([member.meta for member in members]),
                }
            )
        )
    return kept


class _Place:
    """a location read for comparison: the places it names and what is left of its name."""

    def __init__(self, index: int, item: Location, known: Sequence[tuple[str, ...]], brands: frozenset[str]):
        self.index = index
        self.item = item
        tokens = canonical_tokens(item.name)
        named = {place for place in known if _contains(tokens, place)}
        fields = {canonical_tokens(value) for value in (item.city, item.region) if value}
        places = (named | fields) - {()}
        self.regions = {place for place in places if " ".join(place) in REGIONS}
        self.cities = places - self.regions
        for place in sorted(known, key=len, reverse=True):
            tokens = _remove(tokens, place)
        self.statement = any(token in STATEMENT_WORDS for token in tokens)
        self.residual = tuple(
            sorted({stem(t) for t in tokens if t not in brands and t not in NAME_STOP | LOCATION_FILLER})
        )
        words = set(self.residual) - STATEMENT_WORDS
        self.address = bool(words & STREET_WORDS) and any(token.isdigit() for token in tokens)
        self.generic = all(token in FACILITY_DESCRIPTORS for token in words)
        # a named facility kind from the reduce step is a facility even when its name is only brand + city
        self.facility = not (self.statement or self.address) and (
            not self.generic or (item.kind in FACILITY_KINDS and bool(self.cities))
        )

    @property
    def rank(self) -> tuple[int, ...]:
        return (
            self.statement,
            self.address,
            not self.facility,
            KIND_RANK.get(self.item.kind, 7),
            not self.cities,
            self.index,
        )

    def same_facility(self, other: "_Place") -> bool:
        if self.cities and other.cities and not self.cities & other.cities:
            return False
        if self.regions and other.regions and not self.regions & other.regions:
            return False
        a, b = set(self.residual), set(other.residual)
        if a == b or same_name(self.residual, other.residual):
            return True
        # "mediclinic at defence colony" and "mediclinic at e-15 defence colony": the extra words are an address
        small, large = (a, b) if len(a) <= len(b) else (b, a)
        extra = large - small
        return bool(small) and small < large and all(token.isdigit() or len(token) == 1 for token in extra)


def _contains(tokens: tuple[str, ...], phrase: tuple[str, ...]) -> bool:
    size = len(phrase)
    return any(tokens[i : i + size] == phrase for i in range(len(tokens) - size + 1))


def _remove(tokens: tuple[str, ...], phrase: tuple[str, ...]) -> tuple[str, ...]:
    size, result, i = len(phrase), [], 0
    while i < len(tokens):
        if tokens[i : i + size] == phrase:
            i += size
        else:
            result.append(tokens[i])
            i += 1
    return tuple(result)


def merge_locations(items: Sequence[Location], brands: frozenset[str]) -> list[Location]:
    """one entry per facility: spelling, dash and brand variants merge; sentences that only place the
    business in a city fold into that city's facility. different facilities in one city stay apart."""
    known = sorted(
        {canonical_tokens(value) for item in items for value in (item.city, item.region) if value} - {()},
        key=len,
        reverse=True,
    )
    places = [_Place(index, item, known, brands) for index, item in enumerate(items)]
    facilities = [place for place in places if place.facility]
    loose = [place for place in places if not place.facility]
    groups = [
        [facilities[i] for i in group] for group in _groups(facilities, lambda a, b: a.same_facility(b))
    ]
    leftover: list[_Place] = []
    for place in loose:
        hosts = [
            group
            for group in groups
            if any(member.cities & place.cities for member in group)
            or (
                not place.cities and place.regions and any(member.regions & place.regions for member in group)
            )
        ]
        if hosts:
            min(hosts, key=lambda group: min(member.rank for member in group)).append(place)
        else:
            leftover.append(place)
    for place in leftover:
        target = next(
            (
                group
                for group in groups
                if not group[0].facility
                and (
                    group[0].cities & place.cities or (not place.cities and group[0].regions & place.regions)
                )
            ),
            None,
        )
        if target is not None and (place.cities or place.regions):
            target.append(place)
        else:
            groups.append([place])
    return [_merge_location(group) for group in sorted(groups, key=lambda group: min(m.index for m in group))]


def _merge_location(group: list[_Place]) -> Location:
    ranked = sorted(group, key=lambda place: place.rank)
    best = ranked[0].item
    items = [place.item for place in ranked]
    city = next(
        (item.city for item in items if item.city and " ".join(canonical_tokens(item.city)) not in REGIONS),
        None,
    )
    region = best.region or next(
        (
            value
            for item in items
            for value in (item.region, item.city)
            if value and " ".join(canonical_tokens(value)) in REGIONS
        ),
        None,
    )

    def first(field: str) -> object:
        return next((getattr(item, field) for item in items if getattr(item, field)), None)

    names = [
        place.item.name for place in ranked[1:] if not (place.statement or place.address or place.generic)
    ]
    return best.model_copy(
        update={
            "city": city,
            "region": region,
            "country": first("country"),
            "address": first("address"),
            "phone": first("phone"),
            "url": first("url"),
            "geo": first("geo"),
            "aliases": _aliases(best.name, names, best.aliases),
            "meta": merged_meta([item.meta for item in items]),
        }
    )
