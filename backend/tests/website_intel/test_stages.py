import json
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select

import app.pipeline.stages.website_intel as stages
from app.adapters.llm.testing import FakeLLMClient
from app.core.config import CrawlProfileSettings
from app.core.db import SessionFactory, UnitOfWork
from app.domain.runs import RunKind, RunNoticeCode, RunTrigger
from app.models.runs import PipelineRun
from app.models.website import BusinessProfile as ProfileRow
from app.models.website import BuyerQuestion, CrawledPage, CrawlRun, ProfileDiff, Website
from app.pipeline.context import RunBudgetLedger
from app.pipeline.contracts import BudgetKind, Market, RunBudget, RunConfigSnapshot, RunContext, StageBudget
from app.ports.artifacts import ArtifactKey
from app.ports.llm import LLMBudgetExceeded, LLMError, LLMErrorKind, TaskId
from app.services.website_intel.artifacts import SiteAcquisition
from app.services.website_intel.inputs.ssrf import SsrfGuard
from app.services.website_intel.service import WebsiteIntelService, deps_from_container
from tests.conftest import make_settings
from tests.contracts.test_pipeline_contracts import Token, UnlimitedLedger
from tests.foundation.factories import create_tenant, insert_row
from tests.website_intel.conftest import NOW, RecordingReporter, html_page

pytestmark = pytest.mark.db

ORIGIN = "https://www.smiledental.in"
FAQ = {
    "@context": "https://schema.org",
    "@type": "FAQPage",
    "mainEntity": [
        {
            "@type": "Question",
            "name": "Do dental implants hurt?",
            "acceptedAnswer": {"@type": "Answer", "text": "Rarely."},
        }
    ],
}
CLINIC = {
    "@context": "https://schema.org",
    "@type": "Dentist",
    "name": "Smile Dental Clinic",
    "address": {
        "@type": "PostalAddress",
        "streetAddress": "12 MG Road",
        "addressLocality": "Pune",
        "addressCountry": "IN",
    },
    "telephone": "+91 20 1234 5678",
}
NAV = [
    ("/services/dental-implants", "Dental Implants"),
    ("/services/root-canal", "Root Canal"),
    ("/about-us", "About us"),
    ("/contact-us", "Contact us"),
]


