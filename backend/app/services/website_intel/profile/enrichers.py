import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from app.domain.profile import (
    ClaimOrigin,
    ContentPillar,
    Evidence,
    FactKind,
    FreshnessDriver,
    FreshnessProfile,
    NicheKeyword,
    SeasonalWindow,
    SiteChangeStats,
    SourceHint,
    WatchEntity,
    WatchTopic,
)
from app.domain.web import ExtractionKind, SourceKind
from app.ports.buyer_questions import BuyerQuestionCandidate
from app.ports.llm import PayloadClass, PromptInput, TaskId, TrustedBlock, UntrustedBlock
from app.services.website_intel.extract.injection import contains_instruction
from app.services.website_intel.extract.language import dominant_script
from app.services.website_intel.inputs.urls import path_of
from app.services.website_intel.models import ExtractedPage, InventoryUrl, PageType
from app.services.website_intel.profile.claims import ClaimFactory, normalise_name, stable_id
from app.services.website_intel.profile.evidence import FactStore
from app.services.website_intel.profile.industry import (
    DEFAULT_PRIOR,
    INDUSTRY_PRIORS,
    REFRESH_POLICIES,
    REGULATED_INDUSTRIES,
    bucket,
)
from app.services.website_intel.profile.llm import LlmGateway
from app.services.website_intel.profile.schemas import EnrichmentOutput, FreshnessOutput
from app.services.website_intel.profile.synthesizer import CoreBundle

# sized so one reply (keywords before questions) stays well under the 12k-token task cap
CAPS = {"seed": 50, "expanded": 60, "questions": 40, "pillars": 7}
EXTRACTION_SOURCE = {
    ExtractionKind.page_text: "page_text",
    ExtractionKind.nav: "nav",
    ExtractionKind.json_ld: "json_ld",
    ExtractionKind.meta: "title",
    ExtractionKind.url_inventory: "url_inventory",
    ExtractionKind.channel_metadata: "youtube_channel_keywords",
    ExtractionKind.user_input: "user",
}
DATED_TYPES = frozenset({PageType.blog_post, PageType.news_press})
FALLBACK_QUESTION_TEMPLATES = (
    ("How much does {name} cost?", "decision", "cost"),
    ("How do I book {name}?", "decision", "booking_logistics"),
)


@dataclass
class Enrichment:
    keywords: list[NicheKeyword]
    questions: list[BuyerQuestionCandidate]
    pillars: list[tuple[ContentPillar, list[str]]]
    used_llm: bool
    dropped: int = 0


@dataclass
class EnrichmentContext:
    store: FactStore
    core: CoreBundle
    claims: ClaimFactory
    inventory: Sequence[InventoryUrl]
    slug_vocabulary: Sequence[str]
    india: bool


def _profile_digest(core: CoreBundle) -> str:
    lines = [
        f"Business: {core.identity.name} — {core.identity.one_liner}",
        f"Industry: {core.identity.industry}",
    ]
    lines += [f"Offering {offering.id}: {offering.name}" for offering in core.offerings[:80]]
    lines += [f"Value: {item.statement}" for item in core.value_propositions[:20]]
    lines += [f"Audience: {item.segment}" for item in core.target_customers[:15]]
    lines += [f"Location: {city}" for city in core.locations.cities[:40]]
    lines += [f"Conversion: {action.type} {action.label}" for action in core.conversion_actions[:10]]
    return "\n".join(lines)


