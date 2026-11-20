import hashlib
from collections.abc import Mapping
from typing import Any

from sqlalchemy import select

import app.adapters.context  # noqa: F401  registers every ContextSource tier
from app.core.registry import artifact_schema_registry, stage_registry
from app.core.tenancy import TenantScope
from app.domain.profile import BusinessProfile
from app.domain.runs import RunNotice, RunNoticeCode
from app.domain.web import CoverageFacet, SourceKind
from app.domain.youtube import OwnershipLabel
from app.models.tenancy import Client
from app.pipeline.client_settings import merged, read_settings, write_settings
from app.pipeline.contracts import (
    BudgetKind,
    RunConfigSnapshot,
    RunContext,
    StageBudget,
    StagePolicy,
    StageResult,
)
from app.ports.artifacts import ArtifactKey
from app.ports.context import BusinessLookup
from app.services.website_intel.artifacts import SiteAcquisition
from app.services.website_intel.identity.locator import LocatedBusinessDetail
from app.services.website_intel.persistence import PgMapCache, WebsiteIntelStore
from app.services.website_intel.profile.market import market_timezone
from app.services.website_intel.profile.prompts import WEBSITE_PROMPTS, prompt_version
from app.services.website_intel.service import WebsiteIntelService, deps_from_container

LOCATED_BUSINESS = ArtifactKey(
    name="located_business", model=LocatedBusinessDetail, schema_version=1, contains_api_data=True
)
SITE_ACQUISITION = ArtifactKey(
    name="site_acquisition", model=SiteAcquisition, schema_version=1, contains_api_data=True
)
BUSINESS_PROFILE = ArtifactKey(
    name="business_profile", model=BusinessProfile, schema_version=1, contains_api_data=False
)
for _key in (LOCATED_BUSINESS, SITE_ACQUISITION, BUSINESS_PROFILE):
    artifact_schema_registry.register(_key.name, _key.schema_version, _key.model)

SITE_TIERS = frozenset({SourceKind.site_direct, SourceKind.site_rendered})
CHANNEL_STATUS = {OwnershipLabel.verified: "verified", OwnershipLabel.rejected: "rejected"}
# 02's verdict method in the api's verified_by vocabulary
VERIFIED_BY = {"link": "website_link", "oauth": "oauth", "user": "manual"}


def explicit_country(snapshot: RunConfigSnapshot) -> str | None:
    """the market the user picked; a browser locale must not decide how the site's phone numbers parse."""
    return snapshot.market.country if snapshot.market_explicit else None


async def record_market(ctx: RunContext[Any], profile: BusinessProfile) -> None:
    """writes the profile's evidence-based market to the client unless the user picked one."""
    if ctx.snapshot.market_explicit:
        return
    identity = profile.identity
    async with ctx.deps.session_factory() as session, session.begin():
        client = await session.get(Client, ctx.client_id, with_for_update=True)
        if client is None or client.workspace_id != ctx.workspace_id:
            return
        settings = read_settings(client)
        if settings.market_explicit:
            return
        country = identity.country
        language = f"{identity.primary_language}-{country}" if country else client.language
        timezone = (market_timezone(country, client.timezone) if country else None) or client.timezone
        if (client.country, client.language, client.timezone) == (country, language, timezone):
            return
        client.country, client.language, client.timezone = country, language, timezone
        write_settings(client, merged(settings, config_version=settings.config_version + 1))


def website_service(ctx: RunContext[Any]) -> WebsiteIntelService:
    return WebsiteIntelService(deps_from_container(ctx.deps))


def scope(ctx: RunContext[Any]) -> TenantScope:
    return TenantScope(workspace_id=ctx.workspace_id, client_id=ctx.client_id)


