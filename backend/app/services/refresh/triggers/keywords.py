from collections.abc import Iterable
from typing import ClassVar

import regex

from app.domain.refresh import Advisory, ClaimLevel, TriggerEvidence
from app.domain.topics import TopicKind, TopicRef
from app.services.refresh.context import RefreshContext
from app.services.refresh.eligibility import VideoEligibility, VideoState
from app.services.refresh.evidence import VideoContentEvidence
from app.services.refresh.text import KeywordPresence, content_tokens
from app.services.refresh.triggers.base import Finding, TriggerKind, dedupe_key, register_detector

YEAR = r"(20\d{2})"
EVERGREEN_YEAR = regex.compile(
    rf"(?:\b(?:in|for|of)\s+{YEAR}\b|\b{YEAR}\s+(?:guide|list|update|edition|review|tips|plan|roadmap)\b"
    rf"|[(\[]\s*{YEAR}\s*[)\]])",
    regex.IGNORECASE,
)
DATED_CONTEXT = regex.compile(
    rf"(?:\b(?:on|since|from|est\.?|established|founded|©|copyright)\s+(?:\d{{1,2}}\s+\w+\s+)?{YEAR}\b"
    rf"|{YEAR}\s*[-–—/]\s*{YEAR}|\b\d{{1,2}}[/.-]\d{{1,2}}[/.-]{YEAR}\b)",
    regex.IGNORECASE,
)
EXPIRED_NOTICE = regex.compile(
    r"\b(live on|join us on|register by|webinar on|streaming on|premieres? on)\b", regex.IGNORECASE
)
TITLE_HEAD = regex.compile(r"\s*[|:–—]\s*|\s+-\s+")


def title_head(title: str) -> str:
    return TITLE_HEAD.split(title.strip(), maxsplit=1)[0].strip().rstrip(".!?")


def stale_years(text: str, current_year: int) -> list[int]:
    dated = {int(year) for match in DATED_CONTEXT.finditer(text) for year in match.groups() if year}
    years = [int(year) for match in EVERGREEN_YEAR.finditer(text) for year in match.groups() if year]
    return [year for year in years if year < current_year and year not in dated]


def topic_phrases(ref: TopicRef) -> list[str]:
    confirmed = [alias.text for alias in ref.aliases if alias.status == "confirmed" and not alias.ambiguous]
    return list(dict.fromkeys([ref.label, *confirmed]))


def covered(phrase: str, evidence: VideoContentEvidence) -> bool:
    terms = content_tokens(phrase)
    return bool(terms) and all(term in evidence.covered_tokens for term in terms)


def covered_phrases(phrases: Iterable[str], evidence: VideoContentEvidence) -> list[str]:
    return [phrase for phrase in phrases if covered(phrase, evidence)]


@register_detector
class StaleYearDetector:
    kind: ClassVar[TriggerKind] = TriggerKind.stale_year

    def detect(
        self, state: VideoState, gate: VideoEligibility, evidence: VideoContentEvidence, ctx: RefreshContext
    ) -> list[Finding]:
        settings = ctx.settings
        if not gate.keyword_eligible or state.is_event_video:
            return []
        if state.age_days < settings.stale_year_min_age_days:
            return []
        if ctx.now.timetuple().tm_yday < settings.stale_year_from_day_of_year:
            return []
        current = ctx.now.year
        structure = evidence.structure
        first_paragraph = (
            ""
            if structure.first_paragraph_end_line is None
            else "\n".join(structure.lines[: structure.first_paragraph_end_line + 1])
        )
        in_title = stale_years(state.video.title, current)
        in_description = stale_years(first_paragraph, current)
        if not in_title and not in_description:
            return []
        year = (in_title or in_description)[0]
        advisories = ()
        if in_title:
            advisories = (
                Advisory(
                    kind="title_stale_year",
                    message=(
                        f"The title says {year}. Only change it if the content is still accurate for {current}; "
                        "changing the year alone can mislead viewers."
                    ),
                    risk="high",
                ),
            )
        newer = self._newer_own_video(state, evidence, ctx, current)
        demand = ctx.demand.collect(
            str(current),
            extra_filter=lambda ref: bool(set(ref.matched_topic_ids) & evidence.covered_topic_refs),
        )
        return [
            Finding(
                kind=self.kind,
                video_id=state.video_id,
                dedupe_key=dedupe_key(self.kind, str(year)),
                strength=1.0 if in_title else 0.7,
                demand=0.5 + 0.5 * min(1.0, len(demand.outlier_refs) / settings.min_refs),
                urgency=1.0,
                evidence=TriggerEvidence(
                    reference_video_ids=[ref.video.video_id for ref in demand.outlier_refs],
                    defect=f"mentions {year} as if it were current",
                ),
                keyword=str(current) if newer else None,
                claim_level=ClaimLevel.l0_navigational if newer else None,
                params={"newer_video_id": newer, "current_year": str(current)} if newer else {},
                advisories=advisories,
                demand_evidence=demand,
            )
        ]

    @staticmethod
    def _newer_own_video(
        state: VideoState, evidence: VideoContentEvidence, ctx: RefreshContext, current: int
    ) -> str | None:
        candidates = []
        for other in ctx.states.values():
            if (
                other.video_id == state.video_id
                or not other.is_public
                or other.video.published_at.year != current
            ):
                continue
            topics = {
                match.topic_ref_id
                for match in ctx.matches_by_video.get(other.video_id, ())
                if match.accepted and match.combined_score >= ctx.settings.fit_threshold
            }
            if topics & evidence.covered_topic_refs:
                candidates.append(other)
        if not candidates:
            return None
        # a video that names the current year is the natural "update"; traffic breaks ties
        return max(
            candidates, key=lambda other: (str(current) in other.video.title, other.velocity or 0.0)
        ).video_id