class KeywordAndQuestionEnricher:
    """r2: seed and expanded keywords, website-derived buyer questions and content pillars (plan 03 §5.10)."""

    def __init__(self, llm: LlmGateway) -> None:
        self._llm = llm

    async def enrich(self, ctx: EnrichmentContext) -> Enrichment:
        output = await self._call(ctx)
        if output is None:
            return self._fallback(ctx)
        return self._bind(ctx, output)

    async def _call(self, ctx: EnrichmentContext) -> EnrichmentOutput | None:
        if not self._llm.available:
            return None
        facts = "\n".join(
            f"{fact.fact_id} | {fact.kind.value} | {fact.text}"
            for fact in ctx.store.of_kind(FactKind.faq, FactKind.keyword_phrase, FactKind.offering)[:400]
        )
        prompt = PromptInput(
            trusted=[
                TrustedBlock(
                    label="context",
                    text=f"Operates in India or has Indic content: {'yes' if ctx.india else 'no'}",
                )
            ],
            untrusted=[
                UntrustedBlock(label="profile", source="verified_profile", text=_profile_digest(ctx.core)),
                UntrustedBlock(label="facts", source="verified_facts", text=facts or "none"),
                UntrustedBlock(
                    label="slugs", source="url_inventory", text="\n".join(ctx.slug_vocabulary[:300]) or "none"
                ),
            ],
        )
        return await self._llm.structured(
            TaskId.website_keyword_enrichment,
            EnrichmentOutput,
            prompt,
            payload=frozenset(
                {PayloadClass.public_web, PayloadClass.client_profile, PayloadClass.public_youtube}
            ),
            max_output_tokens=12_000,
        )

    def _offering_ids(self, ctx: EnrichmentContext, names: Sequence[str]) -> list[str]:
        by_name = {normalise_name(offering.name): offering.id for offering in ctx.core.offerings}
        return [by_name[name] for name in map(normalise_name, names) if name in by_name]

    def _keyword(
        self,
        ctx: EnrichmentContext,
        *,
        term: str,
        language: str,
        kind: str,
        tier: str,
        evidence: list[Evidence],
        basis: list[str],
        offering_ids: list[str],
        source: str | None = None,
        script: str | None = None,
    ) -> NicheKeyword:
        origin_meta = (
            ctx.claims.evidenced(evidence)
            if evidence
            else ctx.claims.based(basis, origin=ClaimOrigin.inferred)
        )
        resolved_source = source or (
            EXTRACTION_SOURCE.get(evidence[0].extraction, "page_text") if evidence else "llm_expansion"
        )
        detected = dominant_script(term)
        return NicheKeyword(
            id=stable_id("kw", term, language),
            term=term[:120],
            language=language or "en",
            script=script or (detected[1] if detected else None),
            kind=kind,  # type: ignore[arg-type]
            tier=tier,  # type: ignore[arg-type]
            source=resolved_source,  # type: ignore[arg-type]
            weight=1.0 if resolved_source in {"nav", "json_ld"} else 0.8 if tier == "seed" else 0.5,
            offering_ids=offering_ids,
            meta=origin_meta,
        )

    def _slug_evidence(self, ctx: EnrichmentContext, slug: str) -> list[Evidence]:
        token = slug.replace(" ", "-")
        url = next((item.url for item in ctx.inventory if token in path_of(item.url)), None)
        if url is None:
            return []
        return [
            Evidence(
                page_id=None,
                source_kind=SourceKind.site_direct,
                extraction=ExtractionKind.url_inventory,
                source_url=url,
                quote=token[:300],
                match_score=100,
                fetched_at=ctx.claims.now,
            )
        ]

    def _bind(self, ctx: EnrichmentContext, output: EnrichmentOutput) -> Enrichment:
        keywords: dict[str, NicheKeyword] = {}
        dropped = 0
        for item in output.seed_keywords[: CAPS["seed"]]:
            evidence = ctx.store.evidence_for(
                fact_id for fact_id in item.evidence_ids if fact_id in ctx.store.facts
            )
            slugs = [slug for slug in item.inventory_slugs if slug in ctx.slug_vocabulary]
            if not evidence and slugs:
                evidence = self._slug_evidence(ctx, slugs[0])
            if not evidence or contains_instruction(item.term):
                dropped += 1
                continue
            keyword = self._keyword(
                ctx,
                term=item.term,
                language=item.language,
                kind=item.kind,
                tier="seed",
                evidence=evidence,
                basis=[],
                offering_ids=self._offering_ids(ctx, item.offering_names),
            )
            keywords.setdefault(keyword.id, keyword)
        seed_by_term = {normalise_name(keyword.term): keyword.id for keyword in keywords.values()}
        offering_by_name = {normalise_name(offering.name): offering.id for offering in ctx.core.offerings}
        for item in output.expanded_keywords[: CAPS["expanded"]]:
            basis = [
                seed_by_term.get(normalise_name(term)) or offering_by_name.get(normalise_name(term))
                for term in item.basis_terms
            ]
            basis_ids = [value for value in basis if value]
            if not basis_ids or contains_instruction(item.term):
                dropped += 1
                continue
            keyword = self._keyword(
                ctx,
                term=item.term,
                language=item.language,
                kind=item.kind,
                tier="expanded",
                evidence=[],
                basis=basis_ids,
                offering_ids=self._offering_ids(ctx, item.offering_names),
                source="llm_expansion",
                script=item.script or None,
            )
            keywords.setdefault(keyword.id, keyword)
        questions = []
        valid_basis = set(ctx.store.facts) | {offering.id for offering in ctx.core.offerings}
        for item in output.questions[: CAPS["questions"]]:
            offering_ids = self._offering_ids(ctx, item.offering_names)
            basis_ids = [value for value in item.basis_ids if value in valid_basis] + offering_ids
            if not basis_ids or contains_instruction(item.text):
                dropped += 1
                continue
            questions.append(
                BuyerQuestionCandidate(
                    text=item.text[:300],
                    language=item.language or "en",
                    source="website_derived",
                    funnel_stage=item.funnel_stage,
                    intent=item.intent,
                    offering_ids=offering_ids,
                    basis_ids=basis_ids,
                )
            )
        pillars = []
        for item in output.pillars[: CAPS["pillars"]]:
            offering_ids = self._offering_ids(ctx, item.offering_names)
            if len(offering_ids) < 2 and len(item.question_texts) < 3:
                dropped += 1
                continue
            keyword_ids = [
                seed_by_term[n] for n in map(normalise_name, item.keyword_terms) if n in seed_by_term
            ]
            pillars.append(
                (
                    ContentPillar(
                        id=stable_id("pil", item.name),
                        name=item.name[:120],
                        description=item.description[:400],
                        offering_ids=offering_ids,
                        keyword_ids=keyword_ids,
                        meta=ctx.claims.based(offering_ids or keyword_ids),
                    ),
                    list(item.question_texts),
                )
            )
        return Enrichment(list(keywords.values()), questions, pillars, used_llm=True, dropped=dropped)

    def _fallback(self, ctx: EnrichmentContext) -> Enrichment:
        keywords: dict[str, NicheKeyword] = {}
        for offering in ctx.core.offerings[: CAPS["seed"]]:
            keyword = self._keyword(
                ctx,
                term=offering.name,
                language=ctx.core.identity.primary_language,
                kind="offering",
                tier="seed",
                evidence=list(offering.meta.evidence),
                basis=[offering.id],
                offering_ids=[offering.id],
            )
            keywords.setdefault(keyword.id, keyword)
        for fact in ctx.store.of_kind(FactKind.keyword_phrase):
            keyword = self._keyword(
                ctx,
                term=fact.text,
                language=fact.quote_lang or "en",
                kind="category",
                tier="seed",
                evidence=ctx.store.evidence(fact.fact_id),
                basis=[],
                offering_ids=[],
            )
            keywords.setdefault(keyword.id, keyword)
        questions = []
        offerings = ctx.core.offerings
        primary = [offering for offering in offerings if offering.is_primary][:5] or offerings[:5]
        for offering in primary:
            for template, stage, intent in FALLBACK_QUESTION_TEMPLATES:
                questions.append(
                    BuyerQuestionCandidate(
                        text=template.format(name=offering.name),
                        language=ctx.core.identity.primary_language,
                        source="website_derived",
                        funnel_stage=stage,  # type: ignore[arg-type]
                        intent=intent,  # type: ignore[arg-type]
                        offering_ids=[offering.id],
                        basis_ids=[offering.id],
                    )
                )
        return Enrichment(list(keywords.values())[: CAPS["seed"]], questions, [], used_llm=False)