async def record_located(ctx: RunContext[Any], located: LocatedBusinessDetail) -> None:
    """writes the located site and channel (with 02's ownership verdict) back to the client row."""
    async with ctx.deps.session_factory() as session, session.begin():
        client = await session.get(Client, ctx.client_id, with_for_update=True)
        if client is None or client.workspace_id != ctx.workspace_id:
            return
        if located.site is not None and client.website_url is None:
            client.website_url = located.site.canonical_origin
            client.website_domain = located.site.registrable_domain
        channel_id = located.channel_id
        if (
            channel_id is None
            or client.own_channel_status == "verified"
            and client.own_channel_id != channel_id
        ):
            return
        taken = (
            await session.execute(
                select(Client.id).where(
                    Client.workspace_id == ctx.workspace_id,
                    Client.own_channel_id == channel_id,
                    Client.id != ctx.client_id,
                    Client.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if taken is not None:
            return
        ownership = located.ownership
        client.own_channel_id = channel_id
        client.own_channel_status = (
            CHANNEL_STATUS.get(ownership.label, "proposed") if ownership else "proposed"
        )
        if ownership is not None:
            verified = ownership.label is OwnershipLabel.verified
            client.own_channel_verification = [
                {
                    "verified_by": VERIFIED_BY.get(ownership.method or "") if verified else None,
                    "label": ownership.label.value,
                    "score": ownership.score,
                    "signals": [signal.model_dump(mode="json") for signal in ownership.signals],
                }
            ]


@stage_registry.register("resolve_inputs")
class ResolveInputsStage:
    """normalises the website, resolves the channel through 02 and links the two by real links only."""

    name = "resolve_inputs"
    version = 2
    depends_on: tuple[str, ...] = ()
    optional_deps: tuple[str, ...] = ()
    produces = LOCATED_BUSINESS
    policy = StagePolicy(timeout_s=90, soft_timeout_s=45, max_attempts=2)
    weight = 5.0

    def enabled(self, snapshot: RunConfigSnapshot) -> bool:
        return True

    def fingerprint_inputs(self, ctx: RunContext[Any]) -> Mapping[str, Any]:
        snapshot = ctx.snapshot
        return {
            "website": snapshot.website_url,
            "channel": snapshot.own_channel_id or snapshot.channel_ref,
            "name": snapshot.business_name,
            "country": snapshot.market.country,
        }

    async def run(self, ctx: RunContext[Any]) -> StageResult[LocatedBusinessDetail]:
        snapshot = ctx.snapshot
        channel_ref = snapshot.own_channel_id or snapshot.channel_ref
        if not (snapshot.website_url or channel_ref or snapshot.business_name):
            raise ValueError("Add your website, YouTube channel or business name.")
        service = website_service(ctx)
        located = await service.locate(
            BusinessLookup(
                business_name=snapshot.business_name,
                website_url=snapshot.website_url,
                channel_ref=channel_ref,
                country_hint=snapshot.market.country,
            ),
            ctx.reporter,
            client_id=ctx.client_id,
            run_id=ctx.run_id,
        )
        await record_located(ctx, located)
        notices = tuple(
            RunNotice(code=RunNoticeCode.partial_profile, stage=self.name, message=note[:200])
            for note in located.notes[:3]
        )
        status = "degraded" if located.site is None and located.channel_id is None else "succeeded"
        return StageResult(
            status=status,
            artifact=located,
            counts={"site": int(located.site is not None), "channel": int(located.channel_id is not None)},
            notices=notices,
        )


@stage_registry.register("crawl_site")
class CrawlSiteStage:
    """runs the acquisition cascade so a blocked or missing site still yields business context (§14)."""

    name = "crawl_site"
    version = 3
    depends_on: tuple[str, ...] = ("resolve_inputs",)
    optional_deps: tuple[str, ...] = ()
    produces = SITE_ACQUISITION
    policy = StagePolicy(
        timeout_s=600, soft_timeout_s=300, max_attempts=2, budget=StageBudget(tavily_credits=60)
    )
    weight = 35.0

    def enabled(self, snapshot: RunConfigSnapshot) -> bool:
        return True

    def fingerprint_inputs(self, ctx: RunContext[Any]) -> Mapping[str, Any]:
        settings = ctx.deps.settings.website_intel
        digest = hashlib.sha256(settings.model_dump_json().encode()).hexdigest()[:16]
        return {"website": ctx.snapshot.website_url, "settings": digest, "day": ctx.as_of.date().isoformat()}

    async def run(self, ctx: RunContext[Any]) -> StageResult[SiteAcquisition]:
        located = await ctx.artifacts.get(LOCATED_BUSINESS)
        service = website_service(ctx)
        started = ctx.clock.now()
        try:
            outcome = await service.acquire(
                located,
                ctx.reporter,
                client_id=ctx.client_id,
                run_id=ctx.run_id,
                country_hint=explicit_country(ctx.snapshot),
                tavily_credit_cap=ctx.budget.remaining(BudgetKind.tavily_credits),
            )
        finally:
            await service.aclose()
        ctx.budget.charge(BudgetKind.tavily_credits, outcome.tavily_credits)
        ctx.budget.charge(BudgetKind.pages, outcome.fetches)
        website_id = crawl_run_id = None
        if located.site is not None:
            async with ctx.deps.uow() as uow:
                store = WebsiteIntelStore(uow.session, scope(ctx))
                website_id = await store.upsert_website(
                    located.site,
                    scope_mode="subpath" if located.site.focus_path else "domain",
                    ownership_attested=False,
                    related=[],
                )
                crawl_run_id = await store.record_crawl(
                    website_id,
                    run_id=ctx.run_id,
                    trigger="onboarding",
                    profile=service.profile_kind,
                    outcome=outcome,
                    started_at=started,
                    finished_at=ctx.clock.now(),
                )
                await store.upsert_pages(website_id, crawl_run_id, outcome.pages)
                await store.upsert_inventory(website_id, outcome, ctx.clock.now())
                full = SiteAcquisition.from_outcome(outcome, website_id=website_id, crawl_run_id=crawl_run_id)
                await store.save_acquisition(crawl_run_id, full.compressed())
                await uow.commit()
        artifact = SiteAcquisition.from_outcome(outcome, website_id=website_id, crawl_run_id=crawl_run_id)
        if crawl_run_id is not None:
            artifact = artifact.pointer()
        site_pages = sum(1 for page in outcome.pages if page.page.source_kind in SITE_TIERS)
        notices = []
        if outcome.missing:
            notices.append(
                RunNotice(
                    code=RunNoticeCode.partial_profile,
                    stage=self.name,
                    message="Some business details were not found: "
                    + ", ".join(sorted(facet.value for facet in outcome.missing)),
                    detail={"missing": sorted(facet.value for facet in outcome.missing)},
                )
            )
        status = "succeeded" if site_pages and CoverageFacet.home in outcome.covered else "degraded"
        if not outcome.pages:
            status = "degraded"
        return StageResult(
            status=status,
            artifact=artifact,
            counts={"pages": len(outcome.pages), "site_pages": site_pages, "blocked": len(outcome.blocked)},
            notices=tuple(notices),
        )


@stage_registry.register("build_profile")
class BuildProfileStage:
    """map → verify → reduce → enrich → validate → merge with user edits → persist a new version."""

    name = "build_profile"
    version = 4
    depends_on: tuple[str, ...] = ("resolve_inputs", "crawl_site")
    optional_deps: tuple[str, ...] = ()
    produces = BUSINESS_PROFILE
    policy = StagePolicy(timeout_s=600, soft_timeout_s=360, max_attempts=2)
    weight = 20.0

    def enabled(self, snapshot: RunConfigSnapshot) -> bool:
        return True

    def fingerprint_inputs(self, ctx: RunContext[Any]) -> Mapping[str, Any]:
        return {prompt_id: prompt_version(prompt_id) for prompt_id in WEBSITE_PROMPTS}

    async def run(self, ctx: RunContext[Any]) -> StageResult[BusinessProfile]:
        located = await ctx.artifacts.get(LOCATED_BUSINESS)
        acquisition = await ctx.artifacts.get(SITE_ACQUISITION)
        service = website_service(ctx)
        async with ctx.deps.uow() as uow:
            store = WebsiteIntelStore(uow.session, scope(ctx))
            if acquisition.stored and acquisition.crawl_run_id is not None:
                payload = await store.acquisition(acquisition.crawl_run_id)
                if payload is None:
                    raise ValueError("the crawl's pages expired; run the crawl again")
                acquisition = SiteAcquisition.decompressed(payload)
            previous = await store.latest_profile()
            version = await store.next_version()
            tombstones = await store.tombstones()
            existing = await store.buyer_questions()
        result = await service.build(
            client_id=ctx.client_id,
            run_id=ctx.run_id,
            located=located,
            outcome=acquisition.to_outcome(),
            reporter=ctx.reporter,
            version=version,
            previous=previous,
            tombstones=tombstones,
            existing_questions=existing,
            crawl_run_id=acquisition.crawl_run_id,
            country_hint=explicit_country(ctx.snapshot),
            map_cache=PgMapCache(ctx.deps.session_factory, scope(ctx)),
            llm_cost_cap_usd=ctx.budget.remaining(BudgetKind.llm_usd),
        )
        usage = result.llm_usage
        cost = float(usage.get("cost_usd", 0.0))  # type: ignore[arg-type]
        remaining = ctx.budget.remaining(BudgetKind.llm_usd)
        # concurrent calls can overshoot the cap; the spend already happened, so record what fits
        ctx.budget.charge(BudgetKind.llm_usd, cost if remaining is None else min(cost, remaining))
        async with ctx.deps.uow() as uow:
            store = WebsiteIntelStore(uow.session, scope(ctx))
            await store.save_profile(
                result.profile,
                created_by="system",
                llm_usage=usage,
                prompt_versions={prompt_id: prompt_version(prompt_id) for prompt_id in WEBSITE_PROMPTS},
            )
            await store.save_diff(result.diff)
            await store.save_questions(result.questions)
            await uow.commit()
        await record_market(ctx, result.profile)
        profile = result.profile
        status = "degraded" if result.notices else "succeeded"
        return StageResult(
            status=status,
            artifact=profile,
            counts={
                "offerings": len(profile.offerings),
                "conversion_actions": len(profile.conversion_actions),
                "buyer_questions": len(result.questions),
                "facts": len(result.facts.facts),
            },
            notices=tuple(result.notices),
        )