def site(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    links = [(ORIGIN + href, text) for href, text in NAV] + [
        ("tel:+912012345678", "Call now"),
        ("https://wa.me/912012345678", "Chat on WhatsApp"),
    ]
    pages = {
        "/robots.txt": (b"User-agent: *\nAllow: /\n", "text/plain"),
        "/": (
            html_page(
                "Smile Dental Clinic | Dentist in Pune",
                "<p>"
                + "Smile Dental Clinic offers dental implants and painless root canal treatment in Pune. "
                * 12
                + "</p>"
                "<p>Implants start at ₹25,000.</p><a href='/book-appointment'>Book an appointment</a>",
                links=links,
                head=f"<script type='application/ld+json'>{json.dumps(CLINIC)}</script>"
                f"<script type='application/ld+json'>{json.dumps(FAQ)}</script>",
            ),
            "text/html",
        ),
    }
    for href, text in NAV:
        pages[href] = (
            html_page(text, "<p>" + f"{text} at Smile Dental Clinic in Pune. " * 30 + "</p>", links=links),
            "text/html",
        )
    if request.url.host == "smiledental.in":
        return httpx.Response(301, headers={"location": ORIGIN + path})
    if request.url.host != "www.smiledental.in" or path not in pages:
        return httpx.Response(404, text="not found")
    body, content_type = pages[path]
    return httpx.Response(200, content=body, headers={"content-type": content_type})


async def public(host: str, port: int) -> list[str]:
    return ["93.184.216.34"]


class FakeContainer:
    def __init__(
        self, session_factory: SessionFactory, http: httpx.AsyncClient, llm: FakeLLMClient, clock: Any
    ) -> None:
        settings = make_settings()
        fast = CrawlProfileSettings(min_delay_s=0.0, fetch_budget=10, discovery_fetch_budget=4)
        self.settings = settings.model_copy(
            update={"website_intel": settings.website_intel.model_copy(update={"client": fast})}
        )
        self.clock = clock
        self.http = http
        self.redis = None
        self.rate_limiter = None
        self.session_factory = session_factory
        self._llm = llm

    def uow(self, *, read_only: bool = False) -> UnitOfWork:
        return UnitOfWork(self.session_factory, read_only=read_only)

    def adapter(self, port: str) -> Any:
        if port == "llm":
            return self._llm
        raise LookupError(port)


class Artifacts:
    def __init__(self) -> None:
        self.values: dict[str, Any] = {}

    async def get(self, key: ArtifactKey[Any]) -> Any:
        return self.values[key.name]

    async def get_optional(self, key: ArtifactKey[Any]) -> Any:
        return self.values.get(key.name)


def llm(client_class: type[FakeLLMClient] = FakeLLMClient) -> FakeLLMClient:
    def map_reply(call: Any) -> dict[str, Any]:
        pages = []
        for block in call.input.untrusted:
            if "painless root canal" in block.text:
                pages.append(
                    {
                        "page_id": block.label,
                        "facts": [
                            {
                                "kind": "offering",
                                "text": "Root canal treatment",
                                "quote": "painless root canal treatment in Pune",
                            }
                        ],
                    }
                )
        return {"pages": pages}

    freshness = {"llm_score": 1, "rationale": "dental demand is steady"}
    return client_class(
        {
            TaskId.website_page_selection: {"picks": []},
            TaskId.website_page_map: map_reply,
            TaskId.website_freshness_profile: freshness,
        },
        default=lambda call: LLMError(LLMErrorKind.overloaded, "busy"),
    )


async def run_onboarding_stages(
    session_factory: SessionFactory,
    clock: Any,
    monkeypatch: pytest.MonkeyPatch,
    *,
    client: FakeLLMClient,
    budget: Any,
    slug: str,
) -> tuple[Any, Artifacts, RecordingReporter, dict[str, Any]]:
    async with session_factory() as session:
        scope = await create_tenant(session, slug, website_url=ORIGIN)
        run = await insert_row(session, PipelineRun.__table__, scope, {"kind": "onboarding"})
        await session.commit()
    results: dict[str, Any] = {}
    async with httpx.AsyncClient(transport=httpx.MockTransport(site)) as http:
        container = FakeContainer(session_factory, http, client, clock)

        def service(ctx: Any) -> WebsiteIntelService:
            deps = deps_from_container(ctx.deps)
            deps.guard = SsrfGuard(public)
            return WebsiteIntelService(deps)

        monkeypatch.setattr(stages, "website_service", service)
        artifacts = Artifacts()
        snapshot = RunConfigSnapshot(
            workspace_id=scope.workspace_id,
            client_id=scope.client_id,
            config_version=1,
            config_hash="x",
            window_days=30,
            topic_set_version=1,
            website_url="smiledental.in",
            market=Market(country="IN", language="en", timezone="Asia/Kolkata"),
        )
        reporter = RecordingReporter()
        ctx = RunContext(
            run_id=run["id"],
            kind=RunKind.onboarding,
            trigger=RunTrigger.user,
            attempt=1,
            as_of=NOW,
            snapshot=snapshot,
            clock=clock,
            budget=budget,
            reporter=reporter,
            artifacts=artifacts,
            cancel=Token(),
            deps=container,
        )
        for stage in (stages.ResolveInputsStage(), stages.CrawlSiteStage(), stages.BuildProfileStage()):
            result = await stage.run(ctx)
            assert result.artifact is not None
            artifacts.values[stage.produces.name] = result.artifact
            results[stage.name] = result
    return scope, artifacts, reporter, results


async def test_onboarding_stages_persist_crawl_profile_and_questions(
    session_factory: SessionFactory, clock: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    scope, artifacts, reporter, _ = await run_onboarding_stages(
        session_factory, clock, monkeypatch, client=llm(), budget=UnlimitedLedger(), slug="smile"
    )
    located = artifacts.values["located_business"]
    assert located.site.canonical_origin == ORIGIN
    acquisition = artifacts.values["site_acquisition"]
    # the artifact is a pointer; the pages travel gzipped on the crawl run row
    assert acquisition.crawl_run_id is not None and acquisition.stored and not acquisition.pages
    async with session_factory() as session:
        stored = await session.scalar(
            select(CrawlRun.acquisition_gz).where(CrawlRun.id == acquisition.crawl_run_id)
        )
    assert len(SiteAcquisition.decompressed(stored).pages) >= 4
    profile = artifacts.values["business_profile"]
    assert profile.identity.name == "Smile Dental Clinic"
    assert {action.type for action in profile.conversion_actions} >= {"call", "whatsapp", "book_appointment"}
    assert profile.locations.cities == ["Pune"]
    assert "crawl.source_started" in reporter.names()
    async with session_factory() as session:
        counts = {
            model.__tablename__: await session.scalar(
                select(func.count()).select_from(model).where(model.client_id == scope.client_id)
            )
            for model in (Website, CrawlRun, CrawledPage, ProfileRow, ProfileDiff, BuyerQuestion)
        }
        questions = (
            (
                await session.execute(
                    select(BuyerQuestion.text).where(BuyerQuestion.client_id == scope.client_id)
                )
            )
            .scalars()
            .all()
        )
    assert counts["websites"] == 1 and counts["crawl_runs"] == 1 and counts["crawled_pages"] >= 4
    assert counts["business_profiles"] == 1 and counts["profile_diffs"] == 1
    assert "Do dental implants hurt?" in questions


class CostedLLM(FakeLLMClient):
    """each call costs more than its worst-case estimate, so the first map call overshoots a small cap."""

    async def generate_structured(self, **kwargs: Any) -> Any:
        budget = kwargs.get("budget")
        if budget is not None and budget.max_cost_usd is not None and budget.max_cost_usd < 0.02:
            raise LLMBudgetExceeded("call estimate exceeds the call budget")
        result = await super().generate_structured(**kwargs)
        return result.model_copy(update={"cost_usd": 0.06})


async def test_build_profile_degrades_instead_of_failing_when_llm_budget_runs_out(
    session_factory: SessionFactory, clock: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_budget = RunBudget(wall_s=600, youtube_units=0, search_calls=0, llm_usd=0.05, tavily_credits=40)
    ledger = RunBudgetLedger(run_budget).for_stage(StageBudget())
    _, artifacts, _, results = await run_onboarding_stages(
        session_factory, clock, monkeypatch, client=llm(CostedLLM), budget=ledger, slug="smile-budget"
    )
    result = results["build_profile"]
    assert result.status == "degraded"
    assert RunNoticeCode.llm_fallback in {notice.code for notice in result.notices}
    assert artifacts.values["business_profile"].identity.name == "Smile Dental Clinic"
    assert ledger.remaining(BudgetKind.llm_usd) == 0.0