def site_change_stats(
    pages: Sequence[ExtractedPage], inventory: Sequence[InventoryUrl], now: datetime
) -> SiteChangeStats:
    dates: list[date] = []
    for page in pages:
        if page.details.published_at is not None and page.page.page_type in {t.value for t in DATED_TYPES}:
            dates.append(page.details.published_at.date())
    for item in inventory:
        if item.page_type in DATED_TYPES and item.lastmod is not None:
            dates.append(item.lastmod.date())
    dates = sorted(set(dates))
    recent = [day for day in dates if day >= (now - timedelta(days=90)).date()]
    gaps = [(later - earlier).days for earlier, later in zip(dates, dates[1:], strict=False)]
    has_news = any(page.page.page_type == PageType.news_press.value for page in pages) or any(
        item.page_type is PageType.news_press for item in inventory
    )
    return SiteChangeStats(
        dated_pages=len(dates),
        posts_last_90d=len(recent),
        median_days_between_posts=float(statistics.median(gaps)) if gaps else None,
        last_content_update=dates[-1] if dates else None,
        has_news_or_changelog_section=has_news,
    )


def site_signal(stats: SiteChangeStats) -> float:
    median_gap = stats.median_days_between_posts if stats.median_days_between_posts is not None else 365
    gap_term = math.exp(-median_gap / 30)
    value = (
        0.4 * min(stats.posts_last_90d / 12, 1)
        + 0.3 * float(stats.has_news_or_changelog_section)
        + 0.3 * gap_term
    )
    return round(min(max(value, 0.0), 1.0), 4)