@register_detector
class BuyerQuestionGapDetector:
    kind: ClassVar[TriggerKind] = TriggerKind.buyer_question_gap

    def detect(
        self, state: VideoState, gate: VideoEligibility, evidence: VideoContentEvidence, ctx: RefreshContext
    ) -> list[Finding]:
        if not gate.keyword_eligible or state.age_days < ctx.settings.min_age_days:
            return []
        video = state.video
        description = KeywordPresence(video.description)
        title = KeywordPresence(video.title)
        owner_terms = ctx.owner_search_terms.get(video.video_id, ())
        findings = []
        for ref_id, score in evidence.topic_scores.items():
            ref = ctx.topic_refs.get(ref_id)
            if ref is None or ref.kind is not TopicKind.buyer_question:
                continue
            phrases = topic_phrases(ref)
            if description.contains_any(phrases):
                continue
            best = None
            for phrase in covered_phrases(phrases, evidence):
                demand = ctx.demand.collect(phrase, (), owner_terms, topic_ref_id=ref.id)
                if best is None or len(demand.outlier_refs) > len(best.outlier_refs):
                    best = demand
            if best is None:
                continue
            strength = score * (
                ctx.settings.title_presence_relevance_factor if title.contains(best.keyword) else 1.0
            )
            findings.append(
                Finding(
                    kind=self.kind,
                    video_id=video.video_id,
                    dedupe_key=dedupe_key(self.kind, best.keyword),
                    strength=strength,
                    demand=best.demand,
                    urgency=0.6,
                    evidence=TriggerEvidence(
                        reference_video_ids=[item.video.video_id for item in best.outlier_refs],
                        owner_search_terms=list(owner_terms) if best.owner_search_hit else [],
                    ),
                    keyword=best.keyword,
                    claim_level=ClaimLevel.l2_coverage,
                    params={"phrase": best.keyword, "title_head": title_head(video.title)},
                    demand_evidence=best,
                )
            )
        return findings


@register_detector
class AboveFoldKeywordDetector:
    kind: ClassVar[TriggerKind] = TriggerKind.above_fold_keyword

    def detect(
        self, state: VideoState, gate: VideoEligibility, evidence: VideoContentEvidence, ctx: RefreshContext
    ) -> list[Finding]:
        settings = ctx.settings
        if not gate.keyword_eligible or state.age_days < settings.min_age_days:
            return []
        primary = self._primary_ref(evidence, ctx)
        if primary is None:
            return []
        phrases = covered_phrases(topic_phrases(primary), evidence)
        if not phrases:
            return []
        description = state.video.description
        head = description[: settings.above_fold_chars]
        head_presence = KeywordPresence(head)
        structure = evidence.structure
        head_is_footer = (
            structure.is_empty
            or structure.only_boilerplate
            or bool(structure.lines and EXPIRED_NOTICE.search(structure.lines[0]))
        )
        if head_presence.contains_any(phrases) and not head_is_footer:
            return []
        keyword = phrases[0]
        owner_terms = ctx.owner_search_terms.get(state.video_id, ())
        demand = ctx.demand.collect(keyword, tuple(phrases[1:]), owner_terms)
        return [
            Finding(
                kind=self.kind,
                video_id=state.video_id,
                dedupe_key=dedupe_key(self.kind, keyword),
                strength=0.7,
                demand=min(1.0, 0.3 + 0.7 * demand.demand),
                urgency=0.4,
                evidence=TriggerEvidence(
                    reference_video_ids=[ref.video.video_id for ref in demand.outlier_refs],
                    owner_search_terms=list(owner_terms) if demand.owner_search_hit else [],
                    defect=f"'{keyword}' is not in the first {settings.above_fold_chars} characters",
                ),
                keyword=keyword,
                aliases=tuple(phrases[1:]),
                claim_level=ClaimLevel.l2_coverage,
                params={"phrase": keyword, "title_head": title_head(state.video.title)},
                demand_evidence=demand,
            )
        ]

    @staticmethod
    def _primary_ref(evidence: VideoContentEvidence, ctx: RefreshContext) -> TopicRef | None:
        ranked = sorted(evidence.topic_scores.items(), key=lambda item: item[1], reverse=True)
        for ref_id, _ in ranked:
            ref = ctx.topic_refs.get(ref_id)
            if ref is not None and ref.kind in (TopicKind.keyword, TopicKind.offering):
                return ref
        return None
