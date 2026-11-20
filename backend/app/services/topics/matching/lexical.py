from collections.abc import Sequence
from dataclasses import dataclass, field, replace

from rapidfuzz import fuzz

from app.domain.topics import MatchEvidence, PreparedVideoText, TopicAlias, TopicKind
from app.services.topics.lexicon import PhraseSet
from app.services.topics.matching.base import MatchContext, MatcherDeps, MatcherResult, Pair, matcher_registry
from app.services.topics.text.language import NegationDetector
from app.services.topics.text.tokenize import Token, split_hashtag, tokenize
from app.services.topics.text.tokenize import stems as stems_of

FIELDS = ("title", "chapters", "description_head", "description_rest", "tags", "hashtags")
DESCRIPTION_FIELDS = frozenset({"description_head", "description_rest"})
TAG_FIELDS = frozenset({"tags", "hashtags"})
HEADLINE_FIELDS = frozenset({"title", "chapters", "description_head"})
QUALITY = {
    "exact": 1.0,
    "abbreviation": 0.9,
    "slop": 0.85,
    "phonetic": 0.85,
    "bag": 0.5,
    "entity_facet": 0.85,
    "entity_terms": 0.7,
}
CROSS_LINGUAL_KINDS = frozenset({"transliteration", "translation", "spelling_variant"})


@dataclass(frozen=True)
class _Entry:
    ref_id: str
    alias: TopicAlias
    stems: tuple[str, ...]
    phonetics: tuple[str, ...] | None
    specificity: float


@dataclass
class _Hit:
    entry: _Entry
    field: str
    start_token: int
    end_token: int
    start: int
    end: int
    hit_type: str
    quality: float
    sentence: int
    span_text: str
    negated: bool = False
    negative_context: bool = False
    list_mention: bool = False
    subsumed_by: str | None = None
    contribution: float = 0.0


@dataclass
class _Field:
    name: str
    text: str
    tokens: list[Token]
    brand_tokens: set[int] = field(default_factory=set)
    fuzzy: dict[int, dict[str, float]] = field(default_factory=dict)


class AliasIndex:
    """token-sequence index over every usable alias; matching never looks at raw substrings."""

    def __init__(self, ctx: MatchContext) -> None:
        settings = ctx.settings
        self.by_first: dict[str, list[_Entry]] = {}
        self.by_phonetic: dict[str, list[_Entry]] = {}
        self.by_any: dict[str, list[_Entry]] = {}
        # surface form -> stem of alias tokens long enough for fuzzy matching
        self.vocabulary: dict[str, str] = {}
        self.surface_words: set[str] = set()
        usable = {"confirmed", "proposed"} if settings.use_unconfirmed_aliases else {"confirmed"}
        for ref in ctx.refs:
            for alias in ref.aliases:
                if alias.status not in usable:
                    continue
                tokens = tokenize(alias.text)
                if not tokens:
                    continue
                stems = tuple(token.stem for token in tokens)
                phonetics = tuple(token.phonetic for token in tokens if token.phonetic)
                usable_phonetics = phonetics if len(phonetics) == len(tokens) else None
                entry = _Entry(ref.id, alias, stems, usable_phonetics, ctx.specificity(stems))
                self.by_first.setdefault(stems[0], []).append(entry)
                if usable_phonetics and alias.kind != "abbreviation":
                    self.by_phonetic.setdefault(usable_phonetics[0], []).append(entry)
                for stem in set(stems):
                    self.by_any.setdefault(stem, []).append(entry)
                for token in tokens:
                    if len(token.key) >= settings.fuzzy_min_len and not token.is_number:
                        self.vocabulary[token.key] = token.stem
                self.surface_words.update(token.key for token in tokens)