class FreshnessProfiler:
    """r3 plus the deterministic formula 0.5·llm + 0.3·industry prior + 0.2·site signal (plan 03 §5.13)."""

    def __init__(self, llm: LlmGateway) -> None:
        self._llm = llm

    async def profile(
        self,
        *,
        store: FactStore,
        core: CoreBundle,
        claims: ClaimFactory,
        stats: SiteChangeStats,
    ) -> FreshnessProfile:
        output = await self._call(store, core, stats)
        prior = INDUSTRY_PRIORS.get(core.identity.industry, DEFAULT_PRIOR)
        signal = site_signal(stats)
        llm_component = output.llm_score / 4 if output is not None else prior
        score = round(0.5 * llm_component + 0.3 * prior + 0.2 * signal, 4)
        sensitivity = bucket(score)
        offering_by_name = {normalise_name(offering.name): offering.id for offering in core.offerings}
        valid = set(store.facts) | set(offering_by_name.values())

        def offering_ids(names: Sequence[str]) -> list[str]:
            return [offering_by_name[name] for name in map(normalise_name, names) if name in offering_by_name]

        regulated_kind = REGULATED_INDUSTRIES.get(core.identity.industry)
        unclassified = output is not None and core.identity.industry == "other"
        basis = [core.offerings[0].id] if core.offerings else ["identity"]
        entities: list[WatchEntity] = []
        drivers: list[FreshnessDriver] = []
        topics: list[WatchTopic] = []
        seasons: list[SeasonalWindow] = []
        sources: list[SourceHint] = []
        if output is not None:
            for item in output.watch_entities[:40]:
                evidence = store.evidence_for(value for value in item.evidence_ids if value in store.facts)
                item_basis = [value for value in item.basis_ids if value in valid] or basis
                if contains_instruction(item.name):
                    continue
                entities.append(
                    WatchEntity(
                        name=item.name[:120],
                        aliases=item.aliases[:8],
                        kind=item.kind,
                        relation_to_client=item.relation_to_client,
                        importance=item.importance,
                        linked_offering_ids=offering_ids(item.offering_names),
                        meta=claims.evidenced(evidence)
                        if evidence
                        else claims.based(item_basis, origin=ClaimOrigin.inferred),
                    )
                )
            drivers = [
                FreshnessDriver(
                    statement=item.statement[:200],
                    meta=claims.based(
                        [value for value in item.basis_ids if value in valid] or basis,
                        origin=ClaimOrigin.inferred,
                    ),
                )
                for item in output.drivers[:10]
            ]
            topics = [
                WatchTopic(
                    query=item.query[:120],
                    language=item.language,
                    linked_offering_ids=offering_ids(item.offering_names),
                    event_types=item.event_types,
                )
                for item in output.watch_topics[:30]
                if not contains_instruction(item.query)
            ]
            seasons = [
                SeasonalWindow(
                    label=item.label[:80],
                    months=[month for month in item.months if 1 <= month <= 12],
                    geo=item.geo or None,
                    query_terms=item.query_terms[:8],
                    meta=claims.based(
                        [value for value in item.basis_ids if value in valid] or basis,
                        origin=ClaimOrigin.inferred,
                    ),
                )
                for item in output.seasonality[:12]
            ]
            sources = [
                SourceHint(kind=item.kind, domain=item.domain or None, reason=item.reason[:200])
                for item in output.suggested_sources[:12]
            ]
        rationale = (
            output.rationale[:600]
            if output is not None
            else f"Industry prior {prior:.2f} and site publishing signal {signal:.2f}; no model judgement available."
        )
        country = core.identity.country
        return FreshnessProfile(
            sensitivity=sensitivity,
            sensitivity_score=score,
            score_components={"llm": round(llm_component, 4), "industry_prior": prior, "site_signal": signal},
            rationale=rationale,
            drivers=drivers,
            watch_entities=entities,
            watch_topics=topics,
            event_types=output.event_types if output is not None else [],
            seasonality=seasons,
            suggested_sources=sources,
            regions=[country] if country else [],
            languages=core.identity.languages,
            excluded_topics=output.excluded_topics[:20] if output is not None else [],
            # the industry code decides; serving pharma or bank customers does not make a packaging firm regulated.
            # the llm's call counts only when the industry could not be classified
            regulated_domain=bool(regulated_kind) or (unclassified and output.regulated_domain),
            domain_kind=regulated_kind or (output.domain_kind if unclassified else "none"),  # type: ignore[arg-type]
            evergreen_expected=output.evergreen_expected
            if output is not None
            else sensitivity.value in {"very_low", "low", "medium"},
            refresh_policy=REFRESH_POLICIES[sensitivity],
            site_change_stats=stats,
            meta=claims.based(basis + [f"industry:{core.identity.industry}"], origin=ClaimOrigin.derived),
        )

    async def _call(
        self, store: FactStore, core: CoreBundle, stats: SiteChangeStats
    ) -> FreshnessOutput | None:
        if not self._llm.available:
            return None
        facts = "\n".join(
            f"{fact.fact_id} | {fact.kind.value} | {fact.text}"
            for fact in store.of_kind(
                FactKind.entity, FactKind.freshness_cue, FactKind.offering, FactKind.credential
            )[:300]
        )
        stats_text = (
            f"dated pages {stats.dated_pages}; posts in last 90 days {stats.posts_last_90d}; "
            f"median gap {stats.median_days_between_posts}; news section {stats.has_news_or_changelog_section}"
        )
        prompt = PromptInput(
            trusted=[TrustedBlock(label="site_stats", text=stats_text)],
            untrusted=[
                UntrustedBlock(label="profile", source="verified_profile", text=_profile_digest(core)),
                UntrustedBlock(label="facts", source="verified_facts", text=facts or "none"),
            ],
        )
        return await self._llm.structured(
            TaskId.website_freshness_profile,
            FreshnessOutput,
            prompt,
            payload=frozenset({PayloadClass.public_web, PayloadClass.client_profile}),
            max_output_tokens=8_000,
        )
