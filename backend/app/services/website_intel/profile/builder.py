import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Literal
from uuid import UUID, uuid4

from app.core.config import CrawlProfileSettings, WebsiteIntelSettings
from app.domain.profile import BusinessProfile, BuyerQuestion, FactKind, ProfileDiff
from app.domain.runs import ProfileFieldPayload, RunNotice, RunNoticeCode
from app.domain.web import CoverageFacet, SourceKind
from app.ports.clock import Clock
from app.ports.progress import ProgressReporter
from app.services.website_intel.acquisition.cascade import CascadeOutcome
from app.services.website_intel.acquisition.coverage import SITE_SOURCES
from app.services.website_intel.crawl.scoring import TYPE_WEIGHT
from app.services.website_intel.extract.boilerplate import CrossPageBoilerplateRemover
from app.services.website_intel.identity.locator import LocatedBusinessDetail
from app.services.website_intel.identity.related_brands import RelatedBrandDetector
from app.services.website_intel.models import ExtractedPage, PageType
from app.services.website_intel.profile.claims import ClaimFactory, normalise_name
from app.services.website_intel.profile.deterministic import DeterministicFactBuilder, home_urls
from app.services.website_intel.profile.differ import ProfileDiffer
from app.services.website_intel.profile.enrichers import (
    EnrichmentContext,
    FreshnessProfiler,
    KeywordAndQuestionEnricher,
    site_change_stats,
)
from app.services.website_intel.profile.evidence import EvidenceVerifier, FactStore
from app.services.website_intel.profile.llm import LlmGateway
from app.services.website_intel.profile.merger import ProfileMerger, Tombstone
from app.services.website_intel.profile.page_facts import MapCache, MapReport, PageFactExtractor
from app.services.website_intel.profile.quality import coverage_report, profile_quality
from app.services.website_intel.profile.synthesizer import CoreBundle, ProfileSynthesizer, SynthesisContext
from app.services.website_intel.profile.validators import Issue, ValidatorChain
from app.services.website_intel.questions.service import BuyerQuestionService
from app.services.website_intel.questions.sources import (
    SpotlitQuestionSource,
    UserQuestionSource,
    WebsiteDerivedSource,
    WebsiteFaqSource,
)

INDIC_LANGUAGES = frozenset({"hi", "ta", "te", "kn", "ml", "mr", "bn", "gu", "pa"})
PARTIAL_PAGES = 3
ProfileFieldName = Literal["offerings", "value_props", "locations", "conversion_actions", "buyer_questions"]


@dataclass
class BuildResult:
    profile: BusinessProfile
    questions: list[BuyerQuestion]
    facts: FactStore
    diff: ProfileDiff
    map_report: MapReport
    issues: list[Issue]
    llm_usage: dict[str, object]
    notices: list[RunNotice] = field(default_factory=list)


def map_order(pages: Sequence[ExtractedPage]) -> list[ExtractedPage]:
    def weight(page: ExtractedPage) -> float:
        try:
            page_type = PageType(page.page.page_type or "other")
        except ValueError:
            page_type = PageType.other
        return TYPE_WEIGHT.get(page_type, 0.25)

    return sorted(pages, key=lambda page: (-weight(page), page.page.url))