@matcher_registry.register("lexical")
class LexicalMatcher:
    name = "lexical"
    order = 10

    def __init__(self, deps: MatcherDeps | None = None) -> None:
        self._fuzzy_cache: dict[str, dict[str, float]] = {}

    async def score(
        self, videos: Sequence[PreparedVideoText], ctx: MatchContext
    ) -> dict[Pair, MatcherResult]:
        index = AliasIndex(ctx)
        brands = PhraseSet(ctx.non_topical_terms)
        negation = NegationDetector(ctx.lexicons, ctx.settings.negation_window)
        results: dict[Pair, MatcherResult] = {}
        for video in videos:
            for ref_id, result in self.score_video(video, ctx, index, brands, negation).items():
                results[(video.video_id, ref_id)] = result
                ctx.add_candidate(video.video_id, ref_id)
        return results

    def score_video(
        self,
        video: PreparedVideoText,
        ctx: MatchContext,
        index: AliasIndex,
        brands: PhraseSet,
        negation: NegationDetector,
    ) -> dict[str, MatcherResult]:
        fields = self._fields(video, index, brands, ctx)
        headline_keys = {
            token.stem
            for item in fields
            if item.name in {"title", "description_head"}
            for token in item.tokens
        }
        hits: list[_Hit] = []
        for item in fields:
            hits += self._field_hits(item, index, negation, ctx, headline_keys)
        self._subsume(hits, ctx)
        hits += self._question_hits(fields, hits, ctx)
        self._list_mentions(hits, ctx, video)
        weights = self._field_weights(video, ctx)
        by_ref: dict[str, list[_Hit]] = {}
        for hit in hits:
            hit.contribution = self._contribution(hit, weights, ctx)
            by_ref.setdefault(hit.entry.ref_id, []).append(hit)
        return {ref_id: self._result(ref_hits, ctx) for ref_id, ref_hits in by_ref.items()}

    def _fields(
        self, video: PreparedVideoText, index: AliasIndex, brands: PhraseSet, ctx: MatchContext
    ) -> list[_Field]:
        prepared = video.fields
        hashtag_vocabulary = index.surface_words | ctx.lexicons.hashtag_words
        hashtags = [
            split_hashtag(tag, hashtag_vocabulary) if " " not in tag and tag.isascii() else tag
            for tag in prepared.hashtags
        ]
        texts = {
            "title": prepared.title,
            "chapters": "\n".join(prepared.chapters),
            "description_head": prepared.description_head,
            "description_rest": prepared.description_rest,
            "tags": "\n".join(prepared.tags),
            "hashtags": "\n".join(hashtags),
        }
        fields = []
        for name in FIELDS:
            text = texts[name]
            if not text:
                continue
            tokens = tokenize(text)
            item = _Field(name=name, text=text, tokens=tokens)
            stems = [token.stem for token in tokens]
            for start, length in brands.find(stems):
                item.brand_tokens.update(range(start, start + length))
            item.fuzzy = self._fuzzy_alternatives(tokens, index, ctx)
            fields.append(item)
        return fields

    def _fuzzy_alternatives(
        self, tokens: Sequence[Token], index: AliasIndex, ctx: MatchContext
    ) -> dict[int, dict[str, float]]:
        settings = ctx.settings
        alternatives: dict[int, dict[str, float]] = {}
        for position, token in enumerate(tokens):
            key = token.key
            if len(key) < settings.fuzzy_min_len or token.stem in index.by_any or token.is_number:
                continue
            if key not in self._fuzzy_cache:
                matches: dict[str, float] = {}
                for surface, stem in index.vocabulary.items():
                    if abs(len(surface) - len(key)) > 2:
                        continue
                    score = fuzz.ratio(key, surface, score_cutoff=settings.fuzzy_ratio)
                    if score:
                        matches[stem] = max(score, matches.get(stem, 0.0))
                self._fuzzy_cache[key] = matches
            if self._fuzzy_cache[key]:
                alternatives[position] = self._fuzzy_cache[key]
        return alternatives

    def _field_hits(
        self,
        item: _Field,
        index: AliasIndex,
        negation: NegationDetector,
        ctx: MatchContext,
        headline_keys: set[str],
    ) -> list[_Hit]:
        tokens = item.tokens
        stems = [token.stem for token in tokens]
        phonetics = [token.phonetic for token in tokens]
        found: dict[tuple[str, int], _Hit] = {}

        def add(entry: _Entry, start: int, end: int, hit_type: str, quality: float) -> None:
            if any(position in item.brand_tokens for position in range(start, end)):
                return
            key = (entry.ref_id, start)
            current = found.get(key)
            if current is not None and current.quality >= quality:
                return
            found[key] = _Hit(
                entry=entry,
                field=item.name,
                start_token=start,
                end_token=end,
                start=tokens[start].start,
                end=tokens[end - 1].end,
                hit_type=hit_type,
                quality=quality,
                sentence=tokens[start].sentence,
                span_text=item.text[tokens[start].start : tokens[end - 1].end],
            )

        for position, stem in enumerate(stems):
            for entry in index.by_first.get(stem, ()):
                size = len(entry.stems)
                if tuple(stems[position : position + size]) != entry.stems:
                    continue
                if entry.alias.match_mode == "token_exact_case":
                    if all(tokens[i].is_upper for i in range(position, position + size)):
                        add(entry, position, position + size, "abbreviation", QUALITY["abbreviation"])
                    continue
                add(entry, position, position + size, "exact", QUALITY["exact"])
            phonetic = phonetics[position]
            for entry in index.by_phonetic.get(phonetic or "", ()):
                size = len(entry.stems)
                if entry.phonetics and tuple(phonetics[position : position + size]) == entry.phonetics:
                    add(entry, position, position + size, "phonetic", QUALITY["phonetic"])
        present = set(stems)
        candidates = {entry for stem in present for entry in index.by_any.get(stem, ())}
        candidates |= {
            entry
            for alternatives in item.fuzzy.values()
            for stem in alternatives
            for entry in index.by_any.get(stem, ())
        }
        for entry in candidates:
            if entry.alias.match_mode == "token_exact_case" or any(key[0] == entry.ref_id for key in found):
                continue
            self._loose_hits(entry, item, stems, ctx, add)
        hits = list(found.values())
        for hit in hits:
            hit.negated = negation.is_negated(tokens, hit.start_token)
            ref = ctx.refs_by_id[hit.entry.ref_id]
            if hit.entry.alias.ambiguous and ref.negative_contexts:
                hit.negative_context = any(
                    key in headline_keys for term in ref.negative_contexts for key in stems_of(term)
                )
        return hits

    def _loose_hits(self, entry: _Entry, item: _Field, stems: list[str], ctx: MatchContext, add) -> None:
        size = len(entry.stems)
        tokens = item.tokens
        for position in range(len(stems) - size + 1):
            ratios = []
            for offset, wanted in enumerate(entry.stems):
                actual = stems[position + offset]
                if actual == wanted:
                    continue
                ratio = item.fuzzy.get(position + offset, {}).get(wanted)
                if ratio is None:
                    break
                ratios.append(ratio)
            else:
                if ratios:
                    add(entry, position, position + size, "fuzzy", 0.7 * min(ratios) / 100)
                    return
        if size < 2:
            return
        window = size + ctx.settings.slop
        for position, stem in enumerate(stems):
            if stem != entry.stems[0]:
                continue
            cursor, matched = position, 1
            for wanted in entry.stems[1:]:
                nxt = next(
                    (
                        i
                        for i in range(cursor + 1, min(len(stems), position + window))
                        if stems[i] == wanted and tokens[i].sentence == tokens[position].sentence
                    ),
                    None,
                )
                if nxt is None:
                    break
                cursor, matched = nxt, matched + 1
            if matched == size:
                add(entry, position, cursor + 1, "slop", QUALITY["slop"])
                return
        if set(entry.stems) <= set(stems):
            first = min(stems.index(stem) for stem in entry.stems)
            last = max(stems.index(stem) for stem in entry.stems)
            add(entry, first, last + 1, "bag", QUALITY["bag"])

    def _question_hits(self, fields: list[_Field], hits: list[_Hit], ctx: MatchContext) -> list[_Hit]:
        # a buyer question is evidenced where its topic and one of its facet cues share a field
        found = []
        for ref in ctx.refs:
            if ref.kind is not TopicKind.buyer_question or not ref.facets:
                continue
            core = {key for term in ref.core_entity_terms for key in stems_of(term)}
            for item in fields:
                keys = [token.stem for token in item.tokens]
                if not any(ctx.lexicons.facets[facet].contains(keys) for facet in ref.facets):
                    continue
                entity = max(
                    (
                        hit
                        for hit in hits
                        if hit.field == item.name
                        and hit.entry.ref_id in ref.parent_ids
                        and not hit.subsumed_by
                    ),
                    key=lambda hit: hit.quality * hit.entry.alias.weight,
                    default=None,
                )
                quality = QUALITY["entity_facet"]
                if entity is None and core and core <= set(keys):
                    canonical = next(alias for alias in ref.aliases if alias.kind == "canonical")
                    first = min(keys.index(key) for key in core)
                    last = max(keys.index(key) for key in core)
                    token_first, token_last = item.tokens[first], item.tokens[last]
                    entity = _Hit(
                        entry=_Entry(
                            ref.id, canonical, tuple(sorted(core)), None, ctx.specificity(sorted(core))
                        ),
                        field=item.name,
                        start_token=first,
                        end_token=last + 1,
                        start=token_first.start,
                        end=token_last.end,
                        hit_type="entity_terms",
                        quality=QUALITY["bag"],
                        sentence=token_first.sentence,
                        span_text=item.text[token_first.start : token_last.end],
                    )
                    quality = QUALITY["entity_terms"]
                if entity is None:
                    continue
                found.append(
                    replace(
                        entity,
                        entry=replace(entity.entry, ref_id=ref.id),
                        hit_type="entity_facet",
                        quality=quality * entity.quality,
                        subsumed_by=None,
                    )
                )
        return found

    def _subsume(self, hits: list[_Hit], ctx: MatchContext) -> None:
        # longest match wins across refs; a parent of the longer ref is exempt (it is implied anyway)
        for hit in hits:
            for other in hits:
                if other.entry.ref_id == hit.entry.ref_id or other.field != hit.field:
                    continue
                longer = (other.end_token - other.start_token) > (hit.end_token - hit.start_token)
                inside = other.start_token <= hit.start_token and hit.end_token <= other.end_token
                if longer and inside:
                    longer_ref = ctx.refs_by_id[other.entry.ref_id]
                    if hit.entry.ref_id not in longer_ref.parent_ids:
                        hit.subsumed_by = other.entry.ref_id
                        break

    def _list_mentions(self, hits: list[_Hit], ctx: MatchContext, video: PreparedVideoText) -> None:
        if "boilerplate_unprofiled" not in video.quality_flags:
            return
        segments: dict[tuple[str, int], set[str]] = {}
        for hit in hits:
            if hit.field in DESCRIPTION_FIELDS:
                segments.setdefault((hit.field, hit.sentence), set()).add(hit.entry.ref_id)
        crowded = {key for key, refs in segments.items() if len(refs) >= ctx.settings.list_mention_min_refs}
        for hit in hits:
            if (hit.field, hit.sentence) in crowded:
                hit.list_mention = True

    def _field_weights(self, video: PreparedVideoText, ctx: MatchContext) -> dict[str, float]:
        settings = ctx.settings
        weights = dict(settings.field_weights)
        if "tag_stuffed" in video.quality_flags:
            weights["tags"] = weights["tags"] * settings.tag_stuffed_factor
        if "boilerplate_unprofiled" in video.quality_flags:
            for name in DESCRIPTION_FIELDS:
                weights[name] = weights[name] * settings.unprofiled_description_factor
        return weights

    def _contribution(self, hit: _Hit, weights: dict[str, float], ctx: MatchContext) -> float:
        settings = ctx.settings
        value = weights[hit.field] * hit.quality * hit.entry.specificity * hit.entry.alias.weight
        if hit.negated:
            value *= settings.negation_factor
        if hit.subsumed_by:
            value *= settings.subsume_factor
        if hit.negative_context:
            value *= settings.negative_context_factor
        if hit.list_mention:
            value *= settings.list_mention_factor
        return value

    def _result(self, hits: list[_Hit], ctx: MatchContext) -> MatcherResult:
        settings = ctx.settings
        counted: list[_Hit] = []
        for name in FIELDS:
            in_field = sorted((hit for hit in hits if hit.field == name), key=lambda hit: -hit.contribution)
            counted += in_field[: settings.max_hits_per_field]
        miss = 1.0
        for hit in counted:
            miss *= 1.0 - hit.contribution
        flags = set()
        fields = {hit.field for hit in counted}
        if fields <= TAG_FIELDS:
            flags.add("tags_only")
        if all(hit.negated for hit in counted):
            flags.add("all_negated")
        if any(hit.negated for hit in counted):
            flags.add("negated")
        if all(hit.entry.alias.ambiguous for hit in counted):
            flags.add("ambiguous_only")
        if any(hit.negative_context for hit in counted):
            flags.add("negative_context")
        if all(hit.entry.alias.status == "proposed" for hit in counted):
            flags.add("proposed_only")
        if all(hit.entry.specificity < settings.spec_weak for hit in counted):
            flags.add("weak_only")
        if all(hit.subsumed_by for hit in counted):
            flags.add("subsumed_only")
        if any(hit.list_mention for hit in counted):
            flags.add("list_mention")
        if "title" in fields:
            flags.add("title_hit")
        if all(hit.hit_type == "phonetic" or hit.entry.alias.kind in CROSS_LINGUAL_KINDS for hit in counted):
            flags.add("cross_lingual_only")
        if fields & HEADLINE_FIELDS and any(
            hit.field in HEADLINE_FIELDS and not hit.negated and not hit.entry.alias.ambiguous
            for hit in counted
        ):
            flags.add("clean_headline_hit")
        evidence = [self._evidence(hit) for hit in sorted(counted, key=lambda hit: -hit.contribution)]
        detail = {
            "max_specificity": max(hit.entry.specificity for hit in counted),
            "alias_tokens": max(len(hit.entry.stems) for hit in counted),
            "fields": sorted(fields),
        }
        return MatcherResult(score=1.0 - miss, evidence=evidence, flags=frozenset(flags), detail=detail)

    def _evidence(self, hit: _Hit) -> MatchEvidence:
        return MatchEvidence(
            method="lexical",
            field=hit.field,
            span_text=hit.span_text,
            start=hit.start,
            end=hit.end,
            alias_text=hit.entry.alias.text,
            alias_kind=hit.entry.alias.kind,
            alias_status=hit.entry.alias.status,
            match_quality=min(1.0, hit.quality),
            negated=hit.negated,
            subsumed_by_ref=hit.subsumed_by,
            detail={
                "hit_type": hit.hit_type,
                "specificity": round(hit.entry.specificity, 4),
                "contribution": round(hit.contribution, 4),
                "negative_context": hit.negative_context,
                "list_mention": hit.list_mention,
            },
        )