class ProfileBuilder:
    """turns acquired pages into a versioned, evidence-bound BusinessProfile (plan 03 §5.8–5.15)."""

    def __init__(
        self,
        *,
        llm: LlmGateway,
        settings: WebsiteIntelSettings,
        profile_settings: CrawlProfileSettings,
        clock: Clock,
        map_cache: MapCache | None = None,
    ) -> None:
        self._llm = llm
        self._settings = settings
        self._profile = profile_settings
        self._clock = clock
        self._map_cache = map_cache

    async def build(
        self,
        *,
        client_id: UUID,
        outcome: CascadeOutcome,
        located: LocatedBusinessDetail | None,
        reporter: ProgressReporter,
        version: int,
        previous: BusinessProfile | None = None,
        tombstones: Sequence[Tombstone] = (),
        existing_questions: Sequence[BuyerQuestion] = (),
        user_questions: Sequence[str] = (),
        crawl_run_id: UUID | None = None,
        country_hint: str | None = None,
    ) -> BuildResult:
        now = self._clock.now()
        pages = outcome.pages
        site = located.site if located else None
        claims = ClaimFactory(
            version=version,
            now=now,
            home_urls=home_urls(pages),
            stale_after=timedelta(days=self._settings.archive_stale_after_days),
        )
        store = FactStore()
        tables = DeterministicFactBuilder().build(
            pages, store, inventory=outcome.inventory, templates=outcome.templates
        )
        map_pages = map_order(pages)[: self._profile.map_page_budget]
        await reporter.step("profile.extracting", f"Reading facts from {len(map_pages)} pages")
        report = await PageFactExtractor(
            self._llm,
            EvidenceVerifier(
                threshold=self._settings.quote_match_threshold, min_quote_chars=self._settings.min_quote_chars
            ),
            char_cap=self._profile.map_page_char_cap,
            concurrency=self._settings.map_concurrency,
            cache=self._map_cache,
        ).extract(map_pages, self._boilerplate_free(map_pages), tables, store)
        kb_page = next((page for page in pages if page.page.source_kind is SourceKind.knowledge_base), None)
        name_guess = located.business_name if located else None
        brand_signals = (
            RelatedBrandDetector().detect(
                [page for page in pages if page.page.source_kind in SITE_SOURCES],
                business_name=name_guess,
                own_domain=site.registrable_domain,
                knowledge=outcome.knowledge_entity,
                knowledge_page=kb_page,
            )
            if site is not None
            else []
        )
        await reporter.step("profile.extracting", "Synthesising the business profile")
        core = await ProfileSynthesizer(self._llm, token_cap=self._profile.reduce_input_token_cap).synthesize(
            SynthesisContext(
                store=store,
                tables=tables,
                pages=pages,
                nav=outcome.nav,
                templates=outcome.templates,
                inventory_urls=frozenset(item.url for item in outcome.inventory),
                site=site,
                located=located,
                claims=claims,
                brand_signals=brand_signals,
                country_hint=country_hint,
                coverage_notes=outcome.notes[:10],
            )
        )
        await self._report_fields(reporter, core)
        india = core.identity.country == "IN" or any(
            (page.page.lang or "").split("-")[0] in INDIC_LANGUAGES for page in pages
        )
        stats = site_change_stats(pages, outcome.inventory, now)
        enrichment, freshness = await asyncio.gather(
            KeywordAndQuestionEnricher(self._llm).enrich(
                EnrichmentContext(
                    store=store,
                    core=core,
                    claims=claims,
                    inventory=outcome.inventory,
                    slug_vocabulary=tables.slug_vocabulary,
                    india=india,
                )
            ),
            FreshnessProfiler(self._llm).profile(store=store, core=core, claims=claims, stats=stats),
        )
        warnings = [*core.warnings, *outcome.notes]
        coverage = coverage_report(
            pages=pages,
            used=len(map_pages),
            discovered=len(outcome.inventory),
            blocked=outcome.blocked,
            robots_disallowed=outcome.stats.get("robots_disallowed", 0),
            has_prices=bool(tables.prices),
            has_faq_facts=bool(store.of_kind(FactKind.faq)),
            warnings=warnings,
        )
        quality = profile_quality(
            pages_used=len([page for page in map_pages if page.page.source_kind in SITE_SOURCES]),
            verified_facts=len(store.facts),
            dropped_facts=store.dropped + core.dropped + enrichment.dropped,
            injection_flags=sum(page.page.injection_flags for page in pages),
            offerings=len(core.offerings),
            conversions=len(core.conversion_actions),
            value_props=len(core.value_propositions),
            facets_covered=len(outcome.covered),
            facets_total=len(CoverageFacet),
        )
        profile = BusinessProfile(
            profile_id=uuid4(),
            client_id=client_id,
            version=version,
            parent_version=previous.version if previous else None,
            crawl_run_id=crawl_run_id,
            status="awaiting_confirmation",
            generated_at=now,
            identity=core.identity,
            offerings=core.offerings,
            value_propositions=core.value_propositions,
            target_customers=core.target_customers,
            locations=core.locations,
            conversion_actions=core.conversion_actions,
            brand_terms=core.brand_terms,
            niche_keywords=enrichment.keywords,
            competitor_hints=core.competitor_hints,
            content_pillars=[pillar for pillar, _ in enrichment.pillars],
            freshness=freshness,
            coverage=coverage,
            quality=quality,
        )
        profile, issues = ValidatorChain().apply(profile)
        questions = await BuyerQuestionService(
            [
                UserQuestionSource(user_questions, language=core.identity.primary_language),
                SpotlitQuestionSource(),
                WebsiteFaqSource(store),
                WebsiteDerivedSource(enrichment.questions),
            ]
        ).collect(client_id, profile, claims, existing_questions)
        question_ids = {normalise_name(question.text): question.id for question in questions}
        pillars = [
            pillar.model_copy(
                update={
                    "buyer_question_ids": [
                        question_ids[n] for n in map(normalise_name, texts) if n in question_ids
                    ]
                }
            )
            for pillar, texts in enrichment.pillars
            if any(pillar.id == kept.id for kept in profile.content_pillars)
        ]
        profile = profile.model_copy(
            update={"buyer_question_ids": [question.id for question in questions], "content_pillars": pillars}
        )
        merged = ProfileMerger().merge(previous, profile, tombstones)
        diff = ProfileDiffer().diff(previous, merged)
        notices = self._notices(merged, report, core.used_llm, enrichment.used_llm)
        for notice in notices:
            await reporter.notice(notice)
        await reporter.step("profile.ready", f"Profile v{merged.version} is ready for review")
        return BuildResult(
            profile=merged,
            questions=questions,
            facts=store,
            diff=diff,
            map_report=report,
            issues=issues,
            llm_usage=self._llm.usage.as_dict(),
            notices=notices,
        )

    def _boilerplate_free(self, pages: Sequence[ExtractedPage]) -> dict[UUID, str]:
        site_pages = [page for page in pages if page.page.source_kind in SITE_SOURCES]
        cleaned = CrossPageBoilerplateRemover().remove([page.page.text for page in site_pages])
        return {page.page.page_id: text for page, text in zip(site_pages, cleaned, strict=True)}

    @staticmethod
    async def _report_fields(reporter: ProgressReporter, core: CoreBundle) -> None:
        fields: tuple[tuple[ProfileFieldName, list[str]], ...] = (
            ("offerings", [item.name for item in core.offerings]),
            ("value_props", [item.statement for item in core.value_propositions]),
            ("locations", core.locations.cities),
            ("conversion_actions", [item.label for item in core.conversion_actions]),
        )
        for name, values in fields:
            if values:
                await reporter.insight(
                    f"Found {len(values)} {name.replace('_', ' ')}",
                    ProfileFieldPayload(
                        field=name, count=len(values), preview=[value[:80] for value in values[:5]]
                    ),
                )

    @staticmethod
    def _notices(
        profile: BusinessProfile, report: MapReport, core_llm: bool, enrichment_llm: bool
    ) -> list[RunNotice]:
        notices = []
        if not core_llm or not enrichment_llm or report.llm_failed:
            notices.append(
                RunNotice(
                    code=RunNoticeCode.llm_fallback,
                    stage="build_profile",
                    message="We built a basic profile; detailed extraction will retry shortly.",
                    detail={"failed_pages": len(report.llm_failed)},
                )
            )
        if profile.coverage.pages_used < PARTIAL_PAGES:
            notices.append(
                RunNotice(
                    code=RunNoticeCode.partial_profile,
                    stage="build_profile",
                    message="We could read only a few pages, so the profile is partial.",
                )
            )
        if profile.quality.thin_site:
            notices.append(
                RunNotice(
                    code=RunNoticeCode.thin_data,
                    stage="build_profile",
                    message="We found little detail. Add your main services to get sharper recommendations.",
                    suggest={"action": "add_services"},
                )
            )
        return notices
